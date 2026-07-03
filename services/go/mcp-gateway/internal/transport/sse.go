package transport

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

// SSETransport implements MCP transport over Server-Sent Events (SSE) over HTTP.
//
// Architecture per MCP spec:
//   - GET  /sse       — Client connects, server keeps connection open, streams
//                        server→client messages as SSE events.
//   - POST /message   — Client→server messages (JSON-RPC requests/notifications).
//
// The SSE endpoint assigns each client a session ID and streams events on that
// session's channel. The POST endpoint looks up the session by a header/param
// and dispatches the message.
type SSETransport struct {
	logger   *log.Logger
	handler  MessageHandler
	sessions map[string]*sseSession
	mu       sync.RWMutex
}

// sseSession represents a single connected SSE client.
type sseSession struct {
	id      string
	events  chan string
	ctx     context.Context
	cancel  context.CancelFunc
	created time.Time
}

// NewSSETransport creates an SSE transport.
func NewSSETransport(handler MessageHandler) *SSETransport {
	return &SSETransport{
		logger:   log.New(os.Stderr, "[mcp-sse] ", log.LstdFlags|log.Lmsgprefix),
		handler:  handler,
		sessions: make(map[string]*sseSession),
	}
}

// ── HTTP Handlers ────────────────────────────────────────────────────

// ServeHTTP dispatches to the SSE or message endpoint based on method and path.
// Supports both exact paths (/sse, /message) and sub-paths (/mcp/sse, /mcp/message).
func (t *SSETransport) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path
	switch {
	case r.Method == http.MethodGet && (path == "/sse" || strings.HasSuffix(path, "/sse")):
		t.handleSSE(w, r)
	case r.Method == http.MethodPost && (path == "/message" || strings.HasSuffix(path, "/message")):
		t.handleMessage(w, r)
	default:
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	}
}

// handleSSE establishes a long-lived SSE connection for server→client messages.
func (t *SSETransport) handleSSE(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming not supported", http.StatusInternalServerError)
		return
	}

	// Create session
	id := fmt.Sprintf("sse-%d", time.Now().UnixNano())
	ctx, cancel := context.WithCancel(context.Background())
	sess := &sseSession{
		id:      id,
		events:  make(chan string, 64),
		ctx:     ctx,
		cancel:  cancel,
		created: time.Now(),
	}

	t.mu.Lock()
	t.sessions[id] = sess
	t.mu.Unlock()

	defer func() {
		t.mu.Lock()
		delete(t.sessions, id)
		t.mu.Unlock()
		cancel()
	}()

	// SSE headers
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)

	// Send endpoint event with the session ID so the client knows where to POST
	fmt.Fprintf(w, "event: endpoint\ndata: /message?sessionId=%s\n\n", id)
	flusher.Flush()

	t.logger.Printf("SSE client connected: session=%s", id)

	// Stream events until client disconnects or session is cancelled
	for {
		select {
		case <-r.Context().Done():
			t.logger.Printf("SSE client disconnected: session=%s", id)
			return
		case <-ctx.Done():
			return
		case msg := <-sess.events:
			fmt.Fprintf(w, "event: message\ndata: %s\n\n", msg)
			flusher.Flush()
		}
	}
}

// handleMessage accepts client→server messages via HTTP POST.
func (t *SSETransport) handleMessage(w http.ResponseWriter, r *http.Request) {
	sessionID := r.URL.Query().Get("sessionId")
	if sessionID == "" {
		http.Error(w, `{"error":"sessionId query param required"}`, http.StatusBadRequest)
		return
	}

	t.mu.RLock()
	sess, ok := t.sessions[sessionID]
	t.mu.RUnlock()
	if !ok {
		http.Error(w, `{"error":"session not found"}`, http.StatusNotFound)
		return
	}

	// Read body
	body := make([]byte, r.ContentLength)
	if _, err := r.Body.Read(body); err != nil && err.Error() != "EOF" {
		http.Error(w, `{"error":"failed to read body"}`, http.StatusBadRequest)
		return
	}
	r.Body.Close()

	if !json.Valid(body) {
		http.Error(w, `{"error":"invalid JSON"}`, http.StatusBadRequest)
		return
	}

	raw := json.RawMessage(body)
	responses, err := t.handler(r.Context(), raw)
	if err != nil {
		t.logger.Printf("handler error for session %s: %v", sessionID, err)
		http.Error(w, `{"error":"internal error"}`, http.StatusInternalServerError)
		return
	}

	// Stream responses back to the SSE client
	for _, resp := range responses {
		select {
		case sess.events <- string(resp):
		case <-time.After(5 * time.Second):
			t.logger.Printf("timeout sending response to session %s", sessionID)
		}
	}

	// Acknowledge the POST
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusAccepted)
	fmt.Fprintf(w, `{"status":"accepted"}`)
}

// ── Session Management ───────────────────────────────────────────────

// SessionCount returns the number of active SSE sessions.
func (t *SSETransport) SessionCount() int {
	t.mu.RLock()
	defer t.mu.RUnlock()
	return len(t.sessions)
}

// PruneSessions removes sessions older than the given duration.
func (t *SSETransport) PruneSessions(maxAge time.Duration) int {
	t.mu.Lock()
	defer t.mu.Unlock()
	cutoff := time.Now().Add(-maxAge)
	count := 0
	for id, sess := range t.sessions {
		if sess.created.Before(cutoff) {
			sess.cancel()
			delete(t.sessions, id)
			count++
		}
	}
	if count > 0 {
		t.logger.Printf("pruned %d stale SSE sessions", count)
	}
	return count
}

// ── Compatibility wrapper ────────────────────────────────────────────

// SSEHandler wraps SSETransport to expose a clean http.Handler interface
// that can be mounted on any path.
type SSEHandler struct {
	transport *SSETransport
	prefix    string
}

// NewSSEHandler creates an SSE handler mounted at the given path prefix.
// e.g. prefix="/mcp" → GET /mcp/sse, POST /mcp/message
func NewSSEHandler(handler MessageHandler, prefix string) *SSEHandler {
	return &SSEHandler{
		transport: NewSSETransport(handler),
		prefix:    prefix,
	}
}

func (h *SSEHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	h.transport.ServeHTTP(w, r)
}

// SessionCount returns the number of active SSE sessions.
func (h *SSEHandler) SessionCount() int {
	return h.transport.SessionCount()
}

// Transport returns the underlying SSETransport for management operations.
func (h *SSEHandler) Transport() *SSETransport {
	return h.transport
}

// ── Utility: message framing ─────────────────────────────────────────

// FrameMessage wraps a JSON-RPC message for STDIO or SSE transport.
// For STDIO: just append newline.
// For SSE: just the raw JSON (framing handled by SSE event).
func FrameMessage(data []byte) []byte {
	return append(data, '\n')
}

// ReadMessages reads newline-delimited JSON-RPC messages from a buffered reader.
// Each call to the callback receives one complete message.
func ReadMessages(reader *bufio.Reader, callback func(json.RawMessage) error) error {
	for {
		line, err := reader.ReadBytes('\n')
		if err != nil {
			return err
		}
		if len(line) <= 1 {
			continue
		}
		if json.Valid(line) {
			if err := callback(json.RawMessage(line)); err != nil {
				return err
			}
		}
	}
}

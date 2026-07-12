// Package transport provides MCP transport implementations: STDIO and SSE.
package transport

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"os"
	"sync"
)

// STDIOTransport implements MCP transport over standard input/output.
// The server reads JSON-RPC messages line-by-line from stdin and writes
// JSON-RPC responses line-by-line to stdout. Stderr is reserved for
// server-side logging.
type STDIOTransport struct {
	reader  *bufio.Reader
	writer  io.Writer
	logger  *log.Logger
	handler MessageHandler
	mu      sync.Mutex // protects writer
}

// MessageHandler is the callback for processing received messages.
// Each message (JSON-RPC request/notification) is dispatched to the handler.
// The handler returns zero or more responses to write back.
type MessageHandler func(ctx context.Context, raw json.RawMessage) ([]json.RawMessage, error)

// NewSTDIOTransport creates a STDIO transport reading from stdin and writing to stdout.
func NewSTDIOTransport(handler MessageHandler) *STDIOTransport {
	return &STDIOTransport{
		reader:  bufio.NewReader(os.Stdin),
		writer:  os.Stdout,
		logger:  log.New(os.Stderr, "[mcp-stdio] ", log.LstdFlags|log.Lmsgprefix),
		handler: handler,
	}
}

// Serve starts the STDIO message loop. It blocks until stdin is closed or
// the context is cancelled. Each line from stdin is parsed as a JSON-RPC
// message and dispatched to the handler. Responses are written as single
// lines to stdout.
func (t *STDIOTransport) Serve(ctx context.Context) error {
	t.logger.Println("STDIO transport started — waiting for messages on stdin")
	for {
		select {
		case <-ctx.Done():
			t.logger.Println("STDIO transport shutting down")
			return ctx.Err()
		default:
		}

		line, err := t.reader.ReadBytes('\n')
		if err != nil {
			if err == io.EOF {
				t.logger.Println("stdin closed — exiting")
				return nil
			}
			return fmt.Errorf("stdio read error: %w", err)
		}

		// Skip empty lines
		if len(line) <= 1 {
			continue
		}

		// Validate it's JSON
		if !json.Valid(line) {
			t.logger.Printf("received invalid JSON: %s", string(line[:min(len(line), 100)]))
			t.writeError(nil, -32700, "Parse error: invalid JSON")
			continue
		}

		raw := json.RawMessage(line)
		responses, err := t.handler(ctx, raw)
		if err != nil {
			t.logger.Printf("handler error: %v", err)
			continue
		}

		for _, resp := range responses {
			t.writeResponse(resp)
		}
	}
}

// writeResponse writes a single JSON-RPC response to stdout.
func (t *STDIOTransport) writeResponse(raw json.RawMessage) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if _, err := t.writer.Write(append(raw, '\n')); err != nil {
		t.logger.Printf("write error: %v", err)
	}
}

// writeError writes a JSON-RPC error response to stdout for a given request ID.
func (t *STDIOTransport) writeError(id *int64, code int, message string) {
	resp := map[string]any{
		"jsonrpc": "2.0",
		"error": map[string]any{
			"code":    code,
			"message": message,
		},
	}
	if id != nil {
		resp["id"] = *id
	} else {
		resp["id"] = nil
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	json.NewEncoder(t.writer).Encode(resp)
}

package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"sync"
	"time"

	"github.com/agenthub/platform/shared/events"
	"github.com/agenthub/platform/shared/iam"
	"github.com/gorilla/websocket"
)

var upgrader = websocket.Upgrader{
	ReadBufferSize:  4096,
	WriteBufferSize: 4096,
	CheckOrigin:     func(r *http.Request) bool { return true },
}

// Client is a single WebSocket connection bound to a session.
type Client struct {
	hub       *Hub
	conn      *websocket.Conn
	send      chan []byte
	tenantID  string
	sessionID string
	userID    string
	connID    string
}

// Hub is the per-instance connection registry. It maps session_id to the
// set of active clients so stream events can be fanned out. The optional
// routeRegistry persists connection metadata to Redis for multi-instance
// routing and crash recovery.
type Hub struct {
	mu      sync.RWMutex
	clients map[string]map[*Client]struct{}
	routes  *routeRegistry
}

func NewHub() *Hub {
	return &Hub{clients: make(map[string]map[*Client]struct{})}
}

// WithRouteRegistry attaches an optional Redis-backed route registry.
func (h *Hub) WithRouteRegistry(r *routeRegistry) *Hub {
	h.routes = r
	return h
}

func (h *Hub) register(c *Client) {
	h.mu.Lock()
	set, ok := h.clients[c.sessionID]
	if !ok {
		set = make(map[*Client]struct{})
		h.clients[c.sessionID] = set
	}
	set[c] = struct{}{}
	h.mu.Unlock()

	// Write to Redis route registry for cross-instance routing.
	if h.routes != nil {
		entry := RouteEntry{
			ConnID:    c.connID,
			Instance:  h.routes.instance,
			UserID:    c.userID,
			TenantID:  c.tenantID,
			SessionID: c.sessionID,
			Since:     time.Now().UTC().Format(time.RFC3339),
		}
		if err := h.routes.register(context.Background(), entry); err != nil {
			log.Printf("route registry register failed session=%s conn=%s: %v", c.sessionID, c.connID, err)
		}
	}
}

func (h *Hub) unregister(c *Client) {
	h.mu.Lock()
	if set, ok := h.clients[c.sessionID]; ok {
		delete(set, c)
		if len(set) == 0 {
			delete(h.clients, c.sessionID)
		}
	}
	h.mu.Unlock()

	// Remove from Redis route registry.
	if h.routes != nil {
		h.routes.unregister(context.Background(), c.tenantID, c.sessionID, c.connID)
	}
	log.Printf("ws disconnected session=%s user=%s conn=%s clients=%d sessions=%d",
		c.sessionID, c.userID, c.connID, h.clientCount(), h.sessionCount())
}

func (h *Hub) broadcast(sessionID string, payload []byte) {
	h.mu.RLock()
	set := h.clients[sessionID]
	clients := make([]*Client, 0, len(set))
	for c := range set {
		clients = append(clients, c)
	}
	h.mu.RUnlock()
	for _, c := range clients {
		select {
		case c.send <- payload:
		default:
		}
	}
}

func (h *Hub) sessionCount() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	return len(h.clients)
}

func (h *Hub) clientCount() int {
	h.mu.RLock()
	defer h.mu.RUnlock()
	n := 0
	for _, set := range h.clients {
		n += len(set)
	}
	return n
}

// Shutdown closes all WebSocket connections gracefully and cleans up routes.
func (h *Hub) Shutdown() {
	h.mu.Lock()
	defer h.mu.Unlock()
	log.Printf("hub: shutting down %d sessions with %d total connections", len(h.clients), h.clientCount())
	for sessionID, set := range h.clients {
		for c := range set {
			close(c.send)
			_ = c.conn.Close()
		}
		// Clean up Redis route entries
		if h.routes != nil {
			for c := range set {
				h.routes.unregister(context.Background(), "", sessionID, c.connID)
			}
		}
	}
	h.clients = make(map[string]map[*Client]struct{})
}

// ── readPump / writePump ──────────────────────────────────────────────

func (c *Client) readPump() {
	defer func() {
		c.hub.unregister(c)
		_ = c.conn.Close()
	}()
	c.conn.SetReadLimit(1 << 14)
	_ = c.conn.SetReadDeadline(time.Now().Add(60 * time.Second))
	c.conn.SetPongHandler(func(string) error {
		return c.conn.SetReadDeadline(time.Now().Add(60 * time.Second))
	})
	for {
		_, _, err := c.conn.ReadMessage()
		if err != nil {
			return
		}
	}
}

func (c *Client) writePump() {
	ticker := time.NewTicker(30 * time.Second)
	defer func() {
		ticker.Stop()
		_ = c.conn.Close()
	}()
	for {
		select {
		case msg, ok := <-c.send:
			_ = c.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if !ok {
				_ = c.conn.WriteMessage(websocket.CloseMessage, []byte{})
				return
			}
			if err := c.conn.WriteMessage(websocket.TextMessage, msg); err != nil {
				return
			}
		case <-ticker.C:
			_ = c.conn.SetWriteDeadline(time.Now().Add(10 * time.Second))
			if err := c.conn.WriteMessage(websocket.PingMessage, nil); err != nil {
				return
			}
			// Refresh the Redis route TTL on each ping so active sessions
			// survive the 2h expiry window.
			if c.hub.routes != nil {
				c.hub.routes.refreshTTL(context.Background(), c.tenantID, c.sessionID)
			}
		}
	}
}

// ── serveWS ────────────────────────────────────────────────────────────

func serveWS(hub *Hub, issuer *iam.TokenIssuer, w http.ResponseWriter, r *http.Request) {
	token := r.URL.Query().Get("token")
	if token == "" {
		if h := r.Header.Get("Authorization"); len(h) > 7 && h[:7] == "Bearer " {
			token = h[7:]
		}
	}
	claims, err := issuer.Verify(token)
	if err != nil {
		http.Error(w, "invalid token", http.StatusUnauthorized)
		return
	}
	sessionID := r.URL.Query().Get("session_id")
	if sessionID == "" {
		sessionID = claims.SessionID
	}
	if issuer.IsDevMode() && sessionID == "" {
		sessionID = r.URL.Query().Get("session_id")
	}
	if sessionID == "" {
		http.Error(w, "session_id is required", http.StatusBadRequest)
		return
	}
	userID := claims.UserID
	if issuer.IsDevMode() && userID == "" {
		userID = r.URL.Query().Get("user_id")
	}

	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		log.Printf("ws upgrade failed: %v", err)
		return
	}
	client := &Client{
		hub:       hub,
		conn:      conn,
		send:      make(chan []byte, 64),
		tenantID:  claims.TenantID,
		sessionID: sessionID,
		userID:    userID,
		connID:    claims.Subject,
	}
	hub.register(client)
	log.Printf("ws connected session=%s user=%s conn=%s clients=%d sessions=%d",
		sessionID, client.userID, client.connID, hub.clientCount(), hub.sessionCount())

	go client.writePump()
	client.readPump()
}

func dispatchStreamEvent(ctx context.Context, hub *Hub, env events.Envelope) {
	payload, err := json.Marshal(env)
	if err != nil {
		return
	}
	hub.broadcast(env.SessionID, payload)
}

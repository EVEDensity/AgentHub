package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"sync"
	"time"

	"github.com/agenthub/platform/shared/events"
	"github.com/golang-jwt/jwt/v5"
	"github.com/gorilla/websocket"
)

var upgrader = websocket.Upgrader{
	ReadBufferSize:  4096,
	WriteBufferSize: 4096,
	CheckOrigin:     func(r *http.Request) bool { return true }, // per-origin policy enforced at LB in prod
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

// Hub is the per-instance connection registry. It maps session_id to the set
// of active clients so stream events can be fanned out to every connected
// participant. The hub is safe for concurrent use.
type Hub struct {
	mu      sync.RWMutex
	clients map[string]map[*Client]struct{} // session_id -> set of clients
}

func NewHub() *Hub {
	return &Hub{clients: make(map[string]map[*Client]struct{})}
}

func (h *Hub) register(c *Client) {
	h.mu.Lock()
	defer h.mu.Unlock()
	set, ok := h.clients[c.sessionID]
	if !ok {
		set = make(map[*Client]struct{})
		h.clients[c.sessionID] = set
	}
	set[c] = struct{}{}
}

func (h *Hub) unregister(c *Client) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if set, ok := h.clients[c.sessionID]; ok {
		delete(set, c)
		if len(set) == 0 {
			delete(h.clients, c.sessionID)
		}
	}
}

// broadcast sends a serialized envelope to every client watching the session.
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
			// Client buffer full; drop to protect the hub. The slow client will
			// be evicted by its read pump on the next missed ping.
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

// readPump drains incoming frames and enforces ping/pong liveness. Gateway is
// primarily a push channel, so client messages are minimal (pongs / heartbeats).
func (c *Client) readPump() {
	defer func() {
		c.hub.unregister(c)
		_ = c.conn.Close()
	}()
	c.conn.SetReadLimit(1 << 14) // 16KiB
	_ = c.conn.SetReadDeadline(time.Now().Add(60 * time.Second))
	c.conn.SetPongHandler(func(string) error { return c.conn.SetReadDeadline(time.Now().Add(60 * time.Second)) })
	for {
		_, _, err := c.conn.ReadMessage()
		if err != nil {
			return
		}
	}
}

// writePump flushes the send channel to the wire and sends periodic pings to
// keep proxies from idle-closing the socket.
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
		}
	}
}

// serveWS upgrades the HTTP connection to WebSocket after JWT auth, registers
// the client with the hub, and starts the read/write pumps.
func serveWS(hub *Hub, jwtSecret []byte, w http.ResponseWriter, r *http.Request) {
	token := r.URL.Query().Get("token")
	if token == "" {
		if h := r.Header.Get("Authorization"); len(h) > 7 && h[:7] == "Bearer " {
			token = h[7:]
		}
	}
	claims, err := parseJWT(token, jwtSecret)
	if err != nil {
		http.Error(w, "invalid token", http.StatusUnauthorized)
		return
	}
	sessionID := r.URL.Query().Get("session_id")
	if sessionID == "" {
		sessionID = claims.SessionID
	}
	if sessionID == "" {
		http.Error(w, "session_id is required", http.StatusBadRequest)
		return
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
		userID:    claims.UserID,
		connID:    claims.Subject,
	}
	hub.register(client)
	log.Printf("ws connected session=%s user=%s clients=%d sessions=%d", sessionID, client.userID, hub.clientCount(), hub.sessionCount())

	go client.writePump()
	client.readPump()
}

// Claims is the JWT payload used by the gateway.
type Claims struct {
	TenantID  string `json:"tenant_id"`
	UserID    string `json:"user_id"`
	SessionID string `json:"session_id"`
	jwt.RegisteredClaims
}

// parseJWT validates and decodes the token. An empty secret disables auth
// (dev mode) and returns a synthetic claim derived from query params so the
// WebSocket path still works locally without a token issuer.
func parseJWT(tokenString string, secret []byte) (*Claims, error) {
	if len(secret) == 0 {
		// Dev mode: accept any token / no token. session_id/user_id come from
		// query params in serveWS when claims are empty.
		return &Claims{}, nil
	}
	if tokenString == "" {
		return nil, fmt.Errorf("token is required")
	}
	claims := &Claims{}
	_, err := jwt.ParseWithClaims(tokenString, claims, func(t *jwt.Token) (any, error) {
		if _, ok := t.Method.(*jwt.SigningMethodHMAC); !ok {
			return nil, jwt.ErrTokenSignatureInvalid
		}
		return secret, nil
	})
	if err != nil {
		return nil, err
	}
	return claims, nil
}

// dispatchStreamEvent is the callback the hub's JetStream subscriber uses to
// fan out a received envelope to the matching session's WebSocket clients.
func dispatchStreamEvent(ctx context.Context, hub *Hub, env events.Envelope) {
	payload, err := json.Marshal(env)
	if err != nil {
		return
	}
	hub.broadcast(env.SessionID, payload)
}

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"net/url"
	"os"
	"time"

	"github.com/agenthub/platform/shared/db"
	"github.com/agenthub/platform/shared/obs"
	"github.com/agenthub/platform/shared/state"
)

type SessionCapabilities struct {
	HotStateStore string   `json:"hot_state_store"`
	Persistence   string   `json:"persistence"`
	Features      []string `json:"features"`
}

type PresenceRequest struct {
	TenantID  string `json:"tenant_id"`
	SessionID string `json:"session_id"`
	UserID    string `json:"user_id"`
	Name      string `json:"name"`
	Role      string `json:"role"`
	Status    string `json:"status"`
}

type CursorRequest struct {
	TenantID     string `json:"tenant_id"`
	SessionID    string `json:"session_id"`
	ConnectionID string `json:"connection_id"`
	Cursor       string `json:"cursor"`
}

func main() {
	caps := SessionCapabilities{
		HotStateStore: "redis-cluster",
		Persistence:   "postgresql",
		Features:      []string{"presence", "reconnect_cursor", "message_checkpoint", "authorization_view", "stream_resume"},
	}

	redisAddr := getenv("REDIS_ADDR", "127.0.0.1:6379")
	store := state.Connect(redisAddr)
	defer func() {
		if err := store.Close(); err != nil {
			log.Printf("close redis: %v", err)
		}
	}()

	shutdown, err := obs.InitTracer(context.Background(), getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""), "session-service")
	if err != nil {
		log.Fatalf("init tracer: %v", err)
	}
	defer shutdown(context.Background())

	dsn := getenv("DATABASE_DSN", "postgres://agenthub:agenthub@localhost:5432/agenthub?sslmode=disable")
	pool, err := db.Connect(context.Background(), dsn)
	if err != nil {
		log.Fatalf("connect db: %v", err)
	}
	defer pool.Close()
	if err := pool.Migrate(context.Background()); err != nil {
		log.Fatalf("run db migrations: %v", err)
	}
	log.Printf("session-service db migrated")

	streamDeliveryBase := getenv("STREAM_DELIVERY_URL", "http://127.0.0.1:8086")
	httpClient := &http.Client{Timeout: 3 * time.Second}

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("/capabilities", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(caps)
	})
	mux.HandleFunc("/presence", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		var req PresenceRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid json body"})
			return
		}
		if req.TenantID == "" || req.SessionID == "" || req.UserID == "" {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "tenant_id, session_id, user_id are required"})
			return
		}
		status := req.Status
		if status == "" {
			status = "online"
		}
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		key := state.PresenceKey(req.TenantID, req.SessionID)
		if err := store.HSet(ctx, key, req.UserID, status, req.UserID+":name", req.Name, req.UserID+":role", req.Role); err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		_ = store.Expire(ctx, key, 24*time.Hour)
		upsertSessionPresence(ctx, pool, req.TenantID, req.SessionID, req.UserID, req.Role)
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "key": key, "status": status})
	})
	mux.HandleFunc("/cursor", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		var req CursorRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid json body"})
			return
		}
		if req.TenantID == "" || req.SessionID == "" || req.ConnectionID == "" || req.Cursor == "" {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "tenant_id, session_id, connection_id, cursor are required"})
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		key := state.CursorKey(req.TenantID, req.SessionID, req.ConnectionID)
		if err := store.PutJSON(ctx, key, req.Cursor, 24*time.Hour); err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "key": key, "cursor": req.Cursor})
	})
	mux.HandleFunc("/state", func(w http.ResponseWriter, r *http.Request) {
		tenantID := r.URL.Query().Get("tenant_id")
		sessionID := r.URL.Query().Get("session_id")
		connectionID := r.URL.Query().Get("connection_id")
		if tenantID == "" || sessionID == "" {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "tenant_id and session_id are required"})
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		presence, _ := store.HGetAll(ctx, state.PresenceKey(tenantID, sessionID))
		cursor := ""
		if connectionID != "" {
			cursor, _ = store.GetString(ctx, state.CursorKey(tenantID, sessionID, connectionID))
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"presence": presence, "cursor": cursor})
	})
	mux.HandleFunc("/resume", func(w http.ResponseWriter, r *http.Request) {
		tenantID := r.URL.Query().Get("tenant_id")
		sessionID := r.URL.Query().Get("session_id")
		connectionID := r.URL.Query().Get("connection_id")
		if tenantID == "" || sessionID == "" || connectionID == "" {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "tenant_id, session_id, connection_id are required"})
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		cursor, _ := store.GetString(ctx, state.CursorKey(tenantID, sessionID, connectionID))
		endpoint := fmt.Sprintf("%s/streams/replay?session_id=%s&after_cursor=%s", streamDeliveryBase, url.QueryEscape(sessionID), url.QueryEscape(cursor))
		resp, err := httpClient.Get(endpoint)
		if err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		defer resp.Body.Close()
		var replay any
		if err := json.NewDecoder(resp.Body).Decode(&replay); err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid replay response"})
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"tenant_id":     tenantID,
			"session_id":    sessionID,
			"connection_id": connectionID,
			"cursor":        cursor,
			"replay":        replay,
		})
	})
	mux.HandleFunc("/sessions", func(w http.ResponseWriter, r *http.Request) {
		tenantID := r.URL.Query().Get("tenant_id")
		if tenantID == "" {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "tenant_id is required"})
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		rows, err := pool.Query(ctx, `
			SELECT id, tenant_id, name, owner_id, type, visibility, status, created_at, updated_at
			FROM platform_sessions
			WHERE tenant_id=$1
			ORDER BY created_at DESC
			LIMIT 100`, tenantID)
		if err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		defer rows.Close()
		type sess struct {
			ID         string `json:"id"`
			TenantID   string `json:"tenant_id"`
			Name       string `json:"name"`
			OwnerID    string `json:"owner_id"`
			Type       string `json:"type"`
			Visibility string `json:"visibility"`
			Status     string `json:"status"`
			CreatedAt  string `json:"created_at"`
			UpdatedAt  string `json:"updated_at"`
		}
		out := make([]sess, 0, 50)
		for rows.Next() {
			var s sess
			if err := rows.Scan(&s.ID, &s.TenantID, &s.Name, &s.OwnerID, &s.Type, &s.Visibility, &s.Status, &s.CreatedAt, &s.UpdatedAt); err == nil {
				out = append(out, s)
			}
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"tenant_id": tenantID, "count": len(out), "sessions": out})
	})

	addr := getenv("SESSION_ADDR", ":8083")
	log.Printf("session-service listening on %s", addr)
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		obs.MetricsHandler().ServeHTTP(w, r)
	})
	handler := obs.Middleware("session-service", mux)
	log.Fatal(http.ListenAndServe(addr, handler))
}

// upsertSessionPresence records the session row and the member row in PG so the
// presence is durable across Redis evictions. Redis remains the hot read path.
func upsertSessionPresence(ctx context.Context, pool *db.Pool, tenantID, sessionID, userID, role string) {
	if pool == nil {
		return
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO platform_sessions (id, tenant_id, owner_id)
		VALUES ($1, $2, $3)
		ON CONFLICT (id) DO UPDATE SET updated_at=now()`, sessionID, tenantID, userID); err != nil {
		log.Printf("upsert session in db failed id=%s: %v", sessionID, err)
	}
	if role == "" {
		role = "member"
	}
	if _, err := pool.Exec(ctx, `
		INSERT INTO platform_session_members (session_id, tenant_id, user_id, role)
		VALUES ($1, $2, $3, $4)
		ON CONFLICT (session_id, user_id) DO NOTHING`, sessionID, tenantID, userID, role); err != nil {
		log.Printf("upsert session member in db failed session=%s user=%s: %v", sessionID, userID, err)
	}
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

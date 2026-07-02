package main

import (
	"context"
	"encoding/json"
	"net/http"
	"time"

	"github.com/agenthub/platform/shared/state"
)

// RouteEntry is a single WebSocket connection's routing metadata stored in
// Redis so that any gateway instance can locate which instance holds a
// session's active connections.
type RouteEntry struct {
	ConnID    string `json:"conn_id"`
	Instance  string `json:"instance"`
	UserID    string `json:"user_id"`
	TenantID  string `json:"tenant_id"`
	SessionID string `json:"session_id"`
	Since     string `json:"since"`
}

// routeRegistry is the Redis-backed connection registry that augments the
// in-memory Hub. It writes per-connection entries into a Redis hash so that
// multi-instance deployments can route messages to the correct gateway
// instance. The in-memory Hub remains the fast path for local fanout; Redis
// is used for cross-instance discovery and restart survivability.
type routeRegistry struct {
	store    *state.Store
	instance string
}

func newRouteRegistry(store *state.Store, instance string) *routeRegistry {
	return &routeRegistry{store: store, instance: instance}
}

// routeKey returns the Redis hash key for a session's connection set.
//   route:{tenant}:{session} → HSET {conn_id} {json(RouteEntry)}
func routeKey(tenantID, sessionID string) string {
	return "route:" + tenantID + ":" + sessionID
}

// register writes a RouteEntry into the Redis hash for the session. The
// key expires after 2 hours of inactivity (refreshed on each ping from the
// write pump via refreshTTL). This ensures orphaned entries from crashed
// instances are eventually cleaned up.
func (r *routeRegistry) register(ctx context.Context, entry RouteEntry) error {
	if r.store == nil {
		return nil
	}
	payload, err := json.Marshal(entry)
	if err != nil {
		return err
	}
	key := routeKey(entry.TenantID, entry.SessionID)
	if err := r.store.HSet(ctx, key, entry.ConnID, string(payload)); err != nil {
		return err
	}
	return r.store.Expire(ctx, key, 2*time.Hour)
}

// unregister removes a connection from the Redis hash. If the hash becomes
// empty the key is deleted (rather than waiting for expiration) so
// cross-instance queries see an accurate view immediately.
func (r *routeRegistry) unregister(ctx context.Context, tenantID, sessionID, connID string) {
	if r.store == nil {
		return
	}
	key := routeKey(tenantID, sessionID)
	_ = r.store.HDel(ctx, key, connID)
	// Check remaining size; if zero, delete the key.
	count, err := r.store.HLen(ctx, key)
	if err == nil && count == 0 {
		_ = r.store.Del(ctx, key)
	}
}

// refreshTTL bumps the expiry on the session route hash, called each time
// a writePump sends a ping so active sessions never expire.
func (r *routeRegistry) refreshTTL(ctx context.Context, tenantID, sessionID string) {
	if r.store == nil {
		return
	}
	_ = r.store.Expire(ctx, routeKey(tenantID, sessionID), 2*time.Hour)
}

// lookup returns all RouteEntry values for a session, enabling
// cross-instance message routing or admin inspection.
func (r *routeRegistry) lookup(ctx context.Context, tenantID, sessionID string) ([]RouteEntry, error) {
	if r.store == nil {
		return nil, nil
	}
	all, err := r.store.HGetAll(ctx, routeKey(tenantID, sessionID))
	if err != nil {
		return nil, err
	}
	out := make([]RouteEntry, 0, len(all))
	for _, raw := range all {
		var re RouteEntry
		if json.Unmarshal([]byte(raw), &re) == nil {
			out = append(out, re)
		}
	}
	return out, nil
}

// listSessions returns all session route keys matching a tenant (or all
// tenants when tenantID is empty). This is used by the /routes endpoint
// for operational visibility.
func (r *routeRegistry) listSessions(ctx context.Context, tenantID string) (map[string][]RouteEntry, error) {
	if r.store == nil {
		return nil, nil
	}
	pattern := "route:*"
	if tenantID != "" {
		pattern = "route:" + tenantID + ":*"
	}
	keys, err := r.store.Keys(ctx, pattern)
	if err != nil {
		return nil, err
	}

	result := make(map[string][]RouteEntry, len(keys))
	for _, key := range keys {
		all, err := r.store.HGetAll(ctx, key)
		if err != nil {
			continue
		}
		entries := make([]RouteEntry, 0, len(all))
		for _, raw := range all {
			var re RouteEntry
			if json.Unmarshal([]byte(raw), &re) == nil {
				entries = append(entries, re)
			}
		}
		if len(entries) > 0 {
			result[key] = entries
		}
	}
	return result, nil
}

// ── HTTP handlers ──────────────────────────────────────────────────────

// serveRoutes exposes the Redis connection registry for debugging and
// cross-instance routing lookups.
//
//   GET  /routes?tenant_id=X              → list sessions for tenant
//   GET  /routes/{tenant}/{session}       → list connections for session
//   GET  /routes/stats                    → summary counts
func serveRoutes(reg *routeRegistry, w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()

	// /routes/stats
	if r.URL.Path == "/routes/stats" {
		all, err := reg.listSessions(ctx, "")
		if err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		totalConns := 0
		for _, entries := range all {
			totalConns += len(entries)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"total_sessions":    len(all),
			"total_connections": totalConns,
			"instance":          reg.instance,
		})
		return
	}

	// /routes?tenant_id=X
	tenantID := r.URL.Query().Get("tenant_id")
	if tenantID != "" {
		all, err := reg.listSessions(ctx, tenantID)
		if err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"routes": all})
		return
	}

	// /routes/{tenant}/{session}
	// Parse path: /routes/<tenant>/<session>
	path := r.URL.Path
	if len(path) > 8 && path[:8] == "/routes/" {
		rest := path[8:]
		parts := splitN(rest, "/", 2)
		if len(parts) == 2 {
			entries, err := reg.lookup(ctx, parts[0], parts[1])
			if err != nil {
				w.WriteHeader(http.StatusBadGateway)
				_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
				return
			}
			_ = json.NewEncoder(w).Encode(map[string]any{
				"tenant_id":  parts[0],
				"session_id": parts[1],
				"connections": len(entries),
				"entries":     entries,
			})
			return
		}
	}

	// Default: list all
	all, err := reg.listSessions(ctx, "")
	if err != nil {
		w.WriteHeader(http.StatusBadGateway)
		_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	_ = json.NewEncoder(w).Encode(map[string]any{"routes": all})
}

// splitN splits s into at most n substrings separated by sep.
func splitN(s, sep string, n int) []string {
	if n <= 1 {
		return []string{s}
	}
	parts := []string{}
	for i := 0; i < n-1; i++ {
		idx := indexOf(s, sep)
		if idx < 0 {
			parts = append(parts, s)
			return parts
		}
		parts = append(parts, s[:idx])
		s = s[idx+len(sep):]
	}
	parts = append(parts, s)
	return parts
}

func indexOf(s, sep string) int {
	for i := 0; i <= len(s)-len(sep); i++ {
		if s[i:i+len(sep)] == sep {
			return i
		}
	}
	return -1
}

// serveRoutesHandler returns an http.HandlerFunc that delegates to serveRoutes.
func serveRoutesHandler(reg *routeRegistry) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		serveRoutes(reg, w, r)
	}
}

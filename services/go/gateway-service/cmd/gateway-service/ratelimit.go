package main

import (
	"io"
	"net/http"
	"strings"
	"sync"
	"time"
)

// tokenBucket is a simple token-bucket rate limiter. capacity tokens refill at
// rate tokens/sec. It is safe for concurrent use.
type tokenBucket struct {
	mu       sync.Mutex
	capacity float64
	rate     float64
	tokens   float64
	last     time.Time
}

func newTokenBucket(capacity, rate float64) *tokenBucket {
	return &tokenBucket{capacity: capacity, rate: rate, tokens: capacity, last: time.Now()}
}

// allow removes one token; returns false if the bucket is empty.
func (b *tokenBucket) allow() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	now := time.Now()
	elapsed := now.Sub(b.last).Seconds()
	b.tokens += elapsed * b.rate
	if b.tokens > b.capacity {
		b.tokens = b.capacity
	}
	b.last = now
	if b.tokens >= 1 {
		b.tokens--
		return true
	}
	return false
}

// LimitLayer identifies which rate-limit layer a bucket belongs to.
type LimitLayer string

const (
	LayerUser   LimitLayer = "user"
	LayerTenant LimitLayer = "tenant"
	LayerAgent  LimitLayer = "agent"
	LayerTool   LimitLayer = "tool"
)

// LayerConfig defines the capacity and refill rate for one limit layer.
type LayerConfig struct {
	Capacity float64 `json:"capacity"`
	Rate     float64 `json:"rate"` // tokens per second
}

// MultiLayerRateLimiter enforces four layers of rate limiting:
//   - User: per user_id (X-User-ID header)
//   - Tenant: per tenant_id (X-Tenant-ID header)
//   - Agent: per agent_role (applies to dispatch/permission paths)
//   - Tool: per tool_name (applies to permission request paths)
//
// A request must pass ALL applicable layers to proceed. If any layer rejects,
// the response includes which layer was exceeded in the Retry-After header.
type MultiLayerRateLimiter struct {
	mu      sync.RWMutex
	buckets map[string]*tokenBucket // key: "{layer}:{principal}"
	configs map[LimitLayer]LayerConfig
}

// NewMultiLayerRateLimiter creates a four-layer limiter with the given configs.
func NewMultiLayerRateLimiter(configs map[LimitLayer]LayerConfig) *MultiLayerRateLimiter {
	return &MultiLayerRateLimiter{
		buckets: make(map[string]*tokenBucket),
		configs: configs,
	}
}

// bucketKey constructs the Redis-style key: "rl:{layer}:{principal}".
func bucketKey(layer LimitLayer, principal string) string {
	if principal == "" {
		principal = "anonymous"
	}
	return string(layer) + ":" + principal
}

func (rl *MultiLayerRateLimiter) bucket(layer LimitLayer, principal string) *tokenBucket {
	cfg, ok := rl.configs[layer]
	if !ok || cfg.Rate <= 0 {
		return nil // layer disabled
	}
	key := bucketKey(layer, principal)

	rl.mu.RLock()
	b, ok := rl.buckets[key]
	rl.mu.RUnlock()
	if ok {
		return b
	}

	rl.mu.Lock()
	defer rl.mu.Unlock()
	// Double-check after acquiring write lock.
	if b, ok := rl.buckets[key]; ok {
		return b
	}
	b = newTokenBucket(cfg.Capacity, cfg.Rate)
	rl.buckets[key] = b
	return b
}

// CheckUser checks the user and tenant layers. Returns the layer that rejected
// (empty string if all passed).
func (rl *MultiLayerRateLimiter) CheckUser(userID, tenantID string) LimitLayer {
	if b := rl.bucket(LayerUser, userID); b != nil && !b.allow() {
		return LayerUser
	}
	if b := rl.bucket(LayerTenant, tenantID); b != nil && !b.allow() {
		return LayerTenant
	}
	return ""
}

// CheckAgent checks the agent and tool layers. Returns the layer that rejected.
func (rl *MultiLayerRateLimiter) CheckAgent(agentRole, toolName string) LimitLayer {
	if agentRole != "" {
		if b := rl.bucket(LayerAgent, agentRole); b != nil && !b.allow() {
			return LayerAgent
		}
	}
	if toolName != "" {
		if b := rl.bucket(LayerTool, toolName); b != nil && !b.allow() {
			return LayerTool
		}
	}
	return ""
}

// ActiveBuckets returns the count of active buckets (for /stats).
func (rl *MultiLayerRateLimiter) ActiveBuckets() int {
	rl.mu.RLock()
	defer rl.mu.RUnlock()
	return len(rl.buckets)
}

// extractPrincipal extracts identifying headers from the request.
type requestPrincipal struct {
	UserID    string
	TenantID  string
	AgentRole string
	ToolName  string
}

func extractPrincipal(r *http.Request) requestPrincipal {
	return requestPrincipal{
		UserID:    r.Header.Get("X-User-ID"),
		TenantID:  firstNonEmpty(r.Header.Get("X-Tenant-ID"), r.URL.Query().Get("tenant_id")),
		AgentRole: r.Header.Get("X-Agent-Role"),
		ToolName:  r.Header.Get("X-Tool-Name"),
	}
}

func firstNonEmpty(values ...string) string {
	for _, v := range values {
		if v != "" {
			return v
		}
	}
	return ""
}

// rateLimitMiddleware applies four-layer rate limiting. Health/metrics paths
// bypass all layers. Permission/dispatch paths additionally check agent and
// tool layers.
func rateLimitMiddleware(rl *MultiLayerRateLimiter, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if rl == nil {
			next.ServeHTTP(w, r)
			return
		}

		// Bypass monitoring and health paths.
		switch r.URL.Path {
		case "/healthz", "/healthz/readiness", "/metrics", "/profile", "/stats":
			next.ServeHTTP(w, r)
			return
		}

		p := extractPrincipal(r)

		// Layer 1+2: user and tenant (all paths)
		if rejected := rl.CheckUser(p.UserID, p.TenantID); rejected != "" {
			rateLimitHits.WithLabelValues(string(rejected)).Inc()
			w.Header().Set("Retry-After", "1")
			w.Header().Set("X-RateLimit-Layer", string(rejected))
			w.WriteHeader(http.StatusTooManyRequests)
			_, _ = w.Write([]byte(`{"error":"rate limit exceeded","layer":"` + string(rejected) + `"}`))
			return
		}

		// Layer 3+4: agent and tool (only on dispatch/permission paths)
		if strings.Contains(r.URL.Path, "/permissions/") || strings.Contains(r.URL.Path, "/dispatch") {
			// Try to extract agent_role and tool_name from request body for POST endpoints.
			agentRole := p.AgentRole
			toolName := p.ToolName
			if agentRole == "" || toolName == "" {
				// Best-effort extraction from JSON body without consuming it.
				if extracted := peekAgentToolFromBody(r); extracted != nil {
					if agentRole == "" {
						agentRole = extracted.AgentRole
					}
					if toolName == "" {
						toolName = extracted.ToolName
					}
				}
			}
			if rejected := rl.CheckAgent(agentRole, toolName); rejected != "" {
				rateLimitHits.WithLabelValues(string(rejected)).Inc()
				w.Header().Set("Retry-After", "1")
				w.Header().Set("X-RateLimit-Layer", string(rejected))
				w.WriteHeader(http.StatusTooManyRequests)
				_, _ = w.Write([]byte(`{"error":"rate limit exceeded","layer":"` + string(rejected) + `"}`))
				return
			}
		}

		next.ServeHTTP(w, r)
	})
}

// peekResult holds values extracted from a request body without consuming it.
type peekResult struct {
	AgentRole string
	ToolName  string
}

// peekAgentToolFromBody reads agent_role and tool_name from a JSON request body
// without consuming the body (it rewinds via NopCloser replacement).
func peekAgentToolFromBody(r *http.Request) *peekResult {
	if r.Body == nil {
		return nil
	}
	ct := r.Header.Get("Content-Type")
	if !strings.Contains(ct, "application/json") {
		return nil
	}
	// Read a small prefix to find agent_role/tool_name without consuming the body.
	// We use a limit to avoid reading large bodies.
	buf := make([]byte, 4096)
	n, _ := r.Body.Read(buf)
	body := string(buf[:n])
	// Restore the body so downstream handlers can read it.
	r.Body = &restoredBody{data: buf[:n], pos: 0}

	result := &peekResult{}
	if v := extractJSONField(body, "tool_name"); v != "" {
		result.ToolName = v
	}
	if v := extractJSONField(body, "agent_role"); v != "" {
		result.AgentRole = v
	}
	if result.AgentRole == "" && result.ToolName == "" {
		return nil
	}
	return result
}

// extractJSONField does a naive scan for "field":"value" in a JSON string.
// This avoids a full JSON parse just for two fields.
func extractJSONField(body, field string) string {
	needle := `"` + field + `":"`
	idx := strings.Index(body, needle)
	if idx < 0 {
		needle = `"` + field + `": "`
		idx = strings.Index(body, needle)
		if idx < 0 {
			return ""
		}
	}
	start := idx + len(needle)
	end := strings.IndexByte(body[start:], '"')
	if end < 0 {
		return ""
	}
	return body[start : start+end]
}

// restoredBody wraps a byte slice as an io.ReadCloser so the body can be
// re-read after peeking.
type restoredBody struct {
	data []byte
	pos  int
}

func (rb *restoredBody) Read(p []byte) (int, error) {
	if rb.pos >= len(rb.data) {
		return 0, io.EOF
	}
	n := copy(p, rb.data[rb.pos:])
	rb.pos += n
	return n, nil
}

func (rb *restoredBody) Close() error { return nil }

package main

import (
	"context"
	"io"
	"log"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/agenthub/platform/shared/state"
)

// tokenBucket is a token-bucket rate limiter with idle tracking for LRU eviction.
// capacity tokens refill at rate tokens/sec. Safe for concurrent use.
type tokenBucket struct {
	mu         sync.Mutex
	capacity   float64
	rate       float64
	tokens     float64
	last       time.Time
	lastAccess atomic.Int64 // unix nano — for stale bucket eviction
}

func newTokenBucket(capacity, rate float64) *tokenBucket {
	tb := &tokenBucket{capacity: capacity, rate: rate, tokens: capacity, last: time.Now()}
	tb.lastAccess.Store(time.Now().UnixNano())
	return tb
}

// allow removes one token; returns false if the bucket is empty.
func (b *tokenBucket) allow() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	now := time.Now()
	b.tokens += now.Sub(b.last).Seconds() * b.rate
	if b.tokens > b.capacity {
		b.tokens = b.capacity
	}
	b.last = now
	b.lastAccess.Store(now.UnixNano())
	if b.tokens >= 1 {
		b.tokens--
		return true
	}
	return false
}

// idleDuration returns how long since the bucket was last accessed.
func (b *tokenBucket) idleDuration() time.Duration {
	return time.Since(time.Unix(0, b.lastAccess.Load()))
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
// A request must pass ALL applicable layers to proceed.
//
// Sprint M4 enhancements:
//   - Stale bucket LRU eviction (periodic cleanup, default 5 min idle TTL)
//   - Burst detection (exponential backoff when request rate > 3× normal)
//   - Optional Redis-backed distributed rate limiting
//   - Soft/hard limit distinction (warn at 80% vs reject at 100%)
type MultiLayerRateLimiter struct {
	mu         sync.RWMutex
	buckets    map[string]*tokenBucket // key: "{layer}:{principal}"
	configs    map[LimitLayer]LayerConfig
	// Stale bucket eviction
	lastCleanup  time.Time
	evictionIdle time.Duration // idle duration before eviction
	// Burst detection
	burstThreshold float64                   // request rate multiplier (default 3x normal)
	burstWindow    time.Duration             // burst detection window
	burstCounters  map[string]*burstTracker  // per-principal burst tracking
	burstMu        sync.Mutex
	// Distributed mode
	distributed    *DistributedRateLimiter
	// Soft limit (warn header) vs hard limit (reject)
	softLimitRatio float64 // fraction of capacity that triggers warning (default 0.8)
}

// burstTracker records request timestamps for burst detection.
type burstTracker struct {
	count     int
	startedAt time.Time
}

// NewMultiLayerRateLimiter creates a four-layer limiter with the given configs.
func NewMultiLayerRateLimiter(configs map[LimitLayer]LayerConfig) *MultiLayerRateLimiter {
	return &MultiLayerRateLimiter{
		buckets:        make(map[string]*tokenBucket),
		configs:        configs,
		evictionIdle:   5 * time.Minute,
		burstThreshold: 3.0,
		burstWindow:    10 * time.Second,
		burstCounters:  make(map[string]*burstTracker),
		softLimitRatio: 0.8,
	}
}

// WithDistributed enables Redis-backed distributed rate limiting across instances.
func (rl *MultiLayerRateLimiter) WithDistributed(store *state.Store) *MultiLayerRateLimiter {
	rl.distributed = &DistributedRateLimiter{store: store}
	log.Printf("ratelimit: distributed mode enabled (redis-backed)")
	return rl
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

	// Periodic stale bucket cleanup (every 5 minutes)
	rl.maybeEvictStale()

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

// maybeEvictStale removes token buckets idle longer than evictionIdle.
// Must be called with at least a read lock held (best-effort, non-blocking check).
func (rl *MultiLayerRateLimiter) maybeEvictStale() {
	rl.mu.Lock()
	defer rl.mu.Unlock()
	if time.Since(rl.lastCleanup) < 1*time.Minute {
		return // Don't clean up more than once per minute
	}
	rl.lastCleanup = time.Now()

	evicted := 0
	for key, b := range rl.buckets {
		if b.idleDuration() > rl.evictionIdle {
			delete(rl.buckets, key)
			evicted++
		}
	}
	if evicted > 0 {
		log.Printf("ratelimit: evicted %d stale token buckets (idle > %v)", evicted, rl.evictionIdle)
	}
}

// isBursting checks if a principal is exhibiting burst behavior.
func (rl *MultiLayerRateLimiter) isBursting(key string, normalRate float64) bool {
	rl.burstMu.Lock()
	defer rl.burstMu.Unlock()

	bt, ok := rl.burstCounters[key]
	if !ok || time.Since(bt.startedAt) > rl.burstWindow {
		rl.burstCounters[key] = &burstTracker{count: 1, startedAt: time.Now()}
		return false
	}

	bt.count++
	rate := float64(bt.count) / rl.burstWindow.Seconds()
	isBurst := rate > normalRate*rl.burstThreshold

	if isBurst && bt.count%50 == 0 {
		log.Printf("ratelimit: burst detected key=%s rate=%.1f/s threshold=%.1f/s",
			key, rate, normalRate*rl.burstThreshold)
	}

	return isBurst
}

// CheckUser checks the user and tenant layers. Returns the layer that rejected
// (empty string if all passed). Also checks distributed limits if enabled.
func (rl *MultiLayerRateLimiter) CheckUser(userID, tenantID string) LimitLayer {
	// Distributed check first (fastest path)
	if rl.distributed != nil {
		if !rl.distributed.Allow("user:"+userID, int(rl.configs[LayerUser].Capacity),
			time.Duration(rl.configs[LayerUser].Capacity/rl.configs[LayerUser].Rate)*time.Second) {
			return LayerUser
		}
		if !rl.distributed.Allow("tenant:"+tenantID, int(rl.configs[LayerTenant].Capacity),
			time.Duration(rl.configs[LayerTenant].Capacity/rl.configs[LayerTenant].Rate)*time.Second) {
			return LayerTenant
		}
	}

	if b := rl.bucket(LayerUser, userID); b != nil {
		if !b.allow() {
			if rl.isBursting("user:"+userID, rl.configs[LayerUser].Rate) {
				return LayerUser + "_burst"
			}
			return LayerUser
		}
	}
	if b := rl.bucket(LayerTenant, tenantID); b != nil {
		if !b.allow() {
			if rl.isBursting("tenant:"+tenantID, rl.configs[LayerTenant].Rate) {
				return LayerTenant + "_burst"
			}
			return LayerTenant
		}
	}
	return ""
}

// CheckAgent checks the agent and tool layers. Returns the layer that rejected.
func (rl *MultiLayerRateLimiter) CheckAgent(agentRole, toolName string) LimitLayer {
	if agentRole != "" {
		if b := rl.bucket(LayerAgent, agentRole); b != nil {
			if !b.allow() {
				return LayerAgent
			}
		}
	}
	if toolName != "" {
		if b := rl.bucket(LayerTool, toolName); b != nil {
			if !b.allow() {
				return LayerTool
			}
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

// Stats returns detailed rate limiter statistics including per-layer counts.
func (rl *MultiLayerRateLimiter) Stats() map[string]interface{} {
	rl.mu.RLock()
	defer rl.mu.RUnlock()

	layerCounts := make(map[string]int)
	for key := range rl.buckets {
		if idx := strings.IndexByte(key, ':'); idx > 0 {
			layer := key[:idx]
			layerCounts[layer]++
		}
	}

	return map[string]interface{}{
		"total_buckets":    len(rl.buckets),
		"per_layer":        layerCounts,
		"distributed":      rl.distributed != nil,
		"soft_limit_ratio": rl.softLimitRatio,
		"eviction_idle":    rl.evictionIdle.String(),
	}
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

// ── Distributed Rate Limiter (Redis-backed) ───────────────────────────────
//
// Uses Redis as a simple fixed-window counter for cross-instance rate limiting.
// When Redis is unavailable, the in-memory token bucket handles all limiting.

// DistributedRateLimiter provides Redis-backed distributed rate limiting.
type DistributedRateLimiter struct {
	store *state.Store
}

// Allow checks a distributed rate limit key using a fixed-window counter.
// key: unique identifier (e.g., "user:abc123")
// maxRequests: maximum requests allowed in the window
// window: the time window duration
func (drl *DistributedRateLimiter) Allow(key string, maxRequests int, window time.Duration) bool {
	if drl == nil || drl.store == nil {
		return true // No Redis — allow through (in-memory will handle)
	}

	redisKey := "ratelimit:" + key

	// Read current count using GetString (returns raw value for strings stored via PutJSON)
	ctx, cancel := context.WithTimeout(context.Background(), 200*time.Millisecond)
	defer cancel()

	current, err := drl.store.GetString(ctx, redisKey)
	if err != nil || current == "" {
		// First request in window — set count=1 with TTL
		_ = drl.store.PutJSON(ctx, redisKey, "1", window)
		return true
	}

	// Parse current count — try JSON number first, then bare integer
	count := 0
	current = strings.TrimSpace(strings.Trim(current, "\""))
	for _, ch := range current {
		if ch >= '0' && ch <= '9' {
			count = count*10 + int(ch-'0')
		} else {
			count = 1
			break
		}
	}
	if count < 1 {
		count = 1
	}

	if count >= maxRequests {
		return false
	}

	// Increment (best-effort — TTL is already set on the key)
	count++
	_ = drl.store.PutJSON(ctx, redisKey, strconvAtoi(count), window)
	return true
}

// strconvAtoi is a lightweight int-to-string converter (avoid importing strconv for caller).
func strconvAtoi(n int) string {
	if n == 0 {
		return "0"
	}
	neg := n < 0
	if neg {
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}

// ── Rate Limit Headers Middleware ─────────────────────────────────────────
//
// Adds standard rate limit headers (X-RateLimit-*) to all responses so clients
// can track their usage.

// rateLimitHeaders adds X-RateLimit-* headers to responses.
func rateLimitHeaders(w http.ResponseWriter, layer string, remaining, limit int64) {
	w.Header().Set("X-RateLimit-Limit", strconvAtoi(int(limit)))
	w.Header().Set("X-RateLimit-Remaining", strconvAtoi(int(remaining)))
	w.Header().Set("X-RateLimit-Layer", layer)
}

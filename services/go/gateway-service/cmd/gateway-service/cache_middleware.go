package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/agenthub/platform/shared/obs"
	"github.com/agenthub/platform/shared/state"
	"github.com/prometheus/client_golang/prometheus"
)

// ── Cache Metrics ─────────────────────────────────────────────────────────

var (
	cacheHits = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "gateway_cache_hits_total", Help: "Cache hit count by cache name."},
		[]string{"cache"},
	)
	cacheMisses = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "gateway_cache_misses_total", Help: "Cache miss count by cache name."},
		[]string{"cache"},
	)
	cacheWrites = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "gateway_cache_writes_total", Help: "Cache write count by cache name."},
		[]string{"cache"},
	)
	cacheEvictions = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "gateway_cache_evictions_total", Help: "Cache eviction count by cache name."},
		[]string{"cache"},
	)
)

func init() {
	obs.MustRegister(cacheHits, cacheMisses, cacheWrites, cacheEvictions)
}

// ── Cache Entry ───────────────────────────────────────────────────────────

// cacheEntry holds cached response data with metadata.
type cacheEntry struct {
	Body        []byte            `json:"body"`
	StatusCode  int               `json:"status_code"`
	Headers     map[string]string `json:"headers"`
	ContentType string            `json:"content_type"`
	CachedAt    time.Time         `json:"cached_at"`
	ExpiresAt   time.Time         `json:"expires_at"`
	ETag        string            `json:"etag,omitempty"`
}

// ── Two-Tier Cache (L1: in-memory, L2: Redis) ─────────────────────────────

// CacheConfig holds cache parameters.
type CacheConfig struct {
	MemoryMaxEntries int
	MemoryTTL        time.Duration
	RedisTTL         time.Duration
}

// DefaultCacheConfig returns sensible defaults.
func DefaultCacheConfig() CacheConfig {
	return CacheConfig{
		MemoryMaxEntries: getenvInt("CACHE_MEMORY_MAX_ENTRIES", 10000),
		MemoryTTL:        time.Duration(getenvInt("CACHE_MEMORY_TTL_SEC", 60)) * time.Second,
		RedisTTL:         time.Duration(getenvInt("CACHE_REDIS_TTL_SEC", 300)) * time.Second,
	}
}

// TwoTierCache provides L1 (in-memory LRU) + L2 (Redis) caching.
type TwoTierCache struct {
	name    string
	config  CacheConfig
	store   *state.Store
	mu      sync.RWMutex
	memory  map[string]*cacheEntry
	lruKeys []string
}

// NewTwoTierCache creates a two-tier cache.
func NewTwoTierCache(name string, store *state.Store, config CacheConfig) *TwoTierCache {
	c := &TwoTierCache{
		name:    name,
		config:  config,
		store:   store,
		memory:  make(map[string]*cacheEntry),
		lruKeys: make([]string, 0, config.MemoryMaxEntries),
	}
	log.Printf("cache: %s initialized (memory_max=%d ttl=%v redis_ttl=%v redis=%v)",
		name, config.MemoryMaxEntries, config.MemoryTTL, config.RedisTTL, store != nil)
	return c
}

// Get retrieves a cached entry, checking L1 first then L2.
func (c *TwoTierCache) Get(ctx context.Context, key string) (*cacheEntry, bool) {
	// L1: in-memory
	c.mu.RLock()
	entry, ok := c.memory[key]
	c.mu.RUnlock()

	if ok {
		if time.Now().Before(entry.ExpiresAt) {
			cacheHits.WithLabelValues(c.name).Inc()
			c.touchLRU(key)
			return entry, true
		}
		c.mu.Lock()
		delete(c.memory, key)
		c.removeLRUKey(key)
		c.mu.Unlock()
	}

	// L2: Redis
	if c.store != nil {
		data, err := c.store.GetString(ctx, c.redisKey(key))
		if err == nil && data != "" {
			var entry cacheEntry
			if json.Unmarshal([]byte(data), &entry) == nil {
				if time.Now().Before(entry.ExpiresAt) {
					cacheHits.WithLabelValues(c.name).Inc()
					c.setMemory(key, &entry)
					return &entry, true
				}
			}
		}
	}

	cacheMisses.WithLabelValues(c.name).Inc()
	return nil, false
}

// Set stores an entry in both L1 and L2.
func (c *TwoTierCache) Set(ctx context.Context, key string, entry *cacheEntry) {
	entry.CachedAt = time.Now()
	entry.ExpiresAt = time.Now().Add(c.config.MemoryTTL)

	hash := sha256.Sum256(entry.Body)
	entry.ETag = `"` + hex.EncodeToString(hash[:16]) + `"`

	c.setMemory(key, entry)
	cacheWrites.WithLabelValues(c.name).Inc()

	// L2: Redis (async, best-effort)
	if c.store != nil {
		go func() {
			redisEntry := cacheEntry{
				Body:        entry.Body,
				StatusCode:  entry.StatusCode,
				Headers:     entry.Headers,
				ContentType: entry.ContentType,
				CachedAt:    entry.CachedAt,
				ExpiresAt:   time.Now().Add(c.config.RedisTTL),
				ETag:        entry.ETag,
			}
			redisData, _ := json.Marshal(redisEntry)
			_ = c.store.PutJSON(context.Background(), c.redisKey(key), string(redisData), c.config.RedisTTL)
		}()
	}
}

// Invalidate removes an entry from both tiers.
func (c *TwoTierCache) Invalidate(ctx context.Context, key string) {
	c.mu.Lock()
	delete(c.memory, key)
	c.removeLRUKey(key)
	c.mu.Unlock()
	cacheEvictions.WithLabelValues(c.name).Inc()

	if c.store != nil {
		_ = c.store.Del(ctx, c.redisKey(key))
	}
}

// InvalidateByPrefix removes all entries matching a key prefix.
func (c *TwoTierCache) InvalidateByPrefix(ctx context.Context, prefix string) int {
	c.mu.Lock()
	count := 0
	for key := range c.memory {
		if strings.HasPrefix(key, prefix) {
			delete(c.memory, key)
			c.removeLRUKey(key)
			count++
		}
	}
	c.mu.Unlock()

	if c.store != nil {
		// Best-effort Redis prefix invalidation
		keys, err := c.store.Keys(ctx, c.redisKey(prefix)+"*")
		if err == nil {
			for _, k := range keys {
				_ = c.store.Del(ctx, k)
			}
		}
	}
	return count
}

// Stats returns cache statistics.
func (c *TwoTierCache) Stats() map[string]interface{} {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return map[string]interface{}{
		"name":       c.name,
		"memory_len": len(c.memory),
		"max_memory": c.config.MemoryMaxEntries,
		"redis":      c.store != nil,
	}
}

// ── Internal helpers ──────────────────────────────────────────────────────

func (c *TwoTierCache) redisKey(key string) string {
	return "cache:" + c.name + ":" + key
}

func (c *TwoTierCache) setMemory(key string, entry *cacheEntry) {
	c.mu.Lock()
	defer c.mu.Unlock()

	if len(c.memory) >= c.config.MemoryMaxEntries && len(c.lruKeys) > 0 {
		oldest := c.lruKeys[0]
		delete(c.memory, oldest)
		c.lruKeys = c.lruKeys[1:]
		cacheEvictions.WithLabelValues(c.name).Inc()
	}

	c.memory[key] = entry
	c.removeLRUKey(key)
	c.lruKeys = append(c.lruKeys, key)
}

func (c *TwoTierCache) touchLRU(key string) {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.removeLRUKey(key)
	c.lruKeys = append(c.lruKeys, key)
}

func (c *TwoTierCache) removeLRUKey(key string) {
	for i, k := range c.lruKeys {
		if k == key {
			c.lruKeys = append(c.lruKeys[:i], c.lruKeys[i+1:]...)
			return
		}
	}
}

// ── Cache Middleware ──────────────────────────────────────────────────────

// CacheMiddleware wraps an http.Handler with read-through caching.
type CacheMiddleware struct {
	cache       *TwoTierCache
	next        http.Handler
	keyPrefix   string
	ttl         time.Duration
	shouldCache func(r *http.Request) bool
}

// NewCacheMiddleware creates a caching middleware layer.
func NewCacheMiddleware(cache *TwoTierCache, next http.Handler, keyPrefix string, ttl time.Duration, shouldCache func(r *http.Request) bool) *CacheMiddleware {
	if shouldCache == nil {
		shouldCache = func(r *http.Request) bool {
			return r.Method == http.MethodGet
		}
	}
	return &CacheMiddleware{
		cache:       cache,
		next:        next,
		keyPrefix:   keyPrefix,
		ttl:         ttl,
		shouldCache: shouldCache,
	}
}

// ServeHTTP implements http.Handler with read-through caching.
func (cm *CacheMiddleware) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if !cm.shouldCache(r) {
		cm.next.ServeHTTP(w, r)
		return
	}

	if r.Header.Get("Cache-Control") == "no-cache" {
		cm.next.ServeHTTP(w, r)
		return
	}

	cacheKey := cm.keyPrefix + ":" + r.Method + ":" + r.URL.Path
	if r.URL.RawQuery != "" {
		cacheKey += "?" + r.URL.RawQuery
	}

	ctx := r.Context()
	if entry, ok := cm.cache.Get(ctx, cacheKey); ok {
		if etag := r.Header.Get("If-None-Match"); etag != "" && etag == entry.ETag {
			w.WriteHeader(http.StatusNotModified)
			return
		}
		for k, v := range entry.Headers {
			w.Header().Set(k, v)
		}
		w.Header().Set("X-Cache", "HIT")
		w.Header().Set("ETag", entry.ETag)
		w.WriteHeader(entry.StatusCode)
		w.Write(entry.Body)
		return
	}

	w.Header().Set("X-Cache", "MISS")
	rec := &responseRecorder{ResponseWriter: w, statusCode: http.StatusOK}
	cm.next.ServeHTTP(rec, r)

	if rec.statusCode >= 200 && rec.statusCode < 300 {
		entry := &cacheEntry{
			Body:        rec.body.Bytes(),
			StatusCode:  rec.statusCode,
			ContentType: rec.Header().Get("Content-Type"),
			Headers:     make(map[string]string),
		}
		for _, h := range []string{"Content-Type", "Cache-Control"} {
			if v := rec.Header().Get(h); v != "" {
				entry.Headers[h] = v
			}
		}
		cacheTTL := cm.ttl
		if cc := rec.Header().Get("Cache-Control"); cc != "" {
			if maxAge := parseMaxAge(cc); maxAge > 0 {
				cacheTTL = time.Duration(maxAge) * time.Second
			}
		}
		cm.cache.config.MemoryTTL = cacheTTL
		cm.cache.Set(ctx, cacheKey, entry)
	}
}

// ── Response Recorder ─────────────────────────────────────────────────────

type responseRecorder struct {
	http.ResponseWriter
	statusCode  int
	body        bytes.Buffer
	wroteHeader bool
}

func (r *responseRecorder) WriteHeader(statusCode int) {
	if r.wroteHeader {
		return
	}
	r.wroteHeader = true
	r.statusCode = statusCode
	r.ResponseWriter.WriteHeader(statusCode)
}

func (r *responseRecorder) Write(b []byte) (int, error) {
	if !r.wroteHeader {
		r.WriteHeader(http.StatusOK)
	}
	r.body.Write(b)
	return r.ResponseWriter.Write(b)
}

func parseMaxAge(cc string) int {
	const prefix = "max-age="
	idx := strings.Index(cc, prefix)
	if idx < 0 {
		return 0
	}
	rest := cc[idx+len(prefix):]
	end := 0
	for end < len(rest) && rest[end] >= '0' && rest[end] <= '9' {
		end++
	}
	if end == 0 {
		return 0
	}
	val := 0
	fmt.Sscanf(rest[:end], "%d", &val)
	return val
}

// ── Embedding Cache (Specialized) ─────────────────────────────────────────

// EmbeddingCache caches embedding vectors keyed by text hash.
type EmbeddingCache struct {
	cache *TwoTierCache
}

// NewEmbeddingCache creates an embedding cache.
func NewEmbeddingCache(store *state.Store) *EmbeddingCache {
	config := DefaultCacheConfig()
	config.MemoryTTL = 1 * time.Hour
	config.RedisTTL = 24 * time.Hour
	return &EmbeddingCache{
		cache: NewTwoTierCache("embedding", store, config),
	}
}

// GetEmbedding retrieves a cached embedding for the given text hash.
func (ec *EmbeddingCache) GetEmbedding(ctx context.Context, textHash string) ([]float64, bool) {
	entry, ok := ec.cache.Get(ctx, "emb:"+textHash)
	if !ok {
		return nil, false
	}
	var vec []float64
	if err := json.Unmarshal(entry.Body, &vec); err != nil {
		return nil, false
	}
	return vec, true
}

// SetEmbedding stores an embedding vector in the cache.
func (ec *EmbeddingCache) SetEmbedding(ctx context.Context, textHash string, vec []float64) {
	data, err := json.Marshal(vec)
	if err != nil {
		return
	}
	ec.cache.Set(ctx, "emb:"+textHash, &cacheEntry{
		Body:        data,
		StatusCode:  200,
		ContentType: "application/json",
	})
}

// ── Cache Manager ─────────────────────────────────────────────────────────

// CacheManager holds all gateway caches and provides invalidation hooks.
type CacheManager struct {
	mu        sync.RWMutex
	caches    map[string]*TwoTierCache
	embedding *EmbeddingCache
}

// NewCacheManager creates a cache manager.
func NewCacheManager(store *state.Store) *CacheManager {
	return &CacheManager{
		caches: make(map[string]*TwoTierCache),
	}
}

// GetOrCreate returns an existing named cache or creates a new one.
func (cm *CacheManager) GetOrCreate(name string, store *state.Store, config CacheConfig) *TwoTierCache {
	cm.mu.Lock()
	defer cm.mu.Unlock()
	if cache, ok := cm.caches[name]; ok {
		return cache
	}
	cache := NewTwoTierCache(name, store, config)
	cm.caches[name] = cache
	return cache
}

// InvalidateRoute invalidates all cache entries for a given route prefix.
func (cm *CacheManager) InvalidateRoute(ctx context.Context, routePrefix string) int {
	cm.mu.RLock()
	defer cm.mu.RUnlock()
	total := 0
	for _, cache := range cm.caches {
		total += cache.InvalidateByPrefix(ctx, routePrefix)
	}
	if total > 0 {
		log.Printf("cache-manager: invalidated %d entries for route=%s", total, routePrefix)
	}
	return total
}

// Stats returns cache statistics for all caches.
func (cm *CacheManager) Stats() []map[string]interface{} {
	cm.mu.RLock()
	defer cm.mu.RUnlock()
	stats := make([]map[string]interface{}, 0, len(cm.caches))
	for _, cache := range cm.caches {
		stats = append(stats, cache.Stats())
	}
	return stats
}

// WriteThroughInvalidation wraps write operations and invalidates cache after success.
func (cm *CacheManager) WriteThroughInvalidation(routePrefix string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rec := &statusDetector{ResponseWriter: w}
		next.ServeHTTP(rec, r)

		if rec.status >= 200 && rec.status < 300 {
			cm.InvalidateRoute(r.Context(), routePrefix)
		}
	})
}

type statusDetector struct {
	http.ResponseWriter
	status      int
	wroteHeader bool
}

func (sd *statusDetector) WriteHeader(status int) {
	if sd.wroteHeader {
		return
	}
	sd.wroteHeader = true
	sd.status = status
	sd.ResponseWriter.WriteHeader(status)
}

func (sd *statusDetector) Write(b []byte) (int, error) {
	if !sd.wroteHeader {
		sd.WriteHeader(http.StatusOK)
	}
	return sd.ResponseWriter.Write(b)
}

// ── ETag Middleware ───────────────────────────────────────────────────────

// ETagMiddleware wraps responses with ETag headers for conditional requests.
func ETagMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rec := &responseRecorder{ResponseWriter: w, statusCode: http.StatusOK}
		next.ServeHTTP(rec, r)

		if r.Method != http.MethodGet {
			return
		}

		hash := sha256.Sum256(rec.body.Bytes())
		etag := `"` + hex.EncodeToString(hash[:16]) + `"`
		w.Header().Set("ETag", etag)
	})
}

// ── Helpers ───────────────────────────────────────────────────────────────

func bytesReader(b []byte) io.Reader {
	return bytes.NewReader(b)
}

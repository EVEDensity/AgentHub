package main

import (
	"context"
	"log"
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	"github.com/agenthub/platform/shared/obs"
	"github.com/prometheus/client_golang/prometheus"
)

// ── Circuit Breaker for Gateway Downstream Calls ──────────────────────────
//
// Ported from realtime-orchestrator resilience.go, adapted for the gateway's
// proxy pattern. Protects downstream HTTP calls (knowledge service, MCP gateway,
// model adapter) from cascading failures.
//
// State machine:
//   CLOSED → consecutive failures ≥ threshold → OPEN (fast-fail)
//   OPEN → after openDuration → HALF_OPEN (allow one probe)
//   HALF_OPEN → probe succeeds → CLOSED; probe fails → OPEN

var (
	gatewayBreakerState = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{Name: "gateway_circuit_breaker_state", Help: "Gateway circuit breaker state: 0=closed, 1=open, 2=half_open."},
		[]string{"name"},
	)
	gatewayBreakerTrips = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "gateway_circuit_breaker_trips_total", Help: "Gateway circuit breaker trip events."},
		[]string{"name"},
	)
	gatewayBreakerRejected = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "gateway_circuit_breaker_rejected_total", Help: "Gateway requests rejected by open circuit breaker."},
		[]string{"name"},
	)
	gatewayBreakerSuccesses = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "gateway_circuit_breaker_successes_total", Help: "Gateway circuit breaker successful calls."},
		[]string{"name"},
	)
)

func init() {
	obs.MustRegister(gatewayBreakerState, gatewayBreakerTrips, gatewayBreakerRejected, gatewayBreakerSuccesses)
}

// BreakerState represents the circuit breaker state.
type BreakerState int32

const (
	BreakerClosed   BreakerState = 0
	BreakerOpen     BreakerState = 1
	BreakerHalfOpen BreakerState = 2
)

// GWCircuitBreaker is a three-state circuit breaker for gateway downstream calls.
type GWCircuitBreaker struct {
	name         string
	threshold    int
	openDuration time.Duration
	state        atomic.Int32
	failures     atomic.Int32
	openedAt     atomic.Int64
	mu           sync.RWMutex
}

// NewGWCircuitBreaker creates a new circuit breaker.
// threshold: consecutive failures before opening (default 5)
// openDuration: time before transitioning to half-open (default 30s)
func NewGWCircuitBreaker(name string, threshold int, openDuration time.Duration) *GWCircuitBreaker {
	if threshold <= 0 {
		threshold = 5
	}
	if openDuration <= 0 {
		openDuration = 30 * time.Second
	}
	cb := &GWCircuitBreaker{
		name:         name,
		threshold:    threshold,
		openDuration: openDuration,
	}
	cb.state.Store(int32(BreakerClosed))
	gatewayBreakerState.WithLabelValues(name).Set(float64(BreakerClosed))
	return cb
}

// Allow checks whether a request should be allowed through.
func (cb *GWCircuitBreaker) Allow() bool {
	state := BreakerState(cb.state.Load())
	switch state {
	case BreakerClosed:
		return true
	case BreakerOpen:
		openedAt := time.Unix(0, cb.openedAt.Load())
		if time.Since(openedAt) > cb.openDuration {
			if cb.state.CompareAndSwap(int32(BreakerOpen), int32(BreakerHalfOpen)) {
				gatewayBreakerState.WithLabelValues(cb.name).Set(float64(BreakerHalfOpen))
				log.Printf("circuit-breaker: %s transitioning open→half_open (probe allowed)", cb.name)
				return true
			}
			state = BreakerState(cb.state.Load())
			if state == BreakerHalfOpen {
				return true
			}
		}
		gatewayBreakerRejected.WithLabelValues(cb.name).Inc()
		return false
	case BreakerHalfOpen:
		gatewayBreakerRejected.WithLabelValues(cb.name).Inc()
		return false
	}
	return true
}

// RecordSuccess resets the breaker to closed on success.
func (cb *GWCircuitBreaker) RecordSuccess() {
	old := cb.state.Swap(int32(BreakerClosed))
	if old != int32(BreakerClosed) {
		log.Printf("circuit-breaker: %s reset to closed (recovered)", cb.name)
	}
	gatewayBreakerState.WithLabelValues(cb.name).Set(float64(BreakerClosed))
	cb.failures.Store(0)
	gatewayBreakerSuccesses.WithLabelValues(cb.name).Inc()
}

// RecordFailure records a failure and opens the breaker if threshold exceeded.
func (cb *GWCircuitBreaker) RecordFailure() {
	failures := cb.failures.Add(1)
	state := BreakerState(cb.state.Load())

	if state == BreakerHalfOpen {
		cb.openCircuit()
		return
	}

	if failures >= int32(cb.threshold) && state == BreakerClosed {
		cb.openCircuit()
	}
}

func (cb *GWCircuitBreaker) openCircuit() {
	cb.openedAt.Store(time.Now().UnixNano())
	cb.state.Store(int32(BreakerOpen))
	gatewayBreakerState.WithLabelValues(cb.name).Set(float64(BreakerOpen))
	gatewayBreakerTrips.WithLabelValues(cb.name).Inc()
	log.Printf("circuit-breaker: %s tripped open (threshold exceeded)", cb.name)
}

// State returns the current breaker state name.
func (cb *GWCircuitBreaker) State() string {
	switch BreakerState(cb.state.Load()) {
	case BreakerClosed:
		return "closed"
	case BreakerOpen:
		return "open"
	case BreakerHalfOpen:
		return "half_open"
	}
	return "unknown"
}

// ── Circuit Breaker Registry ──────────────────────────────────────────────
//
// Holds all gateway circuit breakers so they can be introspected via /healthz.

// BreakerRegistry holds named circuit breakers.
type BreakerRegistry struct {
	mu       sync.RWMutex
	breakers map[string]*GWCircuitBreaker
}

// NewBreakerRegistry creates a breaker registry.
func NewBreakerRegistry() *BreakerRegistry {
	return &BreakerRegistry{breakers: make(map[string]*GWCircuitBreaker)}
}

// GetOrCreate returns an existing breaker or creates a new one.
func (r *BreakerRegistry) GetOrCreate(name string, threshold int, openDuration time.Duration) *GWCircuitBreaker {
	r.mu.Lock()
	defer r.mu.Unlock()
	if cb, ok := r.breakers[name]; ok {
		return cb
	}
	cb := NewGWCircuitBreaker(name, threshold, openDuration)
	r.breakers[name] = cb
	return cb
}

// States returns all breaker states for introspection.
func (r *BreakerRegistry) States() map[string]string {
	r.mu.RLock()
	defer r.mu.RUnlock()
	states := make(map[string]string, len(r.breakers))
	for name, cb := range r.breakers {
		states[name] = cb.State()
	}
	return states
}

// ── Resilient HTTP RoundTripper ───────────────────────────────────────────
//
// Wraps http.RoundTripper with circuit breaker protection. When the breaker is
// open, returns an error immediately without making the downstream request.
// On HTTP 5xx responses, records a failure; on success, records a success.

// ResilientTransport wraps an http.RoundTripper with circuit breaker protection.
type ResilientTransport struct {
	base    http.RoundTripper
	breaker *GWCircuitBreaker
}

// NewResilientTransport creates a circuit-breaker-protected transport.
func NewResilientTransport(base http.RoundTripper, breaker *GWCircuitBreaker) *ResilientTransport {
	if base == nil {
		base = http.DefaultTransport
	}
	return &ResilientTransport{base: base, breaker: breaker}
}

// RoundTrip implements http.RoundTripper with circuit breaker protection.
func (rt *ResilientTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if !rt.breaker.Allow() {
		return nil, &CircuitOpenError{Name: rt.breaker.name}
	}

	resp, err := rt.base.RoundTrip(req)
	if err != nil {
		rt.breaker.RecordFailure()
		return nil, err
	}

	// 5xx responses count as failures
	if resp.StatusCode >= 500 {
		rt.breaker.RecordFailure()
	} else {
		rt.breaker.RecordSuccess()
	}

	return resp, nil
}

// CircuitOpenError indicates the circuit breaker is open.
type CircuitOpenError struct {
	Name string
}

func (e *CircuitOpenError) Error() string {
	return "circuit breaker open: " + e.Name
}

// ── Resilient HTTP Client ─────────────────────────────────────────────────

// NewResilientHTTPClient creates an http.Client with circuit breaker protection.
func NewResilientHTTPClient(breaker *GWCircuitBreaker, timeout time.Duration) *http.Client {
	return &http.Client{
		Transport: NewResilientTransport(http.DefaultTransport, breaker),
		Timeout:   timeout,
	}
}

// ── Graceful Shutdown ─────────────────────────────────────────────────────

// GracefulShutdownConfig holds shutdown parameters.
type GracefulShutdownConfig struct {
	// DrainTimeout is the maximum time to wait for active connections to finish.
	DrainTimeout time.Duration
	// HealthFailDelay is how long before shutdown to start reporting unhealthy.
	HealthFailDelay time.Duration
}

// DefaultShutdownConfig returns sensible defaults.
func DefaultShutdownConfig() GracefulShutdownConfig {
	return GracefulShutdownConfig{
		DrainTimeout:    30 * time.Second,
		HealthFailDelay: 5 * time.Second,
	}
}

// ShutdownContext holds shutdown state.
type ShutdownContext struct {
	config     GracefulShutdownConfig
	shuttingDown atomic.Bool
	startedAt  atomic.Int64
}

// NewShutdownContext creates a shutdown context.
func NewShutdownContext(config GracefulShutdownConfig) *ShutdownContext {
	return &ShutdownContext{config: config}
}

// Initiate marks the server as shutting down.
func (sc *ShutdownContext) Initiate() {
	sc.shuttingDown.Store(true)
	sc.startedAt.Store(time.Now().UnixNano())
	log.Printf("graceful-shutdown: initiated (drain=%v health_fail=%v)",
		sc.config.DrainTimeout, sc.config.HealthFailDelay)
}

// IsShuttingDown returns true if shutdown has been initiated.
func (sc *ShutdownContext) IsShuttingDown() bool {
	return sc.shuttingDown.Load()
}

// IsReady returns false during shutdown (for readiness probes to drain load balancer).
// After healthFailDelay, reports not-ready so the LB stops routing traffic.
func (sc *ShutdownContext) IsReady() bool {
	if !sc.shuttingDown.Load() {
		return true
	}
	startedAt := time.Unix(0, sc.startedAt.Load())
	// Immediately report not-ready on shutdown
	return time.Since(startedAt) < 0 // always false after shutdown initiated
}

// HealthCheckHandler returns an http.Handler for Kubernetes health/readiness probes
// that respects shutdown state.
func (sc *ShutdownContext) HealthCheckHandler(pool interface{ Ping(context.Context) error }, busConn interface{ IsConnected() bool }) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")

		// If shutting down, always report not-ready (for readiness probe)
		if sc.shuttingDown.Load() {
			w.WriteHeader(http.StatusServiceUnavailable)
			w.Write([]byte(`{"status":"shutting_down","ready":false}`))
			return
		}

		// Normal health check
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()

		pgOK := true
		if pool != nil {
			if err := pool.Ping(ctx); err != nil {
				pgOK = false
			}
		}

		natsOK := busConn.IsConnected()

		status := http.StatusOK
		body := "ok"
		if !pgOK || !natsOK {
			status = http.StatusServiceUnavailable
			body = "degraded"
		}

		w.WriteHeader(status)
		w.Write([]byte(`{"status":"` + body + `","pg":` + boolStr(pgOK) + `,"nats":` + boolStr(natsOK) + `}`))
	}
}

func boolStr(b bool) string {
	if b {
		return "true"
	}
	return "false"
}

// ── Fault Injection (Chaos Engineering) ──────────────────────────────────
//
// Optional fault injection middleware for testing resilience paths.
// Controlled via env vars:
//   CHAOS_LATENCY_MS — inject artificial latency (0 = disabled)
//   CHAOS_ERROR_RATE — fraction of requests to fail (0.0–1.0, 0 = disabled)
//   CHAOS_TARGET_PATH_PREFIX — only affect paths starting with this prefix

// ChaosConfig holds fault injection parameters.
type ChaosConfig struct {
	LatencyMs       int
	ErrorRate       float64
	TargetPathPrefix string
}

// ChaosConfigFromEnv reads chaos config from environment variables.
func ChaosConfigFromEnv() ChaosConfig {
	return ChaosConfig{
		LatencyMs:        getenvInt("CHAOS_LATENCY_MS", 0),
		ErrorRate:        getenvFloat("CHAOS_ERROR_RATE", 0),
		TargetPathPrefix: getenv("CHAOS_TARGET_PATH_PREFIX", ""),
	}
}

// ChaosMiddleware injects faults based on config. Only active when env vars are set.
func ChaosMiddleware(cfg ChaosConfig, next http.Handler) http.Handler {
	if cfg.LatencyMs == 0 && cfg.ErrorRate == 0 {
		return next // no chaos configured, skip overhead
	}

	log.Printf("chaos: enabled latency=%dms error_rate=%.2f target=%q",
		cfg.LatencyMs, cfg.ErrorRate, cfg.TargetPathPrefix)

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Check target path prefix
		if cfg.TargetPathPrefix != "" {
			if !stringsHasPrefix(r.URL.Path, cfg.TargetPathPrefix) {
				next.ServeHTTP(w, r)
				return
			}
		}

		// Inject latency
		if cfg.LatencyMs > 0 {
			select {
			case <-time.After(time.Duration(cfg.LatencyMs) * time.Millisecond):
			case <-r.Context().Done():
				return
			}
		}

		// Inject errors (simple random check)
		if cfg.ErrorRate > 0 {
			if fastRandFloat() < cfg.ErrorRate {
				http.Error(w, "chaos: injected fault", http.StatusServiceUnavailable)
				return
			}
		}

		next.ServeHTTP(w, r)
	})
}

// fastRandFloat returns a pseudo-random float in [0,1) using time-based seed.
// Not cryptographically secure — adequate for chaos fault injection.
func fastRandFloat() float64 {
	return float64(time.Now().UnixNano()%10000) / 10000.0
}

// stringsHasPrefix reports whether s starts with prefix (local copy to avoid import).
func stringsHasPrefix(s, prefix string) bool {
	return len(s) >= len(prefix) && s[:len(prefix)] == prefix
}

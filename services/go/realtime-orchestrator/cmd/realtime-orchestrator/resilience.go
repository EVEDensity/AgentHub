package main

import (
	"context"
	"fmt"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/agenthub/platform/shared/obs"
	"github.com/prometheus/client_golang/prometheus"
)

// =========================================================================
// 熔断器 + 并发限制器（P2-7 worker pool 隔离 + P2-8 故障降级链）
//
// 设计参考 capacity_model.json 的 controls.runtime:
//   "pool concurrency ceilings", "circuit breakers", "budget enforcement"
//
// 熔断器状态机：
//   CLOSED → 连续失败达 threshold → OPEN（快速熔断，拒绝请求）
//   OPEN → 经过 openDuration → HALF_OPEN（放行 1 个探测请求）
//   HALF_OPEN → 探测成功 → CLOSED；探测失败 → OPEN
// =========================================================================

// --- Prometheus 指标 ---

var (
	breakerState = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{Name: "circuit_breaker_state", Help: "Circuit breaker state: 0=closed, 1=open, 2=half_open."},
		[]string{"name"},
	)
	breakerTrips = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "circuit_breaker_trips_total", Help: "Circuit breaker trip events (closed→open)."},
		[]string{"name"},
	)
	breakerRejected = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "circuit_breaker_rejected_total", Help: "Requests rejected by open circuit breaker."},
		[]string{"name"},
	)
	poolActive = prometheus.NewGaugeVec(
		prometheus.GaugeOpts{Name: "deepsearch_pool_active", Help: "Active DeepSearch operations in the pool."},
		[]string{},
	)
	poolWaitTime = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{Name: "deepsearch_pool_wait_seconds", Help: "Time spent waiting for a pool slot.", Buckets: prometheus.DefBuckets},
		[]string{},
	)
)

func init() {
	obs.MustRegister(breakerState, breakerTrips, breakerRejected, poolActive, poolWaitTime)
}

// BreakerState 表示熔断器的状态。
type BreakerState int32

const (
	BreakerClosed   BreakerState = 0
	BreakerOpen     BreakerState = 1
	BreakerHalfOpen BreakerState = 2
)

// CircuitBreaker 是一个简单的三态熔断器。当下游服务连续失败达 threshold 次
// 时打开熔断，在 openDuration 后进入半开状态放行探测请求。
//
// 使用方式：
//   if !cb.Allow() { return fallback }
//   result, err := callDownstream()
//   if err { cb.RecordFailure() } else { cb.RecordSuccess() }
type CircuitBreaker struct {
	name         string
	threshold    int           // 连续失败多少次后熔断
	openDuration time.Duration // 熔断持续时间，过后进入半开
	state        atomic.Int32  // BreakerState
	failures     atomic.Int32  // 连续失败计数
	openedAt     atomic.Int64  // 熔断打开时的 unix nano
}

// NewCircuitBreaker 创建一个熔断器。
func NewCircuitBreaker(name string, threshold int, openDuration time.Duration) *CircuitBreaker {
	cb := &CircuitBreaker{
		name:         name,
		threshold:    threshold,
		openDuration: openDuration,
	}
	cb.state.Store(int32(BreakerClosed))
	breakerState.WithLabelValues(name).Set(float64(BreakerClosed))
	return cb
}

// Allow 检查是否允许请求通过。在 OPEN 状态下，如果已过 openDuration，
// 自动转为 HALF_OPEN 并放行一个探测请求。
func (cb *CircuitBreaker) Allow() bool {
	state := BreakerState(cb.state.Load())
	switch state {
	case BreakerClosed:
		return true
	case BreakerOpen:
		// 检查是否该转入半开
		openedAt := time.Unix(0, cb.openedAt.Load())
		if time.Since(openedAt) > cb.openDuration {
			if cb.state.CompareAndSwap(int32(BreakerOpen), int32(BreakerHalfOpen)) {
				breakerState.WithLabelValues(cb.name).Set(float64(BreakerHalfOpen))
				return true // 放行探测请求
			}
			// CAS 失败说明其他 goroutine 已先转换
			state = BreakerState(cb.state.Load())
			if state == BreakerHalfOpen {
				return true
			}
		}
		breakerRejected.WithLabelValues(cb.name).Inc()
		return false
	case BreakerHalfOpen:
		// 半开状态只允许一个探测请求；如果已有探测在飞，拒绝
		breakerRejected.WithLabelValues(cb.name).Inc()
		return false
	}
	return true
}

// RecordSuccess 记录一次成功调用，将熔断器重置为 CLOSED。
func (cb *CircuitBreaker) RecordSuccess() {
	old := cb.state.Swap(int32(BreakerClosed))
	if old != int32(BreakerClosed) {
		breakerState.WithLabelValues(cb.name).Set(float64(BreakerClosed))
	}
	cb.failures.Store(0)
}

// RecordFailure 记录一次失败调用。连续失败达 threshold 时打开熔断器。
func (cb *CircuitBreaker) RecordFailure() {
	failures := cb.failures.Add(1)
	state := BreakerState(cb.state.Load())

	if state == BreakerHalfOpen {
		// 半开状态下失败，重新打开熔断器
		cb.openCircuit()
		return
	}

	if failures >= int32(cb.threshold) && state == BreakerClosed {
		cb.openCircuit()
	}
}

func (cb *CircuitBreaker) openCircuit() {
	cb.openedAt.Store(time.Now().UnixNano())
	cb.state.Store(int32(BreakerOpen))
	breakerState.WithLabelValues(cb.name).Set(float64(BreakerOpen))
	breakerTrips.WithLabelValues(cb.name).Inc()
}

// State 返回当前状态名称。
func (cb *CircuitBreaker) State() string {
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

// =========================================================================
// DeepSearchPool — 并发限制器
//
// 使用信号量（buffered channel）限制同时执行的 DeepSearch 操作数。
// 防止高并发场景下 orchestrator 同时发起过多 HTTP 请求压垮
// model-adapter 和 retrieval-core。
// =========================================================================

// DeepSearchPool 使用信号量限制 DeepSearch 的并发执行数。
type DeepSearchPool struct {
	sem     chan struct{}
	active  atomic.Int64
}

// NewDeepSearchPool 创建一个并发限制器。maxConcurrent 为最大并发数。
func NewDeepSearchPool(maxConcurrent int) *DeepSearchPool {
	return &DeepSearchPool{
		sem: make(chan struct{}, maxConcurrent),
	}
}

// Acquire 获取一个并发槽位。返回等待耗时和释放函数。
// 如果池已满，调用者将阻塞直到有槽位释放。
func (p *DeepSearchPool) Acquire() (time.Duration, func()) {
	start := time.Now()
	p.sem <- struct{}{}
	wait := time.Since(start)
	poolActive.WithLabelValues().Inc()
	p.active.Add(1)
	return wait, func() {
		<-p.sem
		p.active.Add(-1)
		poolActive.WithLabelValues().Dec()
	}
}

// Active 返回当前活跃操作数。
func (p *DeepSearchPool) Active() int64 {
	return p.active.Load()
}

// =========================================================================
// ResilientDeepSearchFlow — 带熔断 + 并发限制的 DeepSearch
//
// 包装 DeepSearchFlow，在每次调用前检查熔断器和并发池。
// 当熔断器打开时，直接返回降级结果，不发起 HTTP 请求。
// =========================================================================

// ResilientDeepSearchFlow 在 DeepSearchFlow 之上增加熔断器和并发限制。
type ResilientDeepSearchFlow struct {
	inner         *DeepSearchFlow
	pool          *DeepSearchPool
	adapterBreaker  *CircuitBreaker // model-adapter 熔断器
	retrievalBreaker *CircuitBreaker // retrieval-core 熔断器
	mu            sync.Mutex
}

// NewResilientDeepSearchFlow 创建带熔断 + 并发限制的 DeepSearch。
func NewResilientDeepSearchFlow(inner *DeepSearchFlow, maxConcurrent int) *ResilientDeepSearchFlow {
	return &ResilientDeepSearchFlow{
		inner:           inner,
		pool:            NewDeepSearchPool(maxConcurrent),
		adapterBreaker:  NewCircuitBreaker("model-adapter", 5, 30*time.Second),
		retrievalBreaker: NewCircuitBreaker("retrieval-core", 5, 30*time.Second),
	}
}

// Retrieve 执行带熔断 + 并发限制的 DeepSearch 检索。
// 1. 获取并发槽位（阻塞式信号量）
// 2. 检查熔断器状态
// 3. 调用内部 DeepSearchFlow.Retrieve
// 4. 根据结果更新熔断器
// 5. 如果 retrieval-core 熔断，尝试轻量检索降级（simple 模式）
func (f *ResilientDeepSearchFlow) Retrieve(ctx context.Context, query, tenantID, sessionID, traceID string) (*DeepSearchResult, error) {
	wait, release := f.pool.Acquire()
	defer release()
	poolWaitTime.WithLabelValues().Observe(wait.Seconds())

	// 如果两个熔断器都打开，直接返回降级结果
	if !f.adapterBreaker.Allow() && !f.retrievalBreaker.Allow() {
		return &DeepSearchResult{
			OriginalQuery:  query,
			RewrittenQuery: query,
			SubQueries:     []string{query},
			Citations:      []CitationDTO{},
			Degraded:       []string{"all_breakers_open"},
			EvidenceContext: "[no evidence retrieved — circuit breakers open]",
			GroundingWarning: "all downstream services are circuit-broken",
		}, nil
	}

	result, err := f.inner.Retrieve(ctx, query, tenantID, sessionID, traceID)
	if err != nil {
		return result, err
	}

	// 根据降级状态更新熔断器
	// 如果 retrieval-core 完全失败（hybrid_retrieve_failed），记录 retrieval 熔断
	for _, d := range result.Degraded {
		if d == "hybrid_retrieve_failed" {
			f.retrievalBreaker.RecordFailure()
			break
		}
	}
	// 如果没有 hybrid_retrieve_failed，说明 retrieval-core 正常，重置熔断器
	if !contains(result.Degraded, "hybrid_retrieve_failed") && len(result.Citations) >= 0 {
		f.retrievalBreaker.RecordSuccess()
	}

	// 如果 model-adapter 返回 fallback，记录 adapter 熔断
	adapterFailed := false
	for _, d := range result.Degraded {
		if d == "rewrite_fallback" || d == "decompose_fallback" {
			adapterFailed = true
			f.adapterBreaker.RecordFailure()
			break
		}
	}
	if !adapterFailed {
		f.adapterBreaker.RecordSuccess()
	}

	return result, nil
}

// Synthesize 执行带熔断的 DeepSearch 合成。
func (f *ResilientDeepSearchFlow) Synthesize(ctx context.Context, result *DeepSearchResult) (string, error) {
	if !f.adapterBreaker.Allow() {
		// 熔断器打开，返回证据摘要降级
		return fmt.Sprintf("[synthesis fallback — circuit breaker open] Based on %d retrieved citations:\n%s",
			len(result.Citations), result.EvidenceContext), nil
	}

	answer, err := f.inner.Synthesize(ctx, result)
	if err != nil {
		f.adapterBreaker.RecordFailure()
		return answer, err
	}

	// 检查是否是降级响应
	if strings.HasPrefix(answer, "[fallback") || strings.Contains(answer, "fallback") {
		f.adapterBreaker.RecordFailure()
	} else {
		f.adapterBreaker.RecordSuccess()
	}

	return answer, nil
}

// BreakerStates 返回各熔断器的当前状态，用于 /healthz 或 /profile 端点。
func (f *ResilientDeepSearchFlow) BreakerStates() map[string]string {
	return map[string]string{
		"model-adapter":  f.adapterBreaker.State(),
		"retrieval-core": f.retrievalBreaker.State(),
	}
}

// --- 辅助函数 ---

func contains(slice []string, s string) bool {
	for _, x := range slice {
		if x == s {
			return true
		}
	}
	return false
}

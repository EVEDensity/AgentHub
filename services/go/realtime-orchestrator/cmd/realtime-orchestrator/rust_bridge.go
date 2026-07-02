package main

import (
	"context"
	"fmt"
	"log"
	"sync"
	"time"

	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
	"github.com/agenthub/platform/shared/obs"
)

// =========================================================================
// RustCoreBridge — 统一管理 Go↔Rust NATS 通信
//
// Sprint D: 打通 5 个 Rust 核心与 Go 服务的 NATS 集成。
//
// 方向与协议：
//   Go → Rust (JetStream): 发布到 agenthub.{fanout,patch,memory,retrieval}.>
//                           JetStream 自动 fanout 到 Rust async_nats subscriber。
//   Rust → Go (core NATS):  Rust async_nats publish → Go SubscribeCore 接收。
//                           为确保持久化，对应 subject 空间已注册 JetStream stream。
//
// 五个核心：
//   1. stream-core:      已有连接（Go 发布 agenthub.session.stream.events，stream-core 订阅）
//   2. retrieval-core:   NATS 异步检索（PublishRetrievalQuery → 等待 retrieval.fusion 响应）
//   3. fanout-core:      广播事件分发（PublishFanoutEvent）
//   4. patch-merge-core: 冲突合并请求（PublishPatchMerge → 等待 patch.audit 响应）
//   5. memory-segment-core: 窗口压缩请求（PublishMemoryCompact → 等待 memory.audit 响应）
// =========================================================================

// RustCoreBridge handles NATS communication with all five Rust core services.
// It provides both fire-and-forget publish (fanout) and request-response
// patterns (retrieval, patch-merge, memory-compact) with correlation IDs.
type RustCoreBridge struct {
	bus      *eventbus.Client
	instance string

	// Pending request-response correlations.
	mu              sync.RWMutex
	pendingRetrieval map[string]chan *fusionResult
	pendingPatch     map[string]chan *patchMergeResult
	pendingMemory    map[string]chan *memoryCompactResult
}

// patchMergeResult is the Go-side representation of Rust patch-merge-core's
// PatchMergeResponse (see services/rust/crates/patch-merge-core/src/lib.rs).
type patchMergeResult struct {
	RequestID    string   `json:"request_id"`
	Strategy     string   `json:"strategy"`
	Success      bool     `json:"success"`
	ConflictScore float64 `json:"conflict_score"`
	MergedText   string   `json:"merged_text,omitempty"`
	ElapsedMs    int64    `json:"elapsed_ms"`
}

// memoryCompactResult is the Go-side representation of Rust memory-segment-core's
// MemoryCompactResponse (see services/rust/crates/memory-segment-core/src/lib.rs).
type memoryCompactResult struct {
	RequestID        string `json:"request_id"`
	SegmentID        string `json:"segment_id"`
	Strategy         string `json:"strategy"`
	OriginalMsgCount int    `json:"original_msg_count"`
	CompactedCount   int    `json:"compacted_count"`
	SummaryText      string `json:"summary_text,omitempty"`
	CursorAfter      string `json:"cursor_after,omitempty"`
	ElapsedMs        int64  `json:"elapsed_ms"`
}

// NewRustCoreBridge creates the bridge and starts background subscribers for
// Rust→Go response subjects (retrieval.fusion, patch.audit, memory.audit).
// The bridge MUST be created before any ReactMachine starts processing.
func NewRustCoreBridge(bus *eventbus.Client) *RustCoreBridge {
	b := &RustCoreBridge{
		bus:              bus,
		instance:         getenv("HOSTNAME", "local"),
		pendingRetrieval: make(map[string]chan *fusionResult),
		pendingPatch:     make(map[string]chan *patchMergeResult),
		pendingMemory:    make(map[string]chan *memoryCompactResult),
	}

	// Subscribe to Rust→Go response subjects via core NATS (Rust async_nats
	// publishes via core NATS, not JetStream). The JetStream FANOUT/PATCH/MEMORY
	// streams capture these for replay/audit, but the real-time path uses core NATS
	// for lowest latency.

	// retrieval-core results: agenthub.retrieval.fusion
	if _, err := bus.SubscribeCore(eventbus.RetrievalFusionSubject, b.handleRetrievalFusion); err != nil {
		log.Printf("rust_bridge: subscribe retrieval.fusion failed: %v", err)
	}

	// patch-merge-core results: agenthub.patch.audit
	if _, err := bus.SubscribeCore(eventbus.PatchAuditSubject, b.handlePatchAudit); err != nil {
		log.Printf("rust_bridge: subscribe patch.audit failed: %v", err)
	}

	// memory-segment-core results: agenthub.memory.audit
	if _, err := bus.SubscribeCore(eventbus.MemoryAuditSubject, b.handleMemoryAudit); err != nil {
		log.Printf("rust_bridge: subscribe memory.audit failed: %v", err)
	}

	log.Printf("rust_bridge: initialised (instance=%s), listening on retrieval.fusion / patch.audit / memory.audit", b.instance)
	return b
}

// ── Retrieval Core ──────────────────────────────────────────────────────

// PublishRetrievalQuery sends a retrieval request to retrieval-core via NATS
// and blocks until the response arrives or ctx is cancelled. This is the NATS
// async path — complementary to the synchronous HTTP path in DeepSearchFlow.
//
// When retrieval-core is busy, messages queue in the RETRIEVAL JetStream stream;
// the durable consumer on retrieval-core's side picks them up in order.
func (b *RustCoreBridge) PublishRetrievalQuery(ctx context.Context, req retrievalRequest) (*fusionResult, error) {
	ch := make(chan *fusionResult, 1)
	b.mu.Lock()
	b.pendingRetrieval[req.RequestID] = ch
	b.mu.Unlock()

	defer func() {
		b.mu.Lock()
		delete(b.pendingRetrieval, req.RequestID)
		b.mu.Unlock()
	}()

	env := events.NewEnvelope(
		events.EventRetrievalQueryRequested,
		"",  // tenantID — set by caller if needed
		req.RequestID,
		"",  // traceID
		events.Producer{Service: "realtime-orchestrator", Instance: b.instance},
		map[string]any{
			"request_id":       req.RequestID,
			"query":            req.Query,
			"mode":             req.Mode,
			"knowledge_scope":  req.KnowledgeScope,
			"top_k":            req.TopK,
			"timeout_ms":       req.TimeoutMs,
		},
	)
	env.EventID = fmt.Sprintf("retr-%s", req.RequestID)
	env.Routing = &events.Routing{Channel: "retrieval", PartitionKey: req.RequestID, Priority: events.PriorityNormal}

	if err := b.bus.PublishEnvelope(ctx, eventbus.RetrievalQuerySubject, env); err != nil {
		return nil, fmt.Errorf("publish retrieval query: %w", err)
	}
	obs.IncEventPublished("realtime-orchestrator", string(events.EventRetrievalQueryRequested))

	select {
	case result := <-ch:
		if result == nil {
			return nil, fmt.Errorf("retrieval fusion: empty response")
		}
		return result, nil
	case <-ctx.Done():
		return nil, fmt.Errorf("retrieval fusion: %w", ctx.Err())
	}
}

func (b *RustCoreBridge) handleRetrievalFusion(env events.Envelope) {
	if env.EventType != events.EventRetrievalFusionComplete {
		return
	}
	obs.IncEventReceived("realtime-orchestrator", string(env.EventType))

	requestID, _ := env.Payload["request_id"].(string)
	if requestID == "" {
		return
	}

	b.mu.RLock()
	ch, ok := b.pendingRetrieval[requestID]
	b.mu.RUnlock()
	if !ok {
		return // no pending request (stale or already timed out)
	}

	result := parseFusionResult(env.Payload)
	select {
	case ch <- result:
	default:
	}
}

// ── Fanout Core ─────────────────────────────────────────────────────────

// PublishFanoutEvent sends a stream/transition event to fanout-core for
// high-cardinality broadcast. fanout-core subscribes to agenthub.fanout.events
// and fans out to channel-specific subscribers.
//
// This is fire-and-forget — fanout-core publishes delivery receipts to
// agenthub.fanout.audit, which audit-log-service consumes.
func (b *RustCoreBridge) PublishFanoutEvent(ctx context.Context, channel string, env events.Envelope) error {
	fanoutEnv := events.NewEnvelope(
		events.EventAgentReactTransition,
		env.TenantID,
		env.SessionID,
		env.TraceID,
		events.Producer{Service: "realtime-orchestrator", Instance: b.instance},
		map[string]any{
			"channel":        channel,
			"source_event_id": env.EventID,
			"source_type":    string(env.EventType),
			"session_id":     env.SessionID,
			"tenant_id":      env.TenantID,
		},
	)
	fanoutEnv.EventID = fmt.Sprintf("fanout-%s-%d", env.SessionID, time.Now().UnixMilli())
	fanoutEnv.MessageID = env.MessageID
	fanoutEnv.ActorID = env.ActorID
	fanoutEnv.Routing = &events.Routing{Channel: channel, PartitionKey: env.SessionID, Priority: events.PriorityNormal}

	if err := b.bus.PublishEnvelope(ctx, eventbus.FanoutEventsSubject, fanoutEnv); err != nil {
		return fmt.Errorf("publish fanout event: %w", err)
	}
	obs.IncEventPublished("realtime-orchestrator", fmt.Sprintf("fanout.%s", channel))
	return nil
}

// ── Patch-Merge Core ────────────────────────────────────────────────────

// PublishPatchMerge sends a merge request to patch-merge-core (LCS diff +
// diff3 three-way merge + conflict scoring) and blocks until the response
// arrives or ctx is cancelled.
//
// baseText: the common ancestor version.
// leftText: the current in-memory version.
// rightText: the incoming concurrent edit.
func (b *RustCoreBridge) PublishPatchMerge(ctx context.Context, tenantID, sessionID, traceID, messageID string, baseText, leftText, rightText string) (*patchMergeResult, error) {
	requestID := fmt.Sprintf("patch-%s-%d", sessionID, time.Now().UnixMilli())
	ch := make(chan *patchMergeResult, 1)
	b.mu.Lock()
	b.pendingPatch[requestID] = ch
	b.mu.Unlock()

	defer func() {
		b.mu.Lock()
		delete(b.pendingPatch, requestID)
		b.mu.Unlock()
	}()

	env := events.NewEnvelope(
		events.EventSessionMessageReceived, // placeholder — patch-merge uses payload fields
		tenantID,
		sessionID,
		traceID,
		events.Producer{Service: "realtime-orchestrator", Instance: b.instance},
		map[string]any{
			"request_id": requestID,
			"base_text":  baseText,
			"left_text":  leftText,
			"right_text": rightText,
			"strategy":   "lcs_diff3",
		},
	)
	env.EventID = requestID
	env.MessageID = messageID
	env.Routing = &events.Routing{Channel: "patch", PartitionKey: sessionID, Priority: events.PriorityHigh}

	if err := b.bus.PublishEnvelope(ctx, eventbus.PatchMergeRequestedSubject, env); err != nil {
		return nil, fmt.Errorf("publish patch merge: %w", err)
	}
	obs.IncEventPublished("realtime-orchestrator", string(eventbus.PatchMergeRequestedSubject))

	select {
	case result := <-ch:
		if result == nil {
			return nil, fmt.Errorf("patch merge: empty response")
		}
		return result, nil
	case <-ctx.Done():
		return nil, fmt.Errorf("patch merge: %w", ctx.Err())
	}
}

func (b *RustCoreBridge) handlePatchAudit(env events.Envelope) {
	obs.IncEventReceived("realtime-orchestrator", string(env.EventType))

	requestID, _ := env.Payload["request_id"].(string)
	if requestID == "" {
		return
	}

	b.mu.RLock()
	ch, ok := b.pendingPatch[requestID]
	b.mu.RUnlock()
	if !ok {
		return
	}

	result := &patchMergeResult{
		RequestID:     requestID,
		Strategy:      str(env.Payload, "strategy"),
		Success:       parseBool(env.Payload, "success"),
		ConflictScore: parseFloat(env.Payload, "conflict_score"),
		MergedText:    str(env.Payload, "merged_text"),
		ElapsedMs:     parseInt64(env.Payload, "elapsed_ms"),
	}
	select {
	case ch <- result:
	default:
	}
}

// ── Memory-Segment Core ─────────────────────────────────────────────────

// PublishMemoryCompact requests window compaction from memory-segment-core.
// When a session's message window exceeds the compaction threshold, the
// orchestrator calls this to compress older messages into a summary checkpoint.
//
// msgCount: number of messages in the segment to compact.
// cursorBefore / cursorAfter: Redis Stream cursor range to compact.
func (b *RustCoreBridge) PublishMemoryCompact(ctx context.Context, tenantID, sessionID, traceID, messageID string, msgCount int, cursorBefore, cursorAfter string) (*memoryCompactResult, error) {
	requestID := fmt.Sprintf("mem-%s-%d", sessionID, time.Now().UnixMilli())
	ch := make(chan *memoryCompactResult, 1)
	b.mu.Lock()
	b.pendingMemory[requestID] = ch
	b.mu.Unlock()

	defer func() {
		b.mu.Lock()
		delete(b.pendingMemory, requestID)
		b.mu.Unlock()
	}()

	env := events.NewEnvelope(
		events.EventSessionMessageReceived, // placeholder
		tenantID,
		sessionID,
		traceID,
		events.Producer{Service: "realtime-orchestrator", Instance: b.instance},
		map[string]any{
			"request_id":     requestID,
			"segment_id":     fmt.Sprintf("seg-%s-%d", sessionID, time.Now().UnixMilli()),
			"msg_count":      msgCount,
			"cursor_before":  cursorBefore,
			"cursor_after":   cursorAfter,
			"strategy":       "window_summary",
		},
	)
	env.EventID = requestID
	env.MessageID = messageID
	env.Routing = &events.Routing{Channel: "memory", PartitionKey: sessionID, Priority: events.PriorityLow}

	if err := b.bus.PublishEnvelope(ctx, eventbus.MemoryCompactRequestedSubject, env); err != nil {
		return nil, fmt.Errorf("publish memory compact: %w", err)
	}
	obs.IncEventPublished("realtime-orchestrator", string(eventbus.MemoryCompactRequestedSubject))

	select {
	case result := <-ch:
		if result == nil {
			return nil, fmt.Errorf("memory compact: empty response")
		}
		return result, nil
	case <-ctx.Done():
		return nil, fmt.Errorf("memory compact: %w", ctx.Err())
	}
}

func (b *RustCoreBridge) handleMemoryAudit(env events.Envelope) {
	obs.IncEventReceived("realtime-orchestrator", string(env.EventType))

	requestID, _ := env.Payload["request_id"].(string)
	if requestID == "" {
		return
	}

	b.mu.RLock()
	ch, ok := b.pendingMemory[requestID]
	b.mu.RUnlock()
	if !ok {
		return
	}

	result := &memoryCompactResult{
		RequestID:        requestID,
		SegmentID:        str(env.Payload, "segment_id"),
		Strategy:         str(env.Payload, "strategy"),
		OriginalMsgCount: int(parseInt64(env.Payload, "original_msg_count")),
		CompactedCount:   int(parseInt64(env.Payload, "compacted_count")),
		SummaryText:      str(env.Payload, "summary_text"),
		CursorAfter:      str(env.Payload, "cursor_after"),
		ElapsedMs:        parseInt64(env.Payload, "elapsed_ms"),
	}
	select {
	case ch <- result:
	default:
	}
}

// ── Lifecycle ───────────────────────────────────────────────────────────

// PendingCounts returns the number of in-flight request-response correlations.
// Useful for /resilience/state endpoint monitoring.
func (b *RustCoreBridge) PendingCounts() map[string]int {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return map[string]int{
		"retrieval_pending": len(b.pendingRetrieval),
		"patch_pending":     len(b.pendingPatch),
		"memory_pending":    len(b.pendingMemory),
	}
}

// ── Payload parsers ─────────────────────────────────────────────────────

func parseFusionResult(payload map[string]any) *fusionResult {
	if payload == nil {
		return nil
	}
	r := &fusionResult{
		RequestID: str(payload, "request_id"),
		Strategy:  str(payload, "strategy"),
	}
	if v, ok := payload["top_k"].(float64); ok {
		r.TopK = int(v)
	}
	if v, ok := payload["qdrant_hits"].(float64); ok {
		r.QdrantHits = int(v)
	}
	if v, ok := payload["opensearch_hits"].(float64); ok {
		r.OpenSearchHits = int(v)
	}
	if v, ok := payload["elapsed_ms"].(float64); ok {
		r.ElapsedMs = int64(v)
	}
	// Parse citations array.
	if raw, ok := payload["citations"]; ok {
		if arr, ok := raw.([]any); ok {
			for _, item := range arr {
				if m, ok := item.(map[string]any); ok {
					c := CitationDTO{
						SourceID:   str(m, "source_id"),
						Collection: str(m, "collection"),
						Snippet:    str(m, "snippet"),
					}
					if v, ok := m["score"].(float64); ok {
						c.Score = float32(v)
					}
					r.Citations = append(r.Citations, c)
				}
			}
		}
	}
	// Parse candidates array.
	if raw, ok := payload["candidates"]; ok {
		if arr, ok := raw.([]any); ok {
			r.Candidates = make([]map[string]any, 0, len(arr))
			for _, item := range arr {
				if m, ok := item.(map[string]any); ok {
					r.Candidates = append(r.Candidates, m)
				}
			}
		}
	}
	// Parse degraded array.
	if raw, ok := payload["degraded"]; ok {
		if arr, ok := raw.([]any); ok {
			for _, item := range arr {
				if s, ok := item.(string); ok {
					r.Degraded = append(r.Degraded, s)
				}
			}
		}
	}
	return r
}

func parseBool(payload map[string]any, key string) bool {
	if v, ok := payload[key]; ok {
		if b, ok := v.(bool); ok {
			return b
		}
	}
	return false
}

func parseFloat(payload map[string]any, key string) float64 {
	if v, ok := payload[key]; ok {
		if f, ok := v.(float64); ok {
			return f
		}
	}
	return 0
}

func parseInt64(payload map[string]any, key string) int64 {
	if v, ok := payload[key]; ok {
		if f, ok := v.(float64); ok {
			return int64(f)
		}
	}
	return 0
}

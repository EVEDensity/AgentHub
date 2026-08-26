package eventbus

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"math"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/agenthub/platform/shared/events"
	"github.com/nats-io/nats.go"
)

// Subjects. Stream chunks live under "agenthub.session.>" so they are
// captured by the SESSION JetStream alongside session lifecycle events.
//
// Sprint D: 新增 fanout / patch / memory 三类 subject，打通 Go↔Rust NATS 集成。
// Go→Rust 方向走 JetStream（自动 fanout 到 Rust core NATS subscriber），
// Rust→Go 方向走 core NATS subscribe（Rust 用 async_nats publish）。
const (
	SessionEventsSubject          = "agenthub.session.events"
	StreamEventsSubject           = "agenthub.session.stream.events"
	AgentRuntimeDispatchSubject   = "agenthub.agent.runtime.dispatch"
	AgentRuntimeResultsSubject    = "agenthub.agent.runtime.results"
	ToolPermissionRequestsSubject = "agenthub.tool.permission.requests"
	ToolPermissionResolvedSubject = "agenthub.tool.permission.resolved"
	RetrievalQuerySubject         = "agenthub.retrieval.query"
	RetrievalFusionSubject        = "agenthub.retrieval.fusion"
	AuditSecurityEventsSubject    = "agenthub.audit.security.events"

	// Sprint D: Rust core integration subjects (Go → Rust via JetStream).
	FanoutEventsSubject           = "agenthub.fanout.events"
	PatchMergeRequestedSubject    = "agenthub.patch.merge.requested"
	MemoryCompactRequestedSubject = "agenthub.memory.compact.requested"

	// Sprint D: Rust core audit subjects (Rust → Go via core NATS).
	FanoutAuditSubject = "agenthub.fanout.audit"
	PatchAuditSubject  = "agenthub.patch.audit"
	MemoryAuditSubject = "agenthub.memory.audit"

	// Sprint H: ContextOS — unified context engine subjects.
	ContextSearchSubject   = "agenthub.context.search"
	ContextSegmentSubject  = "agenthub.context.segment"
	ContextCompressSubject = "agenthub.context.compress"
	ContextDecisionSubject = "agenthub.context.decision"

	// Sprint I: AgentNet — decentralized multi-agent collaboration subjects.
	AgentNetCapabilitiesSubject = "agenthub.agentnet.capabilities"
	AgentNetTasksSubject        = "agenthub.agentnet.tasks"
	AgentNetResultsSubject      = "agenthub.agentnet.results"
	AgentNetSpawnSubject        = "agenthub.agentnet.spawn"
	AgentNetMemorySubject       = "agenthub.agentnet.memory"

	// Sprint J: Digital Identity + Sandbox subjects.
	AgentIdentitySubject  = "agenthub.agent.identity"
	SandboxControlSubject = "agenthub.sandbox.control"
	SandboxExecSubject    = "agenthub.sandbox.exec"
	WorkspaceFileSubject  = "agenthub.workspace.file"
)

// streamDefs declares the JetStream streams mandated by platform/data_plane.json.
// EnsureStreams creates/updates them idempotently.
//
// Sprint D: 新增 FANOUT / PATCH / MEMORY 三条 stream，覆盖 Rust core 的 subject 空间。
// Rust 通过 core NATS publish，JetStream stream 捕获后持久化，Go 通过 durable consumer 消费。
var streamDefs = []nats.StreamConfig{
	{Name: "SESSION", Subjects: []string{"agenthub.session.>"}, Retention: nats.LimitsPolicy, MaxAge: 72 * time.Hour, Storage: nats.FileStorage, Replicas: 1},
	{Name: "AGENT-RUNTIME", Subjects: []string{"agenthub.agent.runtime.>"}, Retention: nats.LimitsPolicy, MaxAge: 24 * time.Hour, Storage: nats.FileStorage, Replicas: 1},
	{Name: "RETRIEVAL", Subjects: []string{"agenthub.retrieval.>"}, Retention: nats.LimitsPolicy, MaxAge: 24 * time.Hour, Storage: nats.FileStorage, Replicas: 1},
	{Name: "TOOL-PERMISSIONS", Subjects: []string{"agenthub.tool.permission.>"}, Retention: nats.LimitsPolicy, MaxAge: 7 * 24 * time.Hour, Storage: nats.FileStorage, Replicas: 1},
	{Name: "AUDIT", Subjects: []string{"agenthub.audit.>"}, Retention: nats.LimitsPolicy, MaxAge: 90 * 24 * time.Hour, Storage: nats.FileStorage, Replicas: 1},
	{Name: "FANOUT", Subjects: []string{"agenthub.fanout.>"}, Retention: nats.LimitsPolicy, MaxAge: 24 * time.Hour, Storage: nats.FileStorage, Replicas: 1},
	{Name: "PATCH", Subjects: []string{"agenthub.patch.>"}, Retention: nats.LimitsPolicy, MaxAge: 7 * 24 * time.Hour, Storage: nats.FileStorage, Replicas: 1},
	{Name: "MEMORY", Subjects: []string{"agenthub.memory.>"}, Retention: nats.LimitsPolicy, MaxAge: 24 * time.Hour, Storage: nats.FileStorage, Replicas: 1},
	{Name: "CONTEXT", Subjects: []string{"agenthub.context.>"}, Retention: nats.LimitsPolicy, MaxAge: 7 * 24 * time.Hour, Storage: nats.FileStorage, Replicas: 1},
	{Name: "AGENTNET", Subjects: []string{"agenthub.agentnet.>"}, Retention: nats.LimitsPolicy, MaxAge: 72 * time.Hour, Storage: nats.FileStorage, Replicas: 1},
	{Name: "SANDBOX", Subjects: []string{"agenthub.sandbox.>"}, Retention: nats.LimitsPolicy, MaxAge: 24 * time.Hour, Storage: nats.FileStorage, Replicas: 1},
	{Name: "WORKSPACE", Subjects: []string{"agenthub.workspace.>"}, Retention: nats.LimitsPolicy, MaxAge: 72 * time.Hour, Storage: nats.FileStorage, Replicas: 1},
}

// Client wraps a NATS connection with a JetStream context. All publishes go
// through JetStream so messages are durable; subscribers use durable consumers
// so they resume from the last acked message after a restart.
type Client struct {
	conn  *nats.Conn
	js    nats.JetStreamContext
	local *localBus
}

type localBus struct {
	mu   sync.RWMutex
	subs map[string][]func(events.Envelope)
}

// Bus is the transport contract shared by server (NATS) and desktop (memory)
// profiles. Client remains the compatibility implementation for existing
// handlers while new local code can depend on this narrow contract.
type Bus interface {
	IsConnected() bool
	PublishEnvelope(context.Context, string, events.Envelope) error
	Subscribe(string, string, func(events.Envelope)) (*nats.Subscription, error)
	QueueSubscribe(string, string, string, func(events.Envelope)) (*nats.Subscription, error)
	SubscribeCore(string, func(events.Envelope)) (*nats.Subscription, error)
	Close()
}

var _ Bus = (*Client)(nil)

// ConnectLocal creates the single-process backend used by the desktop profile.
// It deliberately provides ephemeral delivery; server deployments must use NATS.
func ConnectLocal() *Client {
	return &Client{local: &localBus{subs: make(map[string][]func(events.Envelope))}}
}

func (c *Client) IsConnected() bool {
	return c != nil && (c.local != nil || (c.conn != nil && c.conn.IsConnected()))
}

// Connect dials NATS, opens a JetStream context, and ensures all platform
// streams exist. It fails fast if JetStream is unavailable.
func Connect(url string) (*Client, error) {
	conn, err := nats.Connect(
		url,
		nats.Name("agenthub-platform"),
		nats.MaxReconnects(-1),
		nats.ReconnectWait(2*time.Second),
	)
	if err != nil {
		return nil, fmt.Errorf("connect nats: %w", err)
	}
	js, err := conn.JetStream(nats.MaxWait(5 * time.Second))
	if err != nil {
		conn.Close()
		return nil, fmt.Errorf("jetstream context: %w", err)
	}
	c := &Client{conn: conn, js: js}
	if err := c.ensureStreams(); err != nil {
		conn.Close()
		return nil, fmt.Errorf("ensure streams: %w", err)
	}
	return c, nil
}

func (c *Client) ensureStreams() error {
	for i := range streamDefs {
		def := streamDefs[i]
		if _, err := c.js.StreamInfo(def.Name); err != nil {
			if _, err := c.js.AddStream(&def); err != nil {
				return fmt.Errorf("add stream %s: %w", def.Name, err)
			}
			log.Printf("eventbus: created jetstream stream %s (%v)", def.Name, def.Subjects)
			continue
		}
		if _, err := c.js.UpdateStream(&def); err != nil {
			return fmt.Errorf("update stream %s: %w", def.Name, err)
		}
	}
	return nil
}

// Conn returns the underlying NATS connection. Callers that need core NATS
// subscriptions (e.g. for Rust→Go messages published via async_nats) can use
// this instead of the JetStream context.
func (c *Client) Conn() *nats.Conn { return c.conn }

func (c *Client) Close() {
	if c.local != nil {
		return
	}
	if c != nil && c.conn != nil {
		c.conn.Close()
	}
}

// PublishEnvelope marshals and publishes an envelope to the given subject via
// JetStream. The publish is acknowledged synchronously (stored on disk). The
// caller's context bounds how long we wait for the ack; the JetStream context
// itself is configured with a 5s default timeout.
func (c *Client) PublishEnvelope(ctx context.Context, subject string, event events.Envelope) error {
	if c.local != nil {
		c.local.mu.RLock()
		handlers := append([]func(events.Envelope){}, c.local.subs[subject]...)
		for pattern, subscribers := range c.local.subs {
			if strings.HasSuffix(pattern, ">") && strings.HasPrefix(subject, strings.TrimSuffix(pattern, ">")) {
				handlers = append(handlers, subscribers...)
			}
		}
		c.local.mu.RUnlock()
		for _, handler := range handlers {
			handler(event)
		}
		return nil
	}
	payload, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("marshal event envelope: %w", err)
	}
	type publishResult struct {
		ack *nats.PubAck
		err error
	}
	done := make(chan publishResult, 1)
	go func() {
		ack, err := c.js.Publish(subject, payload)
		done <- publishResult{ack: ack, err: err}
	}()
	select {
	case r := <-done:
		if r.err != nil {
			return fmt.Errorf("publish event envelope: %w", r.err)
		}
		return nil
	case <-ctx.Done():
		return fmt.Errorf("publish event envelope: %w", ctx.Err())
	}
}

// Subscribe creates a durable broadcast consumer: every message on the subject
// is delivered to this consumer. Use a unique durable name per replica for
// fanout consumers (e.g. stream-delivery, audit-log). The consumer auto-acks
// after the handler returns; a panic is treated as a nak and redelivered.
func (c *Client) Subscribe(durable, subject string, handler func(events.Envelope)) (*nats.Subscription, error) {
	if c.local != nil {
		c.local.mu.Lock()
		c.local.subs[subject] = append(c.local.subs[subject], handler)
		c.local.mu.Unlock()
		return nil, nil
	}
	return c.js.Subscribe(subject, dispatch(handler),
		nats.Durable(durable),
		nats.DeliverAll(),
		nats.AckWait(30*time.Second),
		nats.MaxDeliver(5),
	)
}

// QueueSubscribe creates a durable competing consumer: each message is
// delivered to exactly one member of the queue group. Use this for work-queue
// subjects (orchestrator, tool-permission, agent-runtime dispatch) so multiple
// replicas share load.
func (c *Client) QueueSubscribe(durable, queue, subject string, handler func(events.Envelope)) (*nats.Subscription, error) {
	if c.local != nil {
		return c.Subscribe(durable, subject, handler)
	}
	return c.js.QueueSubscribe(subject, queue, dispatch(handler),
		nats.Durable(durable),
		nats.DeliverAll(),
		nats.AckWait(30*time.Second),
		nats.MaxDeliver(5),
	)
}

// SubscribeCore creates a core NATS subscription (non-JetStream). Use this for
// subjects where the publisher uses core NATS (e.g. Rust async_nats). Messages
// are ephemeral — no persistence, no replay, no ack. For durable delivery,
// ensure a JetStream stream covers the subject and use Subscribe instead.
func (c *Client) SubscribeCore(subject string, handler func(events.Envelope)) (*nats.Subscription, error) {
	if c.local != nil {
		return c.Subscribe("core", subject, handler)
	}
	return c.conn.Subscribe(subject, func(msg *nats.Msg) {
		var env events.Envelope
		if err := json.Unmarshal(msg.Data, &env); err != nil {
			log.Printf("eventbus: core sub unmarshal failed: %v", err)
			return
		}
		defer func() {
			if r := recover(); r != nil {
				log.Printf("eventbus: core sub handler panic recovered: %v", r)
			}
		}()
		handler(env)
	})
}

func dispatch(handler func(events.Envelope)) func(*nats.Msg) {
	return func(msg *nats.Msg) {
		var env events.Envelope
		if err := json.Unmarshal(msg.Data, &env); err != nil {
			log.Printf("eventbus: unmarshal envelope failed: %v", err)
			_ = msg.Nak()
			return
		}
		// Auto-ack on successful return; the JetStream client will nak on panic.
		defer func() {
			if r := recover(); r != nil {
				log.Printf("eventbus: handler panic recovered: %v", r)
				_ = msg.Nak()
			}
		}()
		handler(env)
		_ = msg.Ack()
	}
}

// ── Sprint M2: Enhanced Reliability ───────────────────────────────────────
//
// Retry with exponential backoff, idempotent publishing, dead-letter queue,
// and slow consumer detection.

// PublishWithRetry publishes an envelope with exponential backoff retry.
// Retries up to maxRetries times with backoff starting at baseDelay.
// Returns the last error if all retries are exhausted.
func (c *Client) PublishWithRetry(ctx context.Context, subject string, event events.Envelope, maxRetries int, baseDelay time.Duration) error {
	if maxRetries <= 0 {
		maxRetries = 3
	}
	if baseDelay <= 0 {
		baseDelay = 100 * time.Millisecond
	}

	var lastErr error
	for attempt := 0; attempt <= maxRetries; attempt++ {
		if attempt > 0 {
			// Exponential backoff: baseDelay * 2^(attempt-1), capped at 30s
			backoff := time.Duration(float64(baseDelay) * math.Pow(2, float64(attempt-1)))
			if backoff > 30*time.Second {
				backoff = 30 * time.Second
			}
			log.Printf("eventbus: retry attempt %d/%d for subject=%s (backoff=%v)",
				attempt, maxRetries, subject, backoff)
			select {
			case <-time.After(backoff):
			case <-ctx.Done():
				return fmt.Errorf("publish retry cancelled: %w", ctx.Err())
			}
		}

		err := c.PublishEnvelope(ctx, subject, event)
		if err == nil {
			if attempt > 0 {
				log.Printf("eventbus: retry successful on attempt %d for subject=%s", attempt, subject)
			}
			return nil
		}
		lastErr = err
		log.Printf("eventbus: publish attempt %d failed for subject=%s: %v", attempt, subject, err)
	}

	return fmt.Errorf("publish exhausted %d retries for subject=%s: %w", maxRetries, subject, lastErr)
}

// PublishIdempotent publishes with an idempotency key to prevent duplicates.
// The idempotency key is derived from the event's EventID + a dedup window.
// Uses the Nats-Msg-Id header for JetStream deduplication.
func (c *Client) PublishIdempotent(ctx context.Context, subject string, event events.Envelope, dedupWindow time.Duration) error {
	payload, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("marshal event envelope: %w", err)
	}

	msg := nats.NewMsg(subject)
	msg.Data = payload
	msg.Header.Set("Nats-Msg-Id", event.EventID)
	msg.Header.Set("Nats-Expected-Stream", subjectToStream(subject))

	type result struct {
		ack *nats.PubAck
		err error
	}
	done := make(chan result, 1)
	go func() {
		ack, err := c.js.PublishMsg(msg)
		done <- result{ack: ack, err: err}
	}()

	select {
	case r := <-done:
		if r.err != nil {
			return fmt.Errorf("idempotent publish: %w", r.err)
		}
		return nil
	case <-ctx.Done():
		return fmt.Errorf("idempotent publish cancelled: %w", ctx.Err())
	}
}

// subjectToStream maps a subject to its parent stream name for dedup headers.
func subjectToStream(subject string) string {
	switch {
	case containsSub(subject, "agenthub.session."):
		return "SESSION"
	case containsSub(subject, "agenthub.agent.runtime."):
		return "AGENT-RUNTIME"
	case containsSub(subject, "agenthub.retrieval."):
		return "RETRIEVAL"
	case containsSub(subject, "agenthub.tool.permission."):
		return "TOOL-PERMISSIONS"
	case containsSub(subject, "agenthub.audit."):
		return "AUDIT"
	case containsSub(subject, "agenthub.fanout."):
		return "FANOUT"
	case containsSub(subject, "agenthub.patch."):
		return "PATCH"
	case containsSub(subject, "agenthub.memory."):
		return "MEMORY"
	case containsSub(subject, "agenthub.context."):
		return "CONTEXT"
	case containsSub(subject, "agenthub.agentnet."):
		return "AGENTNET"
	case containsSub(subject, "agenthub.sandbox."):
		return "SANDBOX"
	case containsSub(subject, "agenthub.workspace."):
		return "WORKSPACE"
	}
	return ""
}

func containsSub(s, substr string) bool {
	return len(s) >= len(substr) && s[:len(substr)] == substr
}

// SubscribeWithDLQ creates a durable consumer with dead-letter forwarding.
// After maxRedeliveries failed attempts, the message is published to the DLQ subject
// before being acknowledged (removed from the original stream).
func (c *Client) SubscribeWithDLQ(durable, subject, dlqSubject string, maxRedeliveries int, handler func(events.Envelope) error) (*nats.Subscription, error) {
	if maxRedeliveries <= 0 {
		maxRedeliveries = 3
	}

	return c.js.Subscribe(subject, func(msg *nats.Msg) {
		var env events.Envelope
		if err := json.Unmarshal(msg.Data, &env); err != nil {
			log.Printf("eventbus: DLQ sub unmarshal failed: %v", err)
			_ = msg.Nak()
			return
		}

		metadata, _ := msg.Metadata()
		deliveryCount := 1
		if metadata != nil {
			deliveryCount = int(metadata.NumDelivered)
		}

		defer func() {
			if r := recover(); r != nil {
				log.Printf("eventbus: DLQ handler panic: %v", r)
				c.handleDLQ(msg, env, dlqSubject, deliveryCount, maxRedeliveries)
			}
		}()

		if err := handler(env); err != nil {
			log.Printf("eventbus: handler error (delivery %d/%d): %v", deliveryCount, maxRedeliveries, err)
			if deliveryCount >= maxRedeliveries {
				c.handleDLQ(msg, env, dlqSubject, deliveryCount, maxRedeliveries)
				return
			}
			_ = msg.Nak()
			return
		}
		_ = msg.Ack()
	},
		nats.Durable(durable),
		nats.DeliverAll(),
		nats.AckWait(30*time.Second),
		nats.MaxDeliver(maxRedeliveries+1),
	)
}

func (c *Client) handleDLQ(msg *nats.Msg, env events.Envelope, dlqSubject string, deliveryCount, maxRedeliveries int) {
	dlqPayload := map[string]interface{}{
		"original_subject": msg.Subject,
		"event_id":         env.EventID,
		"event_type":       string(env.EventType),
		"delivery_count":   deliveryCount,
		"max_redeliveries": maxRedeliveries,
		"dead_at":          time.Now().UTC().Format(time.RFC3339),
		"envelope":         env,
	}
	dlqData, _ := json.Marshal(dlqPayload)
	// Use core NATS publish for DLQ (best-effort, don't want DLQ messages to loop)
	_ = c.conn.Publish(dlqSubject, dlqData)
	log.Printf("eventbus: message sent to DLQ subject=%s event_id=%s deliveries=%d",
		dlqSubject, env.EventID, deliveryCount)
	// Ack the original so it's removed from the stream (DLQ has a copy)
	_ = msg.Ack()
}

// ── Slow Consumer Detection ───────────────────────────────────────────────
//
// Tracks per-consumer message rates and warns when processing falls behind.

// SlowConsumerTracker monitors consumer processing rates.
type SlowConsumerTracker struct {
	consumerName string
	received     atomic.Int64
	processed    atomic.Int64
	lastReport   atomic.Int64
	warnBacklog  int64 // warn when unprocessed > this many
}

// NewSlowConsumerTracker creates a tracker. warnBacklog triggers a warning
// when unprocessed messages exceed this count (default 100).
func NewSlowConsumerTracker(name string, warnBacklog int64) *SlowConsumerTracker {
	if warnBacklog <= 0 {
		warnBacklog = 100
	}
	return &SlowConsumerTracker{consumerName: name, warnBacklog: warnBacklog}
}

// OnReceived increments the received counter. Call when a message arrives.
func (t *SlowConsumerTracker) OnReceived() {
	t.received.Add(1)
	t.maybeReport()
}

// OnProcessed increments the processed counter. Call when a message is acked.
func (t *SlowConsumerTracker) OnProcessed() {
	t.processed.Add(1)
}

// Backlog returns the current unprocessed count.
func (t *SlowConsumerTracker) Backlog() int64 {
	return t.received.Load() - t.processed.Load()
}

// maybeReport logs a warning if the backlog exceeds the threshold.
func (t *SlowConsumerTracker) maybeReport() {
	backlog := t.Backlog()
	if backlog > t.warnBacklog {
		now := time.Now().Unix()
		last := t.lastReport.Swap(now)
		// Throttle warnings to once per 30 seconds
		if now-last > 30 {
			log.Printf("eventbus: SLOW CONSUMER %s — backlog=%d (received=%d processed=%d)",
				t.consumerName, backlog, t.received.Load(), t.processed.Load())
		}
	}
}

// WrapHandler wraps a handler with slow consumer tracking.
func (t *SlowConsumerTracker) WrapHandler(handler func(events.Envelope)) func(events.Envelope) {
	return func(env events.Envelope) {
		t.OnReceived()
		handler(env)
		t.OnProcessed()
	}
}

// WrapHandlerErr wraps an error-returning handler with slow consumer tracking.
func (t *SlowConsumerTracker) WrapHandlerErr(handler func(events.Envelope) error) func(events.Envelope) error {
	return func(env events.Envelope) error {
		t.OnReceived()
		err := handler(env)
		t.OnProcessed()
		return err
	}
}

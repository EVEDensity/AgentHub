package eventbus

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
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
	FanoutAuditSubject   = "agenthub.fanout.audit"
	PatchAuditSubject    = "agenthub.patch.audit"
	MemoryAuditSubject   = "agenthub.memory.audit"

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
	AgentIdentitySubject = "agenthub.agent.identity"
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
	conn *nats.Conn
	js   nats.JetStreamContext
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
	if c != nil && c.conn != nil {
		c.conn.Close()
	}
}

// PublishEnvelope marshals and publishes an envelope to the given subject via
// JetStream. The publish is acknowledged synchronously (stored on disk). The
// caller's context bounds how long we wait for the ack; the JetStream context
// itself is configured with a 5s default timeout.
func (c *Client) PublishEnvelope(ctx context.Context, subject string, event events.Envelope) error {
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

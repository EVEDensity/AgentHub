package eventbus

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/agenthub/platform/shared/events"
	"github.com/nats-io/nats.go"
)

const (
	SessionEventsSubject          = "agenthub.session.events"
	StreamEventsSubject           = "agenthub.stream.events"
	AgentRuntimeDispatchSubject   = "agenthub.agent.runtime.dispatch"
	AgentRuntimeResultsSubject    = "agenthub.agent.runtime.results"
	ToolPermissionRequestsSubject = "agenthub.tool.permission.requests"
	ToolPermissionResolvedSubject = "agenthub.tool.permission.resolved"
	AuditSecurityEventsSubject    = "agenthub.audit.security.events"
)

type Client struct {
	conn *nats.Conn
}

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
	return &Client{conn: conn}, nil
}

func (c *Client) Close() {
	if c != nil && c.conn != nil {
		c.conn.Close()
	}
}

func (c *Client) PublishEnvelope(ctx context.Context, subject string, event events.Envelope) error {
	payload, err := json.Marshal(event)
	if err != nil {
		return fmt.Errorf("marshal event envelope: %w", err)
	}
	if err := c.conn.Publish(subject, payload); err != nil {
		return fmt.Errorf("publish event envelope: %w", err)
	}
	// nats.Conn 没有 SetWriteDeadline 方法，用 FlushTimeout 控制写入超时
	timeout := 5 * time.Second
	if deadline, ok := ctx.Deadline(); ok {
		if remaining := time.Until(deadline); remaining > 0 {
			timeout = remaining
		}
	}
	return c.conn.FlushTimeout(timeout)
}

func (c *Client) Subscribe(subject string, handler func(events.Envelope)) (*nats.Subscription, error) {
	return c.conn.Subscribe(subject, func(msg *nats.Msg) {
		var env events.Envelope
		if err := json.Unmarshal(msg.Data, &env); err != nil {
			return
		}
		handler(env)
	})
}

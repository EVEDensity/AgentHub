package state

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

// StreamMaxLen is the approximate cap on each session stream. Redis trims older
// entries when the stream grows beyond this, bounding memory per session while
// keeping enough history for short reconnect replay windows.
const StreamMaxLen = 1000

// StreamKey returns the Redis key for a session's event stream.
func StreamKey(tenantID, sessionID string) string {
	return fmt.Sprintf("stream:%s:%s", tenantID, sessionID)
}

// StreamAdd appends a JSON-serializable event to the session stream and returns
// the Redis stream ID (the durable cursor clients use for replay). The stream
// is trimmed to approximately StreamMaxLen entries.
func (s *Store) StreamAdd(ctx context.Context, tenantID, sessionID string, event any) (string, error) {
	payload, err := json.Marshal(event)
	if err != nil {
		return "", fmt.Errorf("marshal event: %w", err)
	}
	key := StreamKey(tenantID, sessionID)
	id, err := s.client.XAdd(ctx, &redis.XAddArgs{
		Stream: key,
		MaxLen: StreamMaxLen,
		Approx: true,
		Values: map[string]any{"data": string(payload)},
	}).Result()
	if err != nil {
		return "", fmt.Errorf("xadd: %w", err)
	}
	// Keep the stream alive for 24h after last write.
	_ = s.client.Expire(ctx, key, 24*time.Hour).Err()
	return id, nil
}

// StreamEntry is a single replayed event with its cursor ID.
type StreamEntry struct {
	ID   string          `json:"id"`
	Data json.RawMessage `json:"data"`
}

// StreamRange returns events from the session stream after the given cursor
// (exclusive). An empty cursor reads from the start. At most count entries are
// returned (use 0 or negative for "no limit").
func (s *Store) StreamRange(ctx context.Context, tenantID, sessionID, afterCursor string, count int64) ([]StreamEntry, error) {
	key := StreamKey(tenantID, sessionID)
	start := "-"
	if afterCursor != "" {
		start = "(" + afterCursor // exclusive bound (Redis XRANGE syntax)
	}
	var msgs []redis.XMessage
	var err error
	if count > 0 {
		msgs, err = s.client.XRangeN(ctx, key, start, "+", count).Result()
	} else {
		msgs, err = s.client.XRange(ctx, key, start, "+").Result()
	}
	if err != nil {
		return nil, fmt.Errorf("xrange: %w", err)
	}
	out := make([]StreamEntry, 0, len(msgs))
	for _, m := range msgs {
		raw, ok := m.Values["data"].(string)
		if !ok {
			continue
		}
		out = append(out, StreamEntry{ID: m.ID, Data: json.RawMessage(raw)})
	}
	return out, nil
}

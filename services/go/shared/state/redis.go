package state

import (
	"context"
	"fmt"
	"time"

	"github.com/redis/go-redis/v9"
)

type Store struct {
	client *redis.Client
}

func Connect(addr string) *Store {
	return &Store{client: redis.NewClient(&redis.Options{Addr: addr})}
}

func (s *Store) Close() error {
	if s == nil || s.client == nil {
		return nil
	}
	return s.client.Close()
}

func PresenceKey(tenantID, sessionID string) string {
	return fmt.Sprintf("presence:%s:%s", tenantID, sessionID)
}

func CursorKey(tenantID, sessionID, connectionID string) string {
	return fmt.Sprintf("cursor:%s:%s:%s", tenantID, sessionID, connectionID)
}

func PermissionKey(tenantID, requestID string) string {
	return fmt.Sprintf("perm:%s:%s", tenantID, requestID)
}

func (s *Store) PutJSON(ctx context.Context, key string, value string, ttl time.Duration) error {
	return s.client.Set(ctx, key, value, ttl).Err()
}

func (s *Store) GetString(ctx context.Context, key string) (string, error) {
	return s.client.Get(ctx, key).Result()
}

func (s *Store) HSet(ctx context.Context, key string, values ...any) error {
	return s.client.HSet(ctx, key, values...).Err()
}

func (s *Store) HGetAll(ctx context.Context, key string) (map[string]string, error) {
	return s.client.HGetAll(ctx, key).Result()
}

func (s *Store) Expire(ctx context.Context, key string, ttl time.Duration) error {
	return s.client.Expire(ctx, key, ttl).Err()
}

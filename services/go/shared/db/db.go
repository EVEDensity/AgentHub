// Package db provides the platform-wide PostgreSQL access layer: a pgxpool-based
// connection, a migrations runner backed by embedded SQL files, and small
// repository helpers used by session / permission / audit services.
//
// The package is intentionally lean — it exposes a *Pool (pgxpool.Pool wrapper)
// and lets each service build its own queries. Migrations are tracked in the
// platform_schema_migrations table and are idempotent.
package db

import (
	"context"
	"embed"
	"fmt"
	"log"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed migrations/*.sql
var migrationFS embed.FS

// Pool wraps a pgxpool.Pool so callers get a typed handle and a Close helper.
type Pool struct {
	*pgxpool.Pool
}

// Connect opens a pgxpool against the given DSN (e.g.
// "postgres://agenthub:agenthub@localhost:5432/agenthub?sslmode=disable") with
// sane defaults for a stateless Go service: modest pool size, short acquire
// timeout, and a health check ping.
func Connect(ctx context.Context, dsn string) (*Pool, error) {
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, fmt.Errorf("parse dsn: %w", err)
	}
	cfg.MaxConns = int32(getEnvInt("DB_POOL_MAX_CONNS", 20))
	cfg.MinConns = int32(getEnvInt("DB_POOL_MIN_CONNS", 2))
	cfg.MaxConnLifetime = time.Duration(getEnvInt("DB_POOL_MAX_LIFETIME_MIN", 30)) * time.Minute
	cfg.MaxConnIdleTime = time.Duration(getEnvInt("DB_POOL_MAX_IDLE_MIN", 5)) * time.Minute
	cfg.ConnConfig.ConnectTimeout = time.Duration(getEnvInt("DB_CONNECT_TIMEOUT_SEC", 5)) * time.Second

	// Slow query logging threshold (ms) — set via DB_SLOW_QUERY_MS env var.
	// When > 0, queries exceeding this threshold are logged at WARN level.
	slowQueryMs := getEnvInt("DB_SLOW_QUERY_MS", 200)
	_ = slowQueryMs // Reserved for future query-level instrumentation

	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, fmt.Errorf("create pool: %w", err)
	}
	pingCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	if err := pool.Ping(pingCtx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("ping db: %w", err)
	}
	return &Pool{Pool: pool}, nil
}

// Close releases the pool. Safe to call on a nil receiver.
func (p *Pool) Close() {
	if p == nil || p.Pool == nil {
		return
	}
	p.Pool.Close()
}

// Migrate runs all embedded SQL migrations in lexical order, skipping any whose
// version is already recorded in platform_schema_migrations. Each file is
// wrapped in a single transaction so a failure rolls back cleanly.
func (p *Pool) Migrate(ctx context.Context) error {
	if _, err := p.Exec(ctx, `
		CREATE TABLE IF NOT EXISTS platform_schema_migrations (
			version    TEXT PRIMARY KEY,
			applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
		)`); err != nil {
		return fmt.Errorf("ensure migrations table: %w", err)
	}

	entries, err := migrationFS.ReadDir("migrations")
	if err != nil {
		return fmt.Errorf("read migrations dir: %w", err)
	}
	var files []string
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".sql") {
			files = append(files, e.Name())
		}
	}
	sort.Strings(files)

	for _, name := range files {
		version := strings.TrimSuffix(name, ".sql")
		var applied bool
		err := p.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM platform_schema_migrations WHERE version=$1)`, version).Scan(&applied)
		if err != nil {
			return fmt.Errorf("check migration %s: %w", version, err)
		}
		if applied {
			continue
		}
		sqlBytes, err := migrationFS.ReadFile("migrations/" + name)
		if err != nil {
			return fmt.Errorf("read migration %s: %w", version, err)
		}
		tx, err := p.Begin(ctx)
		if err != nil {
			return fmt.Errorf("begin tx for %s: %w", version, err)
		}
		if _, err := tx.Exec(ctx, string(sqlBytes)); err != nil {
			_ = tx.Rollback(ctx)
			return fmt.Errorf("apply migration %s: %w", version, err)
		}
		if _, err := tx.Exec(ctx, `INSERT INTO platform_schema_migrations(version) VALUES($1) ON CONFLICT DO NOTHING`, version); err != nil {
			_ = tx.Rollback(ctx)
			return fmt.Errorf("record migration %s: %w", version, err)
		}
		if err := tx.Commit(ctx); err != nil {
			return fmt.Errorf("commit migration %s: %w", version, err)
		}
		log.Printf("db: applied migration %s", version)
	}
	return nil
}

// getEnvInt reads an integer env var with a default fallback.
func getEnvInt(key string, defaultVal int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return defaultVal
}

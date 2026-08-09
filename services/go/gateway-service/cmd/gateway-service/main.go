package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/agenthub/platform/shared/db"
	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
	"github.com/agenthub/platform/shared/iam"
	"github.com/agenthub/platform/shared/obs"
	"github.com/agenthub/platform/shared/state"
	"github.com/prometheus/client_golang/prometheus"
)

// authDenied counts requests rejected by the IAM auth middleware, so operators
// can spot misconfigured clients or probing attempts in Grafana.
var authDenied = prometheus.NewCounterVec(
	prometheus.CounterOpts{Name: "gateway_auth_denied_total", Help: "Requests rejected by IAM auth middleware."},
	[]string{"reason"},
)

// Sprint K1: Additional gateway metrics for observability completeness.
var (
	// rateLimitHits counts requests rejected at each rate-limiting layer.
	rateLimitHits = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "gateway_rate_limit_hits_total", Help: "Requests rejected by rate limiter, per layer."},
		[]string{"layer"},
	)
	// sensitiveConfirm counts sensitive-tool confirm/deny decisions.
	sensitiveConfirm = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "gateway_sensitive_confirm_total", Help: "Sensitive tool confirmation decisions."},
		[]string{"risk_level", "decision"},
	)
	// sandboxLifecycle counts sandbox container state transitions.
	sandboxLifecycle = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "gateway_sandbox_lifecycle_total", Help: "Sandbox container lifecycle state transitions."},
		[]string{"status"},
	)
)

func init() {
	obs.MustRegister(authDenied)
	obs.MustRegister(rateLimitHits)
	obs.MustRegister(sensitiveConfirm)
	obs.MustRegister(sandboxLifecycle)
}

type ServiceProfile struct {
	Service          string          `json:"service"`
	Layer            string          `json:"layer"`
	Responsibilities []string        `json:"responsibilities"`
	DependsOn        []string        `json:"depends_on"`
	SampleEvent      events.Envelope `json:"sample_event"`
	PublishSubject   string          `json:"publish_subject"`
}

type PublishMessageRequest struct {
	TenantID  string         `json:"tenant_id"`
	SessionID string         `json:"session_id"`
	TraceID   string         `json:"trace_id"`
	MessageID string         `json:"message_id"`
	ActorID   string         `json:"actor_id"`
	Content   string         `json:"content"`
	Metadata  map[string]any `json:"metadata"`
}

type PermissionRequestInput struct {
	TenantID    string         `json:"tenant_id"`
	SessionID   string         `json:"session_id"`
	TraceID     string         `json:"trace_id"`
	RequestID   string         `json:"request_id"`
	ActorID     string         `json:"actor_id"`
	ToolName    string         `json:"tool_name"`
	RiskLevel   string         `json:"risk_level"`
	Reason      string         `json:"reason"`
	TimeoutSecs int            `json:"timeout_seconds"`
	Arguments   map[string]any `json:"arguments"`
}

type PublishResult struct {
	Published bool            `json:"published"`
	Subject   string          `json:"subject"`
	Event     events.Envelope `json:"event"`
}

func main() {
	natsURL := getenv("NATS_URL", "nats://127.0.0.1:4222")
	bus, err := eventbus.Connect(natsURL)
	if err != nil {
		log.Fatalf("connect event bus: %v", err)
	}
	defer bus.Close()

	// ── Database pool (for templates, workspaces, etc.) ──────────────
	dbDSN := getenv("DATABASE_DSN", getenv("DATABASE_URL", "postgres://agenthub:agenthub@127.0.0.1:5434/agenthub?sslmode=disable"))
	pool, err := db.Connect(context.Background(), dbDSN)
	if err != nil {
		log.Printf("WARNING: database connection failed (templates/workspaces will use fallback): %v", err)
		pool = nil // gateway starts without DB; frontend presets serve as fallback
	}
	if pool != nil {
		defer pool.Close()
		if err := pool.Migrate(context.Background()); err != nil {
			log.Printf("WARNING: db migration failed (continuing): %v", err)
		}
	}

	shutdown, err := obs.InitTracer(context.Background(), getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""), "gateway-service")
	if err != nil {
		log.Fatalf("init tracer: %v", err)
	}
	defer shutdown(context.Background())

	// ── Knowledge proxy ───────────────────────────────────────────
	knowledgeURL := parseKnowledgeServiceURL()
	docPipelineURL := parseDocPipelineURL()
	knowledgeHandler := newKnowledgeProxy(knowledgeURL)
	log.Printf("knowledge proxy: %s (doc pipeline: %s)", knowledgeURL, docPipelineURL)

	// WebSocket hub: per-instance connection registry. The broadcast consumer
	// fans stream events out to connected clients by session_id.
	hub := NewHub()
	instance := getenv("HOSTNAME", "local")

	// Redis will be connected after rate limiter creation (for WithDistributed).
	redisAddr := getenv("REDIS_ADDR", "")
	var redisStore *state.Store

	jwtSecret := []byte(getenv("JWT_SECRET", ""))
	// TokenIssuer is shared by the HTTP auth middleware and the WebSocket
	// upgrade path. An empty secret enables dev mode (no signature check) so
	// local development works without a token issuer.
	issuer := iam.NewTokenIssuer(jwtSecret, "iam-service", 24*time.Hour)
	if _, err := bus.Subscribe("gateway-stream-"+instance, eventbus.StreamEventsSubject, func(env events.Envelope) {
		obs.IncEventReceived("gateway-service", string(env.EventType))
		dispatchStreamEvent(context.Background(), hub, env)
	}); err != nil {
		log.Fatalf("subscribe stream events for ws fanout: %v", err)
	}
	if _, err := bus.Subscribe("gateway-runtime-"+instance, eventbus.AgentRuntimeResultsSubject, func(env events.Envelope) {
		obs.IncEventReceived("gateway-service", string(env.EventType))
		dispatchStreamEvent(context.Background(), hub, env)
	}); err != nil {
		log.Fatalf("subscribe runtime results for ws fanout: %v", err)
	}

	profile := ServiceProfile{
		Service: "gateway-service",
		Layer:   "go-ingress",
		Responsibilities: []string{
			"http api ingress",
			"websocket gateway",
			"sse fallback",
			"rate limiting",
			"connection registry",
			"publish envelopes to NATS",
			"publish tool permission requests",
		},
		DependsOn:      []string{"redis", "nats", "session-service", "stream-delivery-service", "tool-permission-service"},
		PublishSubject: eventbus.SessionEventsSubject,
		SampleEvent: events.NewEnvelope(
			events.EventSessionMessageReceived,
			"tenant-demo",
			"session-demo",
			"trace-demo",
			events.Producer{Service: "gateway-service", Instance: "local"},
			map[string]any{"content": "hello"},
		),
	}
	profile.SampleEvent.EventID = "evt-demo-0001"

	// Four-layer rate limiting: user, tenant, agent, tool. Each layer has
	// independent capacity and rate. Set any rate to 0 to disable that layer.
	rl := NewMultiLayerRateLimiter(map[LimitLayer]LayerConfig{
		LayerUser:   {Capacity: getenvFloat("RATE_LIMIT_USER_CAPACITY", 50), Rate: getenvFloat("RATE_LIMIT_USER_RATE", 10)},
		LayerTenant: {Capacity: getenvFloat("RATE_LIMIT_TENANT_CAPACITY", 200), Rate: getenvFloat("RATE_LIMIT_TENANT_RATE", 100)},
		LayerAgent:  {Capacity: getenvFloat("RATE_LIMIT_AGENT_CAPACITY", 50), Rate: getenvFloat("RATE_LIMIT_AGENT_RATE", 20)},
		LayerTool:   {Capacity: getenvFloat("RATE_LIMIT_TOOL_CAPACITY", 20), Rate: getenvFloat("RATE_LIMIT_TOOL_RATE", 5)},
	})

	// ── Redis (Sprint M6: distributed rate limiting + cache + routes) ──
	if redisAddr != "" {
		redisStore = state.Connect(redisAddr)
		defer redisStore.Close()
		rr := newRouteRegistry(redisStore, instance)
		hub.WithRouteRegistry(rr)
		rl.WithDistributed(redisStore)
		log.Printf("gateway: redis enabled addr=%s instance=%s (routes+ratelimit+cache)", redisAddr, instance)
	} else {
		log.Printf("gateway: redis disabled (REDIS_ADDR not set) — single-instance mode")
	}

	// ── Cache Manager (Sprint M3) ────────────────────────────────────
	cacheMgr := NewCacheManager(redisStore)
	_ = cacheMgr

	mux := http.NewServeMux()
	// Sprint K6: Enhanced health check — reports PG and NATS dependency status.
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		pgOK := true
		if pool != nil {
			ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
			defer cancel()
			if err := pool.Ping(ctx); err != nil {
				pgOK = false
			}
		}
		natsOK := bus.Conn().IsConnected()
		status := http.StatusOK
		health := map[string]any{"status": "ok", "pg": pgOK, "nats": natsOK}
		if !pgOK || !natsOK {
			status = http.StatusServiceUnavailable
			health["status"] = "degraded"
		}
		w.WriteHeader(status)
		json.NewEncoder(w).Encode(health)
	})
	mux.HandleFunc("/healthz/readiness", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
		defer cancel()
		ready := map[string]any{"status": "ready", "pg": "connected", "nats": bus.Conn().IsConnected()}
		code := http.StatusOK
		if pool != nil {
			if err := pool.Ping(ctx); err != nil {
				ready["pg"] = err.Error()
				ready["status"] = "not_ready"
				code = http.StatusServiceUnavailable
			}
		} else {
			ready["pg"] = "disabled"
		}
		if !bus.Conn().IsConnected() {
			ready["nats"] = false
			ready["status"] = "not_ready"
			code = http.StatusServiceUnavailable
		}
		w.WriteHeader(code)
		json.NewEncoder(w).Encode(ready)
	})
	mux.HandleFunc("/profile", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(profile)
	})
	mux.HandleFunc("/ws", func(w http.ResponseWriter, r *http.Request) {
		serveWS(hub, issuer, w, r)
	})
	mux.HandleFunc("/stats", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"sessions":           hub.sessionCount(),
			"connections":        hub.clientCount(),
			"jwt_enforced":       len(jwtSecret) > 0,
			"rate_limit_buckets": rl.ActiveBuckets(),
			"rate_limit_stats":   rl.Stats(),
			"redis_connected":    redisStore != nil,
		})
	})
	mux.HandleFunc("/routes", func(w http.ResponseWriter, r *http.Request) {
		if hub.routes != nil {
			serveRoutes(hub.routes, w, r)
		} else {
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"instance":    instance,
				"sessions":    hub.sessionCount(),
				"connections": hub.clientCount(),
				"redis":       false,
			})
		}
	})
	mux.HandleFunc("/routes/", func(w http.ResponseWriter, r *http.Request) {
		if hub.routes != nil {
			serveRoutes(hub.routes, w, r)
		} else {
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{
				"instance": instance,
				"redis":    false,
				"note":     "Route registry disabled (REDIS_ADDR not set). Only in-memory hub active.",
			})
		}
	})
	mux.HandleFunc("/publish", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}

		var req PublishMessageRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid json body"})
			return
		}
		if req.TenantID == "" || req.SessionID == "" || req.TraceID == "" || req.Content == "" {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "tenant_id, session_id, trace_id, content are required"})
			return
		}
		// Multi-tenant isolation (P3-1): the authenticated principal may
		// only publish on behalf of their own tenant. In dev mode the
		// TenantContext is empty and the check passes through.
		if tc, ok := iam.FromContext(r.Context()); ok && !tc.DevMode {
			if !iam.EnforceTenantScope(r.Context(), req.TenantID) {
				authDenied.WithLabelValues("cross_tenant_publish").Inc()
				w.WriteHeader(http.StatusForbidden)
				_ = json.NewEncoder(w).Encode(map[string]string{"error": "forbidden: tenant_id does not match authenticated principal"})
				return
			}
			if req.ActorID == "" {
				req.ActorID = tc.UserID
			}
		}

		event := events.NewEnvelope(
			events.EventSessionMessageReceived,
			req.TenantID,
			req.SessionID,
			req.TraceID,
			events.Producer{Service: "gateway-service", Instance: getenv("HOSTNAME", "local")},
			map[string]any{
				"content":  req.Content,
				"metadata": req.Metadata,
			},
		)
		event.EventID = fallback(req.MessageID, "evt-"+time.Now().UTC().Format("20060102T150405.000000000Z"))
		event.MessageID = req.MessageID
		event.ActorID = req.ActorID
		event.Routing = &events.Routing{Channel: "session", PartitionKey: req.SessionID, Priority: events.PriorityNormal}

		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		if err := bus.PublishEnvelope(ctx, eventbus.SessionEventsSubject, event); err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		obs.IncEventPublished("gateway-service", string(event.EventType))

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(PublishResult{Published: true, Subject: eventbus.SessionEventsSubject, Event: event})
	})
	mux.HandleFunc("/permissions/request", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}

		var req PermissionRequestInput
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "invalid json body"})
			return
		}
		if req.TenantID == "" || req.SessionID == "" || req.TraceID == "" || req.RequestID == "" || req.ToolName == "" {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "tenant_id, session_id, trace_id, request_id, tool_name are required"})
			return
		}
		// Enforce tenant isolation on permission requests too.
		if tc, ok := iam.FromContext(r.Context()); ok && !tc.DevMode {
			if !iam.EnforceTenantScope(r.Context(), req.TenantID) {
				authDenied.WithLabelValues("cross_tenant_permission").Inc()
				w.WriteHeader(http.StatusForbidden)
				_ = json.NewEncoder(w).Encode(map[string]string{"error": "forbidden: tenant_id does not match authenticated principal"})
				return
			}
			if req.ActorID == "" {
				req.ActorID = tc.UserID
			}
		}

		event := events.NewEnvelope(
			events.EventToolPermissionRequested,
			req.TenantID,
			req.SessionID,
			req.TraceID,
			events.Producer{Service: "gateway-service", Instance: getenv("HOSTNAME", "local")},
			map[string]any{
				"request_id":      req.RequestID,
				"tool_name":       req.ToolName,
				"risk_level":      fallback(req.RiskLevel, "normal"),
				"reason":          req.Reason,
				"timeout_seconds": fallbackInt(req.TimeoutSecs, 30),
				"arguments":       req.Arguments,
			},
		)
		event.EventID = "perm-request-" + req.RequestID
		event.MessageID = req.RequestID
		event.ActorID = req.ActorID
		event.Routing = &events.Routing{Channel: "permission", PartitionKey: req.RequestID, Priority: events.PriorityHigh}

		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		if err := bus.PublishEnvelope(ctx, eventbus.ToolPermissionRequestsSubject, event); err != nil {
			w.WriteHeader(http.StatusBadGateway)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		obs.IncEventPublished("gateway-service", string(event.EventType))

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(PublishResult{Published: true, Subject: eventbus.ToolPermissionRequestsSubject, Event: event})
	})

	mux.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		obs.MetricsHandler().ServeHTTP(w, r)
	})

	// ── Knowledge CRUD + retrieval ────────────────────────────────
	mux.Handle("/platform/knowledge/upload", handleKnowledgeUpload(docPipelineURL))
	mux.Handle("/platform/knowledge/", knowledgeHandler)

	// ── Template marketplace ───────────────────────────────────────
	templates := newTemplateHandler(pool)
	mux.Handle("/platform/templates", templates)
	mux.Handle("/platform/templates/", templates)

	// ── Tool marketplace (G1) ──────────────────────────────────────
	tools := newToolHandler()
	mux.Handle("/api/admin/tools", tools)
	mux.Handle("/api/admin/tools/", tools)

	// ── Workspaces ─────────────────────────────────────────────────
	workspaces := newWorkspaceHandler(pool)
	mux.Handle("/platform/workspaces", workspaces)
	mux.Handle("/platform/workspaces/", workspaces)

	// Session Service owns durable chat sessions; Gateway owns public auth and routing.
	sessions := newSessionProxy(parseSessionServiceURL())
	mux.Handle("/platform/sessions", sessions)

	// ── Agent Versions (P1-6) ───────────────────────────────────────
	agentVersions := newAgentVersionHandler()
	mux.Handle("/platform/agent-versions/", agentVersions)

	// ── MCP Gateway Proxy (P1-2) ────────────────────────────────────
	mcpProxy := newMCPProxy(getenv("MCP_GATEWAY_URL", "http://127.0.0.1:8099"))
	mux.Handle("/platform/mcp/", mcpProxy)
	mux.Handle("/platform/mcp", mcpProxy)

	// ── A2A Protocol (P2-2) ─────────────────────────────────────────
	a2aBaseURL := getenv("PUBLIC_BASE_URL", "http://localhost:8081")
	a2aTLS := a2aTLSConfigFromEnv()
	if a2aTLS.Enabled {
		log.Printf("a2a: TLS enabled (cert=%s, key=%s, ca=%s, strict=%v)",
			a2aTLS.CertFile, a2aTLS.KeyFile, a2aTLS.CAFile, a2aTLS.StrictVerify)
	}
	a2aControl := newA2AControlPlaneClient(
		getenv("MISSION_CONTROL_PLANE_URL", "http://127.0.0.1:8000"),
		nil,
	)
	a2a := newA2AHandler(a2aBaseURL, pool, a2aTLS, a2aControl)
	mux.Handle("/platform/a2a/", http.StripPrefix("/platform/a2a", a2a))

	// ── API Keys + Public API ─────────────────────────────────────
	apiKeys := newAPIKeyHandler()
	mux.Handle("/platform/api-keys", apiKeys)
	mux.Handle("/platform/api-keys/", apiKeys)
	mux.HandleFunc("/v1/public/chat", func(w http.ResponseWriter, r *http.Request) {
		handlePublicChat(bus, apiKeys, w, r)
	})

	// ── Image Preprocessing (Sprint L1) ───────────────────────────
	imagePreproc := newImagePreprocHandler()
	mux.Handle("/platform/utils/image-preprocess", imagePreproc)

	// ── Video Frame Extraction (Sprint L1) ───────────────────────────
	videoH := newVideoHandler(bus)
	mux.Handle("/platform/utils/video-frames", videoH)
	mux.Handle("/platform/utils/video-frames/", videoH)

	// ── Public Bot Endpoint (Web App route) ──────────────────────
	globalAgentVersionHandler = agentVersions
	mux.HandleFunc("/api/public/bots/", func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodOptions {
			handlePublicBotOptions(w, r)
			return
		}
		handlePublicBotConfig(w, r, pool)
	})

	// ── Channel Connector (Feishu/WeCom) ──────────────────────────
	channels := newChannelConnector(bus)
	mux.Handle("/platform/channels", channels)
	mux.Handle("/platform/channels/", channels)

	// ── ContextOS — Unified Context Engine ───────────────────────
	ctxEngine := newContextEngine(bus, pool)
	mux.Handle("/context/", ctxEngine)
	mux.Handle("/context", ctxEngine)
	// Initialize decay config with defaults
	cfg := ctxEngine.getDecayConfig()
	log.Printf("context-engine: decay config initialized lambda=%.4f half_life=%.1f days", cfg.Lambda, cfg.HalfLife)

	// ── AgentNet — Decentralized Multi-Agent Collaboration ──────────
	agentNet := newAgentNetHandler(bus, pool)
	mux.Handle("/agentnet/", agentNet)
	mux.Handle("/agentnet", agentNet)

	// ── Digital Identity + Sandbox (Sprint J) ──────────────────────
	digitalID := newDigitalIdentityHandler(bus, pool)
	mux.Handle("/digital/", digitalID)
	mux.Handle("/digital", digitalID)

	// ── Audit log (Sprint J4) ─────────────────────────────────────
	auditH := newAuditHandler(pool)
	mux.Handle("/audit/", auditH)
	mux.Handle("/audit", auditH)

	// ── Logs proxy (Sprint J4) ────────────────────────────────────
	logs := newLogsHandler()
	mux.Handle("/logs/", logs)
	mux.Handle("/logs", logs)

	addr := getenv("GATEWAY_ADDR", ":8081")
	log.Printf("gateway-service listening on %s (dev_mode=%v jwt_enforced=%v rate limit: user=%.0f/%.0f tenant=%.0f/%.0f agent=%.0f/%.0f tool=%.0f/%.0f)",
		addr,
		issuer.IsDevMode(), !issuer.IsDevMode(),
		getenvFloat("RATE_LIMIT_USER_CAPACITY", 50), getenvFloat("RATE_LIMIT_USER_RATE", 10),
		getenvFloat("RATE_LIMIT_TENANT_CAPACITY", 200), getenvFloat("RATE_LIMIT_TENANT_RATE", 100),
		getenvFloat("RATE_LIMIT_AGENT_CAPACITY", 50), getenvFloat("RATE_LIMIT_AGENT_RATE", 20),
		getenvFloat("RATE_LIMIT_TOOL_CAPACITY", 20), getenvFloat("RATE_LIMIT_TOOL_RATE", 5))

	// Auth middleware sits inside rate limiting (so rejected auth still counts
	// against the caller's bucket) and outside the route mux. Public endpoints
	// (/healthz, /metrics, /profile, /ws) bypass auth; /ws runs its own JWT
	// check during the WebSocket upgrade.
	authMW := iam.AuthMiddleware(issuer, []string{"/healthz", "/metrics", "/profile", "/ws", "/api/public/bots/", "/v1/public/"}, func(r *http.Request, reason string) {
		authDenied.WithLabelValues("unauthorized").Inc()
	})
	handler := obs.Middleware("gateway-service", rateLimitMiddleware(rl, authMW(mux)))

	// ── Optional Chaos Middleware (Sprint M6) ────────────────────────
	chaosCfg := ChaosConfigFromEnv()
	if chaosCfg.LatencyMs > 0 || chaosCfg.ErrorRate > 0 {
		handler = ChaosMiddleware(chaosCfg, handler)
	}

	// ── Security Middleware (Sprint N6) ─────────────────────────────
	// Order (outer→inner): body limit → CORS → security headers → trace → obs metrics → chaos → rate limit → auth → mux
	handler = bodyLimitMiddleware(handler)
	handler = corsMiddleware(handler)
	handler = securityHeadersMiddleware(handler)
	handler = noSensitiveHeaders(handler)

	// ── Tracing Middleware (Sprint N1) ──────────────────────────────
	// Creates OTel spans for every request; slow requests (>500ms) emit span events.
	handler = obs.TraceMiddleware("gateway-service", handler)

	// ── Graceful Shutdown (Sprint M6) ───────────────────────────────
	srv := &http.Server{
		Addr:         addr,
		Handler:      handler,
		ReadTimeout:  30 * time.Second,
		WriteTimeout: 60 * time.Second,
		IdleTimeout:  120 * time.Second,
	}

	// Channel to capture server errors
	errCh := make(chan error, 1)

	// Start server in a goroutine
	go func() {
		log.Printf("gateway-service: http server starting on %s", addr)
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			errCh <- err
		}
	}()

	// ── Wait for shutdown signal ────────────────────────────────────
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)
	select {
	case sig := <-quit:
		log.Printf("gateway-service: received signal %v, initiating graceful shutdown...", sig)
	case err := <-errCh:
		log.Printf("gateway-service: server error: %v, shutting down...", err)
	}

	// Mark as shutting down (health/readiness probes will fail)
	shutdownCtx := NewShutdownContext(DefaultShutdownConfig())
	shutdownCtx.Initiate()

	// Give load balancer time to detect the failing readiness probe
	log.Printf("gateway-service: draining for %v before stopping...", shutdownCtx.config.HealthFailDelay)
	time.Sleep(shutdownCtx.config.HealthFailDelay)

	// Create a deadline for graceful shutdown
	ctx, cancel := context.WithTimeout(context.Background(), shutdownCtx.config.DrainTimeout)
	defer cancel()

	// Shutdown HTTP server (stops accepting new connections, waits for active)
	if err := srv.Shutdown(ctx); err != nil {
		log.Printf("gateway-service: forced shutdown after timeout: %v", err)
	} else {
		log.Printf("gateway-service: http server gracefully stopped")
	}

	// Close WebSocket hub
	hub.Shutdown()

	log.Printf("gateway-service: shutdown complete")
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func getenvFloat(key string, fallback float64) float64 {
	if v := os.Getenv(key); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return fallback
}

func fallback(primary, secondary string) string {
	if primary != "" {
		return primary
	}
	return secondary
}

func fallbackInt(primary, secondary int) int {
	if primary > 0 {
		return primary
	}
	return secondary
}

func getenvInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return fallback
}

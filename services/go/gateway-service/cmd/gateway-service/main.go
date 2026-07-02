package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"

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

func init() {
	obs.MustRegister(authDenied)
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

	shutdown, err := obs.InitTracer(context.Background(), getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""), "gateway-service")
	if err != nil {
		log.Fatalf("init tracer: %v", err)
	}
	defer shutdown(context.Background())

	// WebSocket hub: per-instance connection registry. The broadcast consumer
	// fans stream events out to connected clients by session_id.
	hub := NewHub()
	instance := getenv("HOSTNAME", "local")

	// Redis connection registry (optional — works without Redis in dev mode).
	// When REDIS_ADDR is set, the hub writes per-connection route entries so
	// multi-instance deployments can discover which gateway holds a session.
	redisAddr := getenv("REDIS_ADDR", "")
	if redisAddr != "" {
		store := state.Connect(redisAddr)
		defer store.Close()
		rr := newRouteRegistry(store, instance)
		hub.WithRouteRegistry(rr)
		log.Printf("gateway route registry enabled: redis=%s instance=%s", redisAddr, instance)
	} else {
		log.Printf("gateway route registry disabled (REDIS_ADDR not set) — running with in-memory hub only")
	}

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

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
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
				"sessions":          hub.sessionCount(),
				"connections":       hub.clientCount(),
				"jwt_enforced":      len(jwtSecret) > 0,
				"rate_limit_buckets": rl.ActiveBuckets(),
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
	authMW := iam.AuthMiddleware(issuer, []string{"/healthz", "/metrics", "/profile", "/ws"}, func(r *http.Request, reason string) {
		authDenied.WithLabelValues("unauthorized").Inc()
	})
	handler := obs.Middleware("gateway-service", rateLimitMiddleware(rl, authMW(mux)))
	log.Fatal(http.ListenAndServe(addr, handler))
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

package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"sync"
	"sync/atomic"
	"time"

	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
	"github.com/agenthub/platform/shared/obs"
)

// RuntimePools advertises the available worker pools and their concurrency
// limits. The /runtime/pools endpoint exposes this for capacity planning.
type RuntimePools struct {
	Pools []map[string]any `json:"pools"`
}

// EventBuffer is a bounded ring buffer for debugging recent events.
type EventBuffer struct {
	mu    sync.RWMutex
	items []events.Envelope
	limit int
}

func NewEventBuffer(limit int) *EventBuffer {
	return &EventBuffer{items: make([]events.Envelope, 0, limit), limit: limit}
}

func (b *EventBuffer) Add(e events.Envelope) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if len(b.items) >= b.limit {
		b.items = append(b.items[1:], e)
		return
	}
	b.items = append(b.items, e)
}

func (b *EventBuffer) Snapshot() []events.Envelope {
	b.mu.RLock()
	defer b.mu.RUnlock()
	out := make([]events.Envelope, len(b.items))
	copy(out, b.items)
	return out
}

func (b *EventBuffer) Len() int {
	b.mu.RLock()
	defer b.mu.RUnlock()
	return len(b.items)
}

// ModelAdapterClient calls the Python model-adapter-service via HTTP. It maps
// Go-tier pool names to model names and forwards agent dispatch events as
// OpenAI-compatible chat completion requests.
type ModelAdapterClient struct {
	httpClient *http.Client
	baseURL    string
}

func NewModelAdapterClient(baseURL string) *ModelAdapterClient {
	return &ModelAdapterClient{
		httpClient: &http.Client{Timeout: 60 * time.Second},
		baseURL:    baseURL,
	}
}

// poolToModel maps a Go-tier pool name to a model-adapter model name. The
// mapping is configurable via env; defaults use mock-gpt for local dev.
func poolToModel(pool string) string {
	switch pool {
	case "llm-online":
		return getenv("MODEL_ONLINE", "mock-gpt")
	case "llm-fast":
		return getenv("MODEL_FAST", "mock-gpt")
	case "llm-reasoning":
		return getenv("MODEL_REASONING", "mock-gpt")
	case "tool-high":
		return getenv("MODEL_TOOL", "mock-gpt")
	default:
		return "mock-gpt"
	}
}

// chatCompletionRequest is the OpenAI-compatible request body sent to the
// Python model-adapter-service.
type chatCompletionRequest struct {
	Model        string                   `json:"model"`
	Messages     []map[string]string      `json:"messages"`
	Temperature  float64                  `json:"temperature"`
	SystemPrompt string                   `json:"system_prompt,omitempty"`
	AgentRole    string                   `json:"agent_role,omitempty"`
	Stage        string                   `json:"stage,omitempty"`
}

type chatCompletionResponse struct {
	ID      string           `json:"id"`
	Model   string           `json:"model"`
	Choices []map[string]any `json:"choices"`
	Usage   map[string]int   `json:"usage"`
}

// Call forwards a dispatch event to the model-adapter and returns the model's
// response text. If the adapter is unreachable, a degraded fallback response
// is returned so the ReAct loop can continue (fault tolerance).
func (c *ModelAdapterClient) Call(ctx context.Context, pool, input, systemPrompt, agentRole, stage string) (string, error) {
	model := poolToModel(pool)

	reqBody := chatCompletionRequest{
		Model:       model,
		Messages:    []map[string]string{{"role": "user", "content": input}},
		Temperature: 0.7,
	}
	if systemPrompt != "" {
		reqBody.SystemPrompt = systemPrompt
	}
	if agentRole != "" {
		reqBody.AgentRole = agentRole
	}
	if stage != "" {
		reqBody.Stage = stage
	}

	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return "", fmt.Errorf("marshal request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/v1/chat/completions", bytes.NewReader(bodyBytes))
	if err != nil {
		return "", fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		// Degraded fallback: return a placeholder so the ReAct loop continues.
		return fmt.Sprintf("[fallback: model-adapter unreachable: %v]", err), nil
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Sprintf("[fallback: model-adapter status %d: %s]", resp.StatusCode, string(body)), nil
	}

	var result chatCompletionResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("decode response: %w", err)
	}

	if len(result.Choices) == 0 {
		return "[empty response]", nil
	}

	choice := result.Choices[0]
	if msg, ok := choice["message"].(map[string]any); ok {
		if content, ok := msg["content"].(string); ok {
			return content, nil
		}
	}
	return "[no content]", nil
}

// WorkerPool limits concurrent model-adapter calls per pool. Each pool has its
// own semaphore; when all slots are occupied, new dispatches queue until a
// slot frees up. This prevents thundering-herd on the Python tier.
type WorkerPool struct {
	mu         sync.Mutex
	semaphores map[string]chan struct{}
	limits     map[string]int
	active     int64
}

func NewWorkerPool(limits map[string]int) *WorkerPool {
	return &WorkerPool{
		semaphores: make(map[string]chan struct{}),
		limits:     limits,
	}
}

func (wp *WorkerPool) sem(pool string) chan struct{} {
	wp.mu.Lock()
	defer wp.mu.Unlock()
	if ch, ok := wp.semaphores[pool]; ok {
		return ch
	}
	limit, ok := wp.limits[pool]
	if !ok {
		limit = 50 // default concurrency
	}
	ch := make(chan struct{}, limit)
	wp.semaphores[pool] = ch
	return ch
}

// Acquire blocks until a worker slot is available for the pool. Returns a
// release function that must be called when the work is done.
func (wp *WorkerPool) Acquire(pool string) func() {
	ch := wp.sem(pool)
	ch <- struct{}{}
	atomic.AddInt64(&wp.active, 1)
	return func() {
		<-ch
		atomic.AddInt64(&wp.active, -1)
	}
}

func (wp *WorkerPool) Active() int64 {
	return atomic.LoadInt64(&wp.active)
}

func main() {
	pools := RuntimePools{Pools: []map[string]any{
		{"name": "llm-online", "max_concurrency": 300, "kind": "model"},
		{"name": "llm-fast", "max_concurrency": 150, "kind": "model"},
		{"name": "llm-reasoning", "max_concurrency": 100, "kind": "model"},
		{"name": "tool-high", "max_concurrency": 150, "kind": "tool"},
		{"name": "python-offline", "max_concurrency": 200, "kind": "offline"},
	}}

	dispatchBuf, resultBuf := NewEventBuffer(100), NewEventBuffer(100)

	bus, err := eventbus.Connect(getenv("NATS_URL", "nats://127.0.0.1:4222"))
	if err != nil {
		log.Fatalf("connect event bus: %v", err)
	}
	defer bus.Close()

	shutdown, errTr := obs.InitTracer(context.Background(), getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""), "agent-runtime-control-plane")
	if errTr != nil {
		log.Fatalf("init tracer: %v", errTr)
	}
	defer shutdown(context.Background())

	// Python model-adapter-service HTTP client.
	adapterURL := getenv("MODEL_ADAPTER_URL", "http://127.0.0.1:8091")
	adapter := NewModelAdapterClient(adapterURL)

	// Worker pool with per-pool concurrency limits.
	wp := NewWorkerPool(map[string]int{
		"llm-online":    300,
		"llm-fast":      150,
		"llm-reasoning": 100,
		"tool-high":     150,
	})

	// Subscribe to agent.runtime.dispatch events. Each dispatch is forwarded to
	// the model-adapter-service via HTTP; the result is published back as
	// agent.runtime.result. Concurrency is bounded by the worker pool.
	if _, err := bus.QueueSubscribe("agent-runtime", "agent-runtime", eventbus.AgentRuntimeDispatchSubject, func(env events.Envelope) {
		obs.IncEventReceived("agent-runtime-control-plane", string(env.EventType))
		dispatchBuf.Add(env)

		pool := stringValue(env.Payload, "pool")
		input := stringValue(env.Payload, "input")
		systemPrompt := stringValue(env.Payload, "system_prompt")
		agentRole := stringValue(env.Payload, "agent_role")
		stage := stringValue(env.Payload, "stage")
		runtimeID := stringValue(env.Payload, "runtime_id")
		toolName := stringValue(env.Payload, "tool_name")

		// Acquire a worker slot (blocks if pool is at capacity).
		release := wp.Acquire(pool)
		go func() {
			defer release()

			// Call the Python model-adapter-service.
			callCtx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
			defer cancel()

			output, callErr := adapter.Call(callCtx, pool, input, systemPrompt, agentRole, stage)
			status := "completed"
			if callErr != nil {
				status = "error"
				output = fmt.Sprintf("model-adapter call failed: %v", callErr)
			}

			// Publish the result back to the event bus.
			publishCtx, pubCancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer pubCancel()

			result := events.NewEnvelope(
				events.EventAgentRuntimeResult,
				env.TenantID, env.SessionID, env.TraceID,
				events.Producer{Service: "agent-runtime-control-plane", Instance: getenv("HOSTNAME", "local")},
				map[string]any{
					"dispatch_event_id": env.EventID,
					"runtime_id":        runtimeID,
					"pool":              pool,
					"tool_name":         toolName,
					"agent_role":        agentRole,
					"stage":             stage,
					"status":            status,
					"output":            output,
					"model":             poolToModel(pool),
				},
			)
			result.EventID = "runtime-result-" + env.EventID
			result.MessageID = env.MessageID
			result.ActorID = env.ActorID
			result.Routing = &events.Routing{Channel: "runtime", PartitionKey: env.SessionID, Priority: events.PriorityNormal}

			if err := bus.PublishEnvelope(publishCtx, eventbus.AgentRuntimeResultsSubject, result); err != nil {
				log.Printf("publish runtime result failed: %v", err)
				return
			}
			obs.IncEventPublished("agent-runtime-control-plane", string(result.EventType))
			resultBuf.Add(result)
		}()
	}); err != nil {
		log.Fatalf("subscribe runtime dispatch: %v", err)
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("/runtime/pools", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(pools)
	})
	mux.HandleFunc("/runtime/dispatches", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"subject": eventbus.AgentRuntimeDispatchSubject, "count": dispatchBuf.Len(), "events": dispatchBuf.Snapshot()})
	})
	mux.HandleFunc("/runtime/results", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"subject": eventbus.AgentRuntimeResultsSubject, "count": resultBuf.Len(), "events": resultBuf.Snapshot()})
	})
	mux.HandleFunc("/runtime/stats", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"active_workers":   wp.Active(),
			"adapter_url":      adapterURL,
			"dispatches_seen":  dispatchBuf.Len(),
			"results_produced": resultBuf.Len(),
		})
	})
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		obs.MetricsHandler().ServeHTTP(w, r)
	})

	addr := getenv("RUNTIME_ADDR", ":8085")
	log.Printf("agent-runtime-control-plane listening on %s (adapter: %s)", addr, adapterURL)
	handler := obs.Middleware("agent-runtime-control-plane", mux)
	log.Fatal(http.ListenAndServe(addr, handler))
}

func stringValue(payload map[string]any, key string) string {
	v, ok := payload[key]
	if !ok || v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	return ""
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

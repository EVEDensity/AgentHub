package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
	"github.com/agenthub/platform/shared/obs"
	"github.com/agenthub/platform/shared/state"
)

type ReactStage struct{ Name, Description string }
type ServiceProfile struct{ Service, SubscribedTo, PublishesTo, PermissionRequestsTo string; SampleEvent events.Envelope; BufferedEvents, BufferedPermissionEvents, BufferedPendingApprovals int }
type EventBuffer struct{ mu sync.RWMutex; items []events.Envelope; limit int }
type PendingApproval struct{ SessionEvent, PermissionEvent events.Envelope; DetectedTool, DetectedRisk, DetectedReason string; CreatedAt time.Time }
type PendingStore struct{ mu sync.RWMutex; items map[string]PendingApproval }

func NewEventBuffer(n int)*EventBuffer{return &EventBuffer{limit:n,items:make([]events.Envelope,0,n)}}
func NewPendingStore()*PendingStore{return &PendingStore{items:map[string]PendingApproval{}}}
func (b *EventBuffer)Add(e events.Envelope){b.mu.Lock();defer b.mu.Unlock();if len(b.items)>=b.limit{b.items=append(b.items[1:],e);return};b.items=append(b.items,e)}
func (b *EventBuffer)Snapshot()[]events.Envelope{b.mu.RLock();defer b.mu.RUnlock();out:=make([]events.Envelope,len(b.items));copy(out,b.items);return out}
func (b *EventBuffer)Len()int{b.mu.RLock();defer b.mu.RUnlock();return len(b.items)}
func (s *PendingStore)Put(id string,a PendingApproval){s.mu.Lock();defer s.mu.Unlock();s.items[id]=a}
func (s *PendingStore)Get(id string)(PendingApproval,bool){s.mu.RLock();defer s.mu.RUnlock();a,ok:=s.items[id];return a,ok}
func (s *PendingStore)Delete(id string){s.mu.Lock();defer s.mu.Unlock();delete(s.items,id)}
func (s *PendingStore)Len()int{s.mu.RLock();defer s.mu.RUnlock();return len(s.items)}
func (s *PendingStore)Snapshot()map[string]PendingApproval{s.mu.RLock();defer s.mu.RUnlock();out:=make(map[string]PendingApproval,len(s.items));for k,v:=range s.items{out[k]=v};return out}

func main() {
	stages := []ReactStage{
		{"ingest", "validate and normalize message"},
		{"classify", "route to router/planner/search flow"},
		{"retrieve", "trigger deepsearch pipeline"},
		{"plan", "decide next agent actions"},
		{"act", "dispatch tools or model runtimes"},
		{"observe", "collect runtime observations"},
		{"reflect", "critic/reasoning checkpoint"},
		{"continue_or_finish", "budget-aware continuation decision"},
		{"synthesize", "build grounded answer"},
		{"stream_back", "emit aggregated output"},
	}
	buffer, perms, pending := NewEventBuffer(50), NewEventBuffer(50), NewPendingStore()

	bus, err := eventbus.Connect(getenv("NATS_URL", "nats://127.0.0.1:4222"))
	if err != nil {
		log.Fatalf("connect event bus: %v", err)
	}
	defer bus.Close()

	shutdown, errTr := obs.InitTracer(context.Background(), getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""), "realtime-orchestrator")
	if errTr != nil {
		log.Fatalf("init tracer: %v", errTr)
	}
	defer shutdown(context.Background())

	// Redis-backed ReAct state store. Per-session react state is persisted here
	// so any orchestrator replica can resume after a failover. If Redis is
	// unavailable the machine degrades to in-memory-only (nil store).
	store := state.Connect(getenv("REDIS_ADDR", "127.0.0.1:6379"))
	defer store.Close()
	budgets := DefaultBudgets()

	// DeepSearch 客户端：model-adapter (LLM 步骤) + retrieval-core (混合检索)。
	// 两者均通过 HTTP 直连，避免 NATS 异步链路在同步 ReAct 循环中引入额外延迟。
	// 默认地址指向 Docker Compose 中的服务名；本地开发可通过环境变量覆盖。
	//
	// P2-7/8: 用 ResilientDeepSearchFlow 包装，提供：
	//   - 并发池上限（DEEPSEARCH_MAX_CONCURRENT，默认 50）防止高并发压垮下游
	//   - model-adapter / retrieval-core 双熔断器（连续失败 5 次熔断 30s）
	//   - 轻量检索降级（deepsearch 模式失败 → simple 模式重试，在 deepsearch.go 内）
	modelAdapter := NewModelAdapterClient(getenv("MODEL_ADAPTER_URL", "http://model-adapter-service:8091"))
	retrievalCore := NewRetrievalCoreClient(getenv("RETRIEVAL_CORE_URL", "http://retrieval-core:8102"))
	innerFlow := NewDeepSearchFlow(modelAdapter, retrievalCore)
	maxConcurrent := parseMaxConcurrent(getenv("DEEPSEARCH_MAX_CONCURRENT", "50"))
	deepSearch := NewResilientDeepSearchFlow(innerFlow, maxConcurrent)
	// Sprint D: RustCoreBridge unifies NATS communication with all five Rust cores.
		rustBridge := NewRustCoreBridge(bus)
		machine := NewReactMachine(store, bus, budgets, deepSearch, rustBridge)

	// Session events: the main ingress for the ReAct loop. Risky messages are
	// intercepted for permission approval before the loop begins; everything
	// else enters the state machine immediately in a background goroutine so
	// the JetStream consumer can ack quickly.
	if _, err := bus.QueueSubscribe("realtime-orchestrator", "realtime-orchestrator", eventbus.SessionEventsSubject, func(env events.Envelope) {
		obs.IncEventReceived("realtime-orchestrator", string(env.EventType))
		buffer.Add(env)

		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()

		// Permission gate: risky content must be approved before execution.
		if p, a, ok := perm(env); ok {
			if err := lifecycle(ctx, bus, waitEvt(env, a), nil); err != nil {
				return
			}
			if err := bus.PublishEnvelope(ctx, eventbus.ToolPermissionRequestsSubject, p); err != nil {
				return
			}
			obs.IncEventPublished("realtime-orchestrator", string(p.EventType))
			perms.Add(p)
			pending.Put(p.MessageID, a)
			return
		}

		// Non-risky: run the full ReAct state machine asynchronously.
		go func() {
			reactCtx, reactCancel := context.WithTimeout(context.Background(), 30*time.Second)
			defer reactCancel()
			if err := machine.runReactLoop(reactCtx, env); err != nil {
				log.Printf("react loop failed for session %s: %v", env.SessionID, err)
				_, _ = machine.Fail(reactCtx, env, err.Error())
			}
		}()
	}); err != nil {
		log.Fatalf("subscribe session events: %v", err)
	}

	// Permission resolution: when a permission is approved, resume the ReAct
	// loop for the original session event. Denials emit an error stream event.
	if _, err := bus.QueueSubscribe("realtime-orchestrator-perm", "realtime-orchestrator-perm", eventbus.ToolPermissionResolvedSubject, func(env events.Envelope) {
		obs.IncEventReceived("realtime-orchestrator", string(env.EventType))
		if env.EventType != events.EventToolPermissionResolved {
			return
		}
		id, decision := str(env.Payload, "request_id"), strings.ToLower(str(env.Payload, "decision"))
		a, ok := pending.Get(id)
		if !ok {
			return
		}
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		defer cancel()
		if decision == "approved" {
			if err := lifecycle(ctx, bus, resumeEvt(a.SessionEvent, "permission approved, resuming execution", 2, "approved"), nil); err == nil {
				// Run the ReAct loop for the originally-blocked message.
				go func() {
					reactCtx, reactCancel := context.WithTimeout(context.Background(), 30*time.Second)
					defer reactCancel()
					if err := machine.runReactLoop(reactCtx, a.SessionEvent); err != nil {
						log.Printf("react loop (post-approval) failed for session %s: %v", a.SessionEvent.SessionID, err)
						_, _ = machine.Fail(reactCtx, a.SessionEvent, err.Error())
					}
				}()
			}
		} else {
			e := errEvt(a.SessionEvent, a, decision)
			_ = lifecycle(ctx, bus, denyEvt(a.SessionEvent, a, decision), &e)
		}
		pending.Delete(id)
	}); err != nil {
		log.Fatalf("subscribe permission resolved: %v", err)
	}

	profile := ServiceProfile{
		Service: "realtime-orchestrator", SubscribedTo: eventbus.SessionEventsSubject,
		PublishesTo: eventbus.StreamEventsSubject, PermissionRequestsTo: eventbus.ToolPermissionRequestsSubject,
		SampleEvent: events.NewEnvelope(events.EventAgentReactTransition, "tenant-demo", "session-demo", "trace-demo",
			events.Producer{Service: "realtime-orchestrator", Instance: "local"},
			map[string]any{"from_state": "classify", "to_state": "retrieve"}),
	}
	profile.SampleEvent.EventID = "evt-react-0001"

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("/react/stages", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(stages)
	})
	mux.HandleFunc("/agents", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"agents":     ListAgents(),
			"stage_owner": stageOwnerMap(),
		})
	})
	mux.HandleFunc("/react/state", func(w http.ResponseWriter, r *http.Request) {
		tenantID := r.URL.Query().Get("tenant_id")
		sessionID := r.URL.Query().Get("session_id")
		if tenantID == "" || sessionID == "" {
			w.WriteHeader(http.StatusBadRequest)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "tenant_id and session_id are required"})
			return
		}
		run, err := machine.Load(r.Context(), tenantID, sessionID)
		if err != nil || run == nil {
			w.WriteHeader(http.StatusNotFound)
			_ = json.NewEncoder(w).Encode(map[string]string{"error": "no active react run"})
			return
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(run)
	})
	mux.HandleFunc("/profile", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		profile.BufferedEvents, profile.BufferedPermissionEvents, profile.BufferedPendingApprovals = buffer.Len(), perms.Len(), pending.Len()
		_ = json.NewEncoder(w).Encode(profile)
	})
	mux.HandleFunc("/events/recent", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"subject": eventbus.SessionEventsSubject, "count": buffer.Len(), "events": buffer.Snapshot()})
	})
	mux.HandleFunc("/permissions/recent", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"subject": eventbus.ToolPermissionRequestsSubject, "count": perms.Len(), "events": perms.Snapshot()})
	})
	mux.HandleFunc("/permissions/pending", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{"count": pending.Len(), "pending": pending.Snapshot()})
	})
	mux.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		obs.MetricsHandler().ServeHTTP(w, r)
	})
	// P2-7/8: 熔断器与并发池状态端点，用于运维观测故障降级链状态。
	mux.HandleFunc("/resilience/state", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"circuit_breakers":  deepSearch.BreakerStates(),
			"pool_active":       deepSearch.pool.Active(),
			"pool_max":           cap(deepSearch.pool.sem),
				"rust_bridge_pending":   rustBridge.PendingCounts(),
		})
	})

	addr := getenv("ORCHESTRATOR_ADDR", ":8082")
	log.Printf("realtime-orchestrator listening on %s (react budgets: steps=%d tools=%d retrieval=%d time=%s)",
		addr, budgets.MaxSteps, budgets.MaxToolRounds, budgets.MaxRetrievalRound, budgets.MaxOnlineTime)
	handler := obs.Middleware("realtime-orchestrator", mux)
	log.Fatal(http.ListenAndServe(addr, handler))
}

func perm(env events.Envelope)(events.Envelope,PendingApproval,bool){tool,reason,risk,ok:=risky(env);if !ok{return events.Envelope{},PendingApproval{},false};id:=pick(env.MessageID,env.EventID)+"-perm";p:=events.NewEnvelope(events.EventToolPermissionRequested,env.TenantID,env.SessionID,env.TraceID,events.Producer{Service:"realtime-orchestrator",Instance:getenv("HOSTNAME","local")},map[string]any{"request_id":id,"tool_name":tool,"risk_level":risk,"reason":reason,"timeout_seconds":60,"arguments":map[string]any{"source_event_id":env.EventID,"content":str(env.Payload,"content"),"metadata":env.Payload["metadata"]}});p.EventID,p.MessageID,p.ActorID="perm-auto-"+id,id,env.ActorID;p.Routing=&events.Routing{Channel:"permission",PartitionKey:id,Priority:events.PriorityHigh};return p,PendingApproval{SessionEvent:env,PermissionEvent:p,DetectedTool:tool,DetectedRisk:risk,DetectedReason:reason,CreatedAt:time.Now().UTC()},true}
func waitEvt(env events.Envelope,a PendingApproval)events.Envelope{return chunk(env,"stream-wait-"+env.EventID,1,fmt.Sprintf("waiting for permission approval: %s",a.DetectedReason),true,"waiting_permission",a.PermissionEvent.MessageID,events.PriorityHigh)}
func denyEvt(env events.Envelope,a PendingApproval,d string)events.Envelope{return chunk(env,"stream-denied-"+env.EventID,2,fmt.Sprintf("execution blocked: permission %s for %s",d,a.DetectedTool),false,"permission_denied",a.PermissionEvent.MessageID,events.PriorityHigh)}
func resumeEvt(env events.Envelope,prefix string,seq int,status string)events.Envelope{return chunk(env,"stream-"+env.EventID,seq,fmt.Sprintf("%s: %v",prefix,env.Payload["content"]),false,status,"",events.PriorityNormal)}
func errEvt(env events.Envelope,a PendingApproval,d string)events.Envelope{e:=events.NewEnvelope(events.EventSessionStreamError,env.TenantID,env.SessionID,env.TraceID,events.Producer{Service:"realtime-orchestrator",Instance:getenv("HOSTNAME","local")},map[string]any{"stream_id":"stream-error-"+env.EventID,"status":"error","request_id":a.PermissionEvent.MessageID,"error":fmt.Sprintf("permission %s for %s",d,a.DetectedTool)});e.EventID,e.MessageID,e.ActorID="stream-error-"+env.EventID,env.MessageID,env.ActorID;e.Routing=&events.Routing{Channel:"stream",PartitionKey:env.SessionID,Priority:events.PriorityHigh};return e}
func dispatchEvt(env events.Envelope,pool,tool,reason string)events.Envelope{e:=events.NewEnvelope(events.EventAgentRuntimeDispatch,env.TenantID,env.SessionID,env.TraceID,events.Producer{Service:"realtime-orchestrator",Instance:getenv("HOSTNAME","local")},map[string]any{"runtime_id":"runtime-"+env.EventID,"pool":pool,"tool_name":tool,"reason":reason,"input":str(env.Payload,"content")});e.EventID,e.MessageID,e.ActorID="runtime-dispatch-"+env.EventID,env.MessageID,env.ActorID;e.Routing=&events.Routing{Channel:"runtime",PartitionKey:env.SessionID,Priority:events.PriorityNormal};return e}
func lifecycle(ctx context.Context,bus *eventbus.Client,c events.Envelope,e *events.Envelope)error{f:=sib(events.EventSessionStreamFlush,c,c.EventID+"-flush",c.MessageID,"flush");d:=sib(events.EventSessionStreamComplete,c,c.EventID+"-complete",c.MessageID,str(c.Payload,"status"));if err:=bus.PublishEnvelope(ctx,eventbus.StreamEventsSubject,c);err!=nil{return err};obs.IncEventPublished("realtime-orchestrator",string(c.EventType));if err:=bus.PublishEnvelope(ctx,eventbus.StreamEventsSubject,f);err!=nil{return err};obs.IncEventPublished("realtime-orchestrator",string(f.EventType));if e!=nil{if err:=bus.PublishEnvelope(ctx,eventbus.StreamEventsSubject,*e);err!=nil{return err};obs.IncEventPublished("realtime-orchestrator",string(e.EventType))};if err:=bus.PublishEnvelope(ctx,eventbus.StreamEventsSubject,d);err!=nil{return err};obs.IncEventPublished("realtime-orchestrator",string(d.EventType));return nil}
func chunk(env events.Envelope,id string,seq int,content string,thinking bool,status,reqID string,p events.Priority)events.Envelope{e:=events.NewEnvelope(events.EventSessionStreamChunk,env.TenantID,env.SessionID,env.TraceID,events.Producer{Service:"realtime-orchestrator",Instance:getenv("HOSTNAME","local")},map[string]any{"stream_id":id,"sequence":seq,"content":content,"content_type":"text/plain","agent_id":"router-agent","is_thinking":thinking,"status":status,"request_id":reqID});e.EventID,e.MessageID,e.ActorID=id,env.MessageID,env.ActorID;e.Routing=&events.Routing{Channel:"stream",PartitionKey:env.SessionID,Priority:p};return e}
func sib(t events.EventType,base events.Envelope,id,msg,status string)events.Envelope{e:=events.NewEnvelope(t,base.TenantID,base.SessionID,base.TraceID,events.Producer{Service:"realtime-orchestrator",Instance:getenv("HOSTNAME","local")},map[string]any{"stream_id":base.Payload["stream_id"],"status":status});e.EventID,e.MessageID,e.ActorID=id,msg,base.ActorID;e.Routing=&events.Routing{Channel:"stream",PartitionKey:base.SessionID,Priority:events.PriorityNormal};return e}
func risky(env events.Envelope)(string,string,string,bool){c:=strings.ToLower(str(env.Payload,"content")+" "+js(env.Payload["metadata"]));switch{case has(c,[]string{"rm -rf","delete production","drop database","truncate table"}):return "shell","destructive shell/database intent detected","critical",true;case has(c,[]string{"docker compose","kubectl","terraform apply","deploy"}):return "shell","deployment or infrastructure command intent detected","high",true;case has(c,[]string{"read secrets","export token","access prod env"}):return "secrets","sensitive credential access intent detected","high",true;default:return "","","",false}}
func has(input string,xs []string)bool{for _,x:=range xs{if strings.Contains(input,x){return true}};return false}
func str(m map[string]any,k string)string{v,ok:=m[k];if !ok||v==nil{return ""};if x,ok:=v.(string);ok{return x};return fmt.Sprintf("%v",v)}
func js(v any)string{if v==nil{return ""};b,err:=json.Marshal(v);if err!=nil{return ""};return string(b)}
func pick(a,b string)string{if a!=""{return a};return b}
func getenv(k,d string)string{if v:=os.Getenv(k);v!=""{return v};return d}

// parseMaxConcurrent 解析 DEEPSEARCH_MAX_CONCURRENT 环境变量，默认 50，最小 1。
func parseMaxConcurrent(raw string) int {
	var n int
	if _, err := fmt.Sscanf(raw, "%d", &n); err == nil && n > 0 {
		return n
	}
	return 50
}

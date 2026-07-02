package main

import (
	"context"
	"fmt"
	"strings"
	"time"

	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
	"github.com/agenthub/platform/shared/obs"
)

// AgentRole identifies a specialized agent in the multi-agent system. Each role
// owns a subset of ReAct stages and is dispatched via agent.runtime.dispatch
// with role-specific pool and tool_name parameters.
type AgentRole string

const (
	RoleRouter     AgentRole = "router-agent"
	RolePlanner    AgentRole = "planner-agent"
	RoleExecutor   AgentRole = "executor-agent"
	RoleCritic     AgentRole = "critic-agent"
	RoleSummarizer AgentRole = "summarizer-agent"
	RoleSearch     AgentRole = "search-agent"
)

// AgentDescriptor describes a role's capabilities, model pool, and the ReAct
// stages it owns. The orchestrator uses this to dispatch the right agent for
// each stage and to advertise the agent roster via /agents endpoint.
type AgentDescriptor struct {
	Role         AgentRole  `json:"role"`
	DisplayName  string     `json:"display_name"`
	Description  string     `json:"description"`
	ModelPool    string     `json:"model_pool"`     // e.g. "llm-online", "llm-fast", "llm-reasoning"
	OwnsStages   []ReactState `json:"owns_stages"`  // ReAct stages this agent leads
	Capabilities []string   `json:"capabilities"`
	SystemPrompt string     `json:"system_prompt"` // abbreviated prompt; full prompt lives in agent-runtime config
}

// agentRegistry is the canonical roster. The mapping of ReAct stages to agent
// roles mirrors platform/react_deepsearch_flow.json.
var agentRegistry = map[AgentRole]AgentDescriptor{
	RoleRouter: {
		Role:        RoleRouter,
		DisplayName: "Router",
		Description: "Classifies incoming messages and decides the execution flow (simple answer, retrieval-augmented, or multi-step plan).",
		ModelPool:   "llm-fast",
		OwnsStages:  []ReactState{StateClassify},
		Capabilities: []string{
			"intent classification",
			"flow routing (simple/retrieval/multi-step)",
			"route keyword matching (#route: / #路线:)",
		},
		SystemPrompt: "You are the Router agent. Analyze the user message and classify it as: simple (greeting/ack), retrieval (needs knowledge lookup), or multi-step (requires planning). Output a JSON route decision.",
	},
	RolePlanner: {
		Role:        RolePlanner,
		DisplayName: "Planner",
		Description: "Creates an execution plan: which tools to call, which agents to invoke, and in what order.",
		ModelPool:   "llm-reasoning",
		OwnsStages:  []ReactState{StatePlan},
		Capabilities: []string{
			"task decomposition",
			"tool selection",
			"dependency ordering",
			"budget allocation",
		},
		SystemPrompt: "You are the Planner agent. Given the user message and any retrieved context, produce an execution plan as a JSON list of steps. Each step has: agent, tool, arguments, depends_on.",
	},
	RoleExecutor: {
		Role:        RoleExecutor,
		DisplayName: "Executor",
		Description: "Executes tools and model calls according to the plan. Handles tool-call parsing and result aggregation.",
		ModelPool:   "llm-online",
		OwnsStages:  []ReactState{StateAct},
		Capabilities: []string{
			"tool call execution",
			"model invocation",
			"result formatting",
			"streaming output",
		},
		SystemPrompt: "You are the Executor agent. Execute the planned tool calls and model invocations. Return results as structured observations for the Critic.",
	},
	RoleCritic: {
		Role:        RoleCritic,
		DisplayName: "Critic",
		Description: "Evaluates execution results, checks for completeness, and advises whether to continue (loop) or finish.",
		ModelPool:   "llm-reasoning",
		OwnsStages:  []ReactState{StateReflect, StateContinueOrFinish},
		Capabilities: []string{
			"result quality assessment",
			"gap detection",
			"continuation decision",
			"budget-aware stopping",
		},
		SystemPrompt: "You are the Critic agent. Evaluate the observations. Decide: continue (more retrieval/planning needed) or finish (synthesize answer). Consider budget remaining.",
	},
	RoleSummarizer: {
		Role:        RoleSummarizer,
		DisplayName: "Summarizer",
		Description: "Synthesizes the final grounded answer from retrieval results and execution observations.",
		ModelPool:   "llm-online",
		OwnsStages:  []ReactState{StateSynthesize},
		Capabilities: []string{
			"answer synthesis",
			"citation grounding",
			"evidence aggregation",
			"streaming delivery",
		},
		SystemPrompt: "You are the Summarizer agent. Synthesize a grounded answer from the retrieved evidence and observations. Include citations where applicable.",
	},
	RoleSearch: {
		Role:        RoleSearch,
		DisplayName: "Search",
		Description: "Handles DeepSearch: query rewrite, multi-hop decomposition, hybrid retrieval, rerank, and citation grounding.",
		ModelPool:   "llm-fast",
		OwnsStages:  []ReactState{StateRetrieve},
		Capabilities: []string{
			"query rewriting",
			"multi-hop decomposition",
			"hybrid retrieval (BM25 + dense)",
			"rerank",
			"citation grounding",
		},
		SystemPrompt: "You are the Search agent. Rewrite the query, decompose into sub-queries, retrieve from hybrid sources, rerank, and ground citations.",
	},
}

// stageOwner maps a ReAct state to the agent role that owns it. Stages not
// owned by any agent (ingest, observe, stream_back) are handled by the
// orchestrator itself.
var stageOwner = map[ReactState]AgentRole{
	StateClassify:         RoleRouter,
	StateRetrieve:         RoleSearch,
	StatePlan:             RolePlanner,
	StateAct:              RoleExecutor,
	StateReflect:          RoleCritic,
	StateContinueOrFinish: RoleCritic,
	StateSynthesize:       RoleSummarizer,
}

// AgentForStage returns the agent role that owns the given ReAct stage, or
// empty string if the stage is orchestrator-owned.
func AgentForStage(stage ReactState) AgentRole {
	return stageOwner[stage]
}

// stageOwnerMap returns a string-keyed copy of stageOwner for JSON serialization.
func stageOwnerMap() map[string]string {
	out := make(map[string]string, len(stageOwner))
	for stage, role := range stageOwner {
		out[string(stage)] = string(role)
	}
	return out
}

// ListAgents returns all registered agent descriptors as a slice.
func ListAgents() []AgentDescriptor {
	roles := []AgentRole{RoleRouter, RolePlanner, RoleExecutor, RoleCritic, RoleSummarizer, RoleSearch}
	out := make([]AgentDescriptor, 0, len(roles))
	for _, r := range roles {
		out = append(out, agentRegistry[r])
	}
	return out
}

// dispatchAgent publishes an agent.runtime.dispatch event addressed to the
// agent that owns the current ReAct stage. The agent-runtime-control-plane
// picks this up and forwards it to the appropriate model-adapter pool.
func (m *ReactMachine) dispatchAgent(ctx context.Context, env events.Envelope, stage ReactState, input string) {
	role := AgentForStage(stage)
	if role == "" {
		return // orchestrator-owned stage, no agent dispatch
	}
	desc, ok := agentRegistry[role]
	if !ok {
		return
	}
	evt := events.NewEnvelope(
		events.EventAgentRuntimeDispatch,
		env.TenantID,
		env.SessionID,
		env.TraceID,
		events.Producer{Service: "realtime-orchestrator", Instance: m.instance},
		map[string]any{
			"runtime_id":   fmt.Sprintf("rt-%s-%s", role, env.EventID),
			"pool":         desc.ModelPool,
			"tool_name":    string(role),
			"reason":       fmt.Sprintf("react-stage:%s", stage),
			"input":        input,
			"agent_role":   string(role),
			"system_prompt": desc.SystemPrompt,
			"stage":        string(stage),
		},
	)
	evt.EventID = fmt.Sprintf("dispatch-%s-%s", role, env.EventID)
	evt.MessageID = env.MessageID
	evt.ActorID = env.ActorID
	evt.Routing = &events.Routing{Channel: "runtime", PartitionKey: env.SessionID, Priority: events.PriorityNormal}

	publishCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	if err := m.bus.PublishEnvelope(publishCtx, eventbus.AgentRuntimeDispatchSubject, evt); err == nil {
		obs.IncEventPublished("realtime-orchestrator", string(evt.EventType))
	}
}

// routeMessage is the Router agent's classification logic. For the initial
// landing this is a keyword heuristic; P1-3 will replace it with a real LLM
// call via model-adapter. Returns the route decision and whether retrieval
// should be skipped.
type RouteDecision struct {
	Flow           string `json:"flow"`            // "simple", "retrieval", "multi-step"
	SkipRetrieval  bool   `json:"skip_retrieval"`
	Confidence     float64 `json:"confidence"`
	MatchedRoute   string `json:"matched_route,omitempty"`
}

func routeMessage(content string) RouteDecision {
	lc := strings.ToLower(content)

	// Explicit route tokens: #route:name or #路线:name
	if strings.Contains(lc, "#route:") || strings.Contains(content, "#路线:") || strings.Contains(content, "@路线:") {
		return RouteDecision{Flow: "multi-step", SkipRetrieval: false, Confidence: 1.0, MatchedRoute: "explicit"}
	}

	// Multi-step indicators
	multiStepKeywords := []string{"分析", "设计", "实现", "重构", "调试", "计划", "方案", "compare", "design", "implement", "refactor", "plan"}
	for _, kw := range multiStepKeywords {
		if strings.Contains(lc, kw) {
			return RouteDecision{Flow: "multi-step", SkipRetrieval: false, Confidence: 0.8, MatchedRoute: "keyword:" + kw}
		}
	}

	// Retrieval indicators
	retrievalKeywords := []string{"文档", "知识", "搜索", "查找", "检索", "document", "search", "find", "lookup", "knowledge"}
	for _, kw := range retrievalKeywords {
		if strings.Contains(lc, kw) {
			return RouteDecision{Flow: "retrieval", SkipRetrieval: false, Confidence: 0.8, MatchedRoute: "keyword:" + kw}
		}
	}

	// Simple: short messages, greetings, acknowledgments
	if len(content) < 20 {
		return RouteDecision{Flow: "simple", SkipRetrieval: true, Confidence: 0.9}
	}
	simplePrefixes := []string{"hello", "hi ", "hey", "thanks", "ok", "yes", "no", "ping", "test", "你好", "谢谢", "好的"}
	for _, p := range simplePrefixes {
		if strings.HasPrefix(lc, p) {
			return RouteDecision{Flow: "simple", SkipRetrieval: true, Confidence: 0.9}
		}
	}

	// Default: retrieval-augmented
	return RouteDecision{Flow: "retrieval", SkipRetrieval: false, Confidence: 0.5}
}

package main

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/agenthub/platform/shared/eventbus"
	"github.com/agenthub/platform/shared/events"
	"github.com/agenthub/platform/shared/obs"
	"github.com/agenthub/platform/shared/state"
)

// ReactState is a single stage in the ReAct loop. The ordering mirrors
// platform/react_deepsearch_flow.json and is the canonical lifecycle every
// session message traverses.
type ReactState string

const (
	StateIngest           ReactState = "ingest"
	StateClassify         ReactState = "classify"
	StateRetrieve         ReactState = "retrieve"
	StatePlan             ReactState = "plan"
	StateAct              ReactState = "act"
	StateObserve          ReactState = "observe"
	StateReflect          ReactState = "reflect"
	StateContinueOrFinish ReactState = "continue_or_finish"
	StateSynthesize       ReactState = "synthesize"
	StateStreamBack       ReactState = "stream_back"
	StateFinished         ReactState = "finished"
	StateFailed           ReactState = "failed"
	StateBudgetExceeded   ReactState = "budget_exceeded"
)

// reactOrder is the linear sequence for the first pass. After reflect the
// machine may loop back to retrieve/plan (multi-round) or proceed to synthesize.
var reactOrder = []ReactState{
	StateIngest, StateClassify, StateRetrieve, StatePlan, StateAct,
	StateObserve, StateReflect, StateContinueOrFinish, StateSynthesize, StateStreamBack,
}

// validTransitions defines which states may follow a given state. The
// continue_or_finish state is the branch point: it either loops back to
// retrieve (more search needed), plan (replan), or proceeds to synthesize.
var validTransitions = map[ReactState][]ReactState{
	StateIngest:           {StateClassify},
	StateClassify:         {StateRetrieve, StatePlan}, // simple queries skip retrieval
	StateRetrieve:         {StatePlan},
	StatePlan:             {StateAct},
	StateAct:              {StateObserve},
	StateObserve:          {StateReflect},
	StateReflect:          {StateContinueOrFinish},
	StateContinueOrFinish: {StateRetrieve, StatePlan, StateSynthesize},
	StateSynthesize:       {StateStreamBack},
	StateStreamBack:       {StateFinished},
	StateFinished:         {},
	StateFailed:           {StateIngest}, // retry from the top
	StateBudgetExceeded:   {},
}

// Budgets caps a single ReAct run. Defaults come from
// platform/react_deepsearch_flow.json. Overridable via env for testing.
type Budgets struct {
	MaxSteps          int           `json:"max_steps"`
	MaxOnlineTime     time.Duration `json:"max_online_time_ms"`
	MaxToolRounds     int           `json:"max_tool_rounds"`
	MaxRetrievalRound int           `json:"max_retrieval_rounds"`
}

func DefaultBudgets() Budgets {
	return Budgets{
		MaxSteps:          12,
		MaxOnlineTime:     15 * time.Second,
		MaxToolRounds:     4,
		MaxRetrievalRound: 3,
	}
}

// ToolCall records a single tool invocation for audit and budget tracking.
type ToolCall struct {
	ToolName  string    `json:"tool_name"`
	Stage     ReactState `json:"stage"`
	Timestamp time.Time `json:"timestamp"`
	Result    string    `json:"result,omitempty"`
}

// ReactRun is the live state of one ReAct execution for a session. It is
// persisted to Redis as a hash under session:state:{tenant}:{session} so any
// orchestrator replica can resume after a failover.
type ReactRun struct {
	TenantID       string     `json:"tenant_id"`
	SessionID      string     `json:"session_id"`
	TraceID        string     `json:"trace_id"`
	MessageID      string     `json:"message_id"`
	CurrentState   ReactState `json:"current_state"`
	PreviousState  ReactState `json:"previous_state"`
	StepCount      int        `json:"step_count"`
	ToolRounds     int        `json:"tool_rounds"`
	RetrievalRound int        `json:"retrieval_rounds"`
	StartedAt      time.Time  `json:"started_at"`
	UpdatedAt      time.Time  `json:"updated_at"`
	ToolHistory    []ToolCall `json:"tool_history"`
	Budgets        Budgets    `json:"budgets"`
	ContinueDecision string   `json:"continue_decision,omitempty"` // "continue" or "finish"
	FailureReason    string   `json:"failure_reason,omitempty"`
}

// ReactMachine drives the state machine for a session. It owns the Redis store
// for persistence and the eventbus for publishing transition events.
type ReactMachine struct {
	store   *state.Store
	bus     *eventbus.Client
	budgets Budgets
	instance string
}

func NewReactMachine(store *state.Store, bus *eventbus.Client, budgets Budgets) *ReactMachine {
	return &ReactMachine{
		store:    store,
		bus:      bus,
		budgets:  budgets,
		instance: getenv("HOSTNAME", "local"),
	}
}

// stateKey is the Redis key under which the ReactRun hash is persisted.
func stateKey(tenantID, sessionID string) string {
	return fmt.Sprintf("session:state:%s:%s", tenantID, sessionID)
}

// Begin starts a new ReAct run for the session. It overwrites any prior run
// (a session has at most one active run at a time) and publishes the initial
// ingest transition.
func (m *ReactMachine) Begin(ctx context.Context, env events.Envelope) (*ReactRun, error) {
	run := &ReactRun{
		TenantID:      env.TenantID,
		SessionID:     env.SessionID,
		TraceID:       env.TraceID,
		MessageID:     env.MessageID,
		CurrentState:  StateIngest,
		PreviousState: "",
		StepCount:     1,
		StartedAt:     time.Now().UTC(),
		UpdatedAt:     time.Now().UTC(),
		ToolHistory:   []ToolCall{},
		Budgets:       m.budgets,
	}
	if err := m.persist(ctx, run); err != nil {
		return nil, fmt.Errorf("persist react run: %w", err)
	}
	m.publishTransition(ctx, env, "", StateIngest, run)
	return run, nil
}

// Advance moves the run to the next state, enforcing valid transitions and
// budget caps. It returns the updated run and an error if the transition is
// invalid or budget is exceeded.
func (m *ReactMachine) Advance(ctx context.Context, env events.Envelope, target ReactState, reason string) (*ReactRun, error) {
	run, err := m.Load(ctx, env.TenantID, env.SessionID)
	if err != nil {
		return nil, fmt.Errorf("load react run: %w", err)
	}
	if run == nil {
		return nil, fmt.Errorf("no active react run for session %s", env.SessionID)
	}

	// Budget check before any transition.
	if exceeded, bReason := m.checkBudget(run); exceeded {
		run.PreviousState = run.CurrentState
		run.CurrentState = StateBudgetExceeded
		run.FailureReason = bReason
		run.UpdatedAt = time.Now().UTC()
		_ = m.persist(ctx, run)
		m.publishTransition(ctx, env, run.PreviousState, StateBudgetExceeded, run)
		return run, fmt.Errorf("budget exceeded: %s", bReason)
	}

	// Validate transition.
	allowed, ok := validTransitions[run.CurrentState]
	if !ok {
		return run, fmt.Errorf("unknown current state: %s", run.CurrentState)
	}
	valid := false
	for _, s := range allowed {
		if s == target {
			valid = true
			break
		}
	}
	if !valid {
		return run, fmt.Errorf("invalid transition: %s -> %s", run.CurrentState, target)
	}

	run.PreviousState = run.CurrentState
	run.CurrentState = target
	run.StepCount++
	run.UpdatedAt = time.Now().UTC()

	// Track retrieval and tool rounds for budget enforcement.
	if target == StateRetrieve {
		run.RetrievalRound++
	}
	if target == StateAct {
		run.ToolRounds++
	}

	if err := m.persist(ctx, run); err != nil {
		return run, fmt.Errorf("persist react run: %w", err)
	}
	m.publishTransition(ctx, env, run.PreviousState, target, run)
	return run, nil
}

// RecordToolCall appends a tool invocation to the run's history. Called by the
// act/observe stages.
func (m *ReactMachine) RecordToolCall(ctx context.Context, tenantID, sessionID string, call ToolCall) error {
	run, err := m.Load(ctx, tenantID, sessionID)
	if err != nil || run == nil {
		return err
	}
	run.ToolHistory = append(run.ToolHistory, call)
	run.UpdatedAt = time.Now().UTC()
	return m.persist(ctx, run)
}

// Fail marks the run as failed.
func (m *ReactMachine) Fail(ctx context.Context, env events.Envelope, reason string) (*ReactRun, error) {
	run, err := m.Load(ctx, env.TenantID, env.SessionID)
	if err != nil || run == nil {
		return nil, err
	}
	run.PreviousState = run.CurrentState
	run.CurrentState = StateFailed
	run.FailureReason = reason
	run.UpdatedAt = time.Now().UTC()
	_ = m.persist(ctx, run)
	m.publishTransition(ctx, env, run.PreviousState, StateFailed, run)
	return run, nil
}

// Load reads the current ReactRun from Redis. Returns nil without error if no
// run exists for the session.
func (m *ReactMachine) Load(ctx context.Context, tenantID, sessionID string) (*ReactRun, error) {
	if m.store == nil {
		return nil, nil
	}
	data, err := m.store.GetString(ctx, stateKey(tenantID, sessionID))
	if err != nil {
		if strings.Contains(err.Error(), "nil") {
			return nil, nil
		}
		return nil, err
	}
	if data == "" {
		return nil, nil
	}
	var run ReactRun
	if err := json.Unmarshal([]byte(data), &run); err != nil {
		return nil, fmt.Errorf("unmarshal react run: %w", err)
	}
	return &run, nil
}

// persist writes the run to Redis with a 1-hour TTL. A session that is idle for
// an hour will have its react state expire, which is acceptable — the terminal
// state is already persisted in PostgreSQL via audit-log-service.
func (m *ReactMachine) persist(ctx context.Context, run *ReactRun) error {
	if m.store == nil {
		return nil
	}
	data, err := json.Marshal(run)
	if err != nil {
		return err
	}
	return m.store.PutJSON(ctx, stateKey(run.TenantID, run.SessionID), string(data), time.Hour)
}

// checkBudget returns true if any budget cap has been exceeded.
func (m *ReactMachine) checkBudget(run *ReactRun) (bool, string) {
	if run.StepCount >= run.Budgets.MaxSteps {
		return true, fmt.Sprintf("step budget exhausted (%d/%d)", run.StepCount, run.Budgets.MaxSteps)
	}
	if time.Since(run.StartedAt) > run.Budgets.MaxOnlineTime {
		return true, fmt.Sprintf("online time budget exceeded (%s/%s)", time.Since(run.StartedAt), run.Budgets.MaxOnlineTime)
	}
	if run.ToolRounds >= run.Budgets.MaxToolRounds {
		return true, fmt.Sprintf("tool round budget exhausted (%d/%d)", run.ToolRounds, run.Budgets.MaxToolRounds)
	}
	if run.RetrievalRound >= run.Budgets.MaxRetrievalRound {
		return true, fmt.Sprintf("retrieval round budget exhausted (%d/%d)", run.RetrievalRound, run.Budgets.MaxRetrievalRound)
	}
	return false, ""
}

// publishTransition emits an agent.react.transition event so downstream
// services (stream-delivery, audit-log) can react to state changes.
func (m *ReactMachine) publishTransition(ctx context.Context, env events.Envelope, from, to ReactState, run *ReactRun) {
	evt := events.NewEnvelope(
		events.EventAgentReactTransition,
		env.TenantID,
		env.SessionID,
		env.TraceID,
		events.Producer{Service: "realtime-orchestrator", Instance: m.instance},
		map[string]any{
			"from_state":       string(from),
			"to_state":         string(to),
			"step_count":       run.StepCount,
			"tool_rounds":      run.ToolRounds,
			"retrieval_rounds": run.RetrievalRound,
			"message_id":       run.MessageID,
			"budgets": map[string]any{
				"max_steps":           run.Budgets.MaxSteps,
				"max_online_time_ms":  run.Budgets.MaxOnlineTime.Milliseconds(),
				"max_tool_rounds":     run.Budgets.MaxToolRounds,
				"max_retrieval_rounds": run.Budgets.MaxRetrievalRound,
			},
		},
	)
	evt.EventID = fmt.Sprintf("react-%s-%s-%d", env.SessionID, to, run.StepCount)
	evt.MessageID = env.MessageID
	evt.ActorID = env.ActorID
	evt.Routing = &events.Routing{Channel: "react", PartitionKey: env.SessionID, Priority: events.PriorityNormal}

	publishCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	if err := m.bus.PublishEnvelope(publishCtx, eventbus.StreamEventsSubject, evt); err == nil {
		obs.IncEventPublished("realtime-orchestrator", string(evt.EventType))
	}
}

// runReactLoop drives a session message through the full ReAct lifecycle. This
// is the synchronous happy path: ingest → classify → retrieve → plan → act →
// observe → reflect → continue_or_finish → synthesize → stream_back → finished.
//
// In production the act/retrieve stages dispatch to async services (agent-runtime,
// retrieval-core) and the loop would be event-driven. For the initial landing
// we run the full loop synchronously with placeholder stage outputs so the
// state machine, budget enforcement, and transition events are exercised
// end-to-end. P1-3 will replace the act stage with a real model-adapter call.
func (m *ReactMachine) runReactLoop(ctx context.Context, env events.Envelope) error {
	// Stage 1: ingest — begin the run.
	run, err := m.Begin(ctx, env)
	if err != nil {
		return err
	}

	// Stage 2: classify — Router agent classifies the message and decides the
	// execution flow. For the initial landing this is a keyword heuristic;
	// P1-3 will replace it with a real LLM call via model-adapter.
	content := str(env.Payload, "content")
	decision := routeMessage(content)
	if run, err = m.Advance(ctx, env, StateClassify, fmt.Sprintf("classify: flow=%s confidence=%.2f", decision.Flow, decision.Confidence)); err != nil {
		return err
	}
	_ = run
	m.dispatchAgent(ctx, env, StateClassify, content)

	// Stage 3 (conditional): retrieve — Search agent handles DeepSearch.
	if !decision.SkipRetrieval {
		if _, err = m.Advance(ctx, env, StateRetrieve, "retrieve: search agent deepsearch"); err != nil {
			return err
		}
		m.dispatchAgent(ctx, env, StateRetrieve, content)
	}

	// Stage 4: plan — Planner agent creates an execution plan.
	if _, err = m.Advance(ctx, env, StatePlan, "plan: planner agent creating execution plan"); err != nil {
		return err
	}
	m.dispatchAgent(ctx, env, StatePlan, content)

	// Stage 5: act — Executor agent runs tools and model calls.
	if _, err = m.Advance(ctx, env, StateAct, "act: executor agent dispatching tools"); err != nil {
		return err
	}
	m.dispatchAgent(ctx, env, StateAct, content)

	// Stage 6: observe — orchestrator collects runtime observations (no agent).
	if _, err = m.Advance(ctx, env, StateObserve, "observe: collecting runtime observations"); err != nil {
		return err
	}

	// Stage 7: reflect — Critic agent evaluates results.
	if _, err = m.Advance(ctx, env, StateReflect, "reflect: critic agent evaluating results"); err != nil {
		return err
	}
	m.dispatchAgent(ctx, env, StateReflect, content)

	// Stage 8: continue_or_finish — Critic agent decides whether to loop or
	// finish. For the synchronous landing we always finish on the first pass.
	// P1-3 will add real continuation logic driven by the critic's response.
	if _, err = m.Advance(ctx, env, StateContinueOrFinish, "continue_or_finish: critic decision (finish)"); err != nil {
		return err
	}
	m.dispatchAgent(ctx, env, StateContinueOrFinish, content)

	// Stage 9: synthesize — Summarizer agent builds the grounded answer.
	if _, err = m.Advance(ctx, env, StateSynthesize, "synthesize: summarizer agent building answer"); err != nil {
		return err
	}
	m.dispatchAgent(ctx, env, StateSynthesize, content)
	m.emitSynthesis(ctx, env)

	// Stage 10: stream_back — orchestrator emits the final stream output.
	if _, err = m.Advance(ctx, env, StateStreamBack, "stream_back: emitting aggregated output"); err != nil {
		return err
	}

	// Terminal: finished.
	if _, err = m.Advance(ctx, env, StateFinished, "finished: react loop complete"); err != nil {
		return err
	}
	return nil
}

// emitSynthesis publishes a stream chunk with the synthesized answer. In the
// synchronous landing this is a placeholder; P1-3 will stream real model output.
func (m *ReactMachine) emitSynthesis(ctx context.Context, env events.Envelope) {
	chunkEvt := chunk(env, "stream-synth-"+env.EventID, 1,
		"[synthesized response]", false, "synthesizing", "", events.PriorityNormal)
	flushEvt := sib(events.EventSessionStreamFlush, chunkEvt, chunkEvt.EventID+"-flush", env.MessageID, "flush")
	completeEvt := sib(events.EventSessionStreamComplete, chunkEvt, chunkEvt.EventID+"-complete", env.MessageID, "complete")

	publishCtx, cancel := context.WithTimeout(ctx, 3*time.Second)
	defer cancel()
	for _, e := range []events.Envelope{chunkEvt, flushEvt, completeEvt} {
		if err := m.bus.PublishEnvelope(publishCtx, eventbus.StreamEventsSubject, e); err == nil {
			obs.IncEventPublished("realtime-orchestrator", string(e.EventType))
		}
	}
}

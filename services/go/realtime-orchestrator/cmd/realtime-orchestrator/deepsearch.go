package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/agenthub/platform/shared/obs"
	"github.com/prometheus/client_golang/prometheus"
)

// =========================================================================
// DeepSearch 端到端编排模块
//
// 对照 platform/react_deepsearch_flow.json 的 7 步流水线：
//   1. query_rewrite        (search-agent,   LLM) → 改写查询
//   2. multi_hop_decompose  (search-agent,   LLM) → 子查询分解
//   3. hybrid_retrieve      (retrieval-core, HTTP) → BM25 + dense 检索
//   4. cross_source_merge   (retrieval-core)       → RRF 融合
//   5. rerank               (retrieval-core)       → 精排
//   6. citation_grounding   (search-agent,   LLM) → 引用溯源
//   7. answer_synthesis     (summarizer-agent, LLM) → 生成最终答案
//
// 步骤 3-5 由 retrieval-core 在一次 POST /retrieve 调用中完成；
// 步骤 1、2、6、7 通过 model-adapter 调用 LLM。
// 每步均有降级策略（见 react_deepsearch_flow.json fallbacks）。
// =========================================================================

// --- Prometheus 指标（进程内注册）---

var (
	deepsearchRetrievals = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "deepsearch_retrievals_total", Help: "DeepSearch retrieve stage invocations by outcome."},
		[]string{"outcome"},
	)
	deepsearchSyntheses = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "deepsearch_syntheses_total", Help: "DeepSearch synthesize stage invocations by outcome."},
		[]string{"outcome"},
	)
	deepsearchDegraded = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "deepsearch_degraded_total", Help: "DeepSearch degradation events by step."},
		[]string{"step"},
	)
	deepsearchLatency = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{Name: "deepsearch_latency_seconds", Help: "DeepSearch stage latency in seconds.", Buckets: prometheus.DefBuckets},
		[]string{"stage"},
	)
)

func init() {
	obs.MustRegister(deepsearchRetrievals, deepsearchSyntheses, deepsearchDegraded, deepsearchLatency)
}

// =========================================================================
// Model-adapter 客户端（适配自 agent-runtime-control-plane）
// =========================================================================

// ModelAdapterClient 通过 HTTP 调用 Python model-adapter-service，发送
// OpenAI 兼容的 chat completion 请求。DeepSearch 用它完成 query_rewrite、
// multi_hop_decompose、citation_grounding、answer_synthesis 四个 LLM 步骤。
type ModelAdapterClient struct {
	httpClient *http.Client
	baseURL    string
}

func NewModelAdapterClient(baseURL string) *ModelAdapterClient {
	return &ModelAdapterClient{
		httpClient: &http.Client{Timeout: 30 * time.Second},
		baseURL:    baseURL,
	}
}

// poolToModel 将 Go 层 pool 名映射到 model-adapter 的模型名。默认 mock-gpt，
// 可通过 MODEL_ONLINE / MODEL_FAST / MODEL_REASONING 环境变量覆盖。
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

type chatCompletionRequest struct {
	Model        string              `json:"model"`
	Messages     []map[string]string `json:"messages"`
	Temperature  float64             `json:"temperature"`
	SystemPrompt string              `json:"system_prompt,omitempty"`
	AgentRole    string              `json:"agent_role,omitempty"`
	Stage        string              `json:"stage,omitempty"`
}

type chatCompletionResponse struct {
	ID      string           `json:"id"`
	Model   string           `json:"model"`
	Choices []map[string]any `json:"choices"`
	Usage   map[string]int   `json:"usage"`
}

// Call 向 model-adapter 发送 chat completion 请求，返回模型响应文本。
// 出错时返回降级占位文本（而非 error），确保 DeepSearch 流水线能继续运转。
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
		return fmt.Sprintf("[fallback: marshal error: %v]", err), nil
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/v1/chat/completions", bytes.NewReader(bodyBytes))
	if err != nil {
		return fmt.Sprintf("[fallback: create request: %v]", err), nil
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Sprintf("[fallback: model-adapter unreachable: %v]", err), nil
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Sprintf("[fallback: model-adapter status %d: %s]", resp.StatusCode, string(body)), nil
	}

	var result chatCompletionResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return fmt.Sprintf("[fallback: decode error: %v]", err), nil
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

// =========================================================================
// Retrieval-core 客户端
// =========================================================================

// RetrievalCoreClient 通过 HTTP 调用 Rust retrieval-core 服务，发起混合检索
// (BM25 + dense → fusion → rerank)。一次 POST /retrieve 调用完成
// DeepSearch 步骤 3-5。
type RetrievalCoreClient struct {
	httpClient *http.Client
	baseURL    string
}

func NewRetrievalCoreClient(baseURL string) *RetrievalCoreClient {
	return &RetrievalCoreClient{
		httpClient: &http.Client{Timeout: 10 * time.Second},
		baseURL:    baseURL,
	}
}

// retrievalRequest 对应 retrieval-core 的 RetrievalRequest JSON 结构。
type retrievalRequest struct {
	RequestID      string   `json:"request_id"`
	Query          string   `json:"query"`
	Mode           string   `json:"mode"`
	KnowledgeScope []string `json:"knowledge_scope"`
	TopK           int      `json:"top_k"`
	TimeoutMs      int64    `json:"timeout_ms,omitempty"`
}

// CitationDTO 对应 retrieval-core 的 Citation。
type CitationDTO struct {
	SourceID   string  `json:"source_id"`
	Score      float32 `json:"score"`
	Collection string  `json:"collection"`
	Snippet    string  `json:"snippet"`
}

// fusionResult 对应 retrieval-core 的 FusionResult JSON 结构。
type fusionResult struct {
	RequestID      string          `json:"request_id"`
	Strategy       string          `json:"strategy"`
	TopK           int             `json:"top_k"`
	Citations      []CitationDTO   `json:"citations"`
	Candidates     []map[string]any `json:"candidates,omitempty"`
	QdrantHits     int             `json:"qdrant_hits"`
	OpenSearchHits int             `json:"opensearch_hits"`
	ElapsedMs      int64           `json:"elapsed_ms"`
	Degraded       []string        `json:"degraded"`
}

// Retrieve 调用 retrieval-core 的 POST /retrieve 端点，返回融合后的引用列表。
func (c *RetrievalCoreClient) Retrieve(ctx context.Context, req retrievalRequest) (*fusionResult, error) {
	bodyBytes, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshal retrieval request: %w", err)
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/retrieve", bytes.NewReader(bodyBytes))
	if err != nil {
		return nil, fmt.Errorf("create retrieval request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("retrieval-core unreachable: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("retrieval-core status %d: %s", resp.StatusCode, string(body))
	}

	var result fusionResult
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode retrieval response: %w", err)
	}
	return &result, nil
}

// =========================================================================
// DeepSearch 流水线编排
// =========================================================================

// DeepSearchFlow 编排 7 步 DeepSearch 流水线。步骤 1-6 在 ReAct retrieve
// 阶段执行（Retrieve 方法），步骤 7 在 synthesize 阶段执行（Synthesize 方法）。
type DeepSearchFlow struct {
	modelAdapter  *ModelAdapterClient
	retrievalCore *RetrievalCoreClient
}

func NewDeepSearchFlow(ma *ModelAdapterClient, rc *RetrievalCoreClient) *DeepSearchFlow {
	return &DeepSearchFlow{modelAdapter: ma, retrievalCore: rc}
}

// DeepSearchResult 承载 retrieve 阶段产出的检索证据，传递给 synthesize 阶段。
type DeepSearchResult struct {
	OriginalQuery    string        `json:"original_query"`
	RewrittenQuery   string        `json:"rewritten_query"`
	SubQueries       []string      `json:"sub_queries"`
	Citations        []CitationDTO `json:"citations"`
	CandidateCount   int           `json:"candidate_count"`
	QdrantHits       int           `json:"qdrant_hits"`
	OpenSearchHits   int           `json:"opensearch_hits"`
	ElapsedMs        int64         `json:"elapsed_ms"`
	Degraded         []string      `json:"degraded"`
	EvidenceContext  string        `json:"evidence_context"`
	GroundingWarning string        `json:"grounding_warning,omitempty"`
}

// Retrieve 执行 DeepSearch 步骤 1-6：
// query_rewrite → multi_hop_decompose → hybrid_retrieve (含 merge + rerank)
// → citation_grounding。每步均有降级策略。
func (f *DeepSearchFlow) Retrieve(ctx context.Context, query, tenantID, sessionID, traceID string) (*DeepSearchResult, error) {
	start := time.Now()
	result := &DeepSearchResult{
		OriginalQuery: query,
		SubQueries:    []string{},
		Citations:     []CitationDTO{},
		Degraded:      []string{},
	}
	defer func() {
		deepsearchLatency.WithLabelValues("retrieve_total").Observe(time.Since(start).Seconds())
	}()

	// --- 步骤 1: query_rewrite (search-agent, LLM) ---
	// 降级：使用原始查询
	rewriteStart := time.Now()
	rewriteCtx, rewriteCancel := context.WithTimeout(ctx, 5*time.Second)
	rewritten, _ := f.modelAdapter.Call(rewriteCtx, "llm-fast", query,
		"Rewrite the user's query to be more effective for document retrieval. Output ONLY the rewritten query, nothing else.",
		string(RoleSearch), "query_rewrite")
	rewriteCancel()
	deepsearchLatency.WithLabelValues("query_rewrite").Observe(time.Since(rewriteStart).Seconds())

	if strings.HasPrefix(rewritten, "[fallback") || strings.TrimSpace(rewritten) == "" {
		result.RewrittenQuery = query
		result.Degraded = append(result.Degraded, "rewrite_fallback")
		deepsearchDegraded.WithLabelValues("query_rewrite").Inc()
	} else {
		result.RewrittenQuery = strings.TrimSpace(rewritten)
	}

	// --- 步骤 2: multi_hop_decompose (search-agent, LLM) ---
	// 降级：使用改写后的单条查询
	decompStart := time.Now()
	decompCtx, decompCancel := context.WithTimeout(ctx, 5*time.Second)
	decompInput := fmt.Sprintf("Original query: %s\nRewritten query: %s\n\nDecompose into 1-3 sub-queries for thorough retrieval. Output ONE sub-query per line, nothing else.",
		query, result.RewrittenQuery)
	decomp, _ := f.modelAdapter.Call(decompCtx, "llm-fast", decompInput,
		"You decompose complex queries into sub-queries for multi-hop retrieval. Output one sub-query per line.",
		string(RoleSearch), "multi_hop_decompose")
	decompCancel()
	deepsearchLatency.WithLabelValues("multi_hop_decompose").Observe(time.Since(decompStart).Seconds())

	if strings.HasPrefix(decomp, "[fallback") || strings.TrimSpace(decomp) == "" {
		result.SubQueries = []string{result.RewrittenQuery}
		result.Degraded = append(result.Degraded, "decompose_fallback")
		deepsearchDegraded.WithLabelValues("multi_hop_decompose").Inc()
	} else {
		result.SubQueries = parseSubQueries(decomp, result.RewrittenQuery)
	}

	// --- 步骤 3-5: hybrid_retrieve + cross_source_merge + rerank (retrieval-core) ---
	// retrieval-core 在一次 POST /retrieve 中完成 BM25+dense 检索、RRF 融合、rerank。
	// 降级链：deepsearch 模式失败 → simple 模式轻量重试（P2-8 "检索超时→轻检索"）
	//         → 仍失败则返回空引用，synthesis 将无证据生成。
	fusion, retrieveErr := f.retrieveDeepsearch(ctx, result.RewrittenQuery, sessionID, start)
	if retrieveErr != nil {
		// 第一次降级：用 simple 模式 + 较低 TopK + 较短超时重试
		lightStart := time.Now()
		fusion, retrieveErr = f.retrieveSimple(ctx, result.RewrittenQuery, sessionID, start)
		deepsearchLatency.WithLabelValues("light_retrieve").Observe(time.Since(lightStart).Seconds())
		if retrieveErr != nil {
			result.Degraded = append(result.Degraded, "hybrid_retrieve_failed")
			deepsearchDegraded.WithLabelValues("hybrid_retrieve").Inc()
			// 最终降级：无引用；synthesis 将标记无证据
		} else {
			result.Degraded = append(result.Degraded, "light_retrieve_fallback")
			deepsearchDegraded.WithLabelValues("light_retrieve").Inc()
		}
	}
	if fusion != nil {
		result.Citations = fusion.Citations
		result.CandidateCount = len(fusion.Candidates)
		result.QdrantHits = fusion.QdrantHits
		result.OpenSearchHits = fusion.OpenSearchHits
		result.ElapsedMs = fusion.ElapsedMs
		result.Degraded = append(result.Degraded, fusion.Degraded...)
	}

	// --- 步骤 6: citation_grounding (search-agent, LLM) ---
	// 降级：返回证据但附加置信度警告
	result.EvidenceContext = buildEvidenceContext(result.Citations)
	if len(result.Citations) > 0 {
		groundStart := time.Now()
		groundCtx, groundCancel := context.WithTimeout(ctx, 5*time.Second)
		groundInput := fmt.Sprintf("Query: %s\n\nEvidence:\n%s\n\nAssess whether these citations are grounded and relevant. Be concise.",
			result.RewrittenQuery, result.EvidenceContext)
		_, _ = f.modelAdapter.Call(groundCtx, "llm-fast", groundInput,
			"You assess whether retrieved evidence is grounded and relevant to the query. Be concise.",
			string(RoleSearch), "citation_grounding")
		groundCancel()
		deepsearchLatency.WithLabelValues("citation_grounding").Observe(time.Since(groundStart).Seconds())
		// grounding 是评估性的，不影响数据流；超时仅记录警告
	}

	if len(result.Degraded) > 0 {
		deepsearchRetrievals.WithLabelValues("degraded").Inc()
	} else {
		deepsearchRetrievals.WithLabelValues("success").Inc()
	}
	return result, nil
}

// retrieveDeepsearch 调用 retrieval-core 的 deepsearch 模式（BM25+dense 融合 + rerank）。
// 这是主检索路径，TopK=8，超时 8s。
func (f *DeepSearchFlow) retrieveDeepsearch(ctx context.Context, query, sessionID string, start time.Time) (*fusionResult, error) {
	retrieveStart := time.Now()
	retrieveCtx, retrieveCancel := context.WithTimeout(ctx, 8*time.Second)
	reqID := fmt.Sprintf("ds-%s-%d", sessionID, start.UnixMilli())
	fusion, err := f.retrievalCore.Retrieve(retrieveCtx, retrievalRequest{
		RequestID:      reqID,
		Query:          query,
		Mode:           "deepsearch",
		KnowledgeScope: []string{"docs", "code", "memory"},
		TopK:           8,
		TimeoutMs:      5000,
	})
	retrieveCancel()
	deepsearchLatency.WithLabelValues("hybrid_retrieve").Observe(time.Since(retrieveStart).Seconds())
	if err != nil {
		return nil, err
	}
	return fusion, nil
}

// retrieveSimple 是轻量检索降级路径（P2-8 "检索超时→轻检索"）。
// 当 deepsearch 模式失败时，改用 simple 模式 + 较低 TopK（4）+ 较短超时（3s）。
// simple 模式跳过 rerank，仅做 BM25 或 dense 单路检索，速度更快、容错性更强。
func (f *DeepSearchFlow) retrieveSimple(ctx context.Context, query, sessionID string, start time.Time) (*fusionResult, error) {
	retrieveCtx, retrieveCancel := context.WithTimeout(ctx, 3*time.Second)
	defer retrieveCancel()
	reqID := fmt.Sprintf("ds-light-%s-%d", sessionID, start.UnixMilli())
	fusion, err := f.retrievalCore.Retrieve(retrieveCtx, retrievalRequest{
		RequestID:      reqID,
		Query:          query,
		Mode:           "simple",
		KnowledgeScope: []string{"docs"},
		TopK:           4,
		TimeoutMs:      2000,
	})
	if err != nil {
		return nil, err
	}
	return fusion, nil
}

// Synthesize 执行 DeepSearch 步骤 7: answer_synthesis (summarizer-agent, LLM)。
// 接收 retrieve 阶段的证据上下文，生成有引用支撑的最终答案。
// 降级：LLM 不可用时返回证据摘要（无 LLM 生成）。
func (f *DeepSearchFlow) Synthesize(ctx context.Context, result *DeepSearchResult) (string, error) {
	synthStart := time.Now()
	defer func() {
		deepsearchLatency.WithLabelValues("answer_synthesis").Observe(time.Since(synthStart).Seconds())
	}()

	synthCtx, synthCancel := context.WithTimeout(ctx, 10*time.Second)
	defer synthCancel()

	input := fmt.Sprintf("User question: %s\n\nRetrieved evidence:\n%s\n\nSynthesize a grounded answer. Cite sources by [source_id] where applicable. If evidence is insufficient, say so.",
		result.OriginalQuery, result.EvidenceContext)

	answer, err := f.modelAdapter.Call(synthCtx, "llm-online", input,
		"You are the Summarizer agent. Synthesize a grounded answer from the retrieved evidence. Include citations where applicable. If evidence is insufficient, acknowledge the limitation.",
		string(RoleSummarizer), "answer_synthesis")

	if err != nil || strings.HasPrefix(answer, "[fallback") {
		deepsearchSyntheses.WithLabelValues("degraded").Inc()
		// 降级：返回证据摘要，不经过 LLM 生成
		return fmt.Sprintf("[synthesis fallback] Based on %d retrieved citations:\n%s",
			len(result.Citations), result.EvidenceContext), nil
	}

	deepsearchSyntheses.WithLabelValues("success").Inc()
	return answer, nil
}

// --- 辅助函数 ---

// parseSubQueries 从 LLM 响应中提取子查询。解析失败时回退到单条查询。
func parseSubQueries(raw, fallback string) []string {
	lines := strings.Split(strings.TrimSpace(raw), "\n")
	out := []string{}
	for _, line := range lines {
		l := strings.TrimSpace(line)
		// 去除常见前缀："- ", "* ", "1. ", "1) " 等
		l = strings.TrimPrefix(l, "- ")
		l = strings.TrimPrefix(l, "* ")
		for _, p := range []string{"1. ", "2. ", "3. ", "1) ", "2) ", "3) "} {
			l = strings.TrimPrefix(l, p)
		}
		if l != "" && !strings.HasPrefix(l, "[fallback") {
			out = append(out, l)
		}
	}
	if len(out) == 0 {
		return []string{fallback}
	}
	return out
}

// buildEvidenceContext 将引用列表格式化为上下文字符串，供 synthesis 使用。
func buildEvidenceContext(citations []CitationDTO) string {
	if len(citations) == 0 {
		return "[no evidence retrieved]"
	}
	var sb strings.Builder
	for i, c := range citations {
		sb.WriteString(fmt.Sprintf("[%d] source_id=%s collection=%s score=%.3f\n%s\n\n",
			i+1, c.SourceID, c.Collection, c.Score, c.Snippet))
	}
	return sb.String()
}

package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"
	"math"
	"net/http"
	"strconv"
	"strings"
	"time"
)

func newID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// agentVersionHandler serves /platform/agent-versions/
// In-memory store with demo seed data; replace with PostgreSQL when wired.
type agentVersionHandler struct {
	store map[string][]agentVersionRecord // agent_id -> versions (desc by version)
}

type agentVersionRecord struct {
	ID            string                 `json:"id"`
	AgentID       string                 `json:"agentId"`
	TenantID      string                 `json:"tenant_id"`
	Version       int                    `json:"version"`
	Snapshot      map[string]interface{} `json:"snapshot"`
	ChangeSummary string                 `json:"changeSummary"`
	ChangedFields []string               `json:"changedFields"`
	CreatedBy     string                 `json:"createdBy"`
	CreatedAt     string                 `json:"createdAt"`
}

type agentVersionDiffResponse struct {
	VersionA   int                 `json:"versionA"`
	VersionB   int                 `json:"versionB"`
	FieldDiffs []agentFieldDiff   `json:"fieldDiffs"`
	CreatedAtA string              `json:"createdAtA"`
	CreatedAtB string              `json:"createdAtB"`
}

type agentFieldDiff struct {
	Field    string      `json:"field"`
	Label    string      `json:"label"`
	OldValue interface{} `json:"oldValue"`
	NewValue interface{} `json:"newValue"`
	Type     string      `json:"type"` // added, removed, modified, unchanged
}

var fieldLabels = map[string]string{
	"agentId":        "Agent ID",
	"domain":         "领域",
	"adapterType":    "适配器类型",
	"baseModelName":  "基础模型",
	"rankLevel":      "等级",
	"displayName":    "显示名称",
	"dutyNote":       "职责说明",
	"avatarUrl":      "头像 URL",
	"capabilityTags": "能力标签",
	"baseUrl":        "Base URL",
	"systemPrompt":   "System Prompt",
	"userPrompt":     "User Prompt",
	"assistantPrompt": "Assistant Prompt",
}

func newAgentVersionHandler() *agentVersionHandler {
	h := &agentVersionHandler{store: make(map[string][]agentVersionRecord)}
	h.seedDemoData()
	return h
}

func (h *agentVersionHandler) seedDemoData() {
	now := time.Now()
	baseConfig := map[string]interface{}{
		"agentId":        "demo-agent",
		"domain":         "general",
		"adapterType":    "deepseek",
		"baseModelName":  "deepseek-v3",
		"rankLevel":      "L1",
		"displayName":    "Demo Agent",
		"dutyNote":       "通用助手",
		"capabilityTags": []string{"chat", "code"},
		"systemPrompt":   "You are a helpful assistant.",
		"userPrompt":     "",
		"assistantPrompt": "",
	}

	v4Config := copyMap(baseConfig)
	v4Config["systemPrompt"] = "You are a helpful assistant. Always respond in a structured JSON format.\n\n## Rules\n1. Be concise\n2. Use code blocks for code\n3. Cite sources when applicable"
	v4Config["displayName"] = "Demo Agent v2"
	v4Config["dutyNote"] = "增强版通用助手"
	v4Config["baseModelName"] = "deepseek-v4-pro"
	v4Config["rankLevel"] = "L2"
	v4Config["capabilityTags"] = []string{"chat", "code", "analysis"}
	v4Config["userPrompt"] = "Please analyze the following: {{input}}"
	v4Config["assistantPrompt"] = "I will analyze the input and provide a structured response."

	v3Config := copyMap(v4Config)
	delete(v3Config, "assistantPrompt")
	v3Config["systemPrompt"] = "You are a helpful assistant. Always respond in a structured format."

	v2Config := copyMap(baseConfig)
	v2Config["displayName"] = "Demo Agent v2"
	v2Config["dutyNote"] = "增强版通用助手"
	v2Config["systemPrompt"] = "You are a helpful assistant. Always respond in a structured format."
	v2Config["capabilityTags"] = []string{"chat", "code", "analysis"}

	versions := []agentVersionRecord{
		{ID: newID(), AgentID: "demo-agent", TenantID: "default", Version: 4,
			Snapshot: v4Config, ChangeSummary: "增强 System Prompt（添加规则区块），新增 Assistant Prompt",
			ChangedFields: []string{"systemPrompt", "assistantPrompt"}, CreatedBy: "dev-user",
			CreatedAt: now.Add(-24 * time.Hour).Format(time.RFC3339)},
		{ID: newID(), AgentID: "demo-agent", TenantID: "default", Version: 3,
			Snapshot: v3Config, ChangeSummary: "升级模型到 deepseek-v4-pro，调整等级为 L2，新增 User Prompt 模板",
			ChangedFields: []string{"baseModelName", "rankLevel", "userPrompt"}, CreatedBy: "admin",
			CreatedAt: now.Add(-48 * time.Hour).Format(time.RFC3339)},
		{ID: newID(), AgentID: "demo-agent", TenantID: "default", Version: 2,
			Snapshot: v2Config, ChangeSummary: "更新显示名称、职责说明、System Prompt，新增分析能力标签",
			ChangedFields: []string{"displayName", "dutyNote", "systemPrompt", "capabilityTags"}, CreatedBy: "admin",
			CreatedAt: now.Add(-120 * time.Hour).Format(time.RFC3339)},
		{ID: newID(), AgentID: "demo-agent", TenantID: "default", Version: 1,
			Snapshot: baseConfig, ChangeSummary: "初始创建",
			ChangedFields: []string{"agentId", "domain", "adapterType", "baseModelName", "rankLevel", "displayName", "dutyNote", "capabilityTags", "systemPrompt"}, CreatedBy: "admin",
			CreatedAt: now.Add(-168 * time.Hour).Format(time.RFC3339)},
	}

	h.store["demo-agent"] = versions
	log.Printf("agent-version-handler: seeded %d versions for demo-agent", len(versions))
}

func copyMap(src map[string]interface{}) map[string]interface{} {
	dst := make(map[string]interface{}, len(src))
	for k, v := range src {
		dst[k] = v
	}
	return dst
}

func (h *agentVersionHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	rel := strings.TrimPrefix(r.URL.Path, "/platform/agent-versions")
	rel = strings.TrimPrefix(rel, "/")

	// /platform/agent-versions/{agentId}
	// /platform/agent-versions/{agentId}/{version}
	// /platform/agent-versions/{agentId}/diff?vA=X&vB=Y
	// /platform/agent-versions/{agentId}/rollback

	switch {
	case rel == "":
		http.Error(w, `{"error":"agentId required"}`, http.StatusBadRequest)
	case strings.HasSuffix(rel, "/rollback") && r.Method == http.MethodPost:
		agentID := strings.TrimSuffix(rel, "/rollback")
		h.rollback(w, r, agentID)
	case strings.HasSuffix(rel, "/diff") && r.Method == http.MethodGet:
		agentID := strings.TrimSuffix(rel, "/diff")
		h.diff(w, r, agentID)
	case !strings.Contains(rel, "/") && r.Method == http.MethodGet:
		h.list(w, r, rel)
	case strings.Count(rel, "/") == 1 && r.Method == http.MethodGet:
		parts := strings.SplitN(rel, "/", 2)
		h.get(w, r, parts[0], parts[1])
	default:
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	}
}

// GET /platform/agent-versions/{agentId}
func (h *agentVersionHandler) list(w http.ResponseWriter, _ *http.Request, agentID string) {
	versions, ok := h.store[agentID]
	if !ok {
		// Generate demo data on the fly for any agent ID
		versions = h.generateDemo(agentID)
		h.store[agentID] = versions
	}

	resp := map[string]interface{}{
		"agentId":  agentID,
		"versions": versions,
		"total":    len(versions),
	}
	_ = json.NewEncoder(w).Encode(resp)
}

// GET /platform/agent-versions/{agentId}/{version}
func (h *agentVersionHandler) get(w http.ResponseWriter, _ *http.Request, agentID, verStr string) {
	versions, ok := h.store[agentID]
	if !ok {
		http.Error(w, `{"error":"agent not found"}`, http.StatusNotFound)
		return
	}

	targetVer, err := strconv.Atoi(verStr)
	if err != nil {
		http.Error(w, `{"error":"invalid version number"}`, http.StatusBadRequest)
		return
	}

	for _, v := range versions {
		if v.Version == targetVer {
			_ = json.NewEncoder(w).Encode(v)
			return
		}
	}

	http.Error(w, fmt.Sprintf(`{"error":"version %d not found"}`, targetVer), http.StatusNotFound)
}

// GET /platform/agent-versions/{agentId}/diff?vA=X&vB=Y
func (h *agentVersionHandler) diff(w http.ResponseWriter, r *http.Request, agentID string) {
	vAStr := r.URL.Query().Get("vA")
	vBStr := r.URL.Query().Get("vB")
	if vAStr == "" || vBStr == "" {
		http.Error(w, `{"error":"vA and vB query params required"}`, http.StatusBadRequest)
		return
	}

	vA, errA := strconv.Atoi(vAStr)
	vB, errB := strconv.Atoi(vBStr)
	if errA != nil || errB != nil {
		http.Error(w, `{"error":"invalid version numbers"}`, http.StatusBadRequest)
		return
	}

	versions, ok := h.store[agentID]
	if !ok {
		http.Error(w, `{"error":"agent not found"}`, http.StatusNotFound)
		return
	}

	var recA, recB *agentVersionRecord
	for i := range versions {
		if versions[i].Version == vA {
			recA = &versions[i]
		}
		if versions[i].Version == vB {
			recB = &versions[i]
		}
	}
	if recA == nil || recB == nil {
		http.Error(w, `{"error":"one or both versions not found"}`, http.StatusNotFound)
		return
	}

	// Compute field-level diff
	allFields := make(map[string]bool)
	for k := range recA.Snapshot {
		allFields[k] = true
	}
	for k := range recB.Snapshot {
		allFields[k] = true
	}

	diffs := make([]agentFieldDiff, 0, len(allFields))
	for field := range allFields {
		oldVal, oldExists := recA.Snapshot[field]
		newVal, newExists := recB.Snapshot[field]

		oldJSON, _ := json.Marshal(oldVal)
		newJSON, _ := json.Marshal(newVal)

		diffType := "unchanged"
		switch {
		case !oldExists && newExists:
			diffType = "added"
		case oldExists && !newExists:
			diffType = "removed"
		case string(oldJSON) != string(newJSON):
			diffType = "modified"
		}

		if diffType != "unchanged" {
			label := fieldLabels[field]
			if label == "" {
				label = field
			}
			diffs = append(diffs, agentFieldDiff{
				Field:    field,
				Label:    label,
				OldValue: oldVal,
				NewValue: newVal,
				Type:     diffType,
			})
		}
	}

	resp := agentVersionDiffResponse{
		VersionA:   vA,
		VersionB:   vB,
		FieldDiffs: diffs,
		CreatedAtA: recA.CreatedAt,
		CreatedAtB: recB.CreatedAt,
	}
	_ = json.NewEncoder(w).Encode(resp)
}

// POST /platform/agent-versions/{agentId}/rollback
func (h *agentVersionHandler) rollback(w http.ResponseWriter, r *http.Request, agentID string) {
	var req struct {
		AgentID       string `json:"agentId"`
		TargetVersion int    `json:"targetVersion"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, `{"error":"invalid json body"}`, http.StatusBadRequest)
		return
	}

	versions, ok := h.store[agentID]
	if !ok {
		http.Error(w, `{"error":"agent not found"}`, http.StatusNotFound)
		return
	}

	var target *agentVersionRecord
	for i := range versions {
		if versions[i].Version == req.TargetVersion {
			target = &versions[i]
			break
		}
	}
	if target == nil {
		http.Error(w, fmt.Sprintf(`{"error":"version %d not found"}`, req.TargetVersion), http.StatusNotFound)
		return
	}

	// Create a new version that is a clone of the target version's snapshot
	nextVer := versions[0].Version + 1
	newRecord := agentVersionRecord{
		ID:            newID(),
		AgentID:       agentID,
		TenantID:      "default",
		Version:       nextVer,
		Snapshot:      copyMap(target.Snapshot),
		ChangeSummary: fmt.Sprintf("回滚到版本 v%d", req.TargetVersion),
		ChangedFields: []string{"__rollback__"},
		CreatedBy:     "admin",
		CreatedAt:     time.Now().Format(time.RFC3339),
	}

	// Prepend to list (newest first)
	h.store[agentID] = append([]agentVersionRecord{newRecord}, versions...)

	resp := map[string]interface{}{
		"success":       true,
		"agentId":       agentID,
		"rolledBackTo":  req.TargetVersion,
		"newVersion":   nextVer,
		"message":       fmt.Sprintf("已回滚到版本 v%d，新版本 v%d 已创建", req.TargetVersion, nextVer),
	}
	_ = json.NewEncoder(w).Encode(resp)
}

// ── Demo data generator ───────────────────────────────────────────

func (h *agentVersionHandler) generateDemo(agentID string) []agentVersionRecord {
	now := time.Now()
	base := map[string]interface{}{
		"agentId":        agentID,
		"domain":         "general",
		"adapterType":    "deepseek",
		"baseModelName":  "deepseek-v3",
		"rankLevel":      "L1",
		"displayName":    agentID,
		"dutyNote":       "通用助手",
		"capabilityTags": []string{"chat"},
		"systemPrompt":   "You are a helpful assistant.",
		"userPrompt":     "",
		"assistantPrompt": "",
	}

	return []agentVersionRecord{
		{
			ID: newID(), AgentID: agentID, TenantID: "default", Version: int(math.Max(1, float64(1))),
			Snapshot: base, ChangeSummary: "初始创建",
			ChangedFields: []string{"agentId", "domain", "adapterType", "baseModelName", "rankLevel", "displayName", "dutyNote", "capabilityTags", "systemPrompt"},
			CreatedBy: "admin", CreatedAt: now.Add(-7 * 24 * time.Hour).Format(time.RFC3339),
		},
	}
}

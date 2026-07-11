package main

import (
	"encoding/json"
	"log"
	"net/http"
	"strings"

	"github.com/agenthub/platform/shared/db"
)

// templateHandler serves /platform/templates CRUD using direct PostgreSQL queries.
type templateHandler struct {
	pool *db.Pool
}

func newTemplateHandler(pool *db.Pool) *templateHandler {
	return &templateHandler{pool: pool}
}

func (h *templateHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	// Route: /platform/templates/{id}/...
	id := strings.TrimPrefix(r.URL.Path, "/platform/templates")
	id = strings.TrimPrefix(id, "/")

	switch {
	case id == "" && r.Method == http.MethodGet:
		h.listTemplates(w, r)
	case id == "" && r.Method == http.MethodPost:
		h.createTemplate(w, r)
	case id != "" && !strings.Contains(id, "/") && r.Method == http.MethodGet:
		h.getTemplate(w, r, id)
	case id != "" && strings.HasSuffix(id, "/create-agent") && r.Method == http.MethodPost:
		// Not implemented here; frontend calls /api/agent/registry directly
		json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
	case id != "" && !strings.Contains(id, "/") && r.Method == http.MethodPut:
		h.updateTemplate(w, r, id)
	case id != "" && !strings.Contains(id, "/") && r.Method == http.MethodDelete:
		h.deleteTemplate(w, r, id)
	default:
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	}
}

func (h *templateHandler) listTemplates(w http.ResponseWriter, r *http.Request) {
	if h.pool == nil {
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"templates": []interface{}{}})
		return
	}

	rows, err := h.pool.Query(r.Context(),
		`SELECT id, tenant_id, name, description, category, icon, tags, source, version, author,
		        workflow_json, prompt_json, tools_json, knowledge_json, agent_config, usage_count, rating,
		        created_at, updated_at
		 FROM platform_agent_templates
		 ORDER BY source ASC, usage_count DESC`)
	if err != nil {
		log.Printf("template list error: %v", err)
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"templates": []interface{}{}})
		return
	}
	defer rows.Close()

	templates := make([]map[string]interface{}, 0)
	for rows.Next() {
		var id, tenantID, name, description, category, icon, source, version, author string
		var workflowJSON, promptJSON, knowledgeJSON, agentConfig string
		var tags, toolsJSON []string
		var usageCount int
		var rating float64
		var createdAt, updatedAt interface{}

		if err := rows.Scan(&id, &tenantID, &name, &description, &category, &icon, &tags,
			&source, &version, &author, &workflowJSON, &promptJSON, &toolsJSON, &knowledgeJSON,
			&agentConfig, &usageCount, &rating, &createdAt, &updatedAt); err != nil {
			log.Printf("template row scan error: %v", err)
			continue
		}

		templates = append(templates, map[string]interface{}{
			"id": id, "tenant_id": tenantID, "name": name, "description": description,
			"category": category, "icon": icon, "tags": tags, "source": source,
			"version": version, "author": author, "workflow_json": workflowJSON,
			"prompt_json": promptJSON, "tools_json": toolsJSON, "knowledge_json": knowledgeJSON,
			"agent_config": agentConfig, "usage_count": usageCount, "rating": rating,
			"created_at": createdAt, "updated_at": updatedAt,
		})
	}

	_ = json.NewEncoder(w).Encode(map[string]interface{}{"templates": templates})
}

func (h *templateHandler) getTemplate(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	var tenantID, name, description, category, icon, source, version, author string
	var workflowJSON, promptJSON, knowledgeJSON, agentConfig string
	var tags, toolsJSON []string
	var usageCount int
	var rating float64
	var createdAt, updatedAt interface{}

	err := h.pool.QueryRow(r.Context(),
		`SELECT id, tenant_id, name, description, category, icon, tags, source, version, author,
		        workflow_json, prompt_json, tools_json, knowledge_json, agent_config, usage_count, rating,
		        created_at, updated_at
		 FROM platform_agent_templates WHERE id=$1`, id).
		Scan(&id, &tenantID, &name, &description, &category, &icon, &tags,
			&source, &version, &author, &workflowJSON, &promptJSON, &toolsJSON, &knowledgeJSON,
			&agentConfig, &usageCount, &rating, &createdAt, &updatedAt)
	if err != nil {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}

	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"id": id, "tenant_id": tenantID, "name": name, "description": description,
		"category": category, "icon": icon, "tags": tags, "source": source,
		"version": version, "author": author, "workflow_json": workflowJSON,
		"prompt_json": promptJSON, "tools_json": toolsJSON, "knowledge_json": knowledgeJSON,
		"agent_config": agentConfig, "usage_count": usageCount, "rating": rating,
		"created_at": createdAt, "updated_at": updatedAt,
	})
}

func (h *templateHandler) createTemplate(w http.ResponseWriter, r *http.Request) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	var t map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&t); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}

	id := getStr(t, "id")
	if id == "" {
		http.Error(w, `{"error":"id is required"}`, http.StatusBadRequest)
		return
	}

	_, err := h.pool.Exec(r.Context(),
		`INSERT INTO platform_agent_templates (id, tenant_id, name, description, category, icon, tags, source, version, author, workflow_json, prompt_json, tools_json, knowledge_json, agent_config, usage_count, rating)
		 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)`,
		id,
		getStr(t, "tenant_id"),
		getStr(t, "name"),
		getStr(t, "description"),
		getStr(t, "category"),
		getStr(t, "icon"),
		getStrArr(t, "tags"),
		getStr(t, "source"),
		getStr(t, "version"),
		getStr(t, "author"),
		getStr(t, "workflow_json"),
		getStr(t, "prompt_json"),
		getStrArr(t, "tools_json"),
		getStr(t, "knowledge_json"),
		getStr(t, "agent_config"),
		getInt(t, "usage_count"),
		getFloat(t, "rating"),
	)
	if err != nil {
		log.Printf("template create error: %v", err)
		http.Error(w, `{"error":"create failed"}`, http.StatusInternalServerError)
		return
	}
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(t)
}

func (h *templateHandler) updateTemplate(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	var t map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&t); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}

	_, err := h.pool.Exec(r.Context(),
		`UPDATE platform_agent_templates SET name=$1, description=$2, category=$3, icon=$4, tags=$5, workflow_json=$6, prompt_json=$7, tools_json=$8, knowledge_json=$9, agent_config=$10, updated_at=now() WHERE id=$11`,
		getStr(t, "name"),
		getStr(t, "description"),
		getStr(t, "category"),
		getStr(t, "icon"),
		getStrArr(t, "tags"),
		getStr(t, "workflow_json"),
		getStr(t, "prompt_json"),
		getStrArr(t, "tools_json"),
		getStr(t, "knowledge_json"),
		getStr(t, "agent_config"),
		id,
	)
	if err != nil {
		log.Printf("template update error: %v", err)
		http.Error(w, `{"error":"update failed"}`, http.StatusInternalServerError)
		return
	}
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func (h *templateHandler) deleteTemplate(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	// Only allow deleting user-created templates
	var source string
	_ = h.pool.QueryRow(r.Context(), "SELECT source FROM platform_agent_templates WHERE id=$1", id).Scan(&source)
	if source == "builtin" {
		http.Error(w, `{"error":"cannot delete builtin template"}`, http.StatusForbidden)
		return
	}

	_, err := h.pool.Exec(r.Context(), "DELETE FROM platform_agent_templates WHERE id=$1 AND source='user'", id)
	if err != nil {
		log.Printf("template delete error: %v", err)
		http.Error(w, `{"error":"delete failed"}`, http.StatusInternalServerError)
		return
	}
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "deleted"})
}

// ── helpers ────────────────────────────────────────────────────────

func getStr(m map[string]interface{}, key string) string {
	if v, ok := m[key]; ok {
		if s, ok := v.(string); ok {
			return s
		}
	}
	return ""
}

func getStrArr(m map[string]interface{}, key string) []string {
	if v, ok := m[key]; ok {
		if arr, ok := v.([]interface{}); ok {
			out := make([]string, 0, len(arr))
			for _, item := range arr {
				if s, ok := item.(string); ok {
					out = append(out, s)
				}
			}
			return out
		}
	}
	return []string{}
}

func getInt(m map[string]interface{}, key string) int {
	if v, ok := m[key]; ok {
		switch n := v.(type) {
		case float64:
			return int(n)
		case int:
			return n
		}
	}
	return 0
}

func getFloat(m map[string]interface{}, key string) float64 {
	if v, ok := m[key]; ok {
		if n, ok := v.(float64); ok {
			return n
		}
	}
	return 0
}

package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"log"
	"net/http"
	"strings"
)

// workspaceHandler serves /platform/workspaces CRUD.
// Uses a JSON file or in-memory store until PostgreSQL is wired to the gateway.
type workspaceHandler struct {
	store map[string]map[string]interface{} // id -> workspace
}

func newWorkspaceHandler() *workspaceHandler {
	h := &workspaceHandler{store: make(map[string]map[string]interface{})}
	// Seed default workspace
	h.store["ws-default"] = map[string]interface{}{
		"id": "ws-default", "tenant_id": "", "name": "Default",
		"description": "Default workspace for all existing resources",
		"owner_id": "system", "member_count": 1, "created_at": "2025-01-01T00:00:00Z",
	}
	return h
}

func (h *workspaceHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	rel := strings.TrimPrefix(r.URL.Path, "/platform/workspaces")
	rel = strings.TrimPrefix(rel, "/")

	switch {
	case rel == "" && r.Method == http.MethodGet:
		h.list(w, r)
	case rel == "" && r.Method == http.MethodPost:
		h.create(w, r)
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodGet:
		h.get(w, r, rel)
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodPut:
		h.update(w, r, rel)
	case rel != "" && !strings.Contains(rel, "/") && r.Method == http.MethodDelete:
		h.del(w, r, rel)
	case strings.HasSuffix(rel, "/members") && r.Method == http.MethodGet:
		id := strings.TrimSuffix(rel, "/members")
		h.listMembers(w, r, id)
	case strings.HasSuffix(rel, "/members") && r.Method == http.MethodPost:
		id := strings.TrimSuffix(rel, "/members")
		h.addMember(w, r, id)
	default:
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	}
}

func (h *workspaceHandler) list(w http.ResponseWriter, _ *http.Request) {
	workspaces := make([]map[string]interface{}, 0, len(h.store))
	for _, ws := range h.store {
		workspaces = append(workspaces, ws)
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"workspaces": workspaces})
}

func (h *workspaceHandler) create(w http.ResponseWriter, r *http.Request) {
	var ws map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&ws); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	id := getStr(ws, "id")
	if id == "" {
		id = "ws-" + randomSuffix()
		ws["id"] = id
	}
	if _, exists := h.store[id]; exists {
		http.Error(w, `{"error":"workspace already exists"}`, http.StatusConflict)
		return
	}
	ws["member_count"] = 1
	ws["created_at"] = "2025-01-01T00:00:00Z"
	h.store[id] = ws
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(ws)
}

func (h *workspaceHandler) get(w http.ResponseWriter, _ *http.Request, id string) {
	ws, ok := h.store[id]
	if !ok {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	_ = json.NewEncoder(w).Encode(ws)
}

func (h *workspaceHandler) update(w http.ResponseWriter, r *http.Request, id string) {
	if _, ok := h.store[id]; !ok {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	var updates map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&updates); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	ws := h.store[id]
	if v, ok := updates["name"]; ok {
		ws["name"] = v
	}
	if v, ok := updates["description"]; ok {
		ws["description"] = v
	}
	_ = json.NewEncoder(w).Encode(ws)
}

func (h *workspaceHandler) del(w http.ResponseWriter, _ *http.Request, id string) {
	if id == "ws-default" {
		http.Error(w, `{"error":"cannot delete default workspace"}`, http.StatusForbidden)
		return
	}
	delete(h.store, id)
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "deleted"})
}

func (h *workspaceHandler) listMembers(w http.ResponseWriter, _ *http.Request, _ string) {
	members := []map[string]interface{}{
		{"user_id": "system", "display_name": "System", "email": "system@agenthub.local", "role": "admin", "joined_at": "2025-01-01T00:00:00Z"},
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"members": members})
}

func (h *workspaceHandler) addMember(w http.ResponseWriter, r *http.Request, _ string) {
	var req map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	email := getStr(req, "email")
	if email == "" {
		http.Error(w, `{"error":"email is required"}`, http.StatusBadRequest)
		return
	}
	log.Printf("workspace: invite member %s (role=%s)", email, getStr(req, "role"))
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "invited", "email": email})
}

func randomSuffix() string {
	b := make([]byte, 4)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

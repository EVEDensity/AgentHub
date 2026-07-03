package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/agenthub/platform/shared/db"
)

// workspaceHandler serves /platform/workspaces CRUD backed by PostgreSQL.
type workspaceHandler struct {
	pool *db.Pool
}

func newWorkspaceHandler(pool *db.Pool) *workspaceHandler {
	return &workspaceHandler{pool: pool}
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
	// DELETE /platform/workspaces/{id}/members/{userId}
	case strings.Contains(rel, "/members/") && r.Method == http.MethodDelete:
		parts := strings.SplitN(rel, "/members/", 2)
		if len(parts) == 2 {
			h.removeMember(w, r, parts[0], parts[1])
		} else {
			http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		}
	default:
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
	}
}

// ── Workspace CRUD ──────────────────────────────────────────────────

func (h *workspaceHandler) list(w http.ResponseWriter, r *http.Request) {
	if h.pool == nil {
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"workspaces": []interface{}{}})
		return
	}

	rows, err := h.pool.Query(r.Context(),
		`SELECT id, tenant_id, name, description, owner_id,
		        (SELECT COUNT(*) FROM platform_workspace_members WHERE workspace_id=w.id) AS member_count,
		        created_at, updated_at
		 FROM platform_workspaces w
		 ORDER BY created_at ASC`)
	if err != nil {
		log.Printf("workspace list error: %v", err)
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"workspaces": []interface{}{}})
		return
	}
	defer rows.Close()

	workspaces := make([]map[string]interface{}, 0)
	for rows.Next() {
		var id, tenantID, name, description, ownerID string
		var memberCount int
		var createdAt, updatedAt time.Time
		if err := rows.Scan(&id, &tenantID, &name, &description, &ownerID, &memberCount, &createdAt, &updatedAt); err != nil {
			log.Printf("workspace row scan error: %v", err)
			continue
		}
		workspaces = append(workspaces, map[string]interface{}{
			"id":           id,
			"tenant_id":    tenantID,
			"name":         name,
			"description":  description,
			"owner_id":     ownerID,
			"member_count": memberCount,
			"created_at":   createdAt.Format(time.RFC3339),
			"updated_at":   updatedAt.Format(time.RFC3339),
		})
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"workspaces": workspaces})
}

func (h *workspaceHandler) create(w http.ResponseWriter, r *http.Request) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	var ws map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&ws); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}

	id := getStr(ws, "id")
	if id == "" {
		id = "ws-" + randomSuffix()
	}
	name := getStr(ws, "name")
	if name == "" {
		name = "Untitled Workspace"
	}
	description := getStr(ws, "description")
	tenantID := getStr(ws, "tenant_id")
	ownerID := getStr(ws, "owner_id")
	if ownerID == "" {
		ownerID = "system"
	}

	now := time.Now()
	_, err := h.pool.Exec(r.Context(),
		`INSERT INTO platform_workspaces (id, tenant_id, name, description, owner_id, created_at, updated_at)
		 VALUES ($1,$2,$3,$4,$5,$6,$7)
		 ON CONFLICT (id) DO NOTHING`,
		id, tenantID, name, description, ownerID, now, now)
	if err != nil {
		log.Printf("workspace create error: %v", err)
		http.Error(w, `{"error":"create failed"}`, http.StatusInternalServerError)
		return
	}

	// Add owner as first member
	_, _ = h.pool.Exec(r.Context(),
		`INSERT INTO platform_workspace_members (workspace_id, tenant_id, user_id, role, invited_by, joined_at)
		 VALUES ($1,$2,$3,'admin',$4,$5)
		 ON CONFLICT (workspace_id, user_id) DO NOTHING`,
		id, tenantID, ownerID, ownerID, now)

	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"id":           id,
		"tenant_id":    tenantID,
		"name":         name,
		"description":  description,
		"owner_id":     ownerID,
		"member_count": 1,
		"created_at":   now.Format(time.RFC3339),
		"updated_at":   now.Format(time.RFC3339),
	})
}

func (h *workspaceHandler) get(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	var tenantID, name, description, ownerID string
	var memberCount int
	var createdAt, updatedAt time.Time
	err := h.pool.QueryRow(r.Context(),
		`SELECT id, tenant_id, name, description, owner_id,
		        (SELECT COUNT(*) FROM platform_workspace_members WHERE workspace_id=w.id),
		        created_at, updated_at
		 FROM platform_workspaces w WHERE id=$1`, id).
		Scan(&id, &tenantID, &name, &description, &ownerID, &memberCount, &createdAt, &updatedAt)
	if err != nil {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"id":           id,
		"tenant_id":    tenantID,
		"name":         name,
		"description":  description,
		"owner_id":     ownerID,
		"member_count": memberCount,
		"created_at":   createdAt.Format(time.RFC3339),
		"updated_at":   updatedAt.Format(time.RFC3339),
	})
}

func (h *workspaceHandler) update(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	var updates map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&updates); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}

	name := getStr(updates, "name")
	description := getStr(updates, "description")

	res, err := h.pool.Exec(r.Context(),
		`UPDATE platform_workspaces SET name=$1, description=$2, updated_at=$3 WHERE id=$4`,
		name, description, time.Now(), id)
	if err != nil {
		log.Printf("workspace update error: %v", err)
		http.Error(w, `{"error":"update failed"}`, http.StatusInternalServerError)
		return
	}
	if res.RowsAffected() == 0 {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}

	// Return updated workspace
	h.get(w, r, id)
}

func (h *workspaceHandler) del(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	if id == "ws-default" {
		http.Error(w, `{"error":"cannot delete default workspace"}`, http.StatusForbidden)
		return
	}

	res, err := h.pool.Exec(r.Context(), `DELETE FROM platform_workspaces WHERE id=$1`, id)
	if err != nil {
		log.Printf("workspace delete error: %v", err)
		http.Error(w, `{"error":"delete failed"}`, http.StatusInternalServerError)
		return
	}
	if res.RowsAffected() == 0 {
		http.Error(w, `{"error":"not found"}`, http.StatusNotFound)
		return
	}
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "deleted"})
}

// ── Members ─────────────────────────────────────────────────────────

func (h *workspaceHandler) listMembers(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	rows, err := h.pool.Query(r.Context(),
		`SELECT user_id, role, invited_by, joined_at
		 FROM platform_workspace_members
		 WHERE workspace_id=$1
		 ORDER BY joined_at ASC`, id)
	if err != nil {
		log.Printf("workspace members list error: %v", err)
		_ = json.NewEncoder(w).Encode(map[string]interface{}{"members": []interface{}{}})
		return
	}
	defer rows.Close()

	members := make([]map[string]interface{}, 0)
	for rows.Next() {
		var userID, role, invitedBy string
		var joinedAt time.Time
		if err := rows.Scan(&userID, &role, &invitedBy, &joinedAt); err != nil {
			log.Printf("member row scan error: %v", err)
			continue
		}
		members = append(members, map[string]interface{}{
			"user_id":      userID,
			"display_name": userID, // display_name stored in user profile, fallback to user_id
			"email":        "",
			"role":         role,
			"invited_by":   invitedBy,
			"joined_at":    joinedAt.Format(time.RFC3339),
		})
	}
	_ = json.NewEncoder(w).Encode(map[string]interface{}{"members": members})
}

func (h *workspaceHandler) addMember(w http.ResponseWriter, r *http.Request, id string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	var req map[string]interface{}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}

	email := getStr(req, "email")
	role := getStr(req, "role")
	if role == "" {
		role = "editor"
	}
	invitedBy := getStr(req, "invited_by")

	// Use email as user_id in the absence of a user directory
	userID := email
	if userID == "" {
		http.Error(w, `{"error":"email is required"}`, http.StatusBadRequest)
		return
	}

	// Fetch tenant_id from workspace
	var tenantID string
	_ = h.pool.QueryRow(r.Context(), `SELECT tenant_id FROM platform_workspaces WHERE id=$1`, id).Scan(&tenantID)

	_, err := h.pool.Exec(r.Context(),
		`INSERT INTO platform_workspace_members (workspace_id, tenant_id, user_id, role, invited_by, joined_at)
		 VALUES ($1,$2,$3,$4,$5,$6)
		 ON CONFLICT (workspace_id, user_id) DO UPDATE SET role=$4, joined_at=$6`,
		id, tenantID, userID, role, invitedBy, time.Now())
	if err != nil {
		log.Printf("workspace add member error: %v", err)
		http.Error(w, `{"error":"invite failed"}`, http.StatusInternalServerError)
		return
	}

	log.Printf("workspace: invited member %s (role=%s) to workspace %s", userID, role, id)
	_ = json.NewEncoder(w).Encode(map[string]interface{}{
		"status":     "invited",
		"email":      email,
		"user_id":    userID,
		"role":       role,
		"invited_by": invitedBy,
		"joined_at":  time.Now().Format(time.RFC3339),
	})
}

func (h *workspaceHandler) removeMember(w http.ResponseWriter, r *http.Request, id, userID string) {
	if h.pool == nil {
		http.Error(w, `{"error":"database not available"}`, http.StatusServiceUnavailable)
		return
	}

	res, err := h.pool.Exec(r.Context(),
		`DELETE FROM platform_workspace_members WHERE workspace_id=$1 AND user_id=$2`,
		id, userID)
	if err != nil {
		log.Printf("workspace remove member error: %v", err)
		http.Error(w, `{"error":"remove failed"}`, http.StatusInternalServerError)
		return
	}
	if res.RowsAffected() == 0 {
		http.Error(w, `{"error":"member not found"}`, http.StatusNotFound)
		return
	}
	log.Printf("workspace: removed member %s from workspace %s", userID, id)
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "removed"})
}

// ── Shared helpers ──────────────────────────────────────────────────

func randomSuffix() string {
	b := make([]byte, 4)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

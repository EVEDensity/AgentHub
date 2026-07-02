// iam-service is the platform identity & access management service (P3-1).
// It issues JWTs carrying tenant + role + scope claims, manages tenants / users
// / roles / sensitive-tool classifications in PostgreSQL, and exposes an
// authorization evaluation endpoint for services that need a server-side ABAC
// decision (the hot path uses the JWT scopes directly).
//
// Port 8088. Endpoints under /iam/* are the management surface; /healthz,
// /profile, /metrics are operational.
package main

import (
	"context"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/agenthub/platform/shared/db"
	"github.com/agenthub/platform/shared/iam"
	"github.com/agenthub/platform/shared/obs"
	"github.com/prometheus/client_golang/prometheus"
)

// ── Prometheus metrics ─────────────────────────────────────────────────
var (
	tokensIssued = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "iam_tokens_issued_total", Help: "JWT access tokens issued."},
		[]string{"tenant_id", "tenant_role"},
	)
	tokenVerifies = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "iam_token_verifies_total", Help: "JWT verify calls."},
		[]string{"result"},
	)
	authzDecisions = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "iam_authz_decisions_total", Help: "Authorization evaluations by decision."},
		[]string{"decision", "action", "resource_type"},
	)
	toolClassifications = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "iam_tool_classifications_total", Help: "Sensitive-tool classifications by risk."},
		[]string{"risk_level", "source"},
	)
)

func init() {
	obs.MustRegister(tokensIssued, tokenVerifies, authzDecisions, toolClassifications)
}

// ── Request / response types ───────────────────────────────────────────

type TokenRequest struct {
	TenantID string `json:"tenant_id"`
	UserID   string `json:"user_id"`
	Email    string `json:"email"`
}

type TokenResponse struct {
	AccessToken string    `json:"access_token"`
	TokenType   string    `json:"token_type"`
	ExpiresAt   time.Time `json:"expires_at"`
	TenantID    string    `json:"tenant_id"`
	UserID      string    `json:"user_id"`
	Roles       []string  `json:"roles"`
	Scopes      []string  `json:"scopes"`
}

type VerifyRequest struct {
	Token string `json:"token"`
}

type VerifyResponse struct {
	Valid  bool        `json:"valid"`
	Claims *iam.Claims `json:"claims,omitempty"`
	Error  string      `json:"error,omitempty"`
}

type TenantInput struct {
	ID        string `json:"id"`
	Name      string `json:"name"`
	Plan      string `json:"plan"`
	Status    string `json:"status"`
	QuotasJSON string `json:"quotas_json"`
}

type RoleInput struct {
	ID          string   `json:"id"`
	TenantID    string   `json:"tenant_id"`
	Name        string   `json:"name"`
	Description string   `json:"description"`
	Scopes      []string `json:"scopes"`
}

type UserRoleInput struct {
	TenantID string   `json:"tenant_id"`
	UserID   string   `json:"user_id"`
	RoleIDs  []string `json:"role_ids"`
}

type AuthzRequest struct {
	TenantID string `json:"tenant_id"`
	UserID   string `json:"user_id"`
	Action   string `json:"action"`
	Resource struct {
		Type       string `json:"type"`
		TenantID   string `json:"tenant_id"`
		OwnerID    string `json:"owner_id"`
		Visibility string `json:"visibility"`
	} `json:"resource"`
	ToolRisk string `json:"tool_risk"`
}

type AuthzResponse struct {
	Decision   string `json:"decision"`
	Reason     string `json:"reason,omitempty"`
	TenantRole string `json:"tenant_role,omitempty"`
}

type SensitiveToolInput struct {
	TenantID             string `json:"tenant_id"`
	ToolName             string `json:"tool_name"`
	RiskLevel            string `json:"risk_level"`
	RequiresConfirmation bool   `json:"requires_confirmation"`
	AllowedRoles         string `json:"allowed_roles"`
}

type ClassifyRequest struct {
	TenantID string `json:"tenant_id"`
	ToolName string `json:"tool_name"`
}

type ClassifyResponse struct {
	ToolName             string `json:"tool_name"`
	RiskLevel            string `json:"risk_level"`
	RequiresConfirmation bool   `json:"requires_confirmation"`
	Source               string `json:"source"` // "tenant_rule" | "builtin"
}

func main() {
	jwtSecret := []byte(getenv("JWT_SECRET", ""))
	ttl := getenvDuration("JWT_TTL", 24*time.Hour)
	issuer := iam.NewTokenIssuer(jwtSecret, "iam-service", ttl)

	dsn := getenv("DATABASE_DSN", "postgres://agenthub:agenthub@localhost:5432/agenthub?sslmode=disable")
	pool, err := db.Connect(context.Background(), dsn)
	if err != nil {
		log.Fatalf("connect db: %v", err)
	}
	defer pool.Close()
	if err := pool.Migrate(context.Background()); err != nil {
		log.Fatalf("run db migrations: %v", err)
	}
	log.Printf("iam-service db migrated (dev_mode=%v)", issuer.IsDevMode())

	shutdown, err := obs.InitTracer(context.Background(), getenv("OTEL_EXPORTER_OTLP_ENDPOINT", ""), "iam-service")
	if err != nil {
		log.Fatalf("init tracer: %v", err)
	}
	defer shutdown(context.Background())

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("ok"))
	})
	mux.HandleFunc("/profile", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"service": "iam-service",
			"layer":   "go-iam",
			"responsibilities": []string{
				"jwt issuance & verification",
				"tenant registry management",
				"rbac role & permission management",
				"user-role assignment",
				"abac authorization evaluation",
				"sensitive tool risk classification",
			},
			"dev_mode":    issuer.IsDevMode(),
			"jwt_enforced": !issuer.IsDevMode(),
		})
	})

	// ── Token issuance & verification ──────────────────────────────────
	mux.HandleFunc("/iam/token", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		var req TokenRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, http.StatusBadRequest, "invalid json body")
			return
		}
		if req.TenantID == "" || req.UserID == "" {
			jsonError(w, http.StatusBadRequest, "tenant_id and user_id are required")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		roles, scopes, tenantRole, err := loadPrincipalScopes(ctx, pool, req.TenantID, req.UserID)
		if err != nil {
			log.Printf("load scopes for %s/%s failed: %v", req.TenantID, req.UserID, err)
			// In dev mode, allow issuance even if the user is not in PG yet.
			if !issuer.IsDevMode() {
				jsonError(w, http.StatusForbidden, "principal not found or disabled")
				return
			}
		}
		claims := iam.Claims{
			TenantID:   req.TenantID,
			UserID:     req.UserID,
			Roles:      roles,
			Scopes:     scopes,
			TenantRole: tenantRole,
		}
		tok, err := issuer.Issue(claims)
		if err != nil {
			jsonError(w, http.StatusInternalServerError, err.Error())
			return
		}
		tokensIssued.WithLabelValues(req.TenantID, tenantRole).Inc()
		expiresAt := time.Now().UTC().Add(ttl)
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(TokenResponse{
			AccessToken: tok,
			TokenType:   "Bearer",
			ExpiresAt:   expiresAt,
			TenantID:    req.TenantID,
			UserID:      req.UserID,
			Roles:       roles,
			Scopes:      scopes,
		})
	})

	mux.HandleFunc("/iam/verify", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		var req VerifyRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, http.StatusBadRequest, "invalid json body")
			return
		}
		claims, err := issuer.Verify(req.Token)
		if err != nil {
			tokenVerifies.WithLabelValues("invalid").Inc()
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(VerifyResponse{Valid: false, Error: err.Error()})
			return
		}
		tokenVerifies.WithLabelValues("valid").Inc()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(VerifyResponse{Valid: true, Claims: claims})
	})

	// ── Tenant management ──────────────────────────────────────────────
	mux.HandleFunc("/iam/tenants", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
			defer cancel()
			rows, err := pool.Query(ctx, `SELECT id, name, plan, status, quotas_json, created_at FROM platform_tenants ORDER BY created_at`)
			if err != nil {
				jsonError(w, http.StatusBadGateway, err.Error())
				return
			}
			defer rows.Close()
			tenants := []map[string]any{}
			for rows.Next() {
				var id, name, plan, status, quotas, created string
				if err := rows.Scan(&id, &name, &plan, &status, &quotas, &created); err == nil {
					tenants = append(tenants, map[string]any{"id": id, "name": name, "plan": plan, "status": status, "quotas_json": quotas, "created_at": created})
				}
			}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{"count": len(tenants), "tenants": tenants})
		case http.MethodPost:
			var req TenantInput
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				jsonError(w, http.StatusBadRequest, "invalid json body")
				return
			}
			if req.ID == "" {
				req.ID = "tenant-" + randID()
			}
			if req.Plan == "" {
				req.Plan = "free"
			}
			if req.Status == "" {
				req.Status = "active"
			}
			ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
			defer cancel()
			_, err := pool.Exec(ctx, `INSERT INTO platform_tenants (id, name, plan, status, quotas_json) VALUES ($1,$2,$3,$4,$5) ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, plan=EXCLUDED.plan, status=EXCLUDED.status, quotas_json=EXCLUDED.quotas_json, updated_at=now()`,
				req.ID, req.Name, req.Plan, req.Status, fallback(req.QuotasJSON, "{}"))
			if err != nil {
				jsonError(w, http.StatusBadGateway, err.Error())
				return
			}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "tenant": req})
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	})

	// ── Role management ────────────────────────────────────────────────
	mux.HandleFunc("/iam/roles", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			tenantID := r.URL.Query().Get("tenant_id")
			ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
			defer cancel()
			rows, err := pool.Query(ctx, `SELECT id, tenant_id, name, description, is_system FROM platform_roles WHERE ($1='' OR tenant_id='' OR tenant_id=$1) ORDER BY tenant_id, name`, tenantID)
			if err != nil {
				jsonError(w, http.StatusBadGateway, err.Error())
				return
			}
			defer rows.Close()
			roles := []map[string]any{}
			for rows.Next() {
				var id, tid, name, desc string
				var sys bool
				if err := rows.Scan(&id, &tid, &name, &desc, &sys); err == nil {
					roles = append(roles, map[string]any{"id": id, "tenant_id": tid, "name": name, "description": desc, "is_system": sys})
				}
			}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{"count": len(roles), "roles": roles})
		case http.MethodPost:
			var req RoleInput
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				jsonError(w, http.StatusBadRequest, "invalid json body")
				return
			}
			if req.Name == "" {
				jsonError(w, http.StatusBadRequest, "name is required")
				return
			}
			if req.ID == "" {
				req.ID = "role-" + randID()
			}
			ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
			defer cancel()
			tx, err := pool.Begin(ctx)
			if err != nil {
				jsonError(w, http.StatusBadGateway, err.Error())
				return
			}
			defer tx.Rollback(ctx)
			if _, err := tx.Exec(ctx, `INSERT INTO platform_roles (id, tenant_id, name, description, is_system) VALUES ($1,$2,$3,$4,false) ON CONFLICT (id) DO UPDATE SET description=EXCLUDED.description`,
				req.ID, req.TenantID, req.Name, req.Description); err != nil {
				jsonError(w, http.StatusBadGateway, err.Error())
				return
			}
			// Replace scopes: delete then re-insert.
			if _, err := tx.Exec(ctx, `DELETE FROM platform_role_permissions WHERE role_id=$1`, req.ID); err != nil {
				jsonError(w, http.StatusBadGateway, err.Error())
				return
			}
			for _, s := range req.Scopes {
				if _, err := tx.Exec(ctx, `INSERT INTO platform_role_permissions (role_id, tenant_id, scope) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING`, req.ID, req.TenantID, s); err != nil {
					jsonError(w, http.StatusBadGateway, err.Error())
					return
				}
			}
			if err := tx.Commit(ctx); err != nil {
				jsonError(w, http.StatusBadGateway, err.Error())
				return
			}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "role": req})
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	})

	// ── User-role assignment ───────────────────────────────────────────
	mux.HandleFunc("/iam/users/roles", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodPost:
			var req UserRoleInput
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				jsonError(w, http.StatusBadRequest, "invalid json body")
				return
			}
			if req.TenantID == "" || req.UserID == "" {
				jsonError(w, http.StatusBadRequest, "tenant_id and user_id are required")
				return
			}
			ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
			defer cancel()
			// Ensure user exists (upsert).
			if _, err := pool.Exec(ctx, `INSERT INTO platform_users (id, tenant_id) VALUES ($1,$2) ON CONFLICT (id) DO NOTHING`, req.UserID, req.TenantID); err != nil {
				jsonError(w, http.StatusBadGateway, err.Error())
				return
			}
			for _, rid := range req.RoleIDs {
				if _, err := pool.Exec(ctx, `INSERT INTO platform_user_roles (tenant_id, user_id, role_id) VALUES ($1,$2,$3) ON CONFLICT (user_id, role_id) DO NOTHING`, req.TenantID, req.UserID, rid); err != nil {
					jsonError(w, http.StatusBadGateway, err.Error())
					return
				}
			}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "user_id": req.UserID, "role_ids": req.RoleIDs})
		case http.MethodGet:
			userID := r.URL.Query().Get("user_id")
			if userID == "" {
				jsonError(w, http.StatusBadRequest, "user_id is required")
				return
			}
			ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
			defer cancel()
			rows, err := pool.Query(ctx, `SELECT r.id, r.tenant_id, r.name, r.is_system FROM platform_user_roles ur JOIN platform_roles r ON r.id=ur.role_id WHERE ur.user_id=$1`, userID)
			if err != nil {
				jsonError(w, http.StatusBadGateway, err.Error())
				return
			}
			defer rows.Close()
			roles := []map[string]any{}
			for rows.Next() {
				var id, tid, name string
				var sys bool
				if err := rows.Scan(&id, &tid, &name, &sys); err == nil {
					roles = append(roles, map[string]any{"id": id, "tenant_id": tid, "name": name, "is_system": sys})
				}
			}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{"user_id": userID, "roles": roles})
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	})

	// ── Authorization evaluation ───────────────────────────────────────
	mux.HandleFunc("/iam/authorize", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		var req AuthzRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, http.StatusBadRequest, "invalid json body")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		roles, scopes, tenantRole, _ := loadPrincipalScopes(ctx, pool, req.TenantID, req.UserID)
		principal := iam.TenantContext{
			TenantID:   req.TenantID,
			UserID:     req.UserID,
			Roles:      roles,
			Scopes:     scopes,
			TenantRole: tenantRole,
		}
		if issuer.IsDevMode() {
			principal.DevMode = true
		}
		azReq := iam.AuthzRequest{
			Principal: principal,
			Action:    iam.Action(req.Action),
			Resource: iam.Resource{
				Type:       req.Resource.Type,
				TenantID:   req.Resource.TenantID,
				OwnerID:    req.Resource.OwnerID,
				Visibility: req.Resource.Visibility,
			},
			ToolRisk: req.ToolRisk,
		}
		decision := iam.Evaluate(azReq)
		reason := ""
		switch decision {
		case iam.DecisionDeny:
			reason = "cross-tenant access denied or insufficient role for critical tool"
		case iam.DecisionNeedConfirmation:
			reason = "sensitive tool requires explicit confirmation"
		}
		authzDecisions.WithLabelValues(string(decision), req.Action, req.Resource.Type).Inc()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(AuthzResponse{Decision: string(decision), Reason: reason, TenantRole: tenantRole})
	})

	// ── Sensitive tool classification ──────────────────────────────────
	mux.HandleFunc("/iam/tools/classify", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			return
		}
		var req ClassifyRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			jsonError(w, http.StatusBadRequest, "invalid json body")
			return
		}
		if req.TenantID == "" || req.ToolName == "" {
			jsonError(w, http.StatusBadRequest, "tenant_id and tool_name are required")
			return
		}
		ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
		defer cancel()
		tenantRules := loadTenantToolRules(ctx, pool, req.TenantID)
		risk, conf := iam.ClassifyTool(req.TenantID, req.ToolName, tenantRules)
		source := "builtin"
		for _, rule := range tenantRules {
			if strings.EqualFold(rule.ToolName, req.ToolName) {
				source = "tenant_rule"
				break
			}
		}
		toolClassifications.WithLabelValues(risk, source).Inc()
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(ClassifyResponse{
			ToolName:             req.ToolName,
			RiskLevel:            risk,
			RequiresConfirmation: conf,
			Source:               source,
		})
	})

	mux.HandleFunc("/iam/tools", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			tenantID := r.URL.Query().Get("tenant_id")
			ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
			defer cancel()
			rows, err := pool.Query(ctx, `SELECT tenant_id, tool_name, risk_level, requires_confirmation, allowed_roles FROM platform_sensitive_tools WHERE ($1='' OR tenant_id=$1)`, tenantID)
			if err != nil {
				jsonError(w, http.StatusBadGateway, err.Error())
				return
			}
			defer rows.Close()
			tools := []map[string]any{}
			for rows.Next() {
				var tid, name, risk, allowed string
				var conf bool
				if err := rows.Scan(&tid, &name, &risk, &conf, &allowed); err == nil {
					tools = append(tools, map[string]any{"tenant_id": tid, "tool_name": name, "risk_level": risk, "requires_confirmation": conf, "allowed_roles": allowed})
				}
			}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{"count": len(tools), "tools": tools})
		case http.MethodPost:
			var req SensitiveToolInput
			if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
				jsonError(w, http.StatusBadRequest, "invalid json body")
				return
			}
			if req.TenantID == "" || req.ToolName == "" {
				jsonError(w, http.StatusBadRequest, "tenant_id and tool_name are required")
				return
			}
			if req.RiskLevel == "" {
				req.RiskLevel = iam.RiskNormal
			}
			ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
			defer cancel()
			_, err := pool.Exec(ctx, `INSERT INTO platform_sensitive_tools (tenant_id, tool_name, risk_level, requires_confirmation, allowed_roles) VALUES ($1,$2,$3,$4,$5) ON CONFLICT (tenant_id, tool_name) DO UPDATE SET risk_level=EXCLUDED.risk_level, requires_confirmation=EXCLUDED.requires_confirmation, allowed_roles=EXCLUDED.allowed_roles, updated_at=now()`,
				req.TenantID, req.ToolName, req.RiskLevel, req.RequiresConfirmation, req.AllowedRoles)
			if err != nil {
				jsonError(w, http.StatusBadGateway, err.Error())
				return
			}
			w.Header().Set("Content-Type", "application/json")
			_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "tool": req})
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	})


		// ── P3-2 Quota & billing ────────────────────────────────────
		mux.HandleFunc("/iam/quotas", func(w http.ResponseWriter, r *http.Request) {
			serveQuotas(pool, w, r)
		})
		mux.HandleFunc("/iam/quotas/", func(w http.ResponseWriter, r *http.Request) {
			serveQuotas(pool, w, r)
		})
		mux.HandleFunc("/iam/quotas/check", func(w http.ResponseWriter, r *http.Request) {
			serveQuotaCheck(pool, w, r)
		})
		mux.HandleFunc("/iam/usage", func(w http.ResponseWriter, r *http.Request) {
			serveUsage(pool, w, r)
		})
		mux.HandleFunc("/iam/billing", func(w http.ResponseWriter, r *http.Request) {
			serveBilling(pool, w, r)
		})
		mux.HandleFunc("/iam/billing/", func(w http.ResponseWriter, r *http.Request) {
			serveBilling(pool, w, r)
		})

		// ── P3-4 KMS & data masking ─────────────────────────────────
		masterKey := getMasterKey()
		mux.HandleFunc("/iam/secrets", func(w http.ResponseWriter, r *http.Request) {
			serveKMS(pool, w, r, masterKey)
		})
		mux.HandleFunc("/iam/secrets/reveal", func(w http.ResponseWriter, r *http.Request) {
			serveKMSReveal(pool, w, r, masterKey)
		})
		mux.HandleFunc("/iam/secrets/rotate", func(w http.ResponseWriter, r *http.Request) {
			serveKMSRotate(pool, w, r, masterKey)
		})
		mux.HandleFunc("/iam/mask", func(w http.ResponseWriter, r *http.Request) {
			serveDataMask(w, r)
		})

	mux.HandleFunc("/metrics", func(w http.ResponseWriter, r *http.Request) {
		obs.MetricsHandler().ServeHTTP(w, r)
	})

	addr := getenv("IAM_ADDR", ":8088")
	log.Printf("iam-service listening on %s (dev_mode=%v jwt_enforced=%v ttl=%s)", addr, issuer.IsDevMode(), !issuer.IsDevMode(), ttl)
	handler := obs.Middleware("iam-service", mux)
	log.Fatal(http.ListenAndServe(addr, handler))
}

// loadPrincipalScopes loads the roles, effective scopes, and tenant role for a
// user from PG. tenant role is derived as tenant_admin if the user holds the
// system tenant_admin role, else member if they hold member, else viewer.
func loadPrincipalScopes(ctx context.Context, pool *db.Pool, tenantID, userID string) ([]string, []string, string, error) {
	rows, err := pool.Query(ctx, `
		SELECT r.id, r.name
		FROM platform_user_roles ur
		JOIN platform_roles r ON r.id = ur.role_id
		WHERE ur.tenant_id = $1 AND ur.user_id = $2`, tenantID, userID)
	if err != nil {
		return nil, nil, "", err
	}
	defer rows.Close()
	var roleIDs, roleNames []string
	for rows.Next() {
		var id, name string
		if err := rows.Scan(&id, &name); err == nil {
			roleIDs = append(roleIDs, id)
			roleNames = append(roleNames, name)
		}
	}
	if len(roleIDs) == 0 {
		// No assigned roles: default to member for backward compatibility.
		roleNames = []string{iam.RoleMember}
		roleIDs = []string{"role-member"}
	}
	// Load scopes for the roles.
	scopeRows, err := pool.Query(ctx, `SELECT scope FROM platform_role_permissions WHERE role_id = ANY($1)`, roleIDs)
	if err != nil {
		return roleNames, iam.ScopesForRoles(roleNames), deriveTenantRole(roleNames), nil
	}
	defer scopeRows.Close()
	seen := map[string]struct{}{}
	scopes := make([]string, 0, 16)
	for scopeRows.Next() {
		var s string
		if err := scopeRows.Scan(&s); err == nil {
			if _, ok := seen[s]; ok {
				continue
			}
			seen[s] = struct{}{}
			scopes = append(scopes, s)
		}
	}
	if len(scopes) == 0 {
		scopes = iam.ScopesForRoles(roleNames)
	}
	return roleNames, scopes, deriveTenantRole(roleNames), nil
}

// deriveTenantRole picks the strongest tenant role from the role name list.
func deriveTenantRole(roleNames []string) string {
	for _, n := range roleNames {
		if n == iam.RoleSuperAdmin {
			return iam.RoleSuperAdmin
		}
	}
	for _, n := range roleNames {
		if n == iam.RoleTenantAdmin {
			return iam.RoleTenantAdmin
		}
	}
	for _, n := range roleNames {
		if n == iam.RoleMember {
			return iam.RoleMember
		}
	}
	return iam.RoleViewer
}

// loadTenantToolRules loads the sensitive-tool classification rows for a tenant.
func loadTenantToolRules(ctx context.Context, pool *db.Pool, tenantID string) []iam.SensitiveToolRule {
	rows, err := pool.Query(ctx, `SELECT tool_name, risk_level, requires_confirmation, allowed_roles FROM platform_sensitive_tools WHERE tenant_id=$1`, tenantID)
	if err != nil {
		return nil
	}
	defer rows.Close()
	var rules []iam.SensitiveToolRule
	for rows.Next() {
		var r iam.SensitiveToolRule
		var allowed string
		if err := rows.Scan(&r.ToolName, &r.RiskLevel, &r.RequiresConfirmation, &allowed); err == nil {
			r.TenantID = tenantID
			if allowed != "" {
				r.AllowedRoles = strings.Split(allowed, ",")
				for i, r2 := range r.AllowedRoles {
					r.AllowedRoles[i] = strings.TrimSpace(r2)
				}
			}
			rules = append(rules, r)
		}
	}
	return rules
}

// ── helpers ────────────────────────────────────────────────────────────

func jsonError(w http.ResponseWriter, code int, msg string) {
	w.WriteHeader(code)
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{"error": msg})
}

func fallback(primary, secondary string) string {
	if primary != "" {
		return primary
	}
	return secondary
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func getenvDuration(key string, fallback time.Duration) time.Duration {
	if v := os.Getenv(key); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			return d
		}
	}
	return fallback
}

// randID returns a short timestamp-based id. Good enough for non-colliding
// tenant/role ids in a single-process service; production deployments should
// use a UUID library, but this avoids adding a dependency.
func randID() string {
	return time.Now().UTC().Format("20060102T150405.000")
}

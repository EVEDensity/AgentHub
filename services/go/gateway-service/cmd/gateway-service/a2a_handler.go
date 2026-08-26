// Package main — A2A (Agent-to-Agent) Protocol Handler (P2-2)
//
// Implements Google's Agent-to-Agent open standard for agent interoperability:
//   - Agent Card publishing (/.well-known/agent-card.json)
//   - Agent discovery registry
//   - JSON-RPC 2.0 task API (tasks/send, tasks/get, tasks/cancel)
//   - A2A agent CRUD for external agent registration
//   - PostgreSQL persistence with in-memory fallback
//   - TLS/mTLS for outbound A2A calls
//   - Agent Card signature verification
//   - Prometheus metrics
//
// Spec: https://github.com/google/A2A
package main

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/agenthub/platform/shared/db"
	"github.com/prometheus/client_golang/prometheus"
)

const maxAgentCardResponseBytes int64 = 1 << 20

const maxA2ATaskResponseBytes int64 = 1 << 20

// a2aDispatchModeFromEnv resolves the outbound dispatch ownership. `gateway`
// (default) keeps the legacy request-path forwarding for compatibility;
// `runner` delegates the remote dispatch to a Runner-supervised outbound A2A
// worker (ADR-0053) so the Gateway never dispatches the same attempt twice.
func a2aDispatchModeFromEnv() string {
	mode := strings.ToLower(strings.TrimSpace(os.Getenv("A2A_DISPATCH_MODE")))
	if mode != "gateway" && mode != "runner" {
		return "gateway"
	}
	return mode
}

func a2aGatewayDispatchEnabled() bool {
	return a2aDispatchModeFromEnv() == "gateway"
}

// ── A2A Prometheus Metrics ────────────────────────────────────────────

var (
	// a2aAgentRegistrations tracks agent register/deregister operations.
	a2aAgentRegistrations = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "a2a_agent_registrations_total",
			Help: "Total A2A agent registrations and deregistrations.",
		},
		[]string{"action"},
	)

	// a2aDiscoveryRequests tracks discovery queries by capability.
	a2aDiscoveryRequests = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "a2a_discovery_requests_total",
			Help: "Total A2A discovery requests by capability.",
		},
		[]string{"capability"},
	)

	// a2aTaskRequests tracks task API calls by method.
	a2aTaskRequests = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "a2a_task_requests_total",
			Help: "Total A2A task API requests by method.",
		},
		[]string{"method"},
	)

	// a2aTaskLatency tracks task operation latency in seconds.
	a2aTaskLatency = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{
			Name:    "a2a_task_latency_seconds",
			Help:    "A2A task operation latency in seconds.",
			Buckets: prometheus.DefBuckets,
		},
		[]string{"method"},
	)

	a2aAgentTrustDecisions = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "a2a_agent_trust_decisions_total",
			Help: "A2A Agent Card trust decisions without agent or key labels.",
		},
		[]string{"decision"},
	)
)

func init() {
	// Register A2A metrics via the shared observability registry.
	// MustRegister is provided by the obs package.
	// We use a local init that calls MustRegister after obs init runs.
	// (obs init runs first because obs.go is imported first; our metrics
	//  are registered here which is after obs.init in lexical order.)
	// If MustRegister is not available yet, we use prometheus.DefaultRegisterer
	// as fallback. The gateway service imports obs which sets up the shared
	// Registry, so MustRegister is available.
	//
	// Note: We register on the shared obs.Registry so these metrics appear
	// alongside all other platform metrics at /metrics.
	prometheus.MustRegister(a2aAgentRegistrations)
	prometheus.MustRegister(a2aDiscoveryRequests)
	prometheus.MustRegister(a2aTaskRequests)
	prometheus.MustRegister(a2aTaskLatency)
	prometheus.MustRegister(a2aAgentTrustDecisions)
}

// ── A2A Protocol Types ───────────────────────────────────────────────

// AgentCard is the standardized A2A agent descriptor (JSON-LD).
// Published at /.well-known/agent-card.json per the A2A spec.
type AgentCard struct {
	ProtocolVersion string            `json:"protocolVersion"` // e.g. "1.0"
	Name            string            `json:"name"`
	Description     string            `json:"description"`
	URL             string            `json:"url"` // base URL of this agent
	Provider        *AgentProvider    `json:"provider,omitempty"`
	Capabilities    AgentCapabilities `json:"capabilities"`
	Skills          []AgentSkill      `json:"skills"`
	Endpoints       AgentEndpoints    `json:"endpoints"`
	AuthSchemes     []AuthScheme      `json:"authSchemes,omitempty"`
	Version         string            `json:"version,omitempty"`
	Documentation   string            `json:"documentation,omitempty"`
	IconURL         string            `json:"iconUrl,omitempty"`
	// Extended metadata (AgentHub-specific)
	TenantID   string   `json:"tenantId,omitempty"`
	Source     string   `json:"source,omitempty"` // "internal" | "external"
	Status     string   `json:"status,omitempty"` // "active" | "inactive" | "error"
	LastSeenAt string   `json:"lastSeenAt,omitempty"`
	CreatedAt  string   `json:"createdAt,omitempty"`
	Tags       []string `json:"tags,omitempty"`
	// Security (A2A extended)
	Security  *AgentSecurity `json:"security,omitempty"`
	Signature string         `json:"signature,omitempty"`
}

type AgentProvider struct {
	Name    string `json:"name,omitempty"`
	URL     string `json:"url,omitempty"`
	OrgName string `json:"organization,omitempty"`
}

type AgentCapabilities struct {
	Streaming         bool `json:"streaming"`
	PushNotifications bool `json:"pushNotifications"`
	StateTransition   bool `json:"stateTransitionHistory"`
	Multimodal        bool `json:"multimodal,omitempty"`
	CodeExecution     bool `json:"codeExecution,omitempty"`
}

type AgentSkill struct {
	ID           string         `json:"id"`
	Name         string         `json:"name"`
	Description  string         `json:"description,omitempty"`
	Tags         []string       `json:"tags"`
	Examples     []string       `json:"examples,omitempty"`
	InputSchema  map[string]any `json:"inputSchema,omitempty"`
	OutputSchema map[string]any `json:"outputSchema,omitempty"`
}

type AgentEndpoints struct {
	TaskAPI    string `json:"taskApi"` // e.g. "https://agent.example.com/a2a/tasks"
	Streaming  string `json:"streaming,omitempty"`
	WebhookURL string `json:"webhookUrl,omitempty"`
}

type AuthScheme struct {
	Type        string   `json:"type"` // "bearer", "oauth2", "apiKey"
	Description string   `json:"description,omitempty"`
	TokenURL    string   `json:"tokenUrl,omitempty"`
	Scopes      []string `json:"scopes,omitempty"`
}

// AgentSecurity holds public key material for signature verification.
type AgentSecurity struct {
	PublicKey    string `json:"public_key,omitempty"`
	KeyAlgorithm string `json:"key_algorithm,omitempty"` // "ed25519", "rsa", etc.
	KeyID        string `json:"key_id,omitempty"`
	KeyVersion   string `json:"key_version,omitempty"`
}

// ── Task API Types (JSON-RPC 2.0) ───────────────────────────────────

type A2ATaskRequest struct {
	JSONRPC string         `json:"jsonrpc"`
	Method  string         `json:"method"`
	Params  map[string]any `json:"params"`
	ID      any            `json:"id"`
}

type A2ATaskResponse struct {
	JSONRPC string    `json:"jsonrpc"`
	Result  any       `json:"result,omitempty"`
	Error   *A2AError `json:"error,omitempty"`
	ID      any       `json:"id"`
}

type A2AError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

type A2ATask struct {
	ID         string        `json:"id"`
	SessionID  string        `json:"sessionId,omitempty"`
	Status     string        `json:"status"`
	MissionID  string        `json:"missionId,omitempty"`
	WorkUnitID string        `json:"workUnitId,omitempty"`
	Message    *A2AMessage   `json:"message,omitempty"`
	Artifacts  []A2AArtifact `json:"artifacts,omitempty"`
	Evidence   []A2AEvidence `json:"evidence,omitempty"`
	CreatedAt  string        `json:"createdAt,omitempty"`
	UpdatedAt  string        `json:"updatedAt,omitempty"`
}

type A2AMessage struct {
	Role  string           `json:"role"`
	Parts []A2AMessagePart `json:"parts"`
}

type A2AMessagePart struct {
	Type string         `json:"type"` // "text" | "file" | "data"
	Text string         `json:"text,omitempty"`
	File *A2AFile       `json:"file,omitempty"`
	Data map[string]any `json:"data,omitempty"`
}

type A2AFile struct {
	Name     string `json:"name,omitempty"`
	MimeType string `json:"mimeType,omitempty"`
	Bytes    string `json:"bytes,omitempty"` // base64
	URL      string `json:"url,omitempty"`
}

type A2AArtifact struct {
	ArtifactID string           `json:"artifactId"`
	Name       string           `json:"name"`
	Parts      []A2AMessagePart `json:"parts"`
}

type A2AArtifactRef struct {
	ID     string `json:"id"`
	Digest string `json:"digest"`
}

type A2AVerifier struct {
	ID                  string `json:"id"`
	Version             string `json:"version"`
	ConfigurationDigest string `json:"configurationDigest,omitempty"`
}

type A2AEvidence struct {
	EvidenceID    string           `json:"evidenceId"`
	WorkUnitID    string           `json:"workUnitId"`
	CriterionID   string           `json:"criterionId"`
	Verifier      A2AVerifier      `json:"verifier"`
	Verdict       string           `json:"verdict"`
	ArtifactRefs  []A2AArtifactRef `json:"artifactRefs"`
	Summary       string           `json:"summary"`
	GeneratedAt   string           `json:"generatedAt"`
	IntegrityHash string           `json:"integrityHash"`
}

// ── TLS Configuration ────────────────────────────────────────────────

// A2ATLSConfig holds TLS/mTLS settings for outbound A2A calls.
type A2ATLSConfig struct {
	CertFile     string
	KeyFile      string
	CAFile       string
	Enabled      bool
	StrictVerify bool
}

// A2ATrustPolicy controls whether Agent Card identity is accepted after
// cryptographic signature verification. Trusted keys are indexed by origin and
// may contain multiple keys to support rotation.
type A2ATrustPolicy struct {
	AllowUnsigned    bool
	RequirePinnedKey bool
	TrustedKeys      map[string]map[string]struct{}
}

func a2aTrustPolicyFromEnv() (A2ATrustPolicy, error) {
	allowUnsigned, err := parseA2ABoolEnv("A2A_ALLOW_UNSIGNED_CARDS", false)
	if err != nil {
		return A2ATrustPolicy{}, err
	}
	requirePinnedKey, err := parseA2ABoolEnv("A2A_REQUIRE_PINNED_KEYS", false)
	if err != nil {
		return A2ATrustPolicy{}, err
	}
	if allowUnsigned && requirePinnedKey {
		return A2ATrustPolicy{}, errors.New("A2A_ALLOW_UNSIGNED_CARDS and A2A_REQUIRE_PINNED_KEYS cannot both be true")
	}
	policy := A2ATrustPolicy{
		AllowUnsigned:    allowUnsigned,
		RequirePinnedKey: requirePinnedKey,
		TrustedKeys:      make(map[string]map[string]struct{}),
	}
	rawPins := strings.TrimSpace(os.Getenv("A2A_TRUSTED_PUBLIC_KEYS_JSON"))
	if rawPins == "" {
		if requirePinnedKey {
			return A2ATrustPolicy{}, errors.New("A2A_REQUIRE_PINNED_KEYS requires A2A_TRUSTED_PUBLIC_KEYS_JSON")
		}
		return policy, nil
	}
	var configured map[string][]string
	if err := json.Unmarshal([]byte(rawPins), &configured); err != nil {
		return A2ATrustPolicy{}, fmt.Errorf("parse A2A_TRUSTED_PUBLIC_KEYS_JSON: %w", err)
	}
	if requirePinnedKey && len(configured) == 0 {
		return A2ATrustPolicy{}, errors.New("A2A_REQUIRE_PINNED_KEYS requires at least one trusted A2A origin")
	}
	for rawOrigin, rawKeys := range configured {
		originURL, err := parseA2AHTTPURL(rawOrigin)
		if err != nil {
			return A2ATrustPolicy{}, fmt.Errorf("invalid trusted A2A origin %q: %w", rawOrigin, err)
		}
		if (originURL.Path != "" && originURL.Path != "/") || originURL.RawQuery != "" {
			return A2ATrustPolicy{}, fmt.Errorf("invalid trusted A2A origin %q: origin must not include a path or query", rawOrigin)
		}
		origin := strings.ToLower(originURL.Scheme + "://" + originURL.Host)
		if len(rawKeys) == 0 {
			return A2ATrustPolicy{}, fmt.Errorf("trusted A2A origin %q has no public keys", rawOrigin)
		}
		pins := policy.TrustedKeys[origin]
		if pins == nil {
			pins = make(map[string]struct{}, len(rawKeys))
			policy.TrustedKeys[origin] = pins
		}
		for _, rawKey := range rawKeys {
			key := strings.ToLower(strings.TrimSpace(rawKey))
			decoded, err := hex.DecodeString(key)
			if err != nil || len(decoded) != ed25519.PublicKeySize {
				return A2ATrustPolicy{}, fmt.Errorf("trusted A2A origin %q contains an invalid Ed25519 public key", rawOrigin)
			}
			pins[key] = struct{}{}
		}
	}
	return policy, nil
}

func parseA2ABoolEnv(name string, defaultValue bool) (bool, error) {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return defaultValue, nil
	}
	value, err := strconv.ParseBool(raw)
	if err != nil {
		return false, fmt.Errorf("%s must be a boolean: %w", name, err)
	}
	return value, nil
}

// a2aTLSConfigFromEnv reads TLS configuration from environment variables.
func a2aTLSConfigFromEnv() *A2ATLSConfig {
	enabled := os.Getenv("A2A_TLS_ENABLED") == "true"
	strict := os.Getenv("A2A_TLS_STRICT") == "true"
	return &A2ATLSConfig{
		CertFile:     os.Getenv("A2A_TLS_CERT"),
		KeyFile:      os.Getenv("A2A_TLS_KEY"),
		CAFile:       os.Getenv("A2A_TLS_CA"),
		Enabled:      enabled,
		StrictVerify: strict,
	}
}

// a2aHTTPClient returns an *http.Client configured with the A2A TLS settings.
func a2aHTTPClient(cfg *A2ATLSConfig) *http.Client {
	if cfg == nil || !cfg.Enabled {
		return &http.Client{
			Timeout: 30 * time.Second,
		}
	}

	tlsConfig := &tls.Config{
		MinVersion: tls.VersionTLS12,
	}

	// Load client certificate if provided
	if cfg.CertFile != "" && cfg.KeyFile != "" {
		cert, err := tls.LoadX509KeyPair(cfg.CertFile, cfg.KeyFile)
		if err != nil {
			log.Printf("a2a: WARNING failed to load client cert (%s, %s): %v", cfg.CertFile, cfg.KeyFile, err)
		} else {
			tlsConfig.Certificates = []tls.Certificate{cert}
		}
	}

	// Load CA certificate for mTLS/server verification
	if cfg.CAFile != "" {
		caCert, err := os.ReadFile(cfg.CAFile)
		if err != nil {
			log.Printf("a2a: WARNING failed to read CA file %s: %v", cfg.CAFile, err)
		} else {
			caCertPool := x509.NewCertPool()
			if caCertPool.AppendCertsFromPEM(caCert) {
				tlsConfig.RootCAs = caCertPool
			} else {
				log.Printf("a2a: WARNING failed to parse CA cert from %s", cfg.CAFile)
			}
		}
	}

	if cfg.StrictVerify {
		tlsConfig.InsecureSkipVerify = false
	} else {
		tlsConfig.InsecureSkipVerify = !cfg.Enabled
	}

	transport := &http.Transport{
		TLSClientConfig: tlsConfig,
	}

	return &http.Client{
		Transport: transport,
		Timeout:   30 * time.Second,
	}
}

// ── Agent Registry (PG-backed with in-memory fallback) ──────────────

type a2aRegistry struct {
	mu       sync.RWMutex
	agents   map[string]*AgentCard // keyed by agent URL
	pool     *db.Pool              // PostgreSQL pool (nil = in-memory only)
	tlsCfg   *A2ATLSConfig
	selfCard *AgentCard
}

// a2aReg is the global agent registry, used by both handler and task forwarding.
var a2aReg = &a2aRegistry{agents: make(map[string]*AgentCard)}

// a2aPGStore provides PostgreSQL CRUD operations for the platform_a2a_agents table.
type a2aPGStore struct {
	pool *db.Pool
}

func (s *a2aPGStore) List(ctx context.Context) ([]*AgentCard, error) {
	rows, err := s.pool.Query(ctx,
		`SELECT name, description, url, protocol_version, provider_name, provider_url, provider_org,
		        capabilities, skills, endpoints, auth_schemes, version, documentation, icon_url,
		        source, status, last_seen_at, tags, created_at
		 FROM platform_a2a_agents
		 ORDER BY created_at DESC`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var cards []*AgentCard
	for rows.Next() {
		card := &AgentCard{}
		var capsJSON, skillsJSON, endpointsJSON, authJSON []byte
		var providerName, providerURL, providerOrg *string
		var lastSeenAt time.Time
		var createdAt time.Time
		var version, documentation, iconURL *string

		if err := rows.Scan(&card.Name, &card.Description, &card.URL, &card.ProtocolVersion,
			&providerName, &providerURL, &providerOrg,
			&capsJSON, &skillsJSON, &endpointsJSON, &authJSON,
			&version, &documentation, &iconURL,
			&card.Source, &card.Status, &lastSeenAt, &card.Tags, &createdAt); err != nil {
			log.Printf("a2a: pg store scan error: %v", err)
			continue
		}

		// Parse JSONB fields
		if len(capsJSON) > 0 {
			_ = json.Unmarshal(capsJSON, &card.Capabilities)
		}
		if len(skillsJSON) > 0 {
			_ = json.Unmarshal(skillsJSON, &card.Skills)
		}
		if len(endpointsJSON) > 0 {
			_ = json.Unmarshal(endpointsJSON, &card.Endpoints)
		}
		if len(authJSON) > 0 {
			_ = json.Unmarshal(authJSON, &card.AuthSchemes)
		}

		// Provider
		if providerName != nil || providerURL != nil || providerOrg != nil {
			card.Provider = &AgentProvider{}
			if providerName != nil {
				card.Provider.Name = *providerName
			}
			if providerURL != nil {
				card.Provider.URL = *providerURL
			}
			if providerOrg != nil {
				card.Provider.OrgName = *providerOrg
			}
		}

		if version != nil {
			card.Version = *version
		}
		if documentation != nil {
			card.Documentation = *documentation
		}
		if iconURL != nil {
			card.IconURL = *iconURL
		}
		if !lastSeenAt.IsZero() {
			card.LastSeenAt = lastSeenAt.Format(time.RFC3339)
		}
		card.CreatedAt = createdAt.Format(time.RFC3339)

		cards = append(cards, card)
	}
	return cards, nil
}

func (s *a2aPGStore) Get(ctx context.Context, url string) (*AgentCard, error) {
	card := &AgentCard{}
	var capsJSON, skillsJSON, endpointsJSON, authJSON []byte
	var providerName, providerURL, providerOrg *string
	var lastSeenAt time.Time
	var createdAt time.Time
	var version, documentation, iconURL *string

	err := s.pool.QueryRow(ctx,
		`SELECT name, description, url, protocol_version, provider_name, provider_url, provider_org,
		        capabilities, skills, endpoints, auth_schemes, version, documentation, icon_url,
		        source, status, last_seen_at, tags, created_at
		 FROM platform_a2a_agents WHERE url=$1`, url).
		Scan(&card.Name, &card.Description, &card.URL, &card.ProtocolVersion,
			&providerName, &providerURL, &providerOrg,
			&capsJSON, &skillsJSON, &endpointsJSON, &authJSON,
			&version, &documentation, &iconURL,
			&card.Source, &card.Status, &lastSeenAt, &card.Tags, &createdAt)
	if err != nil {
		return nil, err
	}

	if len(capsJSON) > 0 {
		_ = json.Unmarshal(capsJSON, &card.Capabilities)
	}
	if len(skillsJSON) > 0 {
		_ = json.Unmarshal(skillsJSON, &card.Skills)
	}
	if len(endpointsJSON) > 0 {
		_ = json.Unmarshal(endpointsJSON, &card.Endpoints)
	}
	if len(authJSON) > 0 {
		_ = json.Unmarshal(authJSON, &card.AuthSchemes)
	}

	if providerName != nil || providerURL != nil || providerOrg != nil {
		card.Provider = &AgentProvider{}
		if providerName != nil {
			card.Provider.Name = *providerName
		}
		if providerURL != nil {
			card.Provider.URL = *providerURL
		}
		if providerOrg != nil {
			card.Provider.OrgName = *providerOrg
		}
	}
	if version != nil {
		card.Version = *version
	}
	if documentation != nil {
		card.Documentation = *documentation
	}
	if iconURL != nil {
		card.IconURL = *iconURL
	}
	if !lastSeenAt.IsZero() {
		card.LastSeenAt = lastSeenAt.Format(time.RFC3339)
	}
	card.CreatedAt = createdAt.Format(time.RFC3339)

	return card, nil
}

func (s *a2aPGStore) Upsert(ctx context.Context, card *AgentCard) error {
	capsJSON, _ := json.Marshal(card.Capabilities)
	skillsJSON, _ := json.Marshal(card.Skills)
	endpointsJSON, _ := json.Marshal(card.Endpoints)
	authJSON, _ := json.Marshal(card.AuthSchemes)

	var providerName, providerURL, providerOrg *string
	if card.Provider != nil {
		if card.Provider.Name != "" {
			providerName = &card.Provider.Name
		}
		if card.Provider.URL != "" {
			providerURL = &card.Provider.URL
		}
		if card.Provider.OrgName != "" {
			providerOrg = &card.Provider.OrgName
		}
	}

	var ver, doc, icon *string
	if card.Version != "" {
		ver = &card.Version
	}
	if card.Documentation != "" {
		doc = &card.Documentation
	}
	if card.IconURL != "" {
		icon = &card.IconURL
	}

	tenantID := card.TenantID
	if tenantID == "" {
		tenantID = "default"
	}

	_, err := s.pool.Exec(ctx,
		`INSERT INTO platform_a2a_agents (tenant_id, name, description, url, protocol_version,
		 provider_name, provider_url, provider_org, capabilities, skills, endpoints,
		 auth_schemes, version, documentation, icon_url, source, status, last_seen_at, tags)
		 VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19)
		 ON CONFLICT (tenant_id, url) DO UPDATE SET
		   name=EXCLUDED.name, description=EXCLUDED.description, protocol_version=EXCLUDED.protocol_version,
		   provider_name=EXCLUDED.provider_name, provider_url=EXCLUDED.provider_url,
		   provider_org=EXCLUDED.provider_org, capabilities=EXCLUDED.capabilities,
		   skills=EXCLUDED.skills, endpoints=EXCLUDED.endpoints, auth_schemes=EXCLUDED.auth_schemes,
		   version=EXCLUDED.version, documentation=EXCLUDED.documentation, icon_url=EXCLUDED.icon_url,
		   status=EXCLUDED.status, last_seen_at=EXCLUDED.last_seen_at, tags=EXCLUDED.tags,
		   updated_at=now()`,
		tenantID, card.Name, card.Description, card.URL, card.ProtocolVersion,
		providerName, providerURL, providerOrg, capsJSON, skillsJSON, endpointsJSON,
		authJSON, ver, doc, icon, card.Source, card.Status, time.Now().UTC(), card.Tags)
	return err
}

func (s *a2aPGStore) Delete(ctx context.Context, url string) error {
	_, err := s.pool.Exec(ctx, `DELETE FROM platform_a2a_agents WHERE url=$1`, url)
	return err
}

func (s *a2aPGStore) Discover(ctx context.Context, capability string) ([]*AgentCard, error) {
	// Search by skills tags or tags array matching capability
	rows, err := s.pool.Query(ctx,
		`SELECT name, description, url, protocol_version, provider_name, provider_url, provider_org,
		        capabilities, skills, endpoints, auth_schemes, version, documentation, icon_url,
		        source, status, last_seen_at, tags, created_at
		 FROM platform_a2a_agents
		 WHERE skills::text ILIKE $1 OR $2 = ANY(tags)
		 ORDER BY created_at DESC LIMIT 100`,
		"%"+capability+"%", capability)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var cards []*AgentCard
	for rows.Next() {
		card := &AgentCard{}
		var capsJSON, skillsJSON, endpointsJSON, authJSON []byte
		var providerName, providerURL, providerOrg *string
		var lastSeenAt time.Time
		var createdAt time.Time
		var version, documentation, iconURL *string

		if err := rows.Scan(&card.Name, &card.Description, &card.URL, &card.ProtocolVersion,
			&providerName, &providerURL, &providerOrg,
			&capsJSON, &skillsJSON, &endpointsJSON, &authJSON,
			&version, &documentation, &iconURL,
			&card.Source, &card.Status, &lastSeenAt, &card.Tags, &createdAt); err != nil {
			continue
		}

		if len(capsJSON) > 0 {
			_ = json.Unmarshal(capsJSON, &card.Capabilities)
		}
		if len(skillsJSON) > 0 {
			_ = json.Unmarshal(skillsJSON, &card.Skills)
		}
		if len(endpointsJSON) > 0 {
			_ = json.Unmarshal(endpointsJSON, &card.Endpoints)
		}
		if len(authJSON) > 0 {
			_ = json.Unmarshal(authJSON, &card.AuthSchemes)
		}

		if providerName != nil || providerURL != nil || providerOrg != nil {
			card.Provider = &AgentProvider{}
			if providerName != nil {
				card.Provider.Name = *providerName
			}
			if providerURL != nil {
				card.Provider.URL = *providerURL
			}
			if providerOrg != nil {
				card.Provider.OrgName = *providerOrg
			}
		}
		if version != nil {
			card.Version = *version
		}
		if documentation != nil {
			card.Documentation = *documentation
		}
		if iconURL != nil {
			card.IconURL = *iconURL
		}
		if !lastSeenAt.IsZero() {
			card.LastSeenAt = lastSeenAt.Format(time.RFC3339)
		}
		card.CreatedAt = createdAt.Format(time.RFC3339)

		cards = append(cards, card)
	}
	return cards, nil
}

// ── In-memory registry methods ──────────────────────────────────────

func (r *a2aRegistry) registerMem(card *AgentCard) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.agents[card.URL] = card
}

func (r *a2aRegistry) unregisterMem(url string) {
	r.mu.Lock()
	defer r.mu.Unlock()
	delete(r.agents, url)
}

func (r *a2aRegistry) getMem(url string) *AgentCard {
	r.mu.RLock()
	defer r.mu.RUnlock()
	return r.agents[url]
}

func (r *a2aRegistry) listAllMem() []*AgentCard {
	r.mu.RLock()
	defer r.mu.RUnlock()
	cards := make([]*AgentCard, 0, len(r.agents))
	for _, c := range r.agents {
		cards = append(cards, c)
	}
	return cards
}

func (r *a2aRegistry) discoverMem(capabilities []string) []*AgentCard {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if len(capabilities) == 0 {
		cards := make([]*AgentCard, 0, len(r.agents))
		for _, c := range r.agents {
			cards = append(cards, c)
		}
		return cards
	}
	var matched []*AgentCard
	for _, card := range r.agents {
		for _, skill := range card.Skills {
			for _, tag := range skill.Tags {
				for _, cap := range capabilities {
					if strings.EqualFold(tag, cap) {
						matched = append(matched, card)
						goto nextCardMem
					}
				}
			}
		}
	nextCardMem:
	}
	return matched
}

// ── Registry write-through methods (PG + memory) ────────────────────

func (r *a2aRegistry) register(card *AgentCard) {
	// Always update in-memory
	r.registerMem(card)

	// Also persist to PG if available
	if r.pool != nil {
		store := &a2aPGStore{pool: r.pool}
		if err := store.Upsert(context.Background(), card); err != nil {
			log.Printf("a2a: PG upsert error for %s: %v", card.URL, err)
		}
	}
}

func (r *a2aRegistry) unregister(url string) {
	r.unregisterMem(url)

	if r.pool != nil {
		store := &a2aPGStore{pool: r.pool}
		if err := store.Delete(context.Background(), url); err != nil {
			log.Printf("a2a: PG delete error for %s: %v", url, err)
		}
	}
}

func (r *a2aRegistry) listAll() []*AgentCard {
	if r.pool != nil {
		store := &a2aPGStore{pool: r.pool}
		cards, err := store.List(context.Background())
		if err == nil && len(cards) > 0 {
			// Merge with self card (always in-memory)
			found := false
			for _, c := range cards {
				if c.URL == r.selfCard.URL {
					found = true
					break
				}
			}
			if !found {
				cards = append([]*AgentCard{r.selfCard}, cards...)
			}
			return cards
		}
		// Fall back to in-memory on error
		log.Printf("a2a: PG list error: %v, falling back to in-memory", err)
	}
	return r.listAllMem()
}

func (r *a2aRegistry) discover(capabilities []string) []*AgentCard {
	for _, cap := range capabilities {
		a2aDiscoveryRequests.WithLabelValues(cap).Inc()
	}

	if r.pool != nil && len(capabilities) > 0 {
		store := &a2aPGStore{pool: r.pool}
		var allMatched []*AgentCard
		seen := make(map[string]bool)
		for _, cap := range capabilities {
			cards, err := store.Discover(context.Background(), cap)
			if err != nil {
				log.Printf("a2a: PG discover error for '%s': %v, falling back to in-memory", cap, err)
				return r.discoverMem(capabilities)
			}
			for _, c := range cards {
				if !seen[c.URL] {
					seen[c.URL] = true
					allMatched = append(allMatched, c)
				}
			}
		}
		return allMatched
	}
	return r.discoverMem(capabilities)
}

// ── AgentHub Self Agent Card ────────────────────────────────────────

func buildAgentHubCard(baseURL string) *AgentCard {
	return &AgentCard{
		ProtocolVersion: "1.0",
		Name:            "AgentHub Platform",
		Description:     "Enterprise self-hosted multi-agent collaboration platform with DAG orchestration, 4-layer memory engine, and hybrid RAG retrieval.",
		URL:             strings.TrimRight(baseURL, "/"),
		Version:         "5.1.0",
		Provider: &AgentProvider{
			Name:    "AgentHub",
			URL:     strings.TrimRight(baseURL, "/"),
			OrgName: "AgentHub Community",
		},
		Capabilities: AgentCapabilities{
			Streaming:         true,
			PushNotifications: true,
			StateTransition:   true,
			Multimodal:        true,
			CodeExecution:     true,
		},
		Skills: []AgentSkill{
			{
				ID:          "knowledge_search",
				Name:        "Knowledge Search",
				Description: "Semantic search across all knowledge bases with hybrid retrieval (Qdrant + OpenSearch)",
				Tags:        []string{"rag", "search", "knowledge", "retrieval"},
				Examples:    []string{"Find documents about AgentNet DAG scheduling", "Search codebase for auth middleware"},
			},
			{
				ID:          "agent_orchestration",
				Name:        "Agent Orchestration",
				Description: "Multi-agent DAG orchestration with 4 dispatch strategies (round-robin, least-loaded, capability-match, cost-optimized)",
				Tags:        []string{"orchestration", "multi-agent", "dag", "workflow"},
				Examples:    []string{"Dispatch a code review task to the most capable agent"},
			},
			{
				ID:          "code_generation",
				Name:        "Code Generation",
				Description: "Generate, review, and refactor code in multiple languages with sandboxed execution",
				Tags:        []string{"code", "generation", "review", "execution"},
				Examples:    []string{"Generate a REST API endpoint in Go", "Review this PR for security issues"},
			},
			{
				ID:          "artifact_preview",
				Name:        "Artifact Preview",
				Description: "Preview generated artifacts: web pages, documents, presentations, and code",
				Tags:        []string{"artifact", "preview", "rendering"},
			},
		},
		Endpoints: AgentEndpoints{
			TaskAPI:   strings.TrimRight(baseURL, "/") + "/platform/a2a/inbox",
			Streaming: strings.TrimRight(baseURL, "/") + "/platform/a2a/stream",
		},
		AuthSchemes: []AuthScheme{
			{
				Type:        "bearer",
				Description: "JWT Bearer token from AgentHub IAM",
				Scopes:      []string{"a2a:read", "a2a:write", "agent:read"},
			},
		},
		Source:    "internal",
		Status:    "active",
		CreatedAt: time.Now().UTC().Format(time.RFC3339),
	}
}

// ── Signature Verification ──────────────────────────────────────────

// VerifyAgentCardSignature verifies the agent card signature if present.
// Returns an error if signature verification fails; returns nil when:
// - No signature field is present (not an error, just unsigned)
// - Signature is valid against the agent's public key
func VerifyAgentCardSignature(card *AgentCard) error {
	if card == nil {
		return errors.New("agent card is required")
	}
	if card.Signature == "" {
		return nil
	}

	if card.Security == nil || card.Security.PublicKey == "" {
		// Has signature but no public key — can't verify
		return fmt.Errorf("agent card has signature but no public key in security.public_key")
	}

	unsigned := *card
	unsigned.Signature = ""
	payload, err := json.Marshal(&unsigned)
	if err != nil {
		return fmt.Errorf("failed to marshal card for signature verification: %w", err)
	}

	keyAlgo := strings.ToLower(strings.TrimSpace(card.Security.KeyAlgorithm))
	if keyAlgo == "" {
		keyAlgo = "ed25519"
	}
	if keyAlgo != "ed25519" {
		return fmt.Errorf("unsupported agent card key algorithm %q", keyAlgo)
	}
	publicKey, err := hex.DecodeString(strings.TrimSpace(card.Security.PublicKey))
	if err != nil || len(publicKey) != ed25519.PublicKeySize {
		return errors.New("agent card Ed25519 public key is invalid")
	}
	signature, err := hex.DecodeString(strings.TrimSpace(card.Signature))
	if err != nil || len(signature) != ed25519.SignatureSize {
		return errors.New("agent card Ed25519 signature is invalid")
	}
	if !ed25519.Verify(ed25519.PublicKey(publicKey), payload, signature) {
		return errors.New("agent card signature verification failed")
	}
	return nil
}

func VerifyAgentCardTrust(card *AgentCard, agentURL string, policy A2ATrustPolicy) error {
	if card == nil {
		a2aAgentTrustDecisions.WithLabelValues("rejected").Inc()
		return errors.New("agent card is required")
	}
	if card.Signature == "" {
		if !policy.AllowUnsigned || policy.RequirePinnedKey {
			a2aAgentTrustDecisions.WithLabelValues("rejected").Inc()
			return errors.New("unsigned Agent Card rejected by trust policy")
		}
		a2aAgentTrustDecisions.WithLabelValues("unsigned_allowed").Inc()
		log.Printf("a2a: WARNING unsigned Agent Card allowed by compatibility policy for '%s' (%s)", card.Name, agentURL)
		return nil
	}
	if err := VerifyAgentCardSignature(card); err != nil {
		a2aAgentTrustDecisions.WithLabelValues("rejected").Inc()
		return err
	}
	origin, err := canonicalA2AOrigin(agentURL)
	if err != nil {
		a2aAgentTrustDecisions.WithLabelValues("rejected").Inc()
		return fmt.Errorf("invalid Agent Card trust origin: %w", err)
	}
	pins := policy.TrustedKeys[origin]
	publicKey := strings.ToLower(strings.TrimSpace(card.Security.PublicKey))
	if len(pins) > 0 {
		if _, trusted := pins[publicKey]; !trusted {
			a2aAgentTrustDecisions.WithLabelValues("rejected").Inc()
			return fmt.Errorf("Agent Card public key is not trusted for origin %s", origin)
		}
	} else if policy.RequirePinnedKey {
		a2aAgentTrustDecisions.WithLabelValues("rejected").Inc()
		return fmt.Errorf("no trusted Agent Card public key is configured for origin %s", origin)
	}
	a2aAgentTrustDecisions.WithLabelValues("verified").Inc()
	return nil
}

func probeAgentCard(ctx context.Context, client *http.Client, agentURL string, requiredCapabilities []string, trustPolicy A2ATrustPolicy) (*AgentCard, error) {
	baseURL, err := parseA2AHTTPURL(agentURL)
	if err != nil {
		return nil, fmt.Errorf("invalid A2A agent URL: %w", err)
	}
	cardURL := &url.URL{Scheme: baseURL.Scheme, Host: baseURL.Host, Path: "/.well-known/agent-card.json"}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, cardURL.String(), nil)
	if err != nil {
		return nil, fmt.Errorf("create Agent Card request: %w", err)
	}
	probeClient := *client
	originalRedirect := client.CheckRedirect
	probeClient.CheckRedirect = func(redirectRequest *http.Request, via []*http.Request) error {
		if !sameA2AOrigin(baseURL, redirectRequest.URL) {
			return errors.New("Agent Card redirect crossed the configured agent origin")
		}
		if originalRedirect != nil {
			return originalRedirect(redirectRequest, via)
		}
		return nil
	}
	resp, err := probeClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("probe Agent Card: %w", err)
	}
	defer resp.Body.Close()
	if !sameA2AOrigin(baseURL, resp.Request.URL) {
		return nil, errors.New("Agent Card redirect crossed the configured agent origin")
	}
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		return nil, fmt.Errorf("Agent Card endpoint returned HTTP %d", resp.StatusCode)
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, maxAgentCardResponseBytes+1))
	if err != nil {
		return nil, fmt.Errorf("read Agent Card: %w", err)
	}
	if int64(len(body)) > maxAgentCardResponseBytes {
		return nil, fmt.Errorf("Agent Card exceeds %d bytes", maxAgentCardResponseBytes)
	}
	var card AgentCard
	if err := json.Unmarshal(body, &card); err != nil {
		return nil, fmt.Errorf("decode Agent Card: %w", err)
	}
	if !isSupportedA2AProtocolVersion(card.ProtocolVersion) {
		return nil, fmt.Errorf("unsupported A2A protocol version %q", card.ProtocolVersion)
	}
	cardBaseURL, err := parseA2AHTTPURL(card.URL)
	if err != nil || !sameA2AOrigin(baseURL, cardBaseURL) {
		return nil, errors.New("Agent Card URL does not match the configured agent origin")
	}
	taskURL, err := parseA2AHTTPURL(card.Endpoints.TaskAPI)
	if err != nil || !sameA2AOrigin(baseURL, taskURL) {
		return nil, errors.New("Agent Card task endpoint must use the configured agent origin")
	}
	if err := VerifyAgentCardTrust(&card, agentURL, trustPolicy); err != nil {
		return nil, err
	}
	if missing := missingAgentCapabilities(&card, requiredCapabilities); len(missing) > 0 {
		return nil, fmt.Errorf("Agent Card does not provide required capabilities: %s", strings.Join(missing, ", "))
	}
	return &card, nil
}

func isSupportedA2AProtocolVersion(version string) bool {
	version = strings.TrimSpace(version)
	if version == "" {
		return false
	}
	major, _, _ := strings.Cut(version, ".")
	return major == "1"
}

func parseA2AHTTPURL(raw string) (*url.URL, error) {
	parsed, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || parsed.Host == "" || parsed.User != nil || parsed.Fragment != "" || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		return nil, errors.New("absolute HTTP(S) URL is required")
	}
	return parsed, nil
}

func canonicalA2AOrigin(raw string) (string, error) {
	parsed, err := parseA2AHTTPURL(raw)
	if err != nil {
		return "", err
	}
	return strings.ToLower(parsed.Scheme + "://" + parsed.Host), nil
}

func sameA2AOrigin(left, right *url.URL) bool {
	return left != nil && right != nil && strings.EqualFold(left.Scheme, right.Scheme) && strings.EqualFold(left.Host, right.Host)
}

func missingAgentCapabilities(card *AgentCard, required []string) []string {
	available := make(map[string]struct{})
	for _, skill := range card.Skills {
		if skillID := strings.ToLower(strings.TrimSpace(skill.ID)); skillID != "" {
			available[skillID] = struct{}{}
		}
		for _, tag := range skill.Tags {
			if normalizedTag := strings.ToLower(strings.TrimSpace(tag)); normalizedTag != "" {
				available[normalizedTag] = struct{}{}
			}
		}
	}
	var missing []string
	for _, capability := range required {
		if _, ok := available[strings.ToLower(strings.TrimSpace(capability))]; !ok {
			missing = append(missing, capability)
		}
	}
	return missing
}

// ── Task Forwarding ─────────────────────────────────────────────────

// forwardTaskToAgent sends a task to a remote A2A agent's task endpoint
// and returns the response.
func forwardTaskToAgent(ctx context.Context, client *http.Client, agentURL, method string, params map[string]any, requiredCapabilities []string, trustPolicy A2ATrustPolicy, peerCredentials *A2APeerCredentials) (*A2ATaskResponse, error) {
	card, err := probeAgentCard(ctx, client, agentURL, requiredCapabilities, trustPolicy)
	if err != nil {
		return nil, err
	}
	taskEndpoint := card.Endpoints.TaskAPI
	bearerToken := ""
	if agentCardRequiresBearer(card) {
		var configured bool
		bearerToken, configured, err = peerCredentials.bearerFor(agentURL)
		if err != nil {
			return nil, err
		}
		if !configured {
			origin, _ := canonicalA2AOrigin(agentURL)
			return nil, fmt.Errorf("Agent Card requires bearer authentication but no receiver-issued credential is configured for origin %s", origin)
		}
	}

	requestID := fmt.Sprintf("%d", time.Now().UnixNano())
	reqBody := A2ATaskRequest{
		JSONRPC: "2.0",
		Method:  method,
		Params:  params,
		ID:      requestID,
	}

	bodyBytes, err := json.Marshal(reqBody)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, taskEndpoint, strings.NewReader(string(bodyBytes)))
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	if bearerToken != "" {
		req.Header.Set("Authorization", "Bearer "+bearerToken)
	}

	taskClient := *client
	originURL, _ := parseA2AHTTPURL(agentURL)
	originalRedirect := client.CheckRedirect
	taskClient.CheckRedirect = func(redirectRequest *http.Request, via []*http.Request) error {
		if !sameA2AOrigin(originURL, redirectRequest.URL) {
			return errors.New("A2A task redirect crossed the configured agent origin")
		}
		if originalRedirect != nil {
			return originalRedirect(redirectRequest, via)
		}
		return nil
	}
	resp, err := taskClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("forward request to %s: %w", taskEndpoint, err)
	}
	defer resp.Body.Close()

	responseBody, err := io.ReadAll(io.LimitReader(resp.Body, maxA2ATaskResponseBytes+1))
	if err != nil {
		return nil, fmt.Errorf("read response from %s: %w", taskEndpoint, err)
	}
	if int64(len(responseBody)) > maxA2ATaskResponseBytes {
		return nil, fmt.Errorf("remote A2A response exceeds %d bytes", maxA2ATaskResponseBytes)
	}
	var taskResp A2ATaskResponse
	if err := json.Unmarshal(responseBody, &taskResp); err != nil {
		return nil, fmt.Errorf("decode response from %s: %w", taskEndpoint, err)
	}
	if taskResp.JSONRPC != "2.0" {
		return nil, fmt.Errorf("remote A2A response from %s has unsupported JSON-RPC version %q", taskEndpoint, taskResp.JSONRPC)
	}
	responseID, ok := taskResp.ID.(string)
	if !ok || responseID != requestID {
		return nil, fmt.Errorf("remote A2A response from %s has mismatched response id", taskEndpoint)
	}
	if taskResp.Result == nil && taskResp.Error == nil {
		return nil, fmt.Errorf("remote A2A response from %s has neither result nor error", taskEndpoint)
	}
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		if taskResp.Error != nil {
			return &taskResp, nil
		}
		return nil, fmt.Errorf("remote A2A endpoint %s returned HTTP %d", taskEndpoint, resp.StatusCode)
	}

	return &taskResp, nil
}

func agentCardRequiresBearer(card *AgentCard) bool {
	if card == nil {
		return false
	}
	for _, scheme := range card.AuthSchemes {
		if strings.EqualFold(strings.TrimSpace(scheme.Type), "bearer") {
			return true
		}
	}
	return false
}

// ── HTTP Handlers ────────────────────────────────────────────────────

// newA2AHandler returns an http.Handler that serves A2A endpoints.
// When pool is non-nil, PostgreSQL persistence is used for the agent registry.
// When pool is nil, an in-memory map serves as fallback.
// tlsCfg enables TLS/mTLS for outbound calls to external A2A agents.
func newA2AHandlerWithTrustPolicy(baseURL string, pool *db.Pool, tlsCfg *A2ATLSConfig, trustPolicy A2ATrustPolicy, signer A2ACardSigner, peerCredentials *A2APeerCredentials, control a2aControlPlane) (http.Handler, error) {
	mux := http.NewServeMux()

	selfCard := buildAgentHubCard(baseURL)
	if signer != nil {
		if err := SignAgentCard(context.Background(), selfCard, signer); err != nil {
			return nil, fmt.Errorf("sign AgentHub Agent Card: %w", err)
		}
	}
	a2aReg.pool = pool
	a2aReg.tlsCfg = tlsCfg
	a2aReg.selfCard = selfCard
	a2aReg.register(selfCard)

	client := a2aHTTPClient(tlsCfg)
	mux.HandleFunc("/inbox", newA2AInboxHandler(selfCard, client, trustPolicy, control))

	// Agent Card endpoint (A2A spec §3.1)
	mux.HandleFunc("/.well-known/agent-card.json", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		writeJSON(w, http.StatusOK, selfCard)
	})

	// Agent Card for AgentHub (convenience alias)
	mux.HandleFunc("/card", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		writeJSON(w, http.StatusOK, selfCard)
	})

	// Registry: List all registered A2A agents
	mux.HandleFunc("/registry", func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			writeJSON(w, http.StatusOK, map[string]any{
				"agents": a2aReg.listAll(),
				"count":  len(a2aReg.listAll()),
			})
		case http.MethodPost:
			// Register an external A2A agent
			var card AgentCard
			if err := json.NewDecoder(r.Body).Decode(&card); err != nil {
				writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid agent card: " + err.Error()})
				return
			}
			if card.URL == "" {
				writeJSON(w, http.StatusBadRequest, map[string]string{"error": "agent URL is required"})
				return
			}

			if trustErr := VerifyAgentCardTrust(&card, card.URL, trustPolicy); trustErr != nil {
				log.Printf("a2a: trust verification FAILED for agent '%s' (%s): %v", card.Name, card.URL, trustErr)
				writeJSON(w, http.StatusBadRequest, map[string]string{
					"error": "Agent Card trust verification failed: " + trustErr.Error(),
				})
				return
			}

			card.Source = "external"
			card.Status = "active"
			card.LastSeenAt = time.Now().UTC().Format(time.RFC3339)
			if card.CreatedAt == "" {
				card.CreatedAt = time.Now().UTC().Format(time.RFC3339)
			}
			a2aReg.register(&card)
			a2aAgentRegistrations.WithLabelValues("register").Inc()
			log.Printf("a2a: registered external agent %s (%s)", card.Name, card.URL)
			writeJSON(w, http.StatusCreated, map[string]any{"status": "registered", "agent": card})
		case http.MethodDelete:
			url := r.URL.Query().Get("url")
			if url == "" {
				writeJSON(w, http.StatusBadRequest, map[string]string{"error": "url query parameter is required"})
				return
			}
			a2aReg.unregister(url)
			a2aAgentRegistrations.WithLabelValues("deregister").Inc()
			writeJSON(w, http.StatusOK, map[string]string{"status": "unregistered", "url": url})
		default:
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		}
	})

	// Discovery: Find agents by capability
	mux.HandleFunc("/discover", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		capabilities := r.URL.Query()["capability"]
		agents := a2aReg.discover(capabilities)
		writeJSON(w, http.StatusOK, map[string]any{
			"agents": agents,
			"count":  len(agents),
			"query": map[string]any{
				"capabilities": capabilities,
			},
		})
	})

	// TLS configuration status endpoint
	mux.HandleFunc("/tls-status", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		status := map[string]any{
			"enabled":       tlsCfg.Enabled,
			"strict_verify": tlsCfg.StrictVerify,
			"cert_file":     tlsCfg.CertFile,
			"key_file":      tlsCfg.KeyFile,
			"ca_file":       tlsCfg.CAFile,
		}
		// Check cert expiry if cert file is present
		if tlsCfg.Enabled && tlsCfg.CertFile != "" {
			if cert, err := tls.LoadX509KeyPair(tlsCfg.CertFile, tlsCfg.KeyFile); err == nil {
				if len(cert.Certificate) > 0 {
					if parsed, err := x509.ParseCertificate(cert.Certificate[0]); err == nil {
						status["cert_expiry"] = parsed.NotAfter.Format(time.RFC3339)
						status["cert_subject"] = parsed.Subject.String()
						status["cert_valid"] = time.Now().Before(parsed.NotAfter)
					}
				}
			}
		}
		writeJSON(w, http.StatusOK, status)
	})

	mux.HandleFunc("/trust-status", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"allow_unsigned":     trustPolicy.AllowUnsigned,
			"require_pinned_key": trustPolicy.RequirePinnedKey,
			"pinned_origins":     len(trustPolicy.TrustedKeys),
			"self_card_signed":   selfCard.Signature != "",
		})
	})

	// Signature verification status endpoint
	mux.HandleFunc("/registry/verify-signatures", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		agents := a2aReg.listAll()
		results := make([]map[string]any, 0, len(agents))
		for _, agent := range agents {
			if agent.Source == "internal" {
				continue // skip self
			}
			err := VerifyAgentCardTrust(agent, agent.URL, trustPolicy)
			status := "verified"
			message := "signature valid"
			if err != nil {
				status = "invalid"
				message = err.Error()
			} else if agent.Signature == "" {
				status = "unsigned_allowed"
				message = "card has no signature and compatibility policy allows it"
			}
			results = append(results, map[string]any{
				"url":     agent.URL,
				"name":    agent.Name,
				"status":  status,
				"message": message,
			})
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"results": results,
			"count":   len(results),
		})
	})

	// Task API (JSON-RPC 2.0)
	mux.HandleFunc("/tasks", func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
			return
		}
		var req A2ATaskRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, A2ATaskResponse{
				JSONRPC: "2.0",
				Error:   &A2AError{Code: -32700, Message: "Parse error: " + err.Error()},
				ID:      "null",
			})
			return
		}
		if req.JSONRPC != "2.0" || strings.TrimSpace(req.Method) == "" {
			writeJSON(w, http.StatusBadRequest, A2ATaskResponse{
				JSONRPC: "2.0",
				Error:   &A2AError{Code: -32600, Message: "Invalid Request: jsonrpc 2.0 and method are required"},
				ID:      req.ID,
			})
			return
		}

		switch req.Method {
		case "tasks/send":
			start := time.Now()
			a2aTaskRequests.WithLabelValues("tasksSend").Inc()

			agentURL, _ := req.Params["agentUrl"].(string)
			if agentURL == "" {
				agentURL, _ = req.Params["target"].(string)
			}
			if agentURL == "" {
				a2aTaskLatency.WithLabelValues("tasksSend").Observe(time.Since(start).Seconds())
				writeJSON(w, http.StatusNotImplemented, A2ATaskResponse{
					JSONRPC: "2.0",
					Error: &A2AError{
						Code:    -32004,
						Message: "A2A task execution is not configured; agentUrl or target is required",
					},
					ID: req.ID,
				})
				return
			}
			workspaceID, _ := req.Params["workspaceId"].(string)
			if workspaceID == "" {
				writeA2AInvalidParams(w, req.ID, "workspaceId is required")
				return
			}
			taskID, _ := req.Params["id"].(string)
			if taskID == "" {
				taskID = genTaskID()
			}
			msg := extractMessage(req.Params)
			objective := extractTextObjective(msg)
			if objective == "" {
				writeA2AInvalidParams(w, req.ID, "message must contain a non-empty text part")
				return
			}
			requiredCapabilities, err := extractRequiredCapabilities(req.Params)
			if err != nil {
				writeA2AInvalidParams(w, req.ID, err.Error())
				return
			}
			if control == nil {
				writeA2AControlError(w, req.ID, taskID, fmt.Errorf("Mission control plane is not configured"))
				return
			}
			controlTask, err := control.Submit(
				r.Context(),
				r.Header.Get("Authorization"),
				a2aControlSubmit{
					TaskID:               taskID,
					WorkspaceID:          workspaceID,
					Objective:            objective,
					AgentURL:             agentURL,
					RequiredCapabilities: requiredCapabilities,
				},
			)
			if err != nil {
				writeA2AControlError(w, req.ID, taskID, err)
				return
			}

			forwardParams := cloneA2AParams(req.Params)
			delete(forwardParams, "agentUrl")
			delete(forwardParams, "target")
			forwardParams["id"] = taskID
			forwardParams["sourceAgentUrl"] = selfCard.URL
			if _, hasRequiredCapabilities := req.Params["requiredCapabilities"]; hasRequiredCapabilities {
				forwardParams["requiredCapabilities"] = requiredCapabilities
			}
			// ADR-0053 cutover: with A2A_DISPATCH_MODE=runner the Gateway only
			// submits the Mission and defers remote dispatch to the outbound
			// Runner worker. The two paths must never dispatch the same attempt.
			if a2aGatewayDispatchEnabled() {
				fwdResp, fwdErr := forwardTaskToAgent(
					r.Context(),
					client,
					agentURL,
					"tasks/send",
					forwardParams,
					requiredCapabilities,
					trustPolicy,
					peerCredentials,
				)
				if fwdErr == nil && fwdResp != nil && fwdResp.Error != nil {
					fwdErr = fmt.Errorf("remote A2A error %d: %s", fwdResp.Error.Code, fwdResp.Error.Message)
				}
				if fwdErr != nil {
					log.Printf("a2a: task forward to %s failed: %v", agentURL, fwdErr)
					controlTask, err = control.Fail(
						r.Context(),
						r.Header.Get("Authorization"),
						workspaceID,
						taskID,
						truncateA2AReason(fwdErr.Error()),
					)
					if err != nil {
						writeA2AControlError(w, req.ID, taskID, fmt.Errorf("record dispatch failure: %w", err))
						return
					}
				}
			}
			task := controlTask.toA2ATask()
			task.Message = msg

			a2aTaskLatency.WithLabelValues("tasksSend").Observe(time.Since(start).Seconds())
			writeJSON(w, http.StatusOK, A2ATaskResponse{
				JSONRPC: "2.0",
				Result:  task,
				ID:      req.ID,
			})

		case "tasks/get":
			start := time.Now()
			a2aTaskRequests.WithLabelValues("tasksGet").Inc()

			taskID, _ := req.Params["id"].(string)
			if taskID == "" {
				a2aTaskLatency.WithLabelValues("tasksGet").Observe(time.Since(start).Seconds())
				writeJSON(w, http.StatusBadRequest, A2ATaskResponse{
					JSONRPC: "2.0",
					Error:   &A2AError{Code: -32602, Message: "Invalid params: task id is required"},
					ID:      req.ID,
				})
				return
			}
			workspaceID, _ := req.Params["workspaceId"].(string)
			if workspaceID == "" {
				writeA2AInvalidParams(w, req.ID, "workspaceId is required")
				return
			}
			if control == nil {
				writeA2AControlError(w, req.ID, taskID, fmt.Errorf("Mission control plane is not configured"))
				return
			}
			controlTask, err := control.Get(r.Context(), r.Header.Get("Authorization"), workspaceID, taskID)
			if err != nil {
				writeA2AControlError(w, req.ID, taskID, err)
				return
			}

			a2aTaskLatency.WithLabelValues("tasksGet").Observe(time.Since(start).Seconds())
			writeJSON(w, http.StatusOK, A2ATaskResponse{
				JSONRPC: "2.0",
				Result:  controlTask.toA2ATask(),
				ID:      req.ID,
			})

		case "tasks/cancel":
			start := time.Now()
			a2aTaskRequests.WithLabelValues("tasksCancel").Inc()

			taskID, _ := req.Params["id"].(string)
			if taskID == "" {
				a2aTaskLatency.WithLabelValues("tasksCancel").Observe(time.Since(start).Seconds())
				writeJSON(w, http.StatusBadRequest, A2ATaskResponse{
					JSONRPC: "2.0",
					Error:   &A2AError{Code: -32602, Message: "Invalid params: task id is required"},
					ID:      req.ID,
				})
				return
			}
			workspaceID, _ := req.Params["workspaceId"].(string)
			if workspaceID == "" {
				writeA2AInvalidParams(w, req.ID, "workspaceId is required")
				return
			}
			if control == nil {
				writeA2AControlError(w, req.ID, taskID, fmt.Errorf("Mission control plane is not configured"))
				return
			}
			controlTask, err := control.Cancel(r.Context(), r.Header.Get("Authorization"), workspaceID, taskID)
			if err != nil {
				writeA2AControlError(w, req.ID, taskID, err)
				return
			}
			if controlTask.AgentURL != "" && a2aGatewayDispatchEnabled() {
				forwardParams := cloneA2AParams(req.Params)
				delete(forwardParams, "agentUrl")
				delete(forwardParams, "target")
				forwardParams["id"] = taskID
				forwardParams["sourceAgentUrl"] = selfCard.URL
				if _, forwardErr := forwardTaskToAgent(
					r.Context(),
					client,
					controlTask.AgentURL,
					"tasks/cancel",
					forwardParams,
					nil,
					trustPolicy,
					peerCredentials,
				); forwardErr != nil {
					log.Printf("a2a: remote cancellation for %s failed: %v", taskID, forwardErr)
				}
			}

			a2aTaskLatency.WithLabelValues("tasksCancel").Observe(time.Since(start).Seconds())
			writeJSON(w, http.StatusOK, A2ATaskResponse{
				JSONRPC: "2.0",
				Result:  controlTask.toA2ATask(),
				ID:      req.ID,
			})

		default:
			writeJSON(w, http.StatusBadRequest, A2ATaskResponse{
				JSONRPC: "2.0",
				Error:   &A2AError{Code: -32601, Message: "Method not found: " + req.Method},
				ID:      req.ID,
			})
		}
	})

	return mux, nil
}

// ── Helpers ──────────────────────────────────────────────────────────

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func writeA2AInvalidParams(w http.ResponseWriter, requestID any, message string) {
	writeJSON(w, http.StatusBadRequest, A2ATaskResponse{
		JSONRPC: "2.0",
		Error:   &A2AError{Code: -32602, Message: "Invalid params: " + message},
		ID:      requestID,
	})
}

func writeA2AControlError(w http.ResponseWriter, requestID any, taskID string, err error) {
	statusCode := http.StatusBadGateway
	code := -32005
	message := "A2A Mission control plane unavailable"
	var controlErr *a2aControlPlaneError
	if errors.As(err, &controlErr) {
		statusCode = controlErr.StatusCode
		switch controlErr.StatusCode {
		case http.StatusNotFound:
			code = -32001
			message = "Task not found"
		case http.StatusConflict:
			code = -32002
			message = "Task state conflict"
		case http.StatusUnauthorized, http.StatusForbidden:
			code = -32003
			message = "Task authorization failed"
		case http.StatusBadRequest, http.StatusUnprocessableEntity:
			code = -32602
			message = "Invalid task request"
		default:
			message = "A2A Mission control plane rejected the request"
		}
	} else if strings.Contains(err.Error(), "not configured") {
		statusCode = http.StatusServiceUnavailable
		code = -32004
		message = "A2A Mission control plane is not configured"
	}
	writeJSON(w, statusCode, A2ATaskResponse{
		JSONRPC: "2.0",
		Error: &A2AError{
			Code:    code,
			Message: message,
			Data:    map[string]string{"id": taskID, "detail": err.Error()},
		},
		ID: requestID,
	})
}

func genTaskID() string {
	b := make([]byte, 12)
	rand.Read(b)
	return "task-" + hex.EncodeToString(b)
}

func extractMessage(params map[string]any) *A2AMessage {
	if params == nil {
		return nil
	}
	if msgRaw, ok := params["message"]; ok {
		if msgMap, ok := msgRaw.(map[string]any); ok {
			msg := &A2AMessage{}
			if role, ok := msgMap["role"].(string); ok {
				msg.Role = role
			}
			if partsRaw, ok := msgMap["parts"].([]any); ok {
				for _, p := range partsRaw {
					if pm, ok := p.(map[string]any); ok {
						part := A2AMessagePart{}
						if t, ok := pm["type"].(string); ok {
							part.Type = t
						}
						if text, ok := pm["text"].(string); ok {
							part.Text = text
						}
						msg.Parts = append(msg.Parts, part)
					}
				}
			}
			return msg
		}
	}
	return nil
}

func extractTextObjective(message *A2AMessage) string {
	if message == nil {
		return ""
	}
	parts := make([]string, 0, len(message.Parts))
	for _, part := range message.Parts {
		if text := strings.TrimSpace(part.Text); text != "" {
			parts = append(parts, text)
		}
	}
	return strings.Join(parts, "\n")
}

func extractRequiredCapabilities(params map[string]any) ([]string, error) {
	raw, exists := params["requiredCapabilities"]
	if !exists {
		return nil, nil
	}
	values, ok := raw.([]any)
	if !ok {
		return nil, errors.New("requiredCapabilities must be an array of strings")
	}
	capabilities := make([]string, 0, len(values))
	seen := make(map[string]struct{}, len(values))
	for index, value := range values {
		capability, ok := value.(string)
		if !ok {
			return nil, fmt.Errorf("requiredCapabilities[%d] must be a string", index)
		}
		capability = strings.TrimSpace(capability)
		if capability == "" {
			return nil, fmt.Errorf("requiredCapabilities[%d] must not be empty", index)
		}
		normalized := strings.ToLower(capability)
		if _, duplicate := seen[normalized]; duplicate {
			return nil, fmt.Errorf("requiredCapabilities contains duplicate capability %q", capability)
		}
		seen[normalized] = struct{}{}
		capabilities = append(capabilities, capability)
	}
	return capabilities, nil
}

func cloneA2AParams(params map[string]any) map[string]any {
	cloned := make(map[string]any, len(params)+1)
	for key, value := range params {
		cloned[key] = value
	}
	return cloned
}

func truncateA2AReason(reason string) string {
	runes := []rune(reason)
	if len(runes) <= 2000 {
		return reason
	}
	return string(runes[:2000])
}

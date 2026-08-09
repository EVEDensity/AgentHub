package main

import (
	"io"
	"log"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/agenthub/platform/shared/iam"
)

const (
	defaultSessionListLimit = 10
	maxSessionListLimit     = 50
)

// sessionProxy keeps public authentication at the Gateway while delegating
// durable chat-session reads to session-service.
type sessionProxy struct {
	baseURL string
	client  *http.Client
}

func newSessionProxy(baseURL string) *sessionProxy {
	return &sessionProxy{
		baseURL: strings.TrimRight(baseURL, "/"),
		client:  &http.Client{Timeout: 10 * time.Second},
	}
}

func (p *sessionProxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", http.MethodGet)
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	principal, ok := iam.FromContext(r.Context())
	if !ok || strings.TrimSpace(principal.TenantID) == "" {
		http.Error(w, "forbidden: tenant context required", http.StatusForbidden)
		return
	}

	endpoint, err := url.Parse(p.baseURL + "/sessions")
	if err != nil {
		http.Error(w, "session service URL is invalid", http.StatusInternalServerError)
		return
	}
	query := endpoint.Query()
	query.Set("tenant_id", principal.TenantID)
	query.Set("limit", strconv.Itoa(normalizeSessionLimit(r.URL.Query().Get("limit"))))
	endpoint.RawQuery = query.Encode()

	request, err := http.NewRequestWithContext(r.Context(), http.MethodGet, endpoint.String(), nil)
	if err != nil {
		http.Error(w, "failed to build session request", http.StatusInternalServerError)
		return
	}
	if authorization := r.Header.Get("Authorization"); authorization != "" {
		request.Header.Set("Authorization", authorization)
	}

	response, err := p.client.Do(request)
	if err != nil {
		log.Printf("session proxy error: %v", err)
		http.Error(w, "session service unavailable", http.StatusBadGateway)
		return
	}
	defer response.Body.Close()

	body, err := io.ReadAll(response.Body)
	if err != nil {
		http.Error(w, "failed to read session response", http.StatusBadGateway)
		return
	}
	if contentType := response.Header.Get("Content-Type"); contentType != "" {
		w.Header().Set("Content-Type", contentType)
	} else {
		w.Header().Set("Content-Type", "application/json")
	}
	w.WriteHeader(response.StatusCode)
	_, _ = w.Write(body)
}

func normalizeSessionLimit(raw string) int {
	limit, err := strconv.Atoi(raw)
	if err != nil || limit < 1 {
		return defaultSessionListLimit
	}
	if limit > maxSessionListLimit {
		return maxSessionListLimit
	}
	return limit
}

func parseSessionServiceURL() string {
	configured := getenv("SESSION_SERVICE_URL", "http://127.0.0.1:8083")
	parsed, err := url.Parse(configured)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return "http://127.0.0.1:8083"
	}
	return strings.TrimRight(configured, "/")
}

package main

import (
	"encoding/json"
	"io"
	"log"
	"net/http"
	"net/url"
	"strings"
)

// knowledgeProxy forwards /platform/knowledge/* requests to the Python
// offline-knowledge-service (api_v2 endpoints). The Python service owns
// Qdrant connectivity; the Go gateway is the single ingress for the frontend.
//
// Route mapping (prefix stripped before forwarding):
//
//	/platform/knowledge/collections           -> GET  /collections
//	/platform/knowledge/collections/...       -> forwarded verbatim
//	/platform/knowledge/retrieval-test        -> POST /retrieval-test
type knowledgeProxy struct {
	baseURL string // e.g. "http://127.0.0.1:8092"
	client  *http.Client
}

func newKnowledgeProxy(baseURL string) *knowledgeProxy {
	return &knowledgeProxy{
		baseURL: baseURL,
		client:  &http.Client{},
	}
}

func (p *knowledgeProxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// Strip /platform/knowledge prefix and build target URL.
	rel := strings.TrimPrefix(r.URL.Path, "/platform/knowledge")
	rel = strings.TrimPrefix(rel, "/")
	targetURL := p.baseURL + "/" + rel
	if r.URL.RawQuery != "" {
		targetURL += "?" + r.URL.RawQuery
	}

	log.Printf("knowledge proxy: %s %s -> %s", r.Method, r.URL.Path, targetURL)

	// Build outgoing request.
	bodyBytes, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, `{"error":"failed to read request body"}`, http.StatusBadRequest)
		return
	}
	defer r.Body.Close()

	proxyReq, err := http.NewRequestWithContext(r.Context(), r.Method, targetURL, strings.NewReader(string(bodyBytes)))
	if err != nil {
		http.Error(w, `{"error":"failed to build proxy request"}`, http.StatusInternalServerError)
		return
	}

	// Copy relevant headers.
	for k, vv := range r.Header {
		for _, v := range vv {
			proxyReq.Header.Add(k, v)
		}
	}
	proxyReq.Header.Set("X-Forwarded-For", r.RemoteAddr)

	resp, err := p.client.Do(proxyReq)
	if err != nil {
		log.Printf("knowledge proxy error: %v", err)
		http.Error(w, `{"error":"knowledge service unavailable"}`, http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	// Copy response.
	respBody, _ := io.ReadAll(resp.Body)
	for k, vv := range resp.Header {
		for _, v := range vv {
			w.Header().Add(k, v)
		}
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	w.Write(respBody)
}

// handleKnowledgeUpload proxies document upload to the document-pipeline service.
// POST /platform/knowledge/upload -> POST /process on document-pipeline.
func handleKnowledgeUpload(docPipelineURL string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			w.WriteHeader(http.StatusMethodNotAllowed)
			json.NewEncoder(w).Encode(map[string]string{"error": "method not allowed"})
			return
		}

		// Forward as multipart to the document pipeline.
		targetURL := docPipelineURL + "/process"
		proxyReq, err := http.NewRequestWithContext(r.Context(), http.MethodPost, targetURL, r.Body)
		if err != nil {
			http.Error(w, `{"error":"failed to build proxy request"}`, http.StatusInternalServerError)
			return
		}
		proxyReq.Header.Set("Content-Type", r.Header.Get("Content-Type"))
		proxyReq.Header.Set("X-Forwarded-For", r.RemoteAddr)

		resp, err := http.DefaultClient.Do(proxyReq)
		if err != nil {
			log.Printf("document pipeline proxy error: %v", err)
			http.Error(w, `{"error":"document pipeline unavailable"}`, http.StatusBadGateway)
			return
		}
		defer resp.Body.Close()

		respBody, _ := io.ReadAll(resp.Body)
		for k, vv := range resp.Header {
			for _, v := range vv {
				w.Header().Add(k, v)
			}
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(resp.StatusCode)
		w.Write(respBody)
	}
}

// parseKnowledgeServiceURL derives the knowledge-service URL from config.
// Defaults to "http://offline-knowledge-service:8092" for Docker; configurable
// via KNOWLEDGE_SERVICE_URL env var for dev.
func parseKnowledgeServiceURL() string {
	if v := getenv("KNOWLEDGE_SERVICE_URL", ""); v != "" {
		u, err := url.Parse(v)
		if err == nil && u.Scheme != "" {
			return strings.TrimSuffix(v, "/")
		}
	}
	return "http://127.0.0.1:8092"
}

// parseDocPipelineURL derives the document-pipeline URL.
func parseDocPipelineURL() string {
	if v := getenv("DOCUMENT_PIPELINE_URL", ""); v != "" {
		u, err := url.Parse(v)
		if err == nil && u.Scheme != "" {
			return strings.TrimSuffix(v, "/")
		}
	}
	return "http://127.0.0.1:8095"
}

package main

import (
	"io"
	"log"
	"net/http"
	"strings"
)

// mcpProxy forwards requests to the MCP Gateway service (port 8099).
// This allows the frontend to reach MCP endpoints through the main gateway
// without CORS issues.
type mcpProxy struct {
	targetURL string
	client    *http.Client
}

func newMCPProxy(targetURL string) *mcpProxy {
	if targetURL == "" {
		targetURL = "http://127.0.0.1:8099"
	}
	return &mcpProxy{
		targetURL: strings.TrimRight(targetURL, "/"),
		client:    &http.Client{},
	}
}

func (p *mcpProxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// Strip /platform/mcp prefix and forward to MCP Gateway
	rel := strings.TrimPrefix(r.URL.Path, "/platform/mcp")
	if rel == "" {
		rel = "/"
	}

	target := p.targetURL + rel
	if r.URL.RawQuery != "" {
		target += "?" + r.URL.RawQuery
	}

	proxyReq, err := http.NewRequest(r.Method, target, r.Body)
	if err != nil {
		http.Error(w, `{"error":"proxy error"}`, http.StatusBadGateway)
		return
	}

	// Copy headers
	for key, values := range r.Header {
		for _, v := range values {
			proxyReq.Header.Add(key, v)
		}
	}

	resp, err := p.client.Do(proxyReq)
	if err != nil {
		log.Printf("mcp-proxy: failed to reach MCP Gateway at %s: %v", target, err)
		// Return a helpful error — MCP Gateway might not be running
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		io.WriteString(w, `{"error":"MCP Gateway unreachable","hint":"Start the MCP Gateway service (port 8099) or use demo mode"}`)
		return
	}
	defer resp.Body.Close()

	// Copy response headers
	for key, values := range resp.Header {
		for _, v := range values {
			w.Header().Add(key, v)
		}
	}

	w.WriteHeader(resp.StatusCode)
	io.Copy(w, resp.Body)
}

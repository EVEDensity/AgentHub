// Sprint N6: Security hardening middleware.
// Provides request body size limiting, Content-Security-Policy headers,
// security response headers (X-Content-Type-Options, X-Frame-Options, etc.),
// and enforces common security best practices at the gateway edge.
//
// All thresholds are configurable via environment variables with sensible
// production defaults.

package main

import (
	"net/http"
	"strconv"
	"strings"
)

// ── Body Size Limits ──────────────────────────────────────────────────

// maxBytesHandler wraps an http.Handler with a global request body size limit.
// Requests exceeding the limit receive a 413 Payload Too Large response.
func maxBytesHandler(maxBytes int64, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Body != nil && r.ContentLength > maxBytes {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusRequestEntityTooLarge)
			w.Write([]byte(`{"error":"request body too large","limit":` + strconv.FormatInt(maxBytes, 10) + `}`))
			return
		}
		// Even when Content-Length is 0 or negative, wrap with MaxBytesReader
		// to prevent unbounded chunked uploads from exhausting memory.
		if r.Body != nil {
			r.Body = http.MaxBytesReader(w, r.Body, maxBytes)
		}
		next.ServeHTTP(w, r)
	})
}

// bodyLimitMiddleware returns a middleware that enforces request body size limits.
// JSON/protobuf/multipart requests get up to maxJSONBytes; binary uploads
// (knowledge base documents, etc.) get up to maxUploadBytes.
func bodyLimitMiddleware(next http.Handler) http.Handler {
	maxJSON := int64(getenvInt("GW_MAX_JSON_BODY_BYTES", 1<<20))     // 1 MB default
	maxUpload := int64(getenvInt("GW_MAX_UPLOAD_BODY_BYTES", 50<<20)) // 50 MB default

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ct := r.Header.Get("Content-Type")
		limit := maxJSON
		if strings.Contains(ct, "multipart/form-data") ||
			strings.Contains(ct, "application/octet-stream") ||
			strings.Contains(ct, "application/pdf") {
			limit = maxUpload
		}
		if r.Body != nil && r.ContentLength > limit {
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(http.StatusRequestEntityTooLarge)
			w.Write([]byte(`{"error":"request body too large","limit":` + strconv.FormatInt(limit, 10) + `}`))
			return
		}
		if r.Body != nil {
			r.Body = http.MaxBytesReader(w, r.Body, limit)
		}
		next.ServeHTTP(w, r)
	})
}

// ── Security Headers ──────────────────────────────────────────────────

// securityHeadersMiddleware adds standard HTTP security headers to every
// response. CSP is configurable via GW_CSP_POLICY env var; other headers
// use secure defaults appropriate for a SPA + API gateway.
func securityHeadersMiddleware(next http.Handler) http.Handler {
	csp := getenv("GW_CSP_POLICY",
		"default-src 'self'; "+
			"script-src 'self' 'unsafe-inline' 'unsafe-eval'; "+
			"style-src 'self' 'unsafe-inline'; "+
			"img-src 'self' data: blob: https:; "+
			"font-src 'self' data:; "+
			"connect-src 'self' ws: wss:; "+
			"frame-ancestors 'none'; "+
			"base-uri 'self'; "+
			"form-action 'self';")

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Security-Policy", csp)
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("X-Frame-Options", "DENY")
		w.Header().Set("X-XSS-Protection", "1; mode=block")
		w.Header().Set("Referrer-Policy", "strict-origin-when-cross-origin")
		w.Header().Set("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
		w.Header().Set("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

		// Remove headers that leak server info
		w.Header().Del("Server")
		w.Header().Del("X-Powered-By")

		next.ServeHTTP(w, r)
	})
}

// ── CORS Headers ──────────────────────────────────────────────────────

// corsMiddleware handles Cross-Origin Resource Sharing. Origins are
// configurable via GW_CORS_ORIGINS (comma-separated); defaults to local
// development origins.
func corsMiddleware(next http.Handler) http.Handler {
	allowedOrigins := getenv("GW_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		origin := r.Header.Get("Origin")
		if origin == "" {
			next.ServeHTTP(w, r)
			return
		}

		allowed := false
		for _, o := range strings.Split(allowedOrigins, ",") {
			if strings.TrimSpace(o) == origin || strings.TrimSpace(o) == "*" {
				allowed = true
				break
			}
		}
		if !allowed {
			next.ServeHTTP(w, r)
			return
		}

		w.Header().Set("Access-Control-Allow-Origin", origin)
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Authorization, Content-Type, X-User-ID, X-Tenant-ID, X-Agent-Role, X-Tool-Name, X-Request-ID, X-Trace-ID, X-Workspace-ID")
		w.Header().Set("Access-Control-Allow-Credentials", "true")
		w.Header().Set("Access-Control-Max-Age", "86400")

		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}

		next.ServeHTTP(w, r)
	})
}

// ── Rate Limit Header Sanitization ────────────────────────────────────

// noSensitiveHeaders strips internal headers from proxied responses so they
// never leak to external clients. Run as the outermost middleware.
func noSensitiveHeaders(next http.Handler) http.Handler {
	sensitive := map[string]bool{
		"X-Internal-Auth":       true,
		"X-Proxy-Target":        true,
		"X-Upstream-Host":       true,
		"X-Forwarded-For-Proxy": true,
	}

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Wrap to intercept headers set by downstream handlers.
		sw := &headerSanitizer{ResponseWriter: w, sensitive: sensitive}
		next.ServeHTTP(sw, r)
	})
}

type headerSanitizer struct {
	http.ResponseWriter
	sensitive   map[string]bool
	wroteHeader bool
}

func (s *headerSanitizer) WriteHeader(code int) {
	if s.wroteHeader {
		return
	}
	s.wroteHeader = true
	for h := range s.sensitive {
		s.ResponseWriter.Header().Del(h)
	}
	s.ResponseWriter.WriteHeader(code)
}

func (s *headerSanitizer) Write(b []byte) (int, error) {
	if !s.wroteHeader {
		s.WriteHeader(http.StatusOK)
	}
	return s.ResponseWriter.Write(b)
}

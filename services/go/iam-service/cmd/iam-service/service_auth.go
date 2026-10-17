package main

import (
	"crypto/sha256"
	"crypto/subtle"
	"errors"
	"io"
	"net/http"
	"os"
	"strings"
)

const maxServiceSecretBytes = 4096

// IAM administration is an operator-only service surface, not a login API.
// Its credential must be separate from the JWT key shared with token consumers.
func loadServiceCredentials(getenv func(string) string) ([]byte, []byte, error) {
	jwtKey := []byte(getenv("JWT_SECRET"))
	if len(jwtKey) < 32 {
		return nil, nil, errors.New("JWT_SECRET must contain at least 32 bytes")
	}
	file, err := os.Open(getenv("IAM_SERVICE_TOKEN_FILE"))
	if err != nil {
		return nil, nil, errors.New("IAM_SERVICE_TOKEN_FILE must name a readable regular file")
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() {
		return nil, nil, errors.New("IAM_SERVICE_TOKEN_FILE must name a regular file")
	}
	data, err := io.ReadAll(io.LimitReader(file, maxServiceSecretBytes+1))
	if err != nil || len(data) > maxServiceSecretBytes {
		return nil, nil, errors.New("IAM service token file is unreadable or oversized")
	}
	token := strings.TrimSpace(string(data))
	if len(token) < 32 || !validServiceToken(token) {
		return nil, nil, errors.New("IAM service token must be at least 32 printable non-whitespace ASCII characters")
	}
	if token == string(jwtKey) {
		return nil, nil, errors.New("IAM service token must differ from JWT_SECRET")
	}
	return jwtKey, []byte(token), nil
}

func validServiceToken(token string) bool {
	for _, char := range token {
		if char < '!' || char > '~' {
			return false
		}
	}
	return true
}

func serviceAuthorization(token []byte, next http.Handler) http.Handler {
	expected := sha256.Sum256(token)
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/healthz", "/profile", "/metrics":
			next.ServeHTTP(w, r)
			return
		}
		if len(token) < 32 {
			jsonError(w, http.StatusServiceUnavailable, "IAM management authorization is not configured")
			return
		}
		values := r.Header.Values("Authorization")
		fields := strings.Fields(r.Header.Get("Authorization"))
		if len(values) != 1 || len(fields) != 2 || !strings.EqualFold(fields[0], "Bearer") {
			w.Header().Set("WWW-Authenticate", "Bearer")
			jsonError(w, http.StatusUnauthorized, "operator service credential required")
			return
		}
		actual := sha256.Sum256([]byte(fields[1]))
		if subtle.ConstantTimeCompare(actual[:], expected[:]) != 1 {
			jsonError(w, http.StatusUnauthorized, "invalid operator service credential")
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, 1<<20)
		next.ServeHTTP(w, r)
	})
}

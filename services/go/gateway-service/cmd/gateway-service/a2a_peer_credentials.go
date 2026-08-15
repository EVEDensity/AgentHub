package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
)

const (
	maxA2APeerCredentialsConfigBytes = 64 * 1024
	maxA2APeerBearerTokenBytes int64 = 16 * 1024
)

// A2APeerCredentials holds receiver-issued outbound credentials by peer
// origin. It is startup-only protocol configuration, never registry data.
type A2APeerCredentials struct {
	bearerByOrigin map[string]string
}

func a2aPeerCredentialsFromEnv() (*A2APeerCredentials, error) {
	raw := strings.TrimSpace(os.Getenv("A2A_PEER_BEARER_TOKEN_FILES_JSON"))
	if raw == "" {
		return &A2APeerCredentials{bearerByOrigin: map[string]string{}}, nil
	}
	if len(raw) > maxA2APeerCredentialsConfigBytes {
		return nil, fmt.Errorf("A2A_PEER_BEARER_TOKEN_FILES_JSON exceeds %d bytes", maxA2APeerCredentialsConfigBytes)
	}
	decoder := json.NewDecoder(strings.NewReader(raw))
	decoder.DisallowUnknownFields()
	var configured map[string]string
	if err := decoder.Decode(&configured); err != nil {
		return nil, fmt.Errorf("parse A2A_PEER_BEARER_TOKEN_FILES_JSON: %w", err)
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		return nil, errors.New("A2A_PEER_BEARER_TOKEN_FILES_JSON must contain exactly one JSON object")
	}
	credentials := &A2APeerCredentials{
		bearerByOrigin: make(map[string]string, len(configured)),
	}
	for rawOrigin, rawPath := range configured {
		originURL, err := parseA2AHTTPURL(rawOrigin)
		if err != nil {
			return nil, fmt.Errorf("invalid A2A peer credential origin %q: %w", rawOrigin, err)
		}
		if (originURL.Path != "" && originURL.Path != "/") || originURL.RawQuery != "" {
			return nil, fmt.Errorf("invalid A2A peer credential origin %q: origin must not include a path or query", rawOrigin)
		}
		origin := strings.ToLower(originURL.Scheme + "://" + originURL.Host)
		if _, exists := credentials.bearerByOrigin[origin]; exists {
			return nil, fmt.Errorf("duplicate canonical A2A peer credential origin %q", origin)
		}
		path := strings.TrimSpace(rawPath)
		if path == "" {
			return nil, fmt.Errorf("A2A peer credential origin %q has an empty token file path", rawOrigin)
		}
		token, err := loadA2APeerBearerToken(path)
		if err != nil {
			return nil, fmt.Errorf("load A2A peer credential for %s: %w", origin, err)
		}
		credentials.bearerByOrigin[origin] = token
	}
	return credentials, nil
}

func loadA2APeerBearerToken(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", fmt.Errorf("open bearer token file: %w", err)
	}
	defer file.Close()
	contents, err := io.ReadAll(io.LimitReader(file, maxA2APeerBearerTokenBytes+1))
	if err != nil {
		return "", fmt.Errorf("read bearer token file: %w", err)
	}
	defer clearBytes(contents)
	if int64(len(contents)) > maxA2APeerBearerTokenBytes {
		return "", fmt.Errorf("bearer token file exceeds %d bytes", maxA2APeerBearerTokenBytes)
	}
	token := strings.TrimSpace(string(bytes.TrimSpace(contents)))
	if token == "" || strings.ContainsAny(token, "\r\n") {
		return "", errors.New("bearer token file must contain a non-empty single-line token")
	}
	return token, nil
}

func (credentials *A2APeerCredentials) bearerFor(agentURL string) (string, bool, error) {
	if credentials == nil {
		return "", false, nil
	}
	origin, err := canonicalA2AOrigin(agentURL)
	if err != nil {
		return "", false, fmt.Errorf("resolve A2A peer credential origin: %w", err)
	}
	token, ok := credentials.bearerByOrigin[origin]
	return token, ok, nil
}

func (credentials *A2APeerCredentials) Count() int {
	if credentials == nil {
		return 0
	}
	return len(credentials.bearerByOrigin)
}

package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"sync"
	"time"
)

const (
	maxA2ARemoteSignerResponseBytes int64 = 32 * 1024
	maxA2ARemoteSignerTokenBytes    int64 = 16 * 1024
	maxA2ARemoteSignerPayloadBytes        = 1024 * 1024
	a2aRemoteSignerTimeout                = 5 * time.Second
	a2aRemoteSignerPurpose                = "a2a_agent_card_v1"
)

type a2aRemoteSignerRequest struct {
	Operation  string `json:"operation"`
	Purpose    string `json:"purpose"`
	KeyID      string `json:"key_id"`
	KeyVersion string `json:"key_version,omitempty"`
	Payload    string `json:"payload,omitempty"`
}

type a2aRemoteSignerResponse struct {
	Algorithm  string `json:"algorithm"`
	KeyID      string `json:"key_id"`
	KeyVersion string `json:"key_version"`
	PublicKey  string `json:"public_key,omitempty"`
	Signature  string `json:"signature,omitempty"`
}

type remoteA2ACardSigner struct {
	endpoint *url.URL
	keyID    string
	token    string
	client   *http.Client

	mu       sync.Mutex
	identity *A2ACardSignerIdentity
}

func remoteA2ACardSignerFromEnv(endpoint string, allowInsecure bool) (A2ACardSigner, error) {
	keyID := strings.TrimSpace(os.Getenv("A2A_CARD_SIGNER_KEY_ID"))
	if keyID == "" {
		return nil, errors.New("A2A_CARD_SIGNER_KEY_ID is required with A2A_CARD_SIGNER_URL")
	}
	tokenPath := strings.TrimSpace(os.Getenv("A2A_CARD_SIGNER_TOKEN_FILE"))
	if tokenPath == "" {
		return nil, errors.New("A2A_CARD_SIGNER_TOKEN_FILE is required with A2A_CARD_SIGNER_URL")
	}
	token, err := loadA2ARemoteSignerToken(tokenPath)
	if err != nil {
		return nil, err
	}
	return newRemoteA2ACardSigner(endpoint, keyID, token, allowInsecure, nil)
}

func newRemoteA2ACardSigner(endpoint, keyID, token string, allowInsecure bool, client *http.Client) (*remoteA2ACardSigner, error) {
	parsed, err := url.Parse(strings.TrimSpace(endpoint))
	if err != nil || !parsed.IsAbs() || parsed.Host == "" {
		return nil, errors.New("A2A Card signer URL must be an absolute HTTP(S) URL")
	}
	if parsed.User != nil || parsed.Fragment != "" || parsed.RawQuery != "" {
		return nil, errors.New("A2A Card signer URL must not contain user-info, query, or fragment")
	}
	switch strings.ToLower(parsed.Scheme) {
	case "https":
	case "http":
		if !allowInsecure || !isLoopbackSignerHost(parsed.Hostname()) {
			return nil, errors.New("A2A Card signer URL requires HTTPS; insecure HTTP is limited to explicitly enabled loopback development")
		}
	default:
		return nil, errors.New("A2A Card signer URL must use HTTP or HTTPS")
	}
	keyID = strings.TrimSpace(keyID)
	if keyID == "" || len(keyID) > 256 {
		return nil, errors.New("A2A Card signer key ID must contain 1 to 256 characters")
	}
	token = strings.TrimSpace(token)
	if token == "" || strings.ContainsAny(token, "\r\n") {
		return nil, errors.New("A2A Card signer token must be non-empty and single-line")
	}
	if client == nil {
		client = &http.Client{}
	}
	controlledClient := *client
	controlledClient.Timeout = a2aRemoteSignerTimeout
	controlledClient.CheckRedirect = func(_ *http.Request, _ []*http.Request) error {
		return http.ErrUseLastResponse
	}
	return &remoteA2ACardSigner{
		endpoint: parsed,
		keyID:    keyID,
		token:    token,
		client:   &controlledClient,
	}, nil
}

func loadA2ARemoteSignerToken(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", fmt.Errorf("open A2A Card signer token file: %w", err)
	}
	defer file.Close()
	contents, err := io.ReadAll(io.LimitReader(file, maxA2ARemoteSignerTokenBytes+1))
	if err != nil {
		return "", fmt.Errorf("read A2A Card signer token file: %w", err)
	}
	defer clearBytes(contents)
	if int64(len(contents)) > maxA2ARemoteSignerTokenBytes {
		return "", fmt.Errorf("A2A Card signer token file exceeds %d bytes", maxA2ARemoteSignerTokenBytes)
	}
	token := strings.TrimSpace(string(contents))
	if token == "" || strings.ContainsAny(token, "\r\n") {
		return "", errors.New("A2A Card signer token file must contain a non-empty single-line token")
	}
	return token, nil
}

func isLoopbackSignerHost(host string) bool {
	if strings.EqualFold(strings.TrimSpace(host), "localhost") {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func (signer *remoteA2ACardSigner) Identity(ctx context.Context) (A2ACardSignerIdentity, error) {
	if signer == nil {
		return A2ACardSignerIdentity{}, errors.New("remote Agent Card signer is not initialized")
	}
	signer.mu.Lock()
	defer signer.mu.Unlock()
	if signer.identity != nil {
		identity := *signer.identity
		identity.PublicKey = append([]byte(nil), identity.PublicKey...)
		return identity, nil
	}
	response, err := signer.call(ctx, a2aRemoteSignerRequest{
		Operation: "public_key",
		Purpose:   a2aRemoteSignerPurpose,
		KeyID:     signer.keyID,
	})
	if err != nil {
		return A2ACardSignerIdentity{}, fmt.Errorf("read remote signing identity: %w", err)
	}
	identity, err := signer.validateIdentityResponse(response)
	if err != nil {
		return A2ACardSignerIdentity{}, err
	}
	signer.identity = &identity
	identity.PublicKey = append([]byte(nil), identity.PublicKey...)
	return identity, nil
}

func (signer *remoteA2ACardSigner) Sign(ctx context.Context, payload []byte) ([]byte, error) {
	if signer == nil {
		return nil, errors.New("remote Agent Card signer is not initialized")
	}
	if len(payload) == 0 || len(payload) > maxA2ARemoteSignerPayloadBytes {
		return nil, fmt.Errorf("remote Agent Card signing payload must contain 1 to %d bytes", maxA2ARemoteSignerPayloadBytes)
	}
	identity, err := signer.Identity(ctx)
	if err != nil {
		return nil, err
	}
	response, err := signer.call(ctx, a2aRemoteSignerRequest{
		Operation:  "sign",
		Purpose:    a2aRemoteSignerPurpose,
		KeyID:      identity.KeyID,
		KeyVersion: identity.KeyVersion,
		Payload:    base64.StdEncoding.EncodeToString(payload),
	})
	if err != nil {
		return nil, fmt.Errorf("request remote signature: %w", err)
	}
	if strings.ToLower(strings.TrimSpace(response.Algorithm)) != identity.Algorithm ||
		strings.TrimSpace(response.KeyID) != identity.KeyID ||
		strings.TrimSpace(response.KeyVersion) != identity.KeyVersion {
		return nil, errors.New("remote signer changed algorithm or key identity during signing")
	}
	if strings.TrimSpace(response.PublicKey) != "" {
		return nil, errors.New("remote signer signature response must not contain a public key")
	}
	signature, err := hex.DecodeString(strings.TrimSpace(response.Signature))
	if err != nil || len(signature) != ed25519.SignatureSize {
		return nil, errors.New("remote signer returned an invalid Ed25519 signature")
	}
	return signature, nil
}

func (signer *remoteA2ACardSigner) validateIdentityResponse(response a2aRemoteSignerResponse) (A2ACardSignerIdentity, error) {
	if strings.TrimSpace(response.Signature) != "" {
		return A2ACardSignerIdentity{}, errors.New("remote signer public-key response must not contain a signature")
	}
	algorithm := strings.ToLower(strings.TrimSpace(response.Algorithm))
	keyID := strings.TrimSpace(response.KeyID)
	keyVersion := strings.TrimSpace(response.KeyVersion)
	if algorithm != "ed25519" {
		return A2ACardSignerIdentity{}, fmt.Errorf("remote signer returned unsupported algorithm %q", algorithm)
	}
	if keyID != signer.keyID {
		return A2ACardSignerIdentity{}, errors.New("remote signer returned a different key ID")
	}
	if keyVersion == "" || len(keyVersion) > 256 {
		return A2ACardSignerIdentity{}, errors.New("remote signer returned an invalid key version")
	}
	publicKey, err := hex.DecodeString(strings.TrimSpace(response.PublicKey))
	if err != nil || len(publicKey) != ed25519.PublicKeySize {
		return A2ACardSignerIdentity{}, errors.New("remote signer returned an invalid Ed25519 public key")
	}
	return A2ACardSignerIdentity{
		Algorithm:  algorithm,
		PublicKey:  publicKey,
		KeyID:      keyID,
		KeyVersion: keyVersion,
	}, nil
}

func (signer *remoteA2ACardSigner) call(ctx context.Context, request a2aRemoteSignerRequest) (a2aRemoteSignerResponse, error) {
	body, err := json.Marshal(request)
	if err != nil {
		return a2aRemoteSignerResponse{}, fmt.Errorf("marshal signer request: %w", err)
	}
	httpRequest, err := http.NewRequestWithContext(ctx, http.MethodPost, signer.endpoint.String(), bytes.NewReader(body))
	if err != nil {
		return a2aRemoteSignerResponse{}, fmt.Errorf("create signer request: %w", err)
	}
	httpRequest.Header.Set("Authorization", "Bearer "+signer.token)
	httpRequest.Header.Set("Content-Type", "application/json")
	httpRequest.Header.Set("Accept", "application/json")
	response, err := signer.client.Do(httpRequest)
	if err != nil {
		return a2aRemoteSignerResponse{}, fmt.Errorf("call signer: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode < http.StatusOK || response.StatusCode >= http.StatusMultipleChoices {
		return a2aRemoteSignerResponse{}, fmt.Errorf("signer returned HTTP %d", response.StatusCode)
	}
	mediaType, _, err := mime.ParseMediaType(response.Header.Get("Content-Type"))
	if err != nil || mediaType != "application/json" {
		return a2aRemoteSignerResponse{}, errors.New("signer response must use application/json")
	}
	responseBody, err := io.ReadAll(io.LimitReader(response.Body, maxA2ARemoteSignerResponseBytes+1))
	if err != nil {
		return a2aRemoteSignerResponse{}, fmt.Errorf("read signer response: %w", err)
	}
	if int64(len(responseBody)) > maxA2ARemoteSignerResponseBytes {
		return a2aRemoteSignerResponse{}, fmt.Errorf("signer response exceeds %d bytes", maxA2ARemoteSignerResponseBytes)
	}
	decoder := json.NewDecoder(bytes.NewReader(responseBody))
	decoder.DisallowUnknownFields()
	var decoded a2aRemoteSignerResponse
	if err := decoder.Decode(&decoded); err != nil {
		return a2aRemoteSignerResponse{}, fmt.Errorf("decode signer response: %w", err)
	}
	if decoder.Decode(&struct{}{}) != io.EOF {
		return a2aRemoteSignerResponse{}, errors.New("signer response must contain exactly one JSON object")
	}
	return decoded, nil
}

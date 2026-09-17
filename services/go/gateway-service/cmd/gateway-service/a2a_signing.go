package main

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"strings"
)

const maxA2ASigningKeyFileBytes int64 = 4096

// A2ACardSigner keeps Agent Card signing independent from key storage so a
// file-backed key can later be replaced by a KMS or HSM adapter.
type A2ACardSigner interface {
	Identity(context.Context) (A2ACardSignerIdentity, error)
	Sign(context.Context, []byte) ([]byte, error)
}

type A2ACardSignerIdentity struct {
	Algorithm  string
	PublicKey  []byte
	KeyID      string
	KeyVersion string
}

type ed25519A2ACardSigner struct {
	privateKey ed25519.PrivateKey
}

func a2aCardSignerFromEnv() (A2ACardSigner, error) {
	path := strings.TrimSpace(os.Getenv("A2A_CARD_SIGNING_KEY_FILE"))
	remoteURL := strings.TrimSpace(os.Getenv("A2A_CARD_SIGNER_URL"))
	allowInsecureRemote, err := parseA2ABoolEnv("A2A_CARD_SIGNER_ALLOW_INSECURE_HTTP", false)
	if err != nil {
		return nil, err
	}
	if path != "" && remoteURL != "" {
		return nil, errors.New("A2A_CARD_SIGNING_KEY_FILE and A2A_CARD_SIGNER_URL are mutually exclusive")
	}
	if remoteURL != "" {
		return remoteA2ACardSignerFromEnv(remoteURL, allowInsecureRemote)
	}
	if allowInsecureRemote {
		return nil, errors.New("A2A_CARD_SIGNER_ALLOW_INSECURE_HTTP requires A2A_CARD_SIGNER_URL")
	}
	if path == "" {
		if strings.TrimSpace(os.Getenv("A2A_CARD_SIGNER_KEY_ID")) != "" ||
			strings.TrimSpace(os.Getenv("A2A_CARD_SIGNER_TOKEN_FILE")) != "" {
			return nil, errors.New("A2A_CARD_SIGNER_KEY_ID and A2A_CARD_SIGNER_TOKEN_FILE require A2A_CARD_SIGNER_URL")
		}
		return nil, nil
	}
	privateKey, err := loadA2AEd25519PrivateKey(path)
	if err != nil {
		return nil, fmt.Errorf("load A2A Card signing key: %w", err)
	}
	return &ed25519A2ACardSigner{privateKey: privateKey}, nil
}

func a2aRequireSignedSelfCardFromEnv() (bool, error) {
	return parseA2ABoolEnv("A2A_REQUIRE_SIGNED_SELF_CARD", false)
}

func loadA2AEd25519PrivateKey(path string) (ed25519.PrivateKey, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, fmt.Errorf("open signing key file: %w", err)
	}
	defer file.Close()
	contents, err := io.ReadAll(io.LimitReader(file, maxA2ASigningKeyFileBytes+1))
	if err != nil {
		return nil, fmt.Errorf("read signing key file: %w", err)
	}
	defer clearBytes(contents)
	if int64(len(contents)) > maxA2ASigningKeyFileBytes {
		return nil, fmt.Errorf("signing key file exceeds %d bytes", maxA2ASigningKeyFileBytes)
	}
	encoded := bytes.TrimSpace(contents)
	decoded := make([]byte, hex.DecodedLen(len(encoded)))
	decodedLength, err := hex.Decode(decoded, encoded)
	if err != nil {
		return nil, errors.New("signing key file must contain hex-encoded Ed25519 key material")
	}
	decoded = decoded[:decodedLength]
	defer clearBytes(decoded)
	switch len(decoded) {
	case ed25519.SeedSize:
		return ed25519.NewKeyFromSeed(decoded), nil
	case ed25519.PrivateKeySize:
		expected := ed25519.NewKeyFromSeed(decoded[:ed25519.SeedSize])
		if !bytes.Equal(expected, decoded) {
			return nil, errors.New("Ed25519 private key public-key suffix is inconsistent with its seed")
		}
		return append(ed25519.PrivateKey(nil), decoded...), nil
	default:
		return nil, errors.New("signing key must be a 32-byte Ed25519 seed or 64-byte Ed25519 private key")
	}
}

func clearBytes(value []byte) {
	for index := range value {
		value[index] = 0
	}
}

func (signer *ed25519A2ACardSigner) Identity(ctx context.Context) (A2ACardSignerIdentity, error) {
	if err := ctx.Err(); err != nil {
		return A2ACardSignerIdentity{}, err
	}
	if signer == nil || len(signer.privateKey) != ed25519.PrivateKeySize {
		return A2ACardSignerIdentity{}, errors.New("Ed25519 Agent Card signer is not initialized")
	}
	publicKey := signer.privateKey.Public().(ed25519.PublicKey)
	return A2ACardSignerIdentity{
		Algorithm: "ed25519",
		PublicKey: append([]byte(nil), publicKey...),
	}, nil
}

func (signer *ed25519A2ACardSigner) Sign(ctx context.Context, payload []byte) ([]byte, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if signer == nil || len(signer.privateKey) != ed25519.PrivateKeySize {
		return nil, errors.New("Ed25519 Agent Card signer is not initialized")
	}
	return ed25519.Sign(signer.privateKey, payload), nil
}

func SignAgentCard(ctx context.Context, card *AgentCard, signer A2ACardSigner) error {
	if card == nil {
		return errors.New("Agent Card is required")
	}
	if signer == nil {
		return errors.New("Agent Card signer is required")
	}
	identity, err := signer.Identity(ctx)
	if err != nil {
		return fmt.Errorf("read Agent Card signing identity: %w", err)
	}
	algorithm := strings.ToLower(strings.TrimSpace(identity.Algorithm))
	if algorithm != "ed25519" {
		return fmt.Errorf("unsupported Agent Card signing algorithm %q", algorithm)
	}
	if len(identity.PublicKey) != ed25519.PublicKeySize {
		return errors.New("Agent Card signer returned an invalid Ed25519 public key")
	}
	signed := *card
	signed.Security = &AgentSecurity{
		PublicKey:    hex.EncodeToString(identity.PublicKey),
		KeyAlgorithm: algorithm,
		KeyID:        strings.TrimSpace(identity.KeyID),
		KeyVersion:   strings.TrimSpace(identity.KeyVersion),
	}
	signed.Signature = ""
	payload, err := json.Marshal(&signed)
	if err != nil {
		return fmt.Errorf("marshal Agent Card for signing: %w", err)
	}
	signature, err := signer.Sign(ctx, payload)
	if err != nil {
		return fmt.Errorf("sign Agent Card: %w", err)
	}
	if len(signature) != ed25519.SignatureSize {
		return errors.New("Agent Card signer returned an invalid Ed25519 signature")
	}
	if !ed25519.Verify(ed25519.PublicKey(identity.PublicKey), payload, signature) {
		return errors.New("Agent Card signer returned a signature that does not match its public key")
	}
	signed.Signature = hex.EncodeToString(signature)
	*card = signed
	return nil
}

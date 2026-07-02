// Package iam provides the platform-wide identity & access management core:
// JWT claim definitions, tenant context propagation, an RBAC policy engine,
// and ABAC attribute evaluation. It is intentionally framework-agnostic —
// services compose it with their own HTTP/NATS layers.
//
// The package centralizes what every online Go service needs for multi-tenant
// isolation: verify a token, extract the tenant + principal + roles, and decide
// whether a given action on a given resource is allowed. Sensitive-tool
// classification (P3-1) builds on top of the RBAC/ABAC primitives here.
package iam

import (
	"errors"
	"fmt"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

// Claims is the JWT payload issued by iam-service and consumed by every Go
// ingress service (gateway, session-service, tool-permission-service, ...).
// Roles carry coarse-grained RBAC identities (e.g. "admin", "member"); Scopes
// carry fine-grained permission strings (e.g. "session:write", "tool:execute").
// TenantRole is the principal's role within the tenant (owner/admin/member/viewer).
type Claims struct {
	TenantID   string   `json:"tenant_id"`
	UserID     string   `json:"user_id"`
	SessionID  string   `json:"session_id,omitempty"`
	Roles      []string `json:"roles,omitempty"`
	Scopes     []string `json:"scopes,omitempty"`
	TenantRole string   `json:"tenant_role,omitempty"`
	jwt.RegisteredClaims
}

// TokenIssuer signs and validates platform JWTs. A single instance is shared
// by iam-service (issuer) and the ingress services (verifiers). An empty
// secret puts the verifier in dev mode: tokens are accepted without signature
// checks and claims come from request parameters.
type TokenIssuer struct {
	secret  []byte
	devMode bool
	issuer  string
	ttl     time.Duration
}

// NewTokenIssuer returns a TokenIssuer. If secret is empty the verifier runs in
// dev mode (no signature enforcement). ttl is the access-token lifetime; the
// issuer is the "iss" claim recorded in each token.
func NewTokenIssuer(secret []byte, issuer string, ttl time.Duration) *TokenIssuer {
	return &TokenIssuer{secret: secret, devMode: len(secret) == 0, issuer: issuer, ttl: ttl}
}

// IsDevMode reports whether signature enforcement is disabled.
func (t *TokenIssuer) IsDevMode() bool { return t.devMode }

// Issue produces a signed JWT for the given principal. The token embeds the
// tenant id, user id, roles, scopes, and tenant role so downstream services
// can make authorization decisions without a second round-trip to iam-service.
func (t *TokenIssuer) Issue(c Claims) (string, error) {
	if c.TenantID == "" || c.UserID == "" {
		return "", errors.New("iam: tenant_id and user_id are required to issue a token")
	}
	now := time.Now().UTC()
	c.RegisteredClaims = jwt.RegisteredClaims{
		Issuer:    t.issuer,
		Subject:   c.UserID,
		IssuedAt:  jwt.NewNumericDate(now),
		ExpiresAt: jwt.NewNumericDate(now.Add(t.ttl)),
		NotBefore: jwt.NewNumericDate(now),
	}
	if t.devMode {
		// Dev mode still returns an unsigned token string so the wire format
		// is consistent; verifiers in dev mode skip signature checks.
		token := jwt.NewWithClaims(jwt.SigningMethodNone, c)
		return token.SignedString(jwt.UnsafeAllowNoneSignatureType)
	}
	token := jwt.NewWithClaims(jwt.SigningMethodHS256, c)
	return token.SignedString(t.secret)
}

// Verify validates the token string and returns the claims. In dev mode the
// returned claims are empty (callers fall back to request parameters); in
// production a missing or invalid token is an error.
func (t *TokenIssuer) Verify(tokenString string) (*Claims, error) {
	if t.devMode {
		return &Claims{}, nil
	}
	if tokenString == "" {
		return nil, errors.New("iam: token is required")
	}
	claims := &Claims{}
	_, err := jwt.ParseWithClaims(tokenString, claims, func(tkn *jwt.Token) (any, error) {
		if _, ok := tkn.Method.(*jwt.SigningMethodHMAC); !ok {
			return nil, fmt.Errorf("iam: unexpected signing method %v", tkn.Header["alg"])
		}
		return t.secret, nil
	})
	if err != nil {
		return nil, fmt.Errorf("iam: verify token: %w", err)
	}
	return claims, nil
}

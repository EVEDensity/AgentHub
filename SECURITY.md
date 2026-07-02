# Security Policy

## Reporting a Vulnerability

**Do not open a public issue.** Email security reports to:

📧 **security@agenthub.dev**

You'll receive an acknowledgment within 48 hours.

### What to include
- Description of the vulnerability
- Steps to reproduce
- Affected components and versions
- Potential impact

### Process
1. Private report → acknowledgment (48h)
2. Investigation and fix development
3. Coordinated disclosure with the fix release

---

## Deployment Checklist

If you're deploying AgentHub in production:

- [ ] Change `JWT_SECRET` and `KMS_MASTER_KEY` from defaults
- [ ] Rotate all database and service passwords
- [ ] Use TLS on external-facing endpoints
- [ ] Keep infrastructure services (NATS, PostgreSQL, Redis) on internal networks
- [ ] Review the sensitive-tool pattern list for your use case
- [ ] Keep dependencies updated (`go mod tidy`, `cargo audit`)

---

## Scope

**In scope:** Auth bypass, JWT forgery, KMS extraction, message injection, sandbox escape, SQL injection, SSRF, sensitive data in logs.

**Out of scope:** DoS via resource exhaustion, physical attacks, social engineering, vulnerabilities in upstream infrastructure (PostgreSQL, Redis, NATS).

---

Thank you for helping keep AgentHub secure. 🔐

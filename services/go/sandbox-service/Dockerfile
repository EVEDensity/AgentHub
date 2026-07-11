# syntax=docker/dockerfile:1.4
# ─────────────────────────────────────────────────────────────────────
# sandbox-service — Docker sandbox execution backend (P0.1-A)
# ─────────────────────────────────────────────────────────────────────
# Go builder stage follows project convention: golang:1.22-alpine +
# apk add git ca-certificates + GOPROXY=https://goproxy.cn,direct +
# go mod tidy. sandbox-service has a standalone go.mod (stdlib only,
# no external deps), so we COPY just the service directory.
#
# Runtime: alpine:3.20 + ca-certificates. The service talks to Docker
# Engine API directly via /var/run/docker.sock (pure Go HTTP over UNIX
# socket, see internal/docker/client.go) — no docker-cli binary needed.
# ─────────────────────────────────────────────────────────────────────
FROM golang:1.22-alpine AS builder
RUN apk add --no-cache git ca-certificates
ENV GOPROXY=https://goproxy.cn,direct
WORKDIR /src
COPY services/go/sandbox-service /src
RUN go mod tidy && CGO_ENABLED=0 go build -o /out/sandbox-service ./cmd/sandbox-service

FROM alpine:3.20
RUN apk add --no-cache ca-certificates
WORKDIR /app
COPY --from=builder /out/sandbox-service /app/sandbox-service
EXPOSE 8097
ENTRYPOINT ["/app/sandbox-service"]

# syntax=docker/dockerfile:1.4
# ─────────────────────────────────────────────────────────────────────
# mcp-gateway — Model Context Protocol gateway service
# Bridges MCP tools/SSE/stdio transport into AgentHub event bus.
# ─────────────────────────────────────────────────────────────────────
FROM golang:1.22-alpine AS builder
RUN apk add --no-cache git ca-certificates
ENV GOPROXY=https://goproxy.cn,direct
WORKDIR /src/services/go/mcp-gateway
COPY services/go/mcp-gateway /src/services/go/mcp-gateway
COPY services/go/shared/iam /src/services/go/shared/iam
RUN go mod download && CGO_ENABLED=0 go build -o /out/mcp-gateway ./cmd/mcp-gateway

FROM alpine:3.20
RUN apk add --no-cache ca-certificates
WORKDIR /app
COPY --from=builder /out/mcp-gateway /app/mcp-gateway
EXPOSE 8099
ENTRYPOINT ["/app/mcp-gateway"]

FROM golang:1.22-alpine AS builder
RUN apk add --no-cache git ca-certificates
ENV GOPROXY=https://goproxy.cn,direct
WORKDIR /src
COPY services/go /src/services/go
WORKDIR /src/services/go/realtime-orchestrator
RUN go mod tidy && go build -o /out/realtime-orchestrator ./cmd/realtime-orchestrator

FROM alpine:3.20
WORKDIR /app
COPY --from=builder /out/realtime-orchestrator /app/realtime-orchestrator
EXPOSE 8082
CMD ["/app/realtime-orchestrator"]

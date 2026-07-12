FROM golang:1.22-alpine AS builder
RUN apk add --no-cache git ca-certificates
ENV GOPROXY=https://goproxy.cn,direct
WORKDIR /src
COPY services/go /src/services/go
WORKDIR /src/services/go/agent-runtime-control-plane
RUN go mod tidy && go build -o /out/agent-runtime-control-plane ./cmd/agent-runtime-control-plane

FROM alpine:3.20
WORKDIR /app
COPY --from=builder /out/agent-runtime-control-plane /app/agent-runtime-control-plane
EXPOSE 8085
CMD ["/app/agent-runtime-control-plane"]

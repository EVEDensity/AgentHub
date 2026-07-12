FROM golang:1.22-alpine AS builder
RUN apk add --no-cache git ca-certificates
ENV GOPROXY=https://goproxy.cn,direct
WORKDIR /src
COPY services/go /src/services/go
WORKDIR /src/services/go/audit-log-service
RUN go mod tidy && go build -o /out/audit-log-service ./cmd/audit-log-service

FROM alpine:3.20
WORKDIR /app
COPY --from=builder /out/audit-log-service /app/audit-log-service
EXPOSE 8087
CMD ["/app/audit-log-service"]

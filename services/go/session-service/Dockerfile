FROM golang:1.22-alpine AS builder
RUN apk add --no-cache git ca-certificates
ENV GOPROXY=https://goproxy.cn,direct
WORKDIR /src
COPY services/go /src/services/go
WORKDIR /src/services/go/session-service
RUN go mod tidy && go build -o /out/session-service ./cmd/session-service

FROM alpine:3.20
WORKDIR /app
COPY --from=builder /out/session-service /app/session-service
EXPOSE 8083
CMD ["/app/session-service"]

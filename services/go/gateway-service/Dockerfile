FROM golang:1.22-alpine AS builder
RUN apk add --no-cache git ca-certificates
ENV GOPROXY=https://goproxy.cn,direct
WORKDIR /src
COPY services/go /src/services/go
WORKDIR /src/services/go/gateway-service
RUN go mod tidy && go build -o /out/gateway-service ./cmd/gateway-service

FROM alpine:3.20
WORKDIR /app
COPY --from=builder /out/gateway-service /app/gateway-service
EXPOSE 8081
CMD ["/app/gateway-service"]

FROM golang:1.22-alpine AS builder
RUN apk add --no-cache git ca-certificates
ENV GOPROXY=https://goproxy.cn,direct
WORKDIR /src
COPY services/go /src/services/go
WORKDIR /src/services/go/stream-delivery-service
RUN go mod tidy && go build -o /out/stream-delivery-service ./cmd/stream-delivery-service

FROM alpine:3.20
WORKDIR /app
COPY --from=builder /out/stream-delivery-service /app/stream-delivery-service
EXPOSE 8086
CMD ["/app/stream-delivery-service"]

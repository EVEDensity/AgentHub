FROM golang:1.22-alpine AS builder
RUN apk add --no-cache git ca-certificates
ENV GOPROXY=https://goproxy.cn,direct
WORKDIR /src
COPY services/go /src/services/go
WORKDIR /src/services/go/tool-permission-service
RUN go mod tidy && go build -o /out/tool-permission-service ./cmd/tool-permission-service

FROM alpine:3.20
WORKDIR /app
COPY --from=builder /out/tool-permission-service /app/tool-permission-service
EXPOSE 8084
CMD ["/app/tool-permission-service"]

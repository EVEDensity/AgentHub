FROM golang:1.22-alpine AS builder
RUN apk add --no-cache git ca-certificates
ENV GOPROXY=https://goproxy.cn,direct
WORKDIR /src
COPY services/go /src/services/go
WORKDIR /src/services/go/iam-service
RUN go mod tidy && go build -o /out/iam-service ./cmd/iam-service

FROM alpine:3.20
WORKDIR /app
COPY --from=builder /out/iam-service /app/iam-service
EXPOSE 8088
CMD ["/app/iam-service"]

module github.com/agenthub/mcp-gateway

go 1.22.0

require github.com/agenthub/platform/shared/iam v0.0.0

require github.com/golang-jwt/jwt/v5 v5.2.1 // indirect

replace github.com/agenthub/platform/shared/iam => ../shared/iam

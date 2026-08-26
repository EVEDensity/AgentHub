// MCP Gateway — Model Context Protocol server for AgentHub.
//
// Implements the MCP specification (2024-11-05) with dual transport:
//   - STDIO mode: Run as a child process, communicate via stdin/stdout.
//     Activated when MCP_TRANSPORT=stdio or when stdin is not a terminal.
//   - SSE mode:  Run as an HTTP server, expose GET /sse + POST /message.
//     Activated by default or when MCP_TRANSPORT=sse.
//
// Exposes AgentHub platform capabilities (knowledge search, agent tools,
// templates, workspaces) as MCP tools, resources, and prompts.
//
// Usage:
//
//	mcp-gateway                           # SSE mode (HTTP on :8099)
//	MCP_TRANSPORT=stdio mcp-gateway       # STDIO mode
//	MCP_ADDR=:9000 mcp-gateway            # Custom SSE port
package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"log"
	"net/http"
	"os"
	"os/signal"
	"path/filepath"
	"syscall"
	"time"

	mcpauth "github.com/agenthub/mcp-gateway/internal/auth"
	"github.com/agenthub/mcp-gateway/internal/protocol"
	"github.com/agenthub/mcp-gateway/internal/registry"
	"github.com/agenthub/mcp-gateway/internal/transport"
	"github.com/agenthub/platform/shared/iam"
)

func main() {
	// ── Configuration ────────────────────────────────────────────────
	transportMode := getenv("MCP_TRANSPORT", "sse")
	localMode := os.Getenv("MCP_LOCAL_MODE") == "true"
	addr := getenv("MCP_ADDR", ":8099")
	if localMode {
		addr = getenv("MCP_ADDR", "127.0.0.1:8099")
	}
	knowledgeURL := getenv("KNOWLEDGE_URL", "http://127.0.0.1:8092")
	gatewayURL := getenv("GATEWAY_URL", "http://127.0.0.1:8081")
	jwtSecret := os.Getenv("JWT_SECRET")
	if localMode && jwtSecret == "" {
		var err error
		jwtSecret, err = localJWTSecret()
		if err != nil {
			log.Fatalf("local JWT secret: %v", err)
		}
	}

	log.SetFlags(log.LstdFlags | log.Lmsgprefix)
	log.SetPrefix("[mcp-gateway] ")
	if transportMode != "stdio" && jwtSecret == "" && os.Getenv("MCP_ALLOW_INSECURE_DEV_AUTH") != "true" {
		log.Fatal("JWT_SECRET is required for HTTP transport; set MCP_ALLOW_INSECURE_DEV_AUTH=true only for local development")
	}

	// ── Initialize registry with AgentHub tools ──────────────────────
	reg := registry.New(knowledgeURL, gatewayURL)
	registry.Logger.Printf("MCP Gateway initialized with %d tools, %d resources, %d prompts",
		len(reg.ListTools()), len(reg.ListResources()), len(reg.ListPrompts()))

	// ── Create protocol handler ──────────────────────────────────────
	handler := protocol.NewHandler(
		protocol.ServerInfo{
			Name:    "AgentHub MCP Gateway",
			Version: "1.0.0",
		},
		protocol.ServerCapabilities{
			Tools:     &protocol.ToolsCapability{ListChanged: false},
			Resources: &protocol.ResourcesCapability{Subscribe: false, ListChanged: false},
			Prompts:   &protocol.PromptsCapability{ListChanged: false},
			Logging:   &protocol.LoggingCapability{},
		},
		reg,
	)

	// ── Message dispatch adapter ─────────────────────────────────────
	dispatcher := func(ctx context.Context, raw json.RawMessage) ([]json.RawMessage, error) {
		results, err := handler.Dispatch(ctx, raw)
		if err != nil {
			return nil, err
		}
		responses := make([]json.RawMessage, 0, len(results))
		for _, r := range results {
			b, err := json.Marshal(r)
			if err != nil {
				return nil, err
			}
			responses = append(responses, b)
		}
		return responses, nil
	}

	// MCP RPC authentication reuses the platform IAM verifier. The transport
	// remains protocol-only; this callback intersects the authenticated
	// principal's permissions with the Contract capability declaration.
	issuer := iam.NewTokenIssuer(
		[]byte(jwtSecret),
		getenv("IAM_ISSUER", "iam-service"),
		time.Hour,
	)
	statelessRPC := transport.NewStatelessHTTPHandlerWithAuthorizer(dispatcher, mcpauth.AuthorizeMCP)
	rpcHandler := mcpauth.Middleware(issuer, statelessRPC, func(_ *http.Request, reason string) {
		log.Printf("MCP authorization denied: %s", reason)
	})

	// ── Start transport ──────────────────────────────────────────────
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()

	switch transportMode {
	case "stdio":
		runSTDIO(ctx, dispatcher)
	default:
		runSSE(ctx, addr, dispatcher, rpcHandler)
	}
}

// ── STDIO Mode ───────────────────────────────────────────────────────

func runSTDIO(ctx context.Context, dispatcher transport.MessageHandler) {
	log.Printf("Starting MCP Gateway in STDIO mode — waiting for JSON-RPC messages on stdin")
	t := transport.NewSTDIOTransport(dispatcher)
	if err := t.Serve(ctx); err != nil {
		log.Fatalf("STDIO transport error: %v", err)
	}
}

// ── SSE (HTTP) Mode ──────────────────────────────────────────────────

func runSSE(ctx context.Context, addr string, dispatcher transport.MessageHandler, rpcHandler http.Handler) {
	sseHandler := transport.NewSSEHandler(dispatcher, "/mcp")

	mux := http.NewServeMux()
	// MCP SSE endpoints
	mux.Handle("/mcp/sse", sseHandler)
	mux.Handle("/mcp/message", sseHandler)
	// Stateless JSON-RPC endpoint; every request carries its own execution context.
	mux.Handle("/mcp/rpc", rpcHandler)

	// Health + info endpoints
	mux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.Write([]byte("ok"))
	})
	mux.HandleFunc("/", func(w http.ResponseWriter, _ *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"service":  "mcp-gateway",
			"version":  "1.0.0",
			"protocol": "2024-11-05",
			"endpoints": map[string]string{
				"sse":     "GET /mcp/sse",
				"message": "POST /mcp/message",
				"rpc":     "POST /mcp/rpc",
				"healthz": "GET /healthz",
			},
			"sessions": sseHandler.SessionCount(),
		})
	})

	// Start session pruner
	go func() {
		ticker := time.NewTicker(5 * time.Minute)
		defer ticker.Stop()
		for {
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
				sseHandler.Transport().PruneSessions(30 * time.Minute)
			}
		}
	}()

	server := &http.Server{Addr: addr, Handler: mux}

	// Graceful shutdown
	go func() {
		<-ctx.Done()
		log.Println("Shutting down MCP Gateway...")
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		server.Shutdown(shutdownCtx)
	}()

	log.Printf("MCP Gateway (SSE mode) listening on %s", addr)
	log.Printf("  SSE endpoint:   GET  http://localhost%s/mcp/sse", addr)
	log.Printf("  Message endpoint: POST http://localhost%s/mcp/message", addr)
	log.Printf("  Stateless RPC:  POST http://localhost%s/mcp/rpc", addr)
	log.Printf("  Health:         GET  http://localhost%s/healthz", addr)

	if err := server.ListenAndServe(); err != http.ErrServerClosed {
		log.Fatalf("SSE server error: %v", err)
	}
	log.Println("MCP Gateway stopped")
}

func localJWTSecret() (string, error) {
	root := os.Getenv("AGENTHUB_LOCAL_DATA")
	if root == "" {
		if base, err := os.UserConfigDir(); err == nil {
			root = filepath.Join(base, "AgentHub", "data")
		} else {
			root = ".agenthub-data"
		}
	}
	path := filepath.Join(root, "mcp-jwt.secret")
	if data, err := os.ReadFile(path); err == nil && len(data) >= 32 {
		return string(data), nil
	}
	secret := make([]byte, 32)
	if _, err := rand.Read(secret); err != nil {
		return "", err
	}
	if err := os.MkdirAll(root, 0700); err != nil {
		return "", err
	}
	if err := os.WriteFile(path, []byte(hex.EncodeToString(secret)), 0600); err != nil {
		return "", err
	}
	return hex.EncodeToString(secret), nil
}

// ── Helpers ──────────────────────────────────────────────────────────

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

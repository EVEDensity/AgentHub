// sandbox-service — Docker sandbox execution backend (P2-1)
//
// Provides HTTP REST API for managing isolated Docker containers used as
// Agent code-execution sandboxes. Supports seccomp profiles, resource
// limits (CPU/memory), and command execution with result capture.
//
// Endpoints:
//   GET    /containers          — list all sandbox containers
//   POST   /containers          — create a new container
//   GET    /containers/{id}     — get container info
//   POST   /containers/{id}/start  — start a container
//   POST   /containers/{id}/stop   — stop a container
//   POST   /containers/{id}/exec   — execute a command
//   DELETE /containers/{id}     — destroy a container
//   GET    /containers/{id}/logs   — get execution logs
//   GET    /stats               — aggregate stats
//   GET    /healthz             — health check
//   GET    /readyz              — Docker readiness check
//
// Unavailable Docker fails closed. Only network=none is currently supported.

package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/agenthub/sandbox-service/internal/docker"
)

// ── Application State ────────────────────────────────────────────────

type sandboxServer struct {
	mu         sync.RWMutex
	docker     dockerBackend
	containers map[string]*docker.ContainerInfo // keyed by container ID
	execLogs   map[string][]execLogEntry        // keyed by container ID
	totalExecs int

	containerSeq int
}

type dockerBackend interface {
	Ping(context.Context) error
	Create(context.Context, docker.ContainerConfig) (*docker.ContainerInfo, error)
	Start(context.Context, string) error
	Stop(context.Context, string) error
	Remove(context.Context, string) error
	Exec(context.Context, string, string) (*docker.ExecResult, error)
}

type execLogEntry struct {
	ID          string `json:"id"`
	ContainerID string `json:"container_id"`
	Command     string `json:"command"`
	ExitCode    int    `json:"exit_code"`
	Stdout      string `json:"stdout"`
	Stderr      string `json:"stderr"`
	DurationMs  int64  `json:"duration_ms"`
	ExecutedAt  string `json:"executed_at"`
}

// ── Main ─────────────────────────────────────────────────────────────

func main() {
	socketPath := os.Getenv("DOCKER_SOCKET")
	if socketPath == "" {
		socketPath = "/var/run/docker.sock" // default Linux
	}

	seccompProfile := os.Getenv("SANDBOX_SECCOMP_PROFILE")

	// Keep the real client on failure so readiness can recover when Docker does.
	// Operations still fail; there is no simulated execution backend.
	dc := docker.NewClient(socketPath, seccompProfile)
	probeCtx, cancelProbe := context.WithTimeout(context.Background(), 3*time.Second)
	if err := dc.Ping(probeCtx); err != nil {
		log.Printf("sandbox-service: Docker unavailable (%v); execution is disabled until readiness recovers", err)
	} else {
		log.Printf("sandbox-service: connected to Docker at %s", socketPath)
	}
	cancelProbe()

	srv := &sandboxServer{
		docker:     dc,
		containers: make(map[string]*docker.ContainerInfo),
		execLogs:   make(map[string][]execLogEntry),
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", srv.handleHealthz)
	mux.HandleFunc("/readyz", srv.handleReadyz)
	mux.HandleFunc("/containers", srv.handleContainers)
	mux.HandleFunc("/containers/", srv.handleContainerByID)
	mux.HandleFunc("/stats", srv.handleStats)
	mux.HandleFunc("/v1/execute", srv.handleV1Execute)

	addr := getenv("SANDBOX_ADDR", ":8097")
	server := &http.Server{Addr: addr, Handler: mux}

	go func() {
		log.Printf("sandbox-service listening on %s", addr)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			log.Fatalf("sandbox-service: %v", err)
		}
	}()

	// Graceful shutdown
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	<-ctx.Done()

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	server.Shutdown(shutdownCtx)
	log.Println("sandbox-service: shut down")
}

// ── Handlers ─────────────────────────────────────────────────────────

func (s *sandboxServer) handleHealthz(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "alive"})
}

func (s *sandboxServer) handleReadyz(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()
	if getenv("SANDBOX_NETWORK_ALLOW", "none") != "none" {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"status": "not_ready", "ready": false, "mode": "unavailable"})
		return
	}
	if err := s.docker.Ping(ctx); err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{"status": "not_ready", "ready": false, "mode": "unavailable"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"status": "ready", "ready": true, "mode": "docker"})
}

func (s *sandboxServer) handleContainers(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	switch r.Method {
	case http.MethodGet:
		// List all containers
		s.mu.RLock()
		defer s.mu.RUnlock()
		list := make([]*docker.ContainerInfo, 0, len(s.containers))
		for _, c := range s.containers {
			list = append(list, c)
		}
		writeJSON(w, http.StatusOK, list)

	case http.MethodPost:
		// Create container
		var req struct {
			AgentID      string   `json:"agent_id"`
			TenantID     string   `json:"tenant_id"`
			CPULimit     float64  `json:"cpu_limit"`
			MemoryMB     int      `json:"memory_mb"`
			DiskMB       int      `json:"disk_mb"`
			Image        string   `json:"image"`
			NetworkAllow []string `json:"network_allow"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body: " + err.Error()})
			return
		}
		if req.AgentID == "" {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "agent_id is required"})
			return
		}
		if len(req.NetworkAllow) > 0 {
			writeFailure(w, http.StatusUnprocessableEntity, docker.ErrNetworkPolicyUnsupported)
			return
		}
		if err := s.docker.Ping(ctx); err != nil {
			writeFailure(w, http.StatusServiceUnavailable, errors.New("Docker is unavailable"))
			return
		}
		if req.TenantID == "" {
			req.TenantID = "default"
		}
		if req.Image == "" {
			req.Image = "agenthub/sandbox:latest"
		}
		if req.CPULimit <= 0 {
			req.CPULimit = 1.0
		}
		if req.MemoryMB <= 0 {
			req.MemoryMB = 512
		}
		if req.DiskMB <= 0 {
			req.DiskMB = 10240
		}

		s.mu.Lock()
		defer s.mu.Unlock()

		s.containerSeq++
		containerName := fmt.Sprintf("ah-sandbox-%s-%d", req.AgentID, s.containerSeq)

		info, err := s.docker.Create(ctx, docker.ContainerConfig{
			Name:         containerName,
			Image:        req.Image,
			AgentID:      req.AgentID,
			TenantID:     req.TenantID,
			CPU:          req.CPULimit,
			MemoryMB:     req.MemoryMB,
			DiskMB:       req.DiskMB,
			Network:      "none",
			NetworkAllow: req.NetworkAllow,
		})
		if err != nil {
			log.Printf("sandbox-service: create container failed: %v", err)
			writeExecutionFailure(w, err)
			return
		}

		s.containers[info.ID] = info
		s.execLogs[info.ID] = make([]execLogEntry, 0)

		log.Printf("sandbox-service: created container %s for agent %s", info.ID, req.AgentID)
		writeJSON(w, http.StatusCreated, info)

	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (s *sandboxServer) handleContainerByID(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	// Parse: /containers/{id}[/{action}]
	path := strings.TrimPrefix(r.URL.Path, "/containers/")
	parts := strings.SplitN(path, "/", 2)
	containerID := parts[0]
	action := ""
	if len(parts) > 1 {
		action = parts[1]
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	info, ok := s.containers[containerID]
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "container not found"})
		return
	}

	switch {
	case info.Status == "failed" && r.Method == http.MethodPost:
		// Unconfirmed exec/cleanup permanently invalidates this container.
		// Start or stop must not erase that fence if Docker later recovers.
		writeJSON(w, http.StatusConflict, map[string]string{"error": "failed container must be deleted"})
		return

	case r.Method == http.MethodGet && action == "logs":
		logs, _ := s.execLogs[containerID]
		writeJSON(w, http.StatusOK, logs)

	case r.Method == http.MethodGet && action == "":
		writeJSON(w, http.StatusOK, info)

	case r.Method == http.MethodPost && action == "start":
		if err := s.docker.Start(ctx, containerID); err != nil {
			writeExecutionFailure(w, err)
			return
		}
		info.Status = "running"
		now := time.Now()
		info.StartedAt = &now
		writeJSON(w, http.StatusOK, map[string]string{"status": "started"})

	case r.Method == http.MethodPost && action == "stop":
		if err := s.docker.Stop(ctx, containerID); err != nil {
			writeExecutionFailure(w, err)
			return
		}
		info.Status = "stopped"
		writeJSON(w, http.StatusOK, map[string]string{"status": "stopped"})

	case r.Method == http.MethodPost && action == "exec":
		var req struct {
			Command string `json:"command"`
			Timeout int    `json:"timeout"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request"})
			return
		}
		if req.Command == "" {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "command is required"})
			return
		}

		if info.Status != "running" {
			writeJSON(w, http.StatusConflict, map[string]string{"error": "container is not running"})
			return
		}

		execCtx, cancelExec := context.WithTimeout(r.Context(), executionTimeout(req.Timeout))
		defer cancelExec()
		result, err := s.docker.Exec(execCtx, containerID, req.Command)
		if err != nil {
			log.Printf("sandbox-service: exec in %s failed: %v", containerID, err)
			// An interrupted HTTP stream does not terminate Docker exec. Force
			// removal with an independent deadline before admitting more work.
			info.Status = "failed"
			if cleanupErr := s.removeContainer(containerID); cleanupErr != nil {
				log.Printf("sandbox-service: failed execution cleanup for %s: %v", containerID, cleanupErr)
			}
			writeExecutionFailure(w, err)
			return
		}

		entry := execLogEntry{
			ID:          fmt.Sprintf("exec-%s-%d", containerID, len(s.execLogs[containerID])),
			ContainerID: containerID,
			Command:     req.Command,
			ExitCode:    result.ExitCode,
			Stdout:      result.Stdout,
			Stderr:      result.Stderr,
			DurationMs:  result.DurationMs,
			ExecutedAt:  time.Now().UTC().Format(time.RFC3339),
		}
		s.execLogs[containerID] = append(s.execLogs[containerID], entry)
		s.totalExecs++

		writeJSON(w, http.StatusOK, entry)

	case r.Method == http.MethodDelete:
		if err := s.docker.Remove(ctx, containerID); err != nil {
			writeExecutionFailure(w, err)
			return
		}
		delete(s.containers, containerID)
		delete(s.execLogs, containerID)
		log.Printf("sandbox-service: destroyed container %s", containerID)
		writeJSON(w, http.StatusOK, map[string]string{"status": "destroyed"})

	default:
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (s *sandboxServer) handleStats(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	s.mu.RLock()
	defer s.mu.RUnlock()

	byStatus := make(map[string]int)
	active := 0
	var totalDur float64
	execCount := 0

	for _, c := range s.containers {
		byStatus[c.Status]++
		if c.Status == "running" {
			active++
		}
	}

	for _, logs := range s.execLogs {
		for _, l := range logs {
			totalDur += float64(l.DurationMs)
			execCount++
		}
	}

	avgMs := 0.0
	if execCount > 0 {
		avgMs = totalDur / float64(execCount)
	}

	stats := docker.Stats{
		TotalContainers:  len(s.containers),
		ActiveContainers: active,
		TotalExecs:       s.totalExecs,
		AvgDurationMs:    avgMs,
		ByStatus:         byStatus,
	}

	writeJSON(w, http.StatusOK, stats)
}

// handleV1Execute — one-shot code execution convenience endpoint (P0.1-B).
//
// Creates a temporary container, starts it, executes the code, captures
// output, and destroys the container — all in a single HTTP call. The
// Python SandboxExecutor calls this endpoint in "remote" mode.
//
// POST /v1/execute
//
//	{"code": "print('hi')", "language": "python", "timeout": 30, "image": ""}
//
// → {"success": true, "stdout": "hi\n", "stderr": "", "exit_code": 0, "duration_ms": 150}
func (s *sandboxServer) handleV1Execute(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req struct {
		Code         string   `json:"code"`
		Language     string   `json:"language"`
		Timeout      int      `json:"timeout"`
		Image        string   `json:"image"`
		NetworkAllow []string `json:"network_allow"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body: " + err.Error()})
		return
	}
	if req.Code == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "code is required"})
		return
	}
	if len(req.NetworkAllow) > 0 {
		writeFailure(w, http.StatusUnprocessableEntity, docker.ErrNetworkPolicyUnsupported)
		return
	}

	lang := strings.ToLower(req.Language)
	if lang == "" {
		lang = "python"
	}
	if lang == "sh" || lang == "shell" {
		lang = "bash"
	}
	if lang != "python" && lang != "bash" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "unsupported language: " + lang})
		return
	}

	ctx, cancel := context.WithTimeout(r.Context(), executionTimeout(req.Timeout))
	defer cancel()

	image := req.Image
	if image == "" {
		image = getenv("SANDBOX_IMAGE", "agenthub/sandbox:latest")
	}
	cpuLimit := getenvFloat("SANDBOX_CPU_LIMIT", 1.0)
	memMB := getenvInt("SANDBOX_MEMORY_MB", 512)
	networkAllow := getenv("SANDBOX_NETWORK_ALLOW", "none")
	if networkAllow != "none" {
		writeFailure(w, http.StatusServiceUnavailable, docker.ErrNetworkPolicyUnsupported)
		return
	}
	if err := s.docker.Ping(ctx); err != nil {
		writeFailure(w, http.StatusServiceUnavailable, errors.New("Docker is unavailable"))
		return
	}

	// Build the exec command. Base64-encode the code to avoid all shell
	// escaping issues (quotes, newlines, special chars).
	encoded := base64.StdEncoding.EncodeToString([]byte(req.Code))
	var execCmd string
	if lang == "python" {
		execCmd = "echo '" + encoded + "' | base64 -d | python"
	} else {
		execCmd = "echo '" + encoded + "' | base64 -d | bash"
	}

	// ── Create container ───────────────────────────────────────────────
	s.mu.Lock()
	s.containerSeq++
	containerName := fmt.Sprintf("ah-exec-%d-%d", time.Now().UnixNano(), s.containerSeq)
	s.mu.Unlock()

	info, err := s.docker.Create(ctx, docker.ContainerConfig{
		Name:     containerName,
		Image:    image,
		AgentID:  "v1-exec",
		TenantID: "default",
		CPU:      cpuLimit,
		MemoryMB: memMB,
		DiskMB:   10240,
		Network:  "none",
	})
	if err != nil {
		log.Printf("sandbox-service /v1/execute: create failed: %v", err)
		writeExecutionFailure(w, err)
		return
	}

	// Cleanup: always destroy the container after exec
	removed := false
	defer func() {
		if !removed {
			if err := s.removeContainer(info.ID); err != nil {
				log.Printf("sandbox-service /v1/execute: cleanup failed for %s: %v", info.ID, err)
			}
		}
	}()

	// ── Start container ────────────────────────────────────────────────
	if err := s.docker.Start(ctx, info.ID); err != nil {
		log.Printf("sandbox-service /v1/execute: start failed: %v", err)
		writeExecutionFailure(w, err)
		return
	}

	// ── Exec code ─────────────────────────────────────────────────────
	result, err := s.docker.Exec(ctx, info.ID, execCmd)
	if err != nil {
		log.Printf("sandbox-service /v1/execute: exec failed: %v", err)
		writeExecutionFailure(w, err)
		return
	}
	if err := s.removeContainer(info.ID); err != nil {
		writeFailure(w, http.StatusBadGateway, errors.New("execution finished but container cleanup failed"))
		return
	}
	removed = true

	s.mu.Lock()
	s.totalExecs++
	s.mu.Unlock()

	writeJSON(w, http.StatusOK, map[string]any{
		"success":      result.ExitCode == 0,
		"stdout":       result.Stdout,
		"stderr":       result.Stderr,
		"exit_code":    result.ExitCode,
		"duration_ms":  result.DurationMs,
		"container_id": info.ID,
		"mode":         "docker",
	})
}

// ── Helpers ──────────────────────────────────────────────────────────

func executionTimeout(seconds int) time.Duration {
	if seconds <= 0 {
		seconds = 30
	}
	if seconds > 300 {
		seconds = 300
	}
	return time.Duration(seconds) * time.Second
}

func (s *sandboxServer) removeContainer(id string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return s.docker.Remove(ctx, id)
}

func writeFailure(w http.ResponseWriter, status int, err error) {
	writeJSON(w, status, map[string]any{"success": false, "exit_code": -1, "error": err.Error()})
}

func writeExecutionFailure(w http.ResponseWriter, err error) {
	status := http.StatusBadGateway
	if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) {
		status = http.StatusGatewayTimeout
	} else if errors.Is(err, docker.ErrUnavailable) {
		status = http.StatusServiceUnavailable
	}
	writeFailure(w, status, err)
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(v)
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func getenvFloat(key string, fallback float64) float64 {
	if v := os.Getenv(key); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return fallback
}

func getenvInt(key string, fallback int) int {
	if v := os.Getenv(key); v != "" {
		if i, err := strconv.Atoi(v); err == nil {
			return i
		}
	}
	return fallback
}

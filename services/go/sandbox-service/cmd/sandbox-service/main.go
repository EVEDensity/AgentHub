// sandbox-service — Docker sandbox execution backend (P2-1)
//
// Provides HTTP REST API for managing isolated Docker containers used as
// Agent code-execution sandboxes. Supports seccomp profiles, resource
// quotas (CPU/memory/disk), and command execution with result capture.
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
//
// When DOCKER_SOCKET is not set or unreachable, the service degrades
// gracefully to noop mode (in-memory simulation) for development.

package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
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
	docker     *docker.Client
	containers map[string]*docker.ContainerInfo // keyed by container ID
	execLogs   map[string][]execLogEntry        // keyed by container ID
	totalExecs int

	// Noop mode counters
	noopContainerSeq int
}

type execLogEntry struct {
	ID         string `json:"id"`
	ContainerID string `json:"container_id"`
	Command    string `json:"command"`
	ExitCode   int    `json:"exit_code"`
	Stdout     string `json:"stdout"`
	Stderr     string `json:"stderr"`
	DurationMs int64  `json:"duration_ms"`
	ExecutedAt string `json:"executed_at"`
}

// ── Main ─────────────────────────────────────────────────────────────

func main() {
	socketPath := os.Getenv("DOCKER_SOCKET")
	if socketPath == "" {
		socketPath = "/var/run/docker.sock" // default Linux
	}

	// Try to connect to Docker; fall back to noop if unreachable
	dc := docker.NewClient(socketPath)
	if !dc.IsNoop() {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		if err := dc.Ping(ctx); err != nil {
			log.Printf("sandbox-service: Docker daemon unreachable (%v) — switching to noop mode", err)
			dc = docker.NewClient("") // re-create in noop mode
		} else {
			log.Printf("sandbox-service: connected to Docker at %s", socketPath)
		}
	} else {
		log.Println("sandbox-service: running in noop (in-memory) mode")
	}

	srv := &sandboxServer{
		docker:     dc,
		containers: make(map[string]*docker.ContainerInfo),
		execLogs:   make(map[string][]execLogEntry),
	}

	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", srv.handleHealthz)
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
	mode := "noop"
	if !s.docker.IsNoop() {
		mode = "docker"
	}
	writeJSON(w, http.StatusOK, map[string]string{
		"status": "ok",
		"mode":   mode,
	})
}

func (s *sandboxServer) handleContainers(w http.ResponseWriter, r *http.Request) {
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

		s.noopContainerSeq++
		containerName := fmt.Sprintf("ah-sandbox-%s-%d", req.AgentID, s.noopContainerSeq)

		network := "none"
		if len(req.NetworkAllow) > 0 {
			network = "bridge"
		}

		info, err := s.docker.Create(docker.ContainerConfig{
			Name:        containerName,
			Image:       req.Image,
			AgentID:     req.AgentID,
			TenantID:    req.TenantID,
			CPU:         req.CPULimit,
			MemoryMB:    req.MemoryMB,
			DiskMB:      req.DiskMB,
			Network:     network,
			NetworkAllow: req.NetworkAllow,
		})
		if err != nil {
			log.Printf("sandbox-service: create container failed: %v", err)
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "container creation failed: " + err.Error()})
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
	case r.Method == http.MethodGet && action == "logs":
		logs, _ := s.execLogs[containerID]
		writeJSON(w, http.StatusOK, logs)

	case r.Method == http.MethodGet && action == "":
		writeJSON(w, http.StatusOK, info)

	case r.Method == http.MethodPost && action == "start":
		if err := s.docker.Start(containerID); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
			return
		}
		info.Status = "running"
		now := time.Now()
		info.StartedAt = &now
		writeJSON(w, http.StatusOK, map[string]string{"status": "started"})

	case r.Method == http.MethodPost && action == "stop":
		if err := s.docker.Stop(containerID); err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
			return
		}
		info.Status = "stopped"
		writeJSON(w, http.StatusOK, map[string]string{"status": "stopped"})

	case r.Method == http.MethodPost && action == "exec":
		var req struct {
			Command string `json:"command"`
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

		result, err := s.docker.Exec(containerID, req.Command)
		if err != nil {
			log.Printf("sandbox-service: exec in %s failed: %v", containerID, err)
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
			return
		}

		entry := execLogEntry{
			ID:          fmt.Sprintf("exec-%s-%d", containerID[:8], len(s.execLogs[containerID])),
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
		if err := s.docker.Remove(containerID); err != nil {
			log.Printf("sandbox-service: remove container %s warning: %v", containerID, err)
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
//   {"code": "print('hi')", "language": "python", "timeout": 30, "image": ""}
// → {"success": true, "stdout": "hi\n", "stderr": "", "exit_code": 0, "duration_ms": 150}
func (s *sandboxServer) handleV1Execute(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var req struct {
		Code     string `json:"code"`
		Language string `json:"language"`
		Timeout  int    `json:"timeout"`
		Image    string `json:"image"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid request body: " + err.Error()})
		return
	}
	if req.Code == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "code is required"})
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

	if req.Timeout <= 0 {
		req.Timeout = 30
	}
	if req.Timeout > 300 {
		req.Timeout = 300 // hard cap 5 min
	}

	image := req.Image
	if image == "" {
		image = getenv("SANDBOX_IMAGE", "agenthub/sandbox:latest")
	}
	cpuLimit := getenvFloat("SANDBOX_CPU_LIMIT", 1.0)
	memMB := getenvInt("SANDBOX_MEMORY_MB", 512)
	networkAllow := getenv("SANDBOX_NETWORK_ALLOW", "none")

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
	s.noopContainerSeq++
	containerName := fmt.Sprintf("ah-exec-%d-%d", time.Now().UnixNano(), s.noopContainerSeq)
	s.mu.Unlock()

	var networkAllowSlice []string
	if networkAllow != "none" && networkAllow != "" {
		networkAllowSlice = strings.Split(networkAllow, ",")
	}

	networkMode := "none"
	if len(networkAllowSlice) > 0 {
		networkMode = "bridge"
	}

	info, err := s.docker.Create(docker.ContainerConfig{
		Name:         containerName,
		Image:        image,
		AgentID:      "v1-exec",
		TenantID:     "default",
		CPU:          cpuLimit,
		MemoryMB:     memMB,
		DiskMB:       10240,
		Network:      networkMode,
		NetworkAllow: networkAllowSlice,
	})
	if err != nil {
		log.Printf("sandbox-service /v1/execute: create failed: %v", err)
		writeJSON(w, http.StatusInternalServerError, map[string]any{
			"success": false,
			"error":   "container creation failed: " + err.Error(),
		})
		return
	}

	// Cleanup: always destroy the container after exec
	defer func() {
		if err := s.docker.Remove(info.ID); err != nil {
			log.Printf("sandbox-service /v1/execute: cleanup warning for %s: %v", info.ID, err)
		}
		s.mu.Lock()
		delete(s.containers, info.ID)
		delete(s.execLogs, info.ID)
		s.mu.Unlock()
	}()

	// ── Start container ────────────────────────────────────────────────
	if err := s.docker.Start(info.ID); err != nil {
		log.Printf("sandbox-service /v1/execute: start failed: %v", err)
		writeJSON(w, http.StatusInternalServerError, map[string]any{
			"success":   false,
			"error":     "container start failed: " + err.Error(),
			"container": info.ID,
		})
		return
	}

	// ── Exec code ──────────────────────────────────────────────────────
	result, err := s.docker.Exec(info.ID, execCmd)
	if err != nil {
		log.Printf("sandbox-service /v1/execute: exec failed: %v", err)
		writeJSON(w, http.StatusInternalServerError, map[string]any{
			"success":   false,
			"error":     "exec failed: " + err.Error(),
			"container": info.ID,
		})
		return
	}

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
		"mode":         "remote",
	})
}

// ── Helpers ──────────────────────────────────────────────────────────

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

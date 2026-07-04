// Package docker — lightweight Docker HTTP API client for sandbox-service.
// Talks to /var/run/docker.sock (UNIX socket) without requiring the
// heavy docker/docker SDK dependency. Covers the subset of the Docker
// Engine API needed for AgentHub sandbox containers.
package docker

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"strings"
	"time"
)

// ── Client ────────────────────────────────────────────────────────────

type Client struct {
	http    *http.Client
	baseURL string
}

// NewClient creates a Docker API client. Use an empty socketPath to
// operate in "noop" mode (always returns success without real containers).
func NewClient(socketPath string) *Client {
	if socketPath == "" {
		log.Println("sandbox/docker: socket path empty — running in noop mode")
		return &Client{}
	}

	transport := &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			var d net.Dialer
			return d.DialContext(ctx, "unix", socketPath)
		},
		MaxIdleConns:    5,
		IdleConnTimeout: 30 * time.Second,
	}

	return &Client{
		http:    &http.Client{Transport: transport, Timeout: 30 * time.Second},
		baseURL: "http://localhost",
	}
}

// IsNoop returns true when no Docker socket is configured.
func (c *Client) IsNoop() bool { return c.http == nil }

// ── Types ────────────────────────────────────────────────────────────

type ContainerConfig struct {
	Name        string
	Image       string
	AgentID     string
	TenantID    string
	CPU         float64 // CPU shares (1.0 = 1 vCPU)
	MemoryMB    int     // memory limit in MB
	DiskMB      int     // disk quota (not enforced via Docker, informational)
	Network     string  // "none" | "bridge" | "host"
	Env         []string
	Command     []string
	NetworkAllow []string // whitelisted outbound domains (informational)
}

type ContainerInfo struct {
	ID        string    `json:"id"`
	Name      string    `json:"name"`
	Image     string    `json:"image"`
	Status    string    `json:"status"` // created | running | stopped | failed | destroyed
	AgentID   string    `json:"agent_id"`
	TenantID  string    `json:"tenant_id"`
	CPULimit  float64   `json:"cpu_limit"`
	MemoryMB  int       `json:"memory_mb"`
	DiskMB    int       `json:"disk_mb"`
	Network   string    `json:"network"`
	CreatedAt time.Time `json:"created_at"`
	StartedAt *time.Time `json:"started_at,omitempty"`
}

type ExecResult struct {
	ExitCode   int    `json:"exit_code"`
	Stdout     string `json:"stdout"`
	Stderr     string `json:"stderr"`
	DurationMs int64  `json:"duration_ms"`
}

type Stats struct {
	TotalContainers int            `json:"total_containers"`
	ActiveContainers int           `json:"active_containers"`
	TotalExecs      int            `json:"total_execs"`
	AvgDurationMs   float64        `json:"avg_duration_ms"`
	ByStatus        map[string]int `json:"by_status"`
}

// ── Container Lifecycle ──────────────────────────────────────────────

// Create starts a new container and leaves it in "created" state.
func (c *Client) Create(cfg ContainerConfig) (*ContainerInfo, error) {
	if c.IsNoop() {
		return &ContainerInfo{
			ID:       fmt.Sprintf("sandbox-%s", shortID()),
			Name:     cfg.Name,
			Image:    cfg.Image,
			Status:   "created",
			AgentID:  cfg.AgentID,
			TenantID: cfg.TenantID,
			CPULimit: cfg.CPU,
			MemoryMB: cfg.MemoryMB,
			DiskMB:   cfg.DiskMB,
		}, nil
	}

	// Docker API: POST /containers/create
	body := map[string]any{
		"Image": cfg.Image,
		"Cmd":   cfg.Command,
		"Env":   cfg.Env,
		"HostConfig": map[string]any{
			"Memory":     int64(cfg.MemoryMB) * 1024 * 1024,
			"NanoCPUs":   int64(cfg.CPU * 1e9),
			"NetworkMode": cfg.Network,
			"SecurityOpt": []string{"seccomp=default", "no-new-privileges=true"},
			"ReadonlyRootfs": true,
			"Tmpfs":          map[string]string{"/tmp": "rw,noexec,nosuid,size=100m"},
			"CapDrop":        []string{"ALL"},
		},
		"Labels": map[string]string{
			"agenthub.agent_id":  cfg.AgentID,
			"agenthub.tenant_id": cfg.TenantID,
		},
	}

	resp, err := c.do("POST", "/containers/create?name="+cfg.Name, body)
	if err != nil {
		return nil, fmt.Errorf("docker create: %w", err)
	}

	var createResp struct {
		ID       string   `json:"Id"`
		Warnings []string `json:"Warnings"`
	}
	if err := json.Unmarshal(resp, &createResp); err != nil {
		return nil, fmt.Errorf("parse docker create response: %w", err)
	}

	for _, w := range createResp.Warnings {
		log.Printf("sandbox/docker create warning: %s", w)
	}

	return &ContainerInfo{
		ID:        createResp.ID,
		Name:      cfg.Name,
		Image:     cfg.Image,
		Status:    "created",
		AgentID:   cfg.AgentID,
		TenantID:  cfg.TenantID,
		CPULimit:  cfg.CPU,
		MemoryMB:  cfg.MemoryMB,
		DiskMB:    cfg.DiskMB,
		CreatedAt: time.Now(),
	}, nil
}

// Start starts a created container.
func (c *Client) Start(containerID string) error {
	if c.IsNoop() {
		return nil
	}
	_, err := c.do("POST", "/containers/"+containerID+"/start", nil)
	return err
}

// Stop stops a running container.
func (c *Client) Stop(containerID string) error {
	if c.IsNoop() {
		return nil
	}
	_, err := c.do("POST", "/containers/"+containerID+"/stop?t=10", nil)
	return err
}

// Remove deletes a container (force if running).
func (c *Client) Remove(containerID string) error {
	if c.IsNoop() {
		return nil
	}
	_, err := c.do("DELETE", "/containers/"+containerID+"?force=true&v=true", nil)
	return err
}

// Exec runs a command inside a container and returns the result.
func (c *Client) Exec(containerID string, command string) (*ExecResult, error) {
	if c.IsNoop() {
		// Noop: return simulated result
		return &ExecResult{
			ExitCode:   0,
			Stdout:     fmt.Sprintf("[sandbox noop] %s\nHello from sandbox!", command),
			Stderr:     "",
			DurationMs: 5,
		}, nil
	}

	start := time.Now()

	// Step 1: Create exec instance
	createBody := map[string]any{
		"Cmd":          []string{"/bin/sh", "-c", command},
		"AttachStdout": true,
		"AttachStderr": true,
	}
	// Use application/vnd.docker.raw-stream for proper TTY=false multiplexing
	createResp, err := c.do("POST", "/containers/"+containerID+"/exec", createBody)
	if err != nil {
		return nil, fmt.Errorf("docker exec create: %w", err)
	}

	var execCreate struct {
		ID string `json:"Id"`
	}
	if err := json.Unmarshal(createResp, &execCreate); err != nil {
		return nil, fmt.Errorf("parse exec create: %w", err)
	}

	// Step 2: Start exec — use raw streaming endpoint with upgraded connection
	startBody := map[string]any{
		"Detach": false,
		"Tty":    false,
	}

	// Use the raw exec start endpoint that returns the multiplexed stream.
	// Docker API returns: [STREAM_TYPE:1][0x00×3][SIZE:4BE][PAYLOAD:SIZE]...
	//   STREAM_TYPE 1 = stdout, 2 = stderr
	rawResp, err := c.doRaw("POST", "/exec/"+execCreate.ID+"/start", startBody)
	if err != nil {
		return nil, fmt.Errorf("docker exec start: %w", err)
	}

	// Step 3: Demux the multiplexed Docker stream
	stdout, stderr := demuxDockerStream(rawResp)

	// Step 4: Inspect the exec instance for the exit code
	exitCode := 0
	inspectResp, err := c.do("GET", "/exec/"+execCreate.ID+"/json", nil)
	if err == nil {
		var inspect struct {
			ExitCode int `json:"ExitCode"`
		}
		if json.Unmarshal(inspectResp, &inspect) == nil {
			exitCode = inspect.ExitCode
		}
	}

	result := &ExecResult{
		ExitCode:   exitCode,
		Stdout:     stdout,
		Stderr:     stderr,
		DurationMs: time.Since(start).Milliseconds(),
	}
	return result, nil
}

// Inspect returns container info by ID.
func (c *Client) Inspect(containerID string) (string, error) {
	if c.IsNoop() {
		return "running", nil
	}
	resp, err := c.do("GET", "/containers/"+containerID+"/json", nil)
	if err != nil {
		return "", err
	}
	var inspect struct {
		State struct {
			Status string `json:"Status"`
		} `json:"State"`
	}
	if err := json.Unmarshal(resp, &inspect); err != nil {
		return "unknown", nil
	}
	return inspect.State.Status, nil
}

// List returns all containers with the agenthub label.
func (c *Client) List() ([]map[string]any, error) {
	if c.IsNoop() {
		return nil, nil
	}
	resp, err := c.do("GET", "/containers/json?all=true&filters="+`{"label":["agenthub.agent_id"]}`, nil)
	if err != nil {
		return nil, err
	}
	var containers []map[string]any
	if err := json.Unmarshal(resp, &containers); err != nil {
		return nil, fmt.Errorf("parse container list: %w", err)
	}
	return containers, nil
}

// Ping checks if the Docker daemon is reachable.
func (c *Client) Ping(ctx context.Context) error {
	if c.IsNoop() {
		return fmt.Errorf("docker not configured (noop mode)")
	}
	_, err := c.do("GET", "/_ping", nil)
	return err
}

// ── Helpers ──────────────────────────────────────────────────────────

// doRaw performs a request and returns the raw response body bytes without
// attempting JSON parsing. Used for Docker's multiplexed stream endpoints.
func (c *Client) doRaw(method, path string, body any) ([]byte, error) {
	var r io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		r = bytes.NewReader(b)
	}

	req, err := http.NewRequest(method, c.baseURL+path, r)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("docker API error %d: %s", resp.StatusCode, strings.TrimSpace(string(data)))
	}

	return data, nil
}

func (c *Client) do(method, path string, body any) ([]byte, error) {
	var r io.Reader
	if body != nil {
		b, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		r = bytes.NewReader(b)
	}

	req, err := http.NewRequest(method, c.baseURL+path, r)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("docker API error %d: %s", resp.StatusCode, strings.TrimSpace(string(data)))
	}

	return data, nil
}

func shortID() string {
	// Minimal unique ID without crypto/rand (used only in noop mode).
	return fmt.Sprintf("%x", time.Now().UnixNano())[:8]
}

// ── Docker Multiplexed Stream Decoder ──────────────────────────────────
//
// When AttachStdout+AttachStderr are set and Tty=false, Docker returns a
// multiplexed stream with the following frame format (8-byte header):
//
//	[STREAM_TYPE:1 byte][0x00:3 bytes][SIZE:4 bytes big-endian][PAYLOAD:SIZE bytes]
//
// STREAM_TYPE values:
//
//	0 = stdin  (not used in exec output)
//	1 = stdout
//	2 = stderr
//
// Reference: https://docs.docker.com/reference/api/engine/version/v1.47/#tag/Exec

func demuxDockerStream(raw []byte) (stdout, stderr string) {
	var outBuf, errBuf []byte
	offset := 0

	for offset+8 <= len(raw) {
		streamType := raw[offset]
		// Skip 3 reserved bytes (offset+1, +2, +3)
		size := int(raw[offset+4])<<24 | int(raw[offset+5])<<16 | int(raw[offset+6])<<8 | int(raw[offset+7])
		offset += 8

		if offset+size > len(raw) {
			// Truncated frame — append remaining as raw
			if streamType == 2 {
				errBuf = append(errBuf, raw[offset:]...)
			} else {
				outBuf = append(outBuf, raw[offset:]...)
			}
			break
		}

		payload := raw[offset : offset+size]
		offset += size

		switch streamType {
		case 1: // stdout
			outBuf = append(outBuf, payload...)
		case 2: // stderr
			errBuf = append(errBuf, payload...)
		}
		// streamType 0 (stdin) is ignored in exec output
	}

	return string(outBuf), string(errBuf)
}

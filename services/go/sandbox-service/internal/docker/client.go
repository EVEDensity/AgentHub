// Package docker implements the subset of the Docker Engine HTTP API needed
// by sandbox-service. Missing Docker is an execution error, never a simulator.
package docker

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

var ErrUnavailable = errors.New("Docker is not configured")
var ErrNetworkPolicyUnsupported = errors.New("only network=none is supported; network allowlists are unavailable")

type Client struct {
	http           *http.Client
	baseURL        string
	seccompProfile string
}

// NewClient uses a Unix socket. An empty path creates an unavailable client;
// every operation on that client returns an error without fabricating output.
func NewClient(socketPath, seccompProfile string) *Client {
	if socketPath == "" {
		return &Client{seccompProfile: seccompProfile}
	}
	transport := &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			var d net.Dialer
			return d.DialContext(ctx, "unix", socketPath)
		},
		MaxIdleConns: 5, IdleConnTimeout: 30 * time.Second,
	}
	// The operation context supplies the deadline, including executions lasting
	// longer than 30 seconds. A separate dial timeout is supplied by the caller.
	return &Client{http: &http.Client{Transport: transport}, baseURL: "http://localhost", seccompProfile: seccompProfile}
}

func (c *Client) IsConfigured() bool { return c != nil && c.http != nil }

func (c *Client) buildSecurityOpts() ([]string, error) {
	opts := []string{"no-new-privileges=true"}
	// Omitting seccomp uses Docker's built-in default. The Engine API expects
	// profile JSON, not the CLI's local-file path or a literal "default" string.
	if c.seccompProfile != "" {
		profile, err := os.ReadFile(c.seccompProfile)
		if err != nil {
			return nil, fmt.Errorf("read seccomp profile: %w", err)
		}
		if !json.Valid(profile) {
			return nil, errors.New("invalid seccomp profile JSON")
		}
		opts = append(opts, "seccomp="+string(profile))
	}
	return opts, nil
}

type ContainerConfig struct {
	Name         string
	Image        string
	AgentID      string
	TenantID     string
	CPU          float64
	MemoryMB     int
	DiskMB       int // Metadata only; disk quota is not enforced.
	Network      string
	Env          []string
	Command      []string
	NetworkAllow []string
}

type ContainerInfo struct {
	ID           string     `json:"id"`
	Name         string     `json:"name"`
	Image        string     `json:"image"`
	Status       string     `json:"status"`
	AgentID      string     `json:"agent_id"`
	TenantID     string     `json:"tenant_id"`
	CPULimit     float64    `json:"cpu_limit"`
	MemoryMB     int        `json:"memory_mb"`
	DiskMB       int        `json:"disk_mb"`
	Network      string     `json:"network"`
	NetworkAllow []string   `json:"network_allow,omitempty"`
	CreatedAt    time.Time  `json:"created_at"`
	StartedAt    *time.Time `json:"started_at,omitempty"`
}

type ExecResult struct {
	ExitCode   int    `json:"exit_code"`
	Stdout     string `json:"stdout"`
	Stderr     string `json:"stderr"`
	DurationMs int64  `json:"duration_ms"`
}

type Stats struct {
	TotalContainers  int            `json:"total_containers"`
	ActiveContainers int            `json:"active_containers"`
	TotalExecs       int            `json:"total_execs"`
	AvgDurationMs    float64        `json:"avg_duration_ms"`
	ByStatus         map[string]int `json:"by_status"`
}

// Create enforces the supported isolation boundary before contacting Docker.
// In-container iptables cannot safely restrict egress with CapDrop=ALL, leaves
// a startup exposure window and does not cover IPv6. Do not enable bridge
// networking until a separately enforced egress boundary has been implemented.
func (c *Client) Create(ctx context.Context, cfg ContainerConfig) (*ContainerInfo, error) {
	if !c.IsConfigured() {
		return nil, ErrUnavailable
	}
	if len(cfg.NetworkAllow) > 0 || (cfg.Network != "" && cfg.Network != "none") {
		return nil, ErrNetworkPolicyUnsupported
	}
	securityOpts, err := c.buildSecurityOpts()
	if err != nil {
		return nil, err
	}
	body := map[string]any{
		"Image": cfg.Image, "Cmd": cfg.Command, "Env": cfg.Env,
		"HostConfig": map[string]any{
			"Memory": int64(cfg.MemoryMB) * 1024 * 1024, "NanoCPUs": int64(cfg.CPU * 1e9),
			"NetworkMode": "none", "SecurityOpt": securityOpts, "ReadonlyRootfs": true,
			"Tmpfs": map[string]string{"/tmp": "rw,noexec,nosuid,size=100m"}, "CapDrop": []string{"ALL"},
		},
		"Labels": map[string]string{"agenthub.agent_id": cfg.AgentID, "agenthub.tenant_id": cfg.TenantID},
	}
	resp, err := c.do(ctx, "POST", "/containers/create?name="+url.QueryEscape(cfg.Name), body)
	if err != nil {
		return nil, fmt.Errorf("docker create: %w", err)
	}
	var created struct {
		ID string `json:"Id"`
	}
	if err := json.Unmarshal(resp, &created); err != nil {
		return nil, fmt.Errorf("parse docker create response: %w", err)
	}
	if created.ID == "" {
		return nil, errors.New("docker create response omitted container ID")
	}
	return &ContainerInfo{
		ID: created.ID, Name: cfg.Name, Image: cfg.Image, Status: "created",
		AgentID: cfg.AgentID, TenantID: cfg.TenantID, CPULimit: cfg.CPU,
		MemoryMB: cfg.MemoryMB, DiskMB: cfg.DiskMB, Network: "none", CreatedAt: time.Now(),
	}, nil
}

func (c *Client) Start(ctx context.Context, containerID string) error {
	_, err := c.do(ctx, "POST", "/containers/"+url.PathEscape(containerID)+"/start", nil)
	return err
}

func (c *Client) Stop(ctx context.Context, containerID string) error {
	_, err := c.do(ctx, "POST", "/containers/"+url.PathEscape(containerID)+"/stop?t=10", nil)
	return err
}

func (c *Client) Remove(ctx context.Context, containerID string) error {
	_, err := c.do(ctx, "DELETE", "/containers/"+url.PathEscape(containerID)+"?force=true&v=true", nil)
	return err
}

// Exec reports a result only after Docker has confirmed a finished execution
// with an exit code. Failure to inspect execution is never interpreted as zero.
func (c *Client) Exec(ctx context.Context, containerID, command string) (*ExecResult, error) {
	start := time.Now()
	created, err := c.do(ctx, "POST", "/containers/"+url.PathEscape(containerID)+"/exec", map[string]any{
		"Cmd": []string{"/bin/sh", "-c", command}, "AttachStdout": true, "AttachStderr": true,
	})
	if err != nil {
		return nil, fmt.Errorf("docker exec create: %w", err)
	}
	var instance struct {
		ID string `json:"Id"`
	}
	if err := json.Unmarshal(created, &instance); err != nil {
		return nil, fmt.Errorf("parse exec create: %w", err)
	}
	if instance.ID == "" {
		return nil, errors.New("docker exec create response omitted exec ID")
	}
	path := "/exec/" + url.PathEscape(instance.ID)
	raw, err := c.do(ctx, "POST", path+"/start", map[string]any{"Detach": false, "Tty": false})
	if err != nil {
		return nil, fmt.Errorf("docker exec start: %w", err)
	}
	inspected, err := c.do(ctx, "GET", path+"/json", nil)
	if err != nil {
		return nil, fmt.Errorf("docker exec inspect: %w", err)
	}
	var state struct {
		ExitCode *int  `json:"ExitCode"`
		Running  *bool `json:"Running"`
	}
	if err := json.Unmarshal(inspected, &state); err != nil {
		return nil, fmt.Errorf("parse exec inspect: %w", err)
	}
	if state.ExitCode == nil || *state.ExitCode < 0 || state.Running == nil || *state.Running {
		return nil, errors.New("Docker did not confirm a finished execution and exit code")
	}
	stdout, stderr := demuxDockerStream(raw)
	return &ExecResult{ExitCode: *state.ExitCode, Stdout: stdout, Stderr: stderr, DurationMs: time.Since(start).Milliseconds()}, nil
}

func (c *Client) Ping(ctx context.Context) error {
	if !c.IsConfigured() {
		return ErrUnavailable
	}
	if _, err := c.buildSecurityOpts(); err != nil {
		return err
	}
	resp, err := c.do(ctx, "GET", "/_ping", nil)
	if err != nil {
		return err
	}
	if strings.TrimSpace(string(resp)) != "OK" {
		return errors.New("Docker ping returned an unexpected response")
	}
	return nil
}

func (c *Client) do(ctx context.Context, method, path string, body any) ([]byte, error) {
	if !c.IsConfigured() {
		return nil, ErrUnavailable
	}
	var reader io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		reader = bytes.NewReader(encoded)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, reader)
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
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("docker API error %d: %s", resp.StatusCode, strings.TrimSpace(string(data)))
	}
	return data, nil
}

// Docker non-TTY output contains frames with an 8-byte header. The HTTP client
// propagates body read errors, so an interrupted stream cannot complete Exec.
func demuxDockerStream(raw []byte) (stdout, stderr string) {
	var outBuf, errBuf []byte
	offset := 0
	for offset+8 <= len(raw) {
		streamType := raw[offset]
		size := int(raw[offset+4])<<24 | int(raw[offset+5])<<16 | int(raw[offset+6])<<8 | int(raw[offset+7])
		offset += 8
		if offset+size > len(raw) {
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
		case 1:
			outBuf = append(outBuf, payload...)
		case 2:
			errBuf = append(errBuf, payload...)
		}
	}
	return string(outBuf), string(errBuf)
}

package docker

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"runtime"
	"strings"
	"testing"
	"time"
)

// These tests intentionally use the real Engine API, never a fixture daemon.
// Opting in makes an unavailable daemon or image a failure, not a skip.
func liveDockerClient(t *testing.T) (*Client, string) {
	t.Helper()
	if os.Getenv("AGENTHUB_TEST_DOCKER") != "1" {
		t.Skip("real Linux Docker gate disabled; set AGENTHUB_TEST_DOCKER=1 to enable")
	}
	if runtime.GOOS != "linux" {
		t.Fatal("enabled real Docker gate requires a Linux execution node")
	}
	socket := os.Getenv("DOCKER_SOCKET")
	if socket == "" {
		socket = "/var/run/docker.sock"
	}
	client := NewClient(socket, "")
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := client.Ping(ctx); err != nil {
		t.Fatalf("real Docker gate enabled but daemon is unavailable: %v", err)
	}
	image := os.Getenv("AGENTHUB_TEST_DOCKER_IMAGE")
	if image == "" {
		image = "agenthub/sandbox-integration:test"
	}
	return client, image
}

func liveContainer(t *testing.T, client *Client, image string) string {
	t.Helper()
	name := fmt.Sprintf("agenthub-docker-gate-%d", time.Now().UnixNano())
	// Register by name before Create, so a lost create response is cleaned too.
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := client.Remove(ctx, name); err != nil && !strings.Contains(err.Error(), "docker API error 404:") {
			t.Errorf("remove real Docker test container %s: %v", name, err)
		}
	})
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	info, err := client.Create(ctx, ContainerConfig{
		Name: name, Image: image, AgentID: "docker-integration", TenantID: "docker-integration",
		CPU: 0.25, MemoryMB: 64, Network: "none",
	})
	if err != nil {
		t.Fatalf("create real container using prebuilt image %q: %v", image, err)
	}
	if err := client.Start(ctx, info.ID); err != nil {
		t.Fatalf("start real Docker test image (its command must stay alive): %v", err)
	}
	return info.ID
}

func TestLiveDockerExecutionAndIsolation(t *testing.T) {
	client, image := liveDockerClient(t)
	id := liveContainer(t, client, image)
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	assertLiveIsolation(t, client, ctx, id)
	result, err := client.Exec(ctx, id, "printf 'real-stdout\\n'; printf 'real-stderr\\n' >&2")
	if err != nil {
		t.Fatalf("real Docker execution failed: %v", err)
	}
	if result.ExitCode != 0 || result.Stdout != "real-stdout\n" || result.Stderr != "real-stderr\n" {
		t.Fatalf("real Docker stdout/stderr/exit result differs: %+v", result)
	}
	result, err = client.Exec(ctx, id, "printf 'actual-failure\\n' >&2; exit 7")
	if err != nil || result == nil || result.ExitCode != 7 || result.Stderr != "actual-failure\n" {
		t.Fatalf("nonzero execution must preserve the real exit code: result=%+v error=%v", result, err)
	}
	result, err = client.Exec(ctx, id, "if (printf forbidden > /agenthub-read-only-probe) 2>/dev/null; then exit 9; fi; printf 'read-only\\n'")
	if err != nil || result == nil || result.ExitCode != 0 || result.Stdout != "read-only\n" {
		t.Fatalf("container root must reject an actual write: result=%+v error=%v", result, err)
	}
}

func assertLiveIsolation(t *testing.T, client *Client, ctx context.Context, id string) {
	t.Helper()
	data, err := client.do(ctx, "GET", "/containers/"+id+"/json", nil)
	if err != nil {
		t.Fatalf("inspect real Docker security configuration: %v", err)
	}
	var actual struct {
		HostConfig struct {
			NetworkMode    string
			ReadonlyRootfs bool
			Memory         int64
			NanoCPUs       int64 `json:"NanoCpus"`
			CapDrop        []string
			SecurityOpt    []string
			Tmpfs          map[string]string
		}
	}
	if err := json.Unmarshal(data, &actual); err != nil {
		t.Fatal(err)
	}
	cfg := actual.HostConfig
	if cfg.NetworkMode != "none" || !cfg.ReadonlyRootfs || cfg.Memory != 64*1024*1024 || cfg.NanoCPUs != 250_000_000 {
		t.Fatalf("real Docker did not apply isolated resource settings: %+v", cfg)
	}
	if strings.Join(cfg.CapDrop, ",") != "ALL" || !strings.Contains(strings.Join(cfg.SecurityOpt, ","), "no-new-privileges") {
		t.Fatalf("real Docker did not apply capability/privilege restrictions: %+v", cfg)
	}
	if !strings.Contains(cfg.Tmpfs["/tmp"], "noexec") || !strings.Contains(cfg.Tmpfs["/tmp"], "nosuid") {
		t.Fatalf("real Docker did not apply the bounded scratch mount: %+v", cfg.Tmpfs)
	}
}

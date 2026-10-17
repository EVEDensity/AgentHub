package main

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/agenthub/sandbox-service/internal/docker"
)

func realCancellationContainer(t *testing.T) (*docker.Client, *docker.ContainerInfo) {
	t.Helper()
	if os.Getenv("AGENTHUB_TEST_DOCKER") != "1" {
		t.Skip("real Linux Docker gate disabled; set AGENTHUB_TEST_DOCKER=1 to enable")
	}
	if runtime.GOOS != "linux" {
		t.Fatal("enabled real Docker gate requires a Linux execution node")
	}
	socket := getenv("DOCKER_SOCKET", "/var/run/docker.sock")
	client := docker.NewClient(socket, "")
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	probe, cancelProbe := context.WithTimeout(ctx, 3*time.Second)
	err := client.Ping(probe)
	cancelProbe()
	if err != nil {
		t.Fatalf("real Docker gate enabled but daemon is unavailable: %v", err)
	}
	name := fmt.Sprintf("agenthub-cancellation-gate-%d", time.Now().UnixNano())
	t.Cleanup(func() {
		cleanup, cancelCleanup := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancelCleanup()
		if err := client.Remove(cleanup, name); err != nil && !strings.Contains(err.Error(), "docker API error 404:") {
			t.Errorf("remove real cancellation test container %s: %v", name, err)
		}
	})
	image := getenv("AGENTHUB_TEST_DOCKER_IMAGE", "agenthub/sandbox-integration:test")
	info, err := client.Create(ctx, docker.ContainerConfig{
		Name: name, Image: image, AgentID: "cancellation-integration", TenantID: "docker-integration",
		CPU: 0.25, MemoryMB: 64, Network: "none",
	})
	if err != nil {
		t.Fatalf("create real container with prebuilt image %q: %v", image, err)
	}
	if err := client.Start(ctx, info.ID); err != nil {
		t.Fatalf("start real container (test image command must stay alive): %v", err)
	}
	info.Status = "running"
	return client, info
}

func TestLiveDockerCancellationRemovesActualContainer(t *testing.T) {
	client, info := realCancellationContainer(t)
	server := &sandboxServer{
		docker: client, containers: map[string]*docker.ContainerInfo{info.ID: info},
		execLogs: map[string][]execLogEntry{},
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	request := httptest.NewRequest(http.MethodPost, "/containers/"+info.ID+"/exec",
		strings.NewReader(`{"command":"printf ready > /tmp/agenthub-exec-started; exec sleep 60","timeout":30}`)).WithContext(ctx)
	response := httptest.NewRecorder()
	done := make(chan struct{})
	go func() {
		server.handleContainerByID(response, request)
		close(done)
	}()
	t.Cleanup(func() {
		cancel()
		select {
		case <-done:
		case <-time.After(15 * time.Second):
			t.Error("real execution handler did not stop after cancellation")
		}
	})
	waitForActualExecution(t, client, info.ID)
	cancel()
	select {
	case <-done:
	case <-time.After(15 * time.Second):
		t.Fatal("cancelled handler exceeded its independent cleanup deadline")
	}
	if response.Code != http.StatusGatewayTimeout || server.totalExecs != 0 || info.Status != "failed" {
		t.Fatalf("cancelled real execution was not failed: code=%d status=%s executions=%d", response.Code, info.Status, server.totalExecs)
	}
	// Probe the real Engine after the handler's automatic cleanup. A failed
	// start with exactly 404 confirms the physical container no longer exists.
	probe, cancelProbe := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancelProbe()
	if err := client.Start(probe, info.ID); err == nil || !strings.Contains(err.Error(), "docker API error 404:") {
		t.Fatalf("cancelled execution container still exists or deletion is unconfirmed: %v", err)
	}
}

func waitForActualExecution(t *testing.T, client *docker.Client, id string) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	for {
		result, err := client.Exec(ctx, id, "test -f /tmp/agenthub-exec-started")
		if err != nil {
			t.Fatalf("observe actual running command before cancellation: %v", err)
		}
		if result.ExitCode == 0 {
			return
		}
		select {
		case <-ctx.Done():
			t.Fatal(errors.New("real command did not start before the observation deadline"))
		case <-time.After(50 * time.Millisecond):
		}
	}
}

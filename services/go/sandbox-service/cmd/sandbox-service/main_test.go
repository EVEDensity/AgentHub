package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/agenthub/sandbox-service/internal/docker"
)

type fakeDocker struct {
	pingErr   error
	createErr error
	startErr  error
	removeErr error
	execErr   error
	exitCode  int
	created   []docker.ContainerConfig
	removed   int
	executed  int
	started   int
	stopped   int
	onExec    func(context.Context)
}

func (f *fakeDocker) Ping(context.Context) error { return f.pingErr }
func (f *fakeDocker) Create(ctx context.Context, cfg docker.ContainerConfig) (*docker.ContainerInfo, error) {
	f.created = append(f.created, cfg)
	if f.createErr != nil {
		return nil, f.createErr
	}
	return &docker.ContainerInfo{ID: "c", Status: "created", Network: cfg.Network}, nil
}
func (f *fakeDocker) Start(context.Context, string) error {
	f.started++
	return f.startErr
}
func (f *fakeDocker) Stop(context.Context, string) error {
	f.stopped++
	return nil
}
func (f *fakeDocker) Remove(ctx context.Context, id string) error {
	if ctx.Err() != nil {
		return ctx.Err()
	}
	f.removed++
	return f.removeErr
}
func (f *fakeDocker) Exec(ctx context.Context, id, command string) (*docker.ExecResult, error) {
	f.executed++
	if f.onExec != nil {
		f.onExec(ctx)
	}
	if f.execErr != nil {
		return nil, f.execErr
	}
	return &docker.ExecResult{ExitCode: f.exitCode, Stdout: "actual output", DurationMs: 3}, nil
}

func newTestServer(backend dockerBackend) *sandboxServer {
	return &sandboxServer{docker: backend, containers: map[string]*docker.ContainerInfo{}, execLogs: map[string][]execLogEntry{}}
}

func call(t *testing.T, handler http.HandlerFunc, method, path, body string) (*httptest.ResponseRecorder, map[string]any) {
	t.Helper()
	w := httptest.NewRecorder()
	handler(w, httptest.NewRequest(method, path, strings.NewReader(body)))
	var decoded map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &decoded); err != nil {
		t.Fatalf("invalid JSON response: %s", w.Body.String())
	}
	return w, decoded
}

func TestUnavailableDockerCannotExecuteOrClaimReadiness(t *testing.T) {
	t.Setenv("SANDBOX_NETWORK_ALLOW", "none")
	for _, backend := range []dockerBackend{docker.NewClient("", ""), &fakeDocker{pingErr: errors.New("socket unavailable")}} {
		s := newTestServer(backend)
		w, body := call(t, s.handleReadyz, "GET", "/readyz", "")
		if w.Code != http.StatusServiceUnavailable || body["ready"] != false || body["mode"] != "unavailable" {
			t.Fatalf("unavailable backend claimed ready: %s", w.Body.String())
		}
		w, body = call(t, s.handleV1Execute, "POST", "/v1/execute", `{"code":"print(1)"}`)
		if w.Code != http.StatusServiceUnavailable || body["success"] != false || body["exit_code"] != float64(-1) || s.totalExecs != 0 {
			t.Fatalf("unavailable backend fabricated execution: %s", w.Body.String())
		}
		w, body = call(t, s.handleHealthz, "GET", "/healthz", "")
		if w.Code != 200 || body["status"] != "alive" || body["ready"] != nil {
			t.Fatalf("liveness must remain distinct from readiness: %s", w.Body.String())
		}
	}
}

func TestUnsupportedAllowlistFailsBeforeContainerCreation(t *testing.T) {
	f := &fakeDocker{}
	s := newTestServer(f)
	w, body := call(t, s.handleContainers, "POST", "/containers", `{"agent_id":"a","network_allow":["example.com"]}`)
	if w.Code != http.StatusUnprocessableEntity || body["success"] != false || len(f.created) != 0 {
		t.Fatalf("managed container permitted unsupported policy: %s", w.Body.String())
	}
	w, body = call(t, s.handleV1Execute, "POST", "/v1/execute", `{"code":"print(1)","network_allow":["example.com"]}`)
	if w.Code != http.StatusUnprocessableEntity || body["success"] != false || len(f.created) != 0 || f.executed != 0 {
		t.Fatalf("one-shot execution ignored unsupported request policy: %s", w.Body.String())
	}
	t.Setenv("SANDBOX_NETWORK_ALLOW", "example.com")
	w, body = call(t, s.handleV1Execute, "POST", "/v1/execute", `{"code":"print(1)"}`)
	if w.Code != http.StatusServiceUnavailable || body["success"] != false || len(f.created) != 0 {
		t.Fatalf("one-shot execution permitted unsupported policy: %s", w.Body.String())
	}
	w, body = call(t, s.handleReadyz, "GET", "/readyz", "")
	if w.Code != http.StatusServiceUnavailable || body["ready"] != false {
		t.Fatalf("unsupported configuration claimed ready: %s", w.Body.String())
	}
}

func TestOneShotReturnsOnlyVerifiedDockerResult(t *testing.T) {
	t.Setenv("SANDBOX_NETWORK_ALLOW", "none")
	for _, exitCode := range []int{0, 7} {
		f := &fakeDocker{exitCode: exitCode}
		f.onExec = func(ctx context.Context) {
			deadline, ok := ctx.Deadline()
			remaining := time.Until(deadline)
			if !ok || remaining <= 0 || remaining > 2*time.Second {
				t.Errorf("requested execution timeout was not forwarded: %v, %v", remaining, ok)
			}
		}
		s := newTestServer(f)
		w, body := call(t, s.handleV1Execute, "POST", "/v1/execute", `{"code":"print(1)","timeout":2}`)
		if w.Code != 200 || body["success"] != (exitCode == 0) || body["exit_code"] != float64(exitCode) || body["mode"] != "docker" {
			t.Fatalf("incorrect execution result: %s", w.Body.String())
		}
		if f.removed != 1 || s.totalExecs != 1 || f.created[0].Network != "none" {
			t.Fatalf("execution must be isolated and cleaned: %+v", f)
		}
	}
}

func TestOneShotFailureAlwaysCleansUp(t *testing.T) {
	t.Setenv("SANDBOX_NETWORK_ALLOW", "none")
	for _, tc := range []struct {
		name string
		fake fakeDocker
		code int
	}{
		{"start failed", fakeDocker{startErr: errors.New("start failed")}, 502},
		{"start timeout", fakeDocker{startErr: context.DeadlineExceeded}, 504},
		{"exec inspect failed", fakeDocker{execErr: errors.New("inspect failed")}, 502},
		{"deadline", fakeDocker{execErr: context.DeadlineExceeded}, 504},
		{"cleanup failed", fakeDocker{removeErr: errors.New("remove failed")}, 502},
	} {
		t.Run(tc.name, func(t *testing.T) {
			s := newTestServer(&tc.fake)
			w, body := call(t, s.handleV1Execute, "POST", "/v1/execute", `{"code":"print(1)"}`)
			if w.Code != tc.code || body["success"] != false || tc.fake.removed == 0 || s.totalExecs != 0 {
				t.Fatalf("failed execution claimed success or missed cleanup: %s, fake=%+v", w.Body.String(), tc.fake)
			}
		})
	}
}

func TestCancelledExecutionCleanupUsesIndependentContext(t *testing.T) {
	t.Setenv("SANDBOX_NETWORK_ALLOW", "none")
	for _, managed := range []bool{false, true} {
		ctx, cancel := context.WithCancel(context.Background())
		f := &fakeDocker{execErr: context.Canceled, onExec: func(context.Context) { cancel() }}
		s := newTestServer(f)
		path, body, handler := "/v1/execute", `{"code":"print(1)"}`, s.handleV1Execute
		if managed {
			s.containers["c"] = &docker.ContainerInfo{ID: "c", Status: "running"}
			path, body, handler = "/containers/c/exec", `{"command":"sleep 10"}`, s.handleContainerByID
		}
		r := httptest.NewRequest("POST", path, strings.NewReader(body)).WithContext(ctx)
		w := httptest.NewRecorder()
		handler(w, r)
		cancel()
		if w.Code != http.StatusGatewayTimeout || f.removed != 1 || s.totalExecs != 0 {
			t.Fatalf("cancellation did not remove running container: code=%d, fake=%+v", w.Code, f)
		}
		if managed {
			w, _ = call(t, s.handleContainerByID, "POST", path, body)
			if w.Code != http.StatusConflict || f.executed != 1 {
				t.Fatal("failed container admitted another execution")
			}
		}
	}
}

func TestFailedDeletionRetainsContainerRecord(t *testing.T) {
	f := &fakeDocker{removeErr: errors.New("Docker unavailable")}
	s := newTestServer(f)
	s.containers["c"] = &docker.ContainerInfo{ID: "c", Status: "running"}
	w, body := call(t, s.handleContainerByID, "DELETE", "/containers/c", "")
	if w.Code != 502 || body["success"] != false || s.containers["c"] == nil {
		t.Fatalf("failed removal was advertised as destroyed: %s", w.Body.String())
	}
}

func TestFailedExecutionCannotBeRestartedAfterCleanupFailure(t *testing.T) {
	f := &fakeDocker{execErr: context.DeadlineExceeded, removeErr: errors.New("daemon unavailable")}
	s := newTestServer(f)
	s.containers["c"] = &docker.ContainerInfo{ID: "c", Status: "running"}
	w, _ := call(t, s.handleContainerByID, "POST", "/containers/c/exec", `{"command":"sleep 10"}`)
	if w.Code != http.StatusGatewayTimeout || s.containers["c"].Status != "failed" || f.removed != 1 {
		t.Fatalf("unconfirmed execution did not invalidate the container: code=%d backend=%+v", w.Code, f)
	}
	// Recovery of the daemon cannot revive the invalidated sandbox.
	f.execErr, f.removeErr = nil, nil
	for _, action := range []string{"stop", "start", "exec"} {
		w, _ = call(t, s.handleContainerByID, "POST", "/containers/c/"+action, `{"command":"echo retry"}`)
		if w.Code != http.StatusConflict || s.containers["c"].Status != "failed" {
			t.Fatalf("%s bypassed failed execution fence: code=%d", action, w.Code)
		}
	}
	if f.started != 0 || f.stopped != 0 || f.executed != 1 {
		t.Fatalf("invalidated container reached Docker again: %+v", f)
	}
	w, _ = call(t, s.handleContainerByID, "DELETE", "/containers/c", "")
	if w.Code != http.StatusOK || s.containers["c"] != nil || f.removed != 2 {
		t.Fatalf("invalidated container must still permit explicit cleanup: code=%d backend=%+v", w.Code, f)
	}
}

func TestExecutionTimeoutDefaultsAndCaps(t *testing.T) {
	for _, tc := range []struct{ seconds, want int }{{-1, 30}, {0, 30}, {2, 2}, {300, 300}, {301, 300}} {
		if got := executionTimeout(tc.seconds); got != time.Duration(tc.want)*time.Second {
			t.Fatalf("timeout(%d)=%v", tc.seconds, got)
		}
	}
}

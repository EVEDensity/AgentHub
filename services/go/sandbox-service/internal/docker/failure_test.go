package docker

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func testClient(t *testing.T, handler http.HandlerFunc) *Client {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	return &Client{http: server.Client(), baseURL: server.URL}
}

func TestUnconfiguredClientNeverSimulatesSuccess(t *testing.T) {
	c := NewClient("", "")
	if c.IsConfigured() {
		t.Fatal("empty socket path must be unavailable")
	}
	ctx := context.Background()
	created, createErr := c.Create(ctx, ContainerConfig{})
	result, execErr := c.Exec(ctx, "container", "echo hello")
	if created != nil || result != nil {
		t.Fatal("unavailable Docker returned a fabricated container or output")
	}
	for _, err := range []error{createErr, execErr, c.Start(ctx, "c"), c.Stop(ctx, "c"), c.Remove(ctx, "c"), c.Ping(ctx)} {
		if !errors.Is(err, ErrUnavailable) {
			t.Fatalf("operation must fail unavailable, got %v", err)
		}
	}
}

func TestCreateEnforcesIsolationBeforeContactingDocker(t *testing.T) {
	c := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		t.Error("unsupported networking must not contact Docker")
	})
	for _, cfg := range []ContainerConfig{
		{Network: "bridge"}, {Network: "host"}, {Network: "none", NetworkAllow: []string{"example.com"}},
	} {
		if info, err := c.Create(context.Background(), cfg); info != nil || !errors.Is(err, ErrNetworkPolicyUnsupported) {
			t.Fatalf("unsupported policy returned info=%v error=%v", info, err)
		}
	}
}

func TestCreateSendsRealSecuritySettings(t *testing.T) {
	profile := `{"defaultAction":"SCMP_ACT_ERRNO"}`
	profilePath := filepath.Join(t.TempDir(), "seccomp.json")
	if err := os.WriteFile(profilePath, []byte(profile), 0600); err != nil {
		t.Fatal(err)
	}
	c := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		var payload struct {
			HostConfig struct {
				NetworkMode    string
				ReadonlyRootfs bool
				SecurityOpt    []string
				CapDrop        []string
				Memory         int64
			}
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Error(err)
		}
		cfg := payload.HostConfig
		if cfg.NetworkMode != "none" || !cfg.ReadonlyRootfs || cfg.Memory != 512*1024*1024 {
			t.Errorf("unsafe container configuration: %+v", cfg)
		}
		if strings.Join(cfg.CapDrop, ",") != "ALL" || strings.Join(cfg.SecurityOpt, ",") != "no-new-privileges=true,seccomp="+profile {
			t.Errorf("security options lost or seccomp sent as a file path: %+v", cfg)
		}
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"Id":"real-container"}`))
	})
	c.seccompProfile = profilePath
	info, err := c.Create(context.Background(), ContainerConfig{Name: "safe", MemoryMB: 512})
	if err != nil || info.ID != "real-container" || info.Network != "none" {
		t.Fatalf("create: info=%v err=%v", info, err)
	}
}

func TestDefaultSeccompUsesEngineDefault(t *testing.T) {
	c := NewClient("socket", "")
	opts, err := c.buildSecurityOpts()
	if err != nil || strings.Join(opts, ",") != "no-new-privileges=true" {
		t.Fatalf("default seccomp must be omitted from Engine API options: %v, %v", opts, err)
	}
}

func TestConfiguredSeccompErrorsFailReadinessAndCreate(t *testing.T) {
	path := filepath.Join(t.TempDir(), "seccomp.json")
	c := testClient(t, func(w http.ResponseWriter, r *http.Request) {
		t.Error("invalid security configuration must fail before contacting Docker")
	})
	c.seccompProfile = path
	for _, profile := range []string{"", "invalid JSON"} {
		if profile != "" {
			if err := os.WriteFile(path, []byte(profile), 0600); err != nil {
				t.Fatal(err)
			}
		}
		if err := c.Ping(context.Background()); err == nil {
			t.Fatal("invalid configured seccomp claimed ready")
		}
		if info, err := c.Create(context.Background(), ContainerConfig{}); info != nil || err == nil {
			t.Fatal("invalid configured seccomp created container")
		}
	}
}

func TestExecRequiresConfirmedExitCode(t *testing.T) {
	for _, tc := range []struct {
		name       string
		inspect    string
		status     int
		wantExit   int
		wantResult bool
	}{
		{"inspect unavailable", "daemon unavailable", 500, 0, false},
		{"malformed", "{", 200, 0, false},
		{"missing exit", `{"Running":false}`, 200, 0, false},
		{"missing running", `{"ExitCode":0}`, 200, 0, false},
		{"still running", `{"ExitCode":0,"Running":true}`, 200, 0, false},
		{"unknown exit", `{"ExitCode":-1,"Running":false}`, 200, 0, false},
		{"success", `{"ExitCode":0,"Running":false}`, 200, 0, true},
		{"command failed", `{"ExitCode":7,"Running":false}`, 200, 7, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := testClient(t, func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/containers/c/exec":
					_, _ = w.Write([]byte(`{"Id":"e"}`))
				case "/exec/e/start":
					_, _ = w.Write([]byte{1, 0, 0, 0, 0, 0, 0, 2, 'o', 'k'})
				case "/exec/e/json":
					w.WriteHeader(tc.status)
					_, _ = w.Write([]byte(tc.inspect))
				default:
					t.Errorf("unexpected Docker API call: %s", r.URL.Path)
				}
			})
			result, err := c.Exec(context.Background(), "c", "echo ok")
			if !tc.wantResult {
				if err == nil || result != nil {
					t.Fatalf("unconfirmed execution returned success: result=%v err=%v", result, err)
				}
				return
			}
			if err != nil || result.ExitCode != tc.wantExit || result.Stdout != "ok" {
				t.Fatalf("unexpected result=%v err=%v", result, err)
			}
		})
	}
}

func TestDockerOperationsHonorContextDeadline(t *testing.T) {
	for _, operation := range []string{"ping", "exec"} {
		t.Run(operation, func(t *testing.T) {
			release := make(chan struct{})
			c := testClient(t, func(w http.ResponseWriter, r *http.Request) {
				if r.URL.Path == "/containers/c/exec" {
					_, _ = w.Write([]byte(`{"Id":"e"}`))
					return
				}
				<-release
			})
			ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
			defer cancel()
			started := time.Now()
			var err error
			if operation == "ping" {
				err = c.Ping(ctx)
			} else {
				_, err = c.Exec(ctx, "c", "sleep 10")
			}
			close(release)
			if !errors.Is(err, context.DeadlineExceeded) || time.Since(started) > time.Second {
				t.Fatalf("deadline not applied: elapsed=%v error=%v", time.Since(started), err)
			}
		})
	}
}

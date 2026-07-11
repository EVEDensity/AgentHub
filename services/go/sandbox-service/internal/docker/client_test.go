package docker

import (
	"encoding/json"
	"testing"
)

// ── Docker Multiplexed Stream Demux ────────────────────────────────────

func TestDemuxDockerStreamStdoutOnly(t *testing.T) {
	// Build a single stdout frame: header(8 bytes) + payload
	// streamType=1, size=5 → "hello"
	frame := []byte{
		1, 0, 0, 0, // stream_type + 3 reserved
		0, 0, 0, 5, // size = 5 (big-endian)
		'h', 'e', 'l', 'l', 'o',
	}

	stdout, stderr := demuxDockerStream(frame)
	if stdout != "hello" {
		t.Fatalf("expected stdout='hello', got '%s'", stdout)
	}
	if stderr != "" {
		t.Fatalf("expected empty stderr, got '%s'", stderr)
	}
}

func TestDemuxDockerStreamStderrOnly(t *testing.T) {
	frame := []byte{
		2, 0, 0, 0, // stream_type = 2 (stderr)
		0, 0, 0, 4, // size = 4
		'o', 'o', 'p', 's',
	}

	stdout, stderr := demuxDockerStream(frame)
	if stdout != "" {
		t.Fatalf("expected empty stdout, got '%s'", stdout)
	}
	if stderr != "oops" {
		t.Fatalf("expected stderr='oops', got '%s'", stderr)
	}
}

func TestDemuxDockerStreamMixed(t *testing.T) {
	// Simulate a real Docker multiplexed response:
	// Frame 1: stdout "OK\n" (3 bytes)
	// Frame 2: stderr "err\n" (4 bytes)
	data := []byte{
		// Frame 1: stdout
		1, 0, 0, 0, // stream_type=1
		0, 0, 0, 3, // size=3
		'O', 'K', '\n',
		// Frame 2: stderr
		2, 0, 0, 0, // stream_type=2
		0, 0, 0, 4, // size=4
		'e', 'r', 'r', '\n',
	}

	stdout, stderr := demuxDockerStream(data)
	if stdout != "OK\n" {
		t.Fatalf("expected stdout='OK\\n', got '%s'", stdout)
	}
	if stderr != "err\n" {
		t.Fatalf("expected stderr='err\\n', got '%s'", stderr)
	}
}

func TestDemuxDockerStreamEmpty(t *testing.T) {
	stdout, stderr := demuxDockerStream(nil)
	if stdout != "" || stderr != "" {
		t.Fatalf("empty input should give empty output, got (%q,%q)", stdout, stderr)
	}

	stdout, stderr = demuxDockerStream([]byte{})
	if stdout != "" || stderr != "" {
		t.Fatalf("empty input should give empty output, got (%q,%q)", stdout, stderr)
	}
}

func TestDemuxDockerStreamPartialHeader(t *testing.T) {
	// Incomplete header (only 5 bytes out of 8)
	data := []byte{1, 0, 0}
	stdout, stderr := demuxDockerStream(data)
	if stdout != "" || stderr != "" {
		t.Fatalf("partial header should be ignored, got (%q,%q)", stdout, stderr)
	}
}

func TestDemuxDockerStreamTruncatedPayload(t *testing.T) {
	// Header declares size=10, but only 2 bytes follow
	data := []byte{
		1, 0, 0, 0,
		0, 0, 0, 10, // size=10
		'a', 'b', // only 2 bytes available
	}
	stdout, stderr := demuxDockerStream(data)
	if stdout != "ab" {
		t.Fatalf("truncated payload should return what's available, got '%s'", stdout)
	}
	if stderr != "" {
		t.Fatalf("expected empty stderr, got '%s'", stderr)
	}
}

func TestDemuxDockerStreamMultipleFrames(t *testing.T) {
	// 3 stdout frames concatenated
	data := make([]byte, 0)
	for i := 0; i < 3; i++ {
		header := []byte{1, 0, 0, 0, 0, 0, 0, 1} // stdout, size=1
		payload := byte('A' + i)
		data = append(data, header...)
		data = append(data, payload)
	}

	stdout, stderr := demuxDockerStream(data)
	if stdout != "ABC" {
		t.Fatalf("expected stdout='ABC', got '%s'", stdout)
	}
	if stderr != "" {
		t.Fatalf("expected empty stderr, got '%s'", stderr)
	}
}

// ── Noop Client Behavior ──────────────────────────────────────────────

func TestNewClientNoop(t *testing.T) {
	c := NewClient("", "")
	if !c.IsNoop() {
		t.Fatal("empty socket path should create noop client")
	}
}

func TestNewClientWithSocket(t *testing.T) {
	c := NewClient("/var/run/docker.sock", "")
	if c.IsNoop() {
		t.Fatal("socket path should create real client")
	}
	if c.http == nil {
		t.Fatal("real client should have HTTP transport")
	}
}

func TestNoopCreate(t *testing.T) {
	c := NewClient("", "")
	info, err := c.Create(ContainerConfig{
		Name:     "test-sandbox",
		Image:    "agenthub/sandbox:latest",
		AgentID:  "agent-1",
		TenantID: "tenant-1",
		CPU:      1.0,
		MemoryMB: 512,
	})
	if err != nil {
		t.Fatalf("noop create should not error: %v", err)
	}
	if info.ID == "" {
		t.Fatal("noop create should return an ID")
	}
	if info.Status != "created" {
		t.Fatalf("expected status 'created', got '%s'", info.Status)
	}
	if info.AgentID != "agent-1" {
		t.Fatalf("expected AgentID 'agent-1', got '%s'", info.AgentID)
	}
	if info.CPULimit != 1.0 {
		t.Fatalf("expected CPULimit 1.0, got %f", info.CPULimit)
	}
	if info.MemoryMB != 512 {
		t.Fatalf("expected MemoryMB 512, got %d", info.MemoryMB)
	}
}

func TestNoopStartStopRemove(t *testing.T) {
	c := NewClient("", "")
	info, _ := c.Create(ContainerConfig{Name: "test", Image: "img", AgentID: "a1", TenantID: "t1"})
	id := info.ID

	// Start
	if err := c.Start(id); err != nil {
		t.Fatalf("noop start: %v", err)
	}
	// Stop
	if err := c.Stop(id); err != nil {
		t.Fatalf("noop stop: %v", err)
	}
	// Remove
	if err := c.Remove(id); err != nil {
		t.Fatalf("noop remove: %v", err)
	}
}

func TestNoopExec(t *testing.T) {
	c := NewClient("", "")
	result, err := c.Exec("sandbox-1", "echo hello")
	if err != nil {
		t.Fatalf("noop exec: %v", err)
	}
	if result.ExitCode != 0 {
		t.Fatalf("noop exec should exit 0, got %d", result.ExitCode)
	}
	if result.Stdout == "" {
		t.Fatal("noop exec should return some stdout")
	}
	if result.DurationMs < 0 {
		t.Fatal("duration must be non-negative")
	}
}

func TestNoopInspect(t *testing.T) {
	c := NewClient("", "")
	status, err := c.Inspect("sandbox-1")
	if err != nil {
		t.Fatalf("noop inspect: %v", err)
	}
	if status != "running" {
		t.Fatalf("noop inspect should return 'running', got '%s'", status)
	}
}

func TestNoopList(t *testing.T) {
	c := NewClient("", "")
	containers, err := c.List()
	if err != nil {
		t.Fatalf("noop list: %v", err)
	}
	if containers != nil {
		t.Fatalf("noop list should return nil, got %v", containers)
	}
}

func TestNoopPing(t *testing.T) {
	c := NewClient("", "")
	err := c.Ping(nil)
	if err == nil {
		t.Fatal("noop ping should return an error")
	}
}

// ── Type Round-Trips ──────────────────────────────────────────────────

func TestContainerInfoRoundTrip(t *testing.T) {
	// Verify JSON round-trip for ContainerInfo (used in API responses).
	info := &ContainerInfo{
		ID:        "abc123",
		Name:      "test-container",
		Image:     "test-image",
		Status:    "running",
		AgentID:   "agent-1",
		TenantID:  "t1",
		CPULimit:  1.5,
		MemoryMB:  1024,
		DiskMB:    5120,
		Network:   "none",
	}

	// Marshal/unmarshal
	b, err := json.Marshal(info)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}

	var parsed ContainerInfo
	if err := json.Unmarshal(b, &parsed); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}

	if parsed.ID != info.ID || parsed.Name != info.Name || parsed.Status != info.Status {
		t.Fatalf("field mismatch after round-trip: %+v", parsed)
	}
	if parsed.CPULimit != info.CPULimit || parsed.MemoryMB != info.MemoryMB {
		t.Fatalf("numeric field mismatch: %+v", parsed)
	}
}

func TestExecResultRoundTrip(t *testing.T) {
	result := &ExecResult{
		ExitCode:   1,
		Stdout:     "hello\n",
		Stderr:     "error\n",
		DurationMs: 150,
	}

	b, err := json.Marshal(result)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}

	var parsed ExecResult
	if err := json.Unmarshal(b, &parsed); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}

	if parsed.ExitCode != 1 || parsed.Stdout != "hello\n" || parsed.DurationMs != 150 {
		t.Fatalf("field mismatch after round-trip: %+v", parsed)
	}
}

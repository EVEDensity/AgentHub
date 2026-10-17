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

// ── Type Round-Trips ──────────────────────────────────────────────────

func TestContainerInfoRoundTrip(t *testing.T) {
	// Verify JSON round-trip for ContainerInfo (used in API responses).
	info := &ContainerInfo{
		ID:       "abc123",
		Name:     "test-container",
		Image:    "test-image",
		Status:   "running",
		AgentID:  "agent-1",
		TenantID: "t1",
		CPULimit: 1.5,
		MemoryMB: 1024,
		DiskMB:   5120,
		Network:  "none",
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

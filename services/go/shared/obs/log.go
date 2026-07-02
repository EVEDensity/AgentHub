// Sprint J4: Structured JSON logging for Loki ingestion.
// Produces log lines in the format Promtail expects for automatic label extraction.
//
// Usage:
//
//	obs.Log.Info("gateway-service", "request processed", obs.F{"trace_id": traceID, "latency_ms": 42})
//	obs.Log.Error("gateway-service", "connection refused", obs.F{"error": err.Error()})
//	obs.Log.Warn("gateway-service", "rate limit approaching", obs.F{"user_id": uid})

package obs

import (
	"encoding/json"
	"log"
	"os"
	"sync"
	"time"
)

// Level represents a log severity level.
type Level string

const (
	LevelDebug Level = "debug"
	LevelInfo  Level = "info"
	LevelWarn  Level = "warn"
	LevelError Level = "error"
)

// F is a convenience alias for map of structured fields.
type F map[string]interface{}

// logEntry is the JSON structure written to stdout for Promtail scraping.
type logEntry struct {
	Timestamp string                 `json:"timestamp"`
	Level     string                 `json:"level"`
	Service   string                 `json:"service"`
	Message   string                 `json:"message"`
	TraceID   string                 `json:"trace_id,omitempty"`
	Fields    map[string]interface{} `json:"fields,omitempty"`
}

// StructuredLogger writes JSON log lines to stdout.
type StructuredLogger struct {
	mu     sync.Mutex
	output *log.Logger
}

// NewStructuredLogger creates a logger that writes to the given output.
// If output is nil, it defaults to os.Stdout.
func NewStructuredLogger(output *log.Logger) *StructuredLogger {
	if output == nil {
		output = log.New(os.Stdout, "", 0) // no prefix — we emit pure JSON
	}
	return &StructuredLogger{output: output}
}

// Log is the global structured logger. Services that import obs can use it
// directly. It defaults to writing to stdout (Promtail will scrape it).
var Log = NewStructuredLogger(nil)

func (l *StructuredLogger) emit(level Level, service, msg string, fields F) {
	l.mu.Lock()
	defer l.mu.Unlock()

	traceID := ""
	if fields != nil {
		if tid, ok := fields["trace_id"]; ok {
			if s, ok2 := tid.(string); ok2 {
				traceID = s
			}
		}
	}

	entry := logEntry{
		Timestamp: time.Now().UTC().Format(time.RFC3339Nano),
		Level:     string(level),
		Service:   service,
		Message:   msg,
		TraceID:   traceID,
		Fields:    fields,
	}

	b, err := json.Marshal(entry)
	if err != nil {
		// Fallback to unstructured — best-effort, never drop the log.
		l.output.Printf(`{"level":"error","service":"obs","message":"json marshal failed","fields":{"error":"%v"}}`, err)
		return
	}
	l.output.Println(string(b))
}

// Debug logs a debug-level message (typically suppressed in production).
func (l *StructuredLogger) Debug(service, msg string, fields F) { l.emit(LevelDebug, service, msg, fields) }

// Info logs an informational message.
func (l *StructuredLogger) Info(service, msg string, fields F) { l.emit(LevelInfo, service, msg, fields) }

// Warn logs a warning message.
func (l *StructuredLogger) Warn(service, msg string, fields F) { l.emit(LevelWarn, service, msg, fields) }

// Error logs an error message.
func (l *StructuredLogger) Error(service, msg string, fields F) { l.emit(LevelError, service, msg, fields) }

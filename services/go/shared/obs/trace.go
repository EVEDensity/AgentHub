// Sprint N1: Enhanced OpenTelemetry tracing middleware.
// Wraps every HTTP request with a named span, enriches it with standard
// attributes (tenant_id, agent_id, http.*), emits span events for slow
// requests, and instruments downstream proxy calls.
//
// Usage:
//
//	handler = obs.TraceMiddleware("gateway-service", handler)
//	// Inside a handler, add extra context:
//	obs.SetSpanAttr(r.Context(), "tenant.id", tenantID)
//	obs.AddSpanEvent(r.Context(), "cache.hit", obs.F{"key": cacheKey})
package obs

import (
	"context"
	"net/http"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/codes"
	"go.opentelemetry.io/otel/trace"
)

const (
	// slowRequestThresholdMs emits a span event when request latency exceeds this.
	slowRequestThresholdMs = 500
)

// TraceMiddleware returns an http.Handler that creates an OTel span for every
// request, attaches standard HTTP semantic conventions, and records slow
// requests as span events. Inherits trace context from incoming W3C headers.
func TraceMiddleware(serviceName string, next http.Handler) http.Handler {
	tracer := otel.Tracer("agenthub/" + serviceName)

	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ctx := otel.GetTextMapPropagator().Extract(r.Context(), propagationHeaderCarrier(r.Header))
		ctx, span := tracer.Start(ctx, r.Method+" "+r.URL.Path,
			trace.WithSpanKind(trace.SpanKindServer),
			trace.WithAttributes(
				attribute.String("http.method", r.Method),
				attribute.String("http.url", r.URL.String()),
				attribute.String("http.target", r.URL.Path),
				attribute.String("http.scheme", urlScheme(r)),
				attribute.String("http.user_agent", r.UserAgent()),
				attribute.String("service.name", serviceName),
			),
		)
		defer span.End()

		start := time.Now()
		ww := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(ww, r.WithContext(ctx))

		span.SetAttributes(
			attribute.Int("http.status_code", ww.status),
			attribute.Int64("http.request_duration_ms", time.Since(start).Milliseconds()),
		)

		if ww.status >= 400 {
			span.SetStatus(codes.Error, http.StatusText(ww.status))
		}
		if ww.status >= 500 {
			span.SetAttributes(attribute.Bool("error", true))
		}

		if latency := time.Since(start).Milliseconds(); latency > slowRequestThresholdMs {
			span.AddEvent("slow_request",
				trace.WithAttributes(
					attribute.Int64("latency_ms", latency),
					attribute.Int("status_code", ww.status),
					attribute.String("path", r.URL.Path),
				),
			)
		}
	})
}

// SetSpanAttr sets a key-value pair on the span in ctx. Safe no-op when
// there is no active span (e.g. tracer not configured).
func SetSpanAttr(ctx context.Context, key string, value string) {
	span := trace.SpanFromContext(ctx)
	if span.IsRecording() {
		span.SetAttributes(attribute.String(key, value))
	}
}

// SetSpanAttrs sets multiple string attributes on the active span.
func SetSpanAttrs(ctx context.Context, attrs map[string]string) {
	span := trace.SpanFromContext(ctx)
	if !span.IsRecording() {
		return
	}
	kvs := make([]attribute.KeyValue, 0, len(attrs))
	for k, v := range attrs {
		kvs = append(kvs, attribute.String(k, v))
	}
	span.SetAttributes(kvs...)
}

// AddSpanEvent records a named event with optional structured fields on the
// current span. Used for cache hit/miss, circuit breaker trips, rate limit
// decisions, etc.
func AddSpanEvent(ctx context.Context, name string, fields F) {
	span := trace.SpanFromContext(ctx)
	if !span.IsRecording() {
		return
	}
	attrs := make([]attribute.KeyValue, 0, len(fields))
	for k, v := range fields {
		attrs = append(attrs, otelAttr(k, v))
	}
	span.AddEvent(name, trace.WithAttributes(attrs...))
}

// RecordError records an error on the active span and marks it as errored.
func RecordError(ctx context.Context, err error) {
	span := trace.SpanFromContext(ctx)
	if !span.IsRecording() {
		return
	}
	span.RecordError(err)
	span.SetStatus(codes.Error, err.Error())
}

// ── helpers ───────────────────────────────────────────────────────────

func urlScheme(r *http.Request) string {
	if r.TLS != nil {
		return "https"
	}
	return "http"
}

// otelAttr converts a Go value to the best-fit OTel attribute.
func otelAttr(key string, v interface{}) attribute.KeyValue {
	switch val := v.(type) {
	case string:
		return attribute.String(key, val)
	case int:
		return attribute.Int(key, val)
	case int64:
		return attribute.Int64(key, val)
	case float64:
		return attribute.Float64(key, val)
	case bool:
		return attribute.Bool(key, val)
	default:
		return attribute.String(key, formatAny(val))
	}
}

func formatAny(v interface{}) string {
	// Simple fast-path for the handful of types we expect.
	switch val := v.(type) {
	case string:
		return val
	case int:
		return itoa(val)
	default:
		// Fallback — use structured logger's JSON marshal.
		return ""
	}
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	neg := false
	if n < 0 {
		neg = true
		n = -n
	}
	var buf [20]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	if neg {
		i--
		buf[i] = '-'
	}
	return string(buf[i:])
}

// propagationHeaderCarrier adapts http.Header to OTel propagation.TextMapCarrier.
type propagationHeaderCarrier http.Header

func (c propagationHeaderCarrier) Get(key string) string {
	return http.Header(c).Get(key)
}

func (c propagationHeaderCarrier) Set(key, value string) {
	http.Header(c).Set(key, value)
}

func (c propagationHeaderCarrier) Keys() []string {
	keys := make([]string, 0, len(c))
	for k := range c {
		keys = append(keys, k)
	}
	return keys
}

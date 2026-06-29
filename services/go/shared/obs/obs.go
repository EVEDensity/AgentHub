// Package obs provides the platform-wide observability bootstrap: a Prometheus
// metrics registry exposed at /metrics, an HTTP middleware that records request
// count and latency, and an OpenTelemetry tracer initializer that exports
// spans over OTLP when OTEL_EXPORTER_OTLP_ENDPOINT is set (and degrades to a
// no-op tracer otherwise, so services run fine without a collector).
package obs

import (
	"context"
	"fmt"
	"log"
	"net/http"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracehttp"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.24.0"
)

// Registry is the process-wide Prometheus registry shared by all services.
var Registry = prometheus.NewRegistry()

func init() {
	// Default collectors: process + Go runtime. Service-specific collectors are
	// registered via MustRegister.
	Registry.MustRegister(collectors.NewGoCollector())
	Registry.MustRegister(collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
}

var (
	httpRequests = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "http_requests_total", Help: "Total HTTP requests by service, path, method, status."},
		[]string{"service", "path", "method", "status"},
	)
	httpLatency = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{Name: "http_request_duration_seconds", Help: "HTTP request latency in seconds.", Buckets: prometheus.DefBuckets},
		[]string{"service", "path", "method"},
	)
	eventsPublished = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "eventbus_published_total", Help: "Events published by service and event_type."},
		[]string{"service", "event_type"},
	)
	eventsReceived = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "eventbus_received_total", Help: "Events received by service and event_type."},
		[]string{"service", "event_type"},
	)
)

func init() {
	Registry.MustRegister(httpRequests, httpLatency, eventsPublished, eventsReceived)
}

// MustRegister registers additional collectors (e.g. service-specific gauges).
func MustRegister(cs ...prometheus.Collector) {
	Registry.MustRegister(cs...)
}

// MetricsHandler returns the HTTP handler that exposes the Prometheus registry.
func MetricsHandler() http.Handler {
	return promhttp.HandlerFor(Registry, promhttp.HandlerOpts{Registry: Registry})
}

// Middleware wraps an http.Handler with request count and latency metrics for
// the given service name. Path is normalized to the registered route pattern
// when the handler sets it via the context-aware PathPattern helper; otherwise
// the raw URL path is used.
func Middleware(service string, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		ww := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(ww, r)
		path := r.URL.Path
		httpRequests.WithLabelValues(service, path, r.Method, http.StatusText(ww.status)).Inc()
		httpLatency.WithLabelValues(service, path, r.Method).Observe(time.Since(start).Seconds())
	})
}

// IncEventPublished increments the published-events counter.
func IncEventPublished(service, eventType string) {
	eventsPublished.WithLabelValues(service, eventType).Inc()
}

// IncEventReceived increments the received-events counter.
func IncEventReceived(service, eventType string) {
	eventsReceived.WithLabelValues(service, eventType).Inc()
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (s *statusRecorder) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

// InitTracer configures the global OpenTelemetry tracer provider. If endpoint
// is non-empty it exports spans via OTLP/HTTP; otherwise it installs a no-op
// tracer so the process runs without a collector. The returned shutdown func
// must be called on exit to flush pending spans.
func InitTracer(ctx context.Context, endpoint, serviceName string) (func(context.Context) error, error) {
	if endpoint == "" {
		// No collector configured: use a no-op tracer provider. Services still
		// run; trace_id correlation relies on envelope fields and logs.
		otel.SetTracerProvider(sdktrace.NewTracerProvider())
		otel.SetTextMapPropagator(propagation.TraceContext{})
		return func(context.Context) error { return nil }, nil
	}
	exp, err := otlptracehttp.New(ctx, otlptracehttp.WithEndpoint(endpoint), otlptracehttp.WithInsecure())
	if err != nil {
		return nil, fmt.Errorf("create otlp trace exporter: %w", err)
	}
	res, err := resource.New(ctx, resource.WithAttributes(semconv.ServiceName(serviceName)))
	if err != nil {
		return nil, fmt.Errorf("create trace resource: %w", err)
	}
	tp := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exp),
		sdktrace.WithResource(res),
		sdktrace.WithSampler(sdktrace.TraceIDRatioBased(0.1)),
	)
	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.TraceContext{})
	log.Printf("obs: otel tracer exporting to %s as %s (10%% sampling)", endpoint, serviceName)
	return tp.Shutdown, nil
}

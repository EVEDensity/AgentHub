package transport

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func validStatelessRequest(t *testing.T, body string) *http.Request {
	t.Helper()
	req := httptest.NewRequest(http.MethodPost, "/mcp/rpc", strings.NewReader(body))
	req.Header.Set("X-AgentHub-Mission-Id", "mis-1")
	req.Header.Set("X-AgentHub-Work-Unit-Id", "wu-1")
	req.Header.Set("X-AgentHub-Attempt", "2")
	req.Header.Set("X-AgentHub-Capability", "repository.read")
	req.Header.Set("X-AgentHub-Capability-Scope", `{"paths":["app/main.py"]}`)
	req.Header.Set("X-AgentHub-Trace-Id", "trace-1")
	return req
}

func TestStatelessHTTPTransportPropagatesRequestContext(t *testing.T) {
	var observed MCPRequestContext
	var observedBody []byte
	handler := func(ctx context.Context, raw json.RawMessage) ([]json.RawMessage, error) {
		var ok bool
		observed, ok = RequestContextFromContext(ctx)
		if !ok {
			return nil, errors.New("request context missing")
		}
		observedBody = append([]byte(nil), raw...)
		return []json.RawMessage{json.RawMessage(`{"jsonrpc":"2.0","id":7,"result":{"content":[]}}`)}, nil
	}

	recorder := httptest.NewRecorder()
	NewStatelessHTTPTransport(handler, 1024).ServeHTTP(
		recorder,
		validStatelessRequest(t, `{"jsonrpc":"2.0","id":7,"method":"tools/call"}`),
	)

	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusOK)
	}
	if recorder.Header().Get("Content-Type") != "application/json" {
		t.Fatalf("content type = %q, want application/json", recorder.Header().Get("Content-Type"))
	}
	if observed.MissionID != "mis-1" || observed.WorkUnitID != "wu-1" || observed.Attempt != 2 {
		t.Fatalf("unexpected execution context: %+v", observed)
	}
	if observed.Capability != "repository.read" || observed.Scope["paths"].([]any)[0] != "app/main.py" {
		t.Fatalf("unexpected capability context: %+v", observed)
	}
	if observed.TraceID != "trace-1" {
		t.Fatalf("trace id = %q, want trace-1", observed.TraceID)
	}
	if string(observedBody) != `{"jsonrpc":"2.0","id":7,"method":"tools/call"}` {
		t.Fatalf("body = %s", observedBody)
	}
}

func TestStatelessHTTPTransportRejectsMissingOrInvalidContext(t *testing.T) {
	tests := []struct {
		name       string
		mutate     func(*http.Request)
		wantStatus int
		wantText   string
	}{
		{
			name:       "missing mission",
			mutate:     func(req *http.Request) { req.Header.Del("X-AgentHub-Mission-Id") },
			wantStatus: http.StatusBadRequest,
			wantText:   "X-AgentHub-Mission-Id header is required",
		},
		{
			name: "invalid attempt",
			mutate: func(req *http.Request) {
				req.Header.Set("X-AgentHub-Attempt", "zero")
			},
			wantStatus: http.StatusBadRequest,
			wantText:   "X-AgentHub-Attempt must be a positive integer",
		},
		{
			name: "invalid scope",
			mutate: func(req *http.Request) {
				req.Header.Set("X-AgentHub-Capability-Scope", "[]")
			},
			wantStatus: http.StatusBadRequest,
			wantText:   "X-AgentHub-Capability-Scope must be a JSON object",
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			req := validStatelessRequest(t, `{}`)
			test.mutate(req)
			recorder := httptest.NewRecorder()
			NewStatelessHTTPHandler(func(context.Context, json.RawMessage) ([]json.RawMessage, error) {
				t.Fatal("handler must not run")
				return nil, nil
			}).ServeHTTP(recorder, req)
			if recorder.Code != test.wantStatus {
				t.Fatalf("status = %d, want %d", recorder.Code, test.wantStatus)
			}
			if !strings.Contains(recorder.Body.String(), test.wantText) {
				t.Fatalf("body = %q, want %q", recorder.Body.String(), test.wantText)
			}
		})
	}
}

func TestStatelessHTTPTransportBoundsBodyAndMethod(t *testing.T) {
	transport := NewStatelessHTTPTransport(func(context.Context, json.RawMessage) ([]json.RawMessage, error) {
		return []json.RawMessage{json.RawMessage(`{"jsonrpc":"2.0","id":1,"result":{}}`)}, nil
	}, 4)

	tooLarge := validStatelessRequest(t, `{"jsonrpc":"2.0"}`)
	recorder := httptest.NewRecorder()
	transport.ServeHTTP(recorder, tooLarge)
	if recorder.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("large body status = %d, want %d", recorder.Code, http.StatusRequestEntityTooLarge)
	}

	get := validStatelessRequest(t, `{}`)
	get.Method = http.MethodGet
	recorder = httptest.NewRecorder()
	transport.ServeHTTP(recorder, get)
	if recorder.Code != http.StatusMethodNotAllowed {
		t.Fatalf("method status = %d, want %d", recorder.Code, http.StatusMethodNotAllowed)
	}
	if recorder.Header().Get("Allow") != http.MethodPost {
		t.Fatalf("allow = %q, want POST", recorder.Header().Get("Allow"))
	}
}

func TestStatelessHTTPTransportReturnsNoContentForNotifications(t *testing.T) {
	transport := NewStatelessHTTPHandler(func(context.Context, json.RawMessage) ([]json.RawMessage, error) {
		return nil, nil
	})
	recorder := httptest.NewRecorder()
	transport.ServeHTTP(recorder, validStatelessRequest(t, `{"jsonrpc":"2.0","method":"initialized"}`))
	if recorder.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusNoContent)
	}
	if body, _ := io.ReadAll(recorder.Body); len(body) != 0 {
		t.Fatalf("notification body = %q, want empty", body)
	}
}

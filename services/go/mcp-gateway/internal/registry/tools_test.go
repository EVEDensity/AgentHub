package registry

import (
	"context"
	"errors"
	"testing"

	"github.com/agenthub/mcp-gateway/internal/protocol"
	"github.com/agenthub/mcp-gateway/internal/transport"
)

func testRegistry(t *testing.T, called *bool) *Registry {
	t.Helper()
	r := &Registry{tools: make(map[string]RegisteredTool)}
	r.registerTool(RegisteredTool{
		Definition: protocol.ToolDefinition{
			Name:        "read_file",
			InputSchema: protocol.ToolInputSchema{Type: "object"},
		},
		Capability: "repository.read",
		Handler: func(context.Context, map[string]any) (*protocol.ToolCallResult, error) {
			*called = true
			return &protocol.ToolCallResult{}, nil
		},
	})
	return r
}

func executionContext(capability string) context.Context {
	return transport.WithRequestContext(context.Background(), transport.MCPRequestContext{
		MissionID:  "mission-1",
		WorkUnitID: "work-unit-1",
		Attempt:    1,
		Capability: capability,
		Scope:      map[string]any{},
	})
}

func TestRegistryAllowsToolBoundToDeclaredCapability(t *testing.T) {
	called := false
	r := testRegistry(t, &called)
	if _, err := r.CallTool(executionContext("repository.read"), "read_file", nil); err != nil {
		t.Fatalf("call bound tool: %v", err)
	}
	if !called {
		t.Fatal("bound tool handler was not called")
	}
}

func TestRegistryRejectsCapabilityMismatchBeforeHandler(t *testing.T) {
	called := false
	r := testRegistry(t, &called)
	_, err := r.CallTool(executionContext("repository.write"), "read_file", nil)
	var mismatch *CapabilityMismatchError
	if !errors.As(err, &mismatch) {
		t.Fatalf("error = %v, want CapabilityMismatchError", err)
	}
	if mismatch.Tool != "read_file" || mismatch.DeclaredCapability != "repository.write" {
		t.Fatalf("unexpected mismatch metadata: %+v", mismatch)
	}
	if called {
		t.Fatal("mismatched tool handler must not run")
	}
}

func TestRegistryKeepsLegacyTransportCompatibilityWithoutExecutionContext(t *testing.T) {
	called := false
	r := testRegistry(t, &called)
	if _, err := r.CallTool(context.Background(), "read_file", nil); err != nil {
		t.Fatalf("legacy call: %v", err)
	}
	if !called {
		t.Fatal("legacy tool handler was not called")
	}
}

func TestBuiltinToolsDeclareStableCapabilities(t *testing.T) {
	r := New("http://knowledge.test", "http://gateway.test")
	want := map[string]string{
		"knowledge_search": "knowledge.search",
		"list_agents":      "agent.read",
		"call_agent":       "agent.dispatch",
		"list_sessions":    "session.read",
		"create_workflow":  "workflow.create",
		"ingest_document":  "document.ingest",
		"system_health":    "platform.health",
	}
	if len(r.tools) != len(want) {
		t.Fatalf("registered tools = %d, want %d", len(r.tools), len(want))
	}
	for name, capability := range want {
		tool, ok := r.tools[name]
		if !ok {
			t.Fatalf("tool %q is not registered", name)
		}
		if tool.Capability != capability {
			t.Fatalf("tool %q capability = %q, want %q", name, tool.Capability, capability)
		}
	}
}

func TestRegistryRejectsIncompleteOrDuplicateBindings(t *testing.T) {
	valid := RegisteredTool{
		Definition: protocol.ToolDefinition{Name: "read_file"},
		Capability: "repository.read",
		Handler: func(context.Context, map[string]any) (*protocol.ToolCallResult, error) {
			return &protocol.ToolCallResult{}, nil
		},
	}
	tests := []struct {
		name  string
		setup func(*Registry)
		tool  RegisteredTool
	}{
		{name: "missing capability", tool: RegisteredTool{Definition: valid.Definition, Handler: valid.Handler}},
		{name: "missing handler", tool: RegisteredTool{Definition: valid.Definition, Capability: valid.Capability}},
		{name: "duplicate", setup: func(r *Registry) { r.registerTool(valid) }, tool: valid},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			r := &Registry{tools: make(map[string]RegisteredTool)}
			if test.setup != nil {
				test.setup(r)
			}
			defer func() {
				if recover() == nil {
					t.Fatal("registration must fail closed")
				}
			}()
			r.registerTool(test.tool)
		})
	}
}

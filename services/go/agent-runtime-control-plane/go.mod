module github.com/agenthub/platform/agent-runtime-control-plane

go 1.22.0

require (
	github.com/agenthub/platform/shared/eventbus v0.0.0
	github.com/agenthub/platform/shared/events v0.0.0
)

replace github.com/agenthub/platform/shared/eventbus => ../shared/eventbus
replace github.com/agenthub/platform/shared/events => ../shared/events

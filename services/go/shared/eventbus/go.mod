module github.com/agenthub/platform/shared/eventbus

go 1.22.0

require (
	github.com/agenthub/platform/shared/events v0.0.0
	github.com/nats-io/nats.go v1.37.0
)

replace github.com/agenthub/platform/shared/events => ../events

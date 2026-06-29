module github.com/agenthub/platform/shared/eventbus

go 1.22.0

require (
	github.com/agenthub/platform/shared/events v0.0.0
	github.com/nats-io/nats.go v1.37.0
)

require (
	github.com/klauspost/compress v1.17.2 // indirect
	github.com/nats-io/nkeys v0.4.7 // indirect
	github.com/nats-io/nuid v1.0.1 // indirect
	golang.org/x/crypto v0.18.0 // indirect
	golang.org/x/sys v0.16.0 // indirect
)

replace github.com/agenthub/platform/shared/events => ../events

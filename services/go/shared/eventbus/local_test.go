package eventbus

import (
	"context"
	"testing"

	"github.com/agenthub/platform/shared/events"
)

func TestLocalBusPublishesExactAndWildcardSubjects(t *testing.T) {
	bus := ConnectLocal()
	got := make(chan string, 2)
	_, _ = bus.Subscribe("exact", "agenthub.session.events", func(env events.Envelope) { got <- env.EventID })
	_, _ = bus.Subscribe("wildcard", "agenthub.session.>", func(env events.Envelope) { got <- env.EventID })
	if err := bus.PublishEnvelope(context.Background(), "agenthub.session.events", events.Envelope{EventID: "evt-1"}); err != nil { t.Fatal(err) }
	if <-got != "evt-1" || <-got != "evt-1" { t.Fatal("local subscribers did not receive event") }
}

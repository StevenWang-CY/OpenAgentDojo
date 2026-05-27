package queue_test

import (
	"context"
	"testing"
	"time"

	"github.com/orders/orders-service/internal/model"
	"github.com/orders/orders-service/internal/queue"
	"github.com/orders/orders-service/internal/store"
)

func newPool(t *testing.T, workers int) (*queue.Pool, *store.Store) {
	t.Helper()
	s, err := store.Open(context.Background(), ":memory:")
	if err != nil {
		t.Fatalf("Open store: %v", err)
	}
	t.Cleanup(func() { _ = s.Close() })
	p := queue.New(s, workers)
	return p, s
}

func TestPoolProcessesEvents(t *testing.T) {
	ctx := context.Background()
	p, s := newPool(t, 2)

	if err := s.Insert(ctx, model.Order{ID: "p-1", SKU: "x", Quantity: 1}); err != nil {
		t.Fatalf("Insert: %v", err)
	}
	p.Start(ctx)
	t.Cleanup(p.Stop)

	if err := p.Submit(queue.Event{OrderID: "p-1"}); err != nil {
		t.Fatalf("Submit: %v", err)
	}

	// Poll for the side effect. Bounded; flakes would be a real bug.
	deadline := time.Now().Add(2 * time.Second)
	for {
		got, err := s.Get(ctx, "p-1")
		if err == nil && got.Status == model.StatusShipped {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("event was not processed before deadline; last err=%v", err)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func TestPoolStopIsIdempotent(t *testing.T) {
	p, _ := newPool(t, 2)
	p.Start(context.Background())
	p.Stop()
	p.Stop() // must not panic, must not deadlock
}

func TestSubmitAfterStopFails(t *testing.T) {
	p, _ := newPool(t, 1)
	p.Start(context.Background())
	p.Stop()
	err := p.Submit(queue.Event{OrderID: "irrelevant"})
	if err == nil {
		t.Fatalf("Submit after Stop: expected error, got nil")
	}
}

// NOTE: the canonical goroutine-leak detector + parent-cancellation
// reclamation tests live in the ``goroutine-leak`` mission's hidden
// suite. Keeping them out of the visible suite means a clean ``make
// test`` still passes on the bug-shipping initial commit.

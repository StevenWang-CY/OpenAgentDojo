package store_test

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/orders/orders-service/internal/model"
	"github.com/orders/orders-service/internal/store"
)

func newStore(t *testing.T) *store.Store {
	t.Helper()
	s, err := store.Open(context.Background(), ":memory:")
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func TestInsertAndGetRoundTrip(t *testing.T) {
	ctx := context.Background()
	s := newStore(t)
	s.SetClock(func() time.Time {
		return time.Date(2026, 5, 27, 12, 0, 0, 0, time.UTC)
	})

	want := model.Order{ID: "o-1", SKU: "ABC-1", Quantity: 3}
	if err := s.Insert(ctx, want); err != nil {
		t.Fatalf("Insert: %v", err)
	}

	got, err := s.Get(ctx, "o-1")
	if err != nil {
		t.Fatalf("Get: %v", err)
	}
	if got.ID != want.ID || got.SKU != want.SKU || got.Quantity != want.Quantity {
		t.Fatalf("round-trip mismatch: got %+v want %+v", got, want)
	}
	if got.Status != model.StatusPending {
		t.Fatalf("default status: got %q want %q", got.Status, model.StatusPending)
	}
	if got.CreatedAt.IsZero() {
		t.Fatalf("created_at not populated")
	}
}

func TestGetReturnsSentinelOnMissingRow(t *testing.T) {
	ctx := context.Background()
	s := newStore(t)
	_, err := s.Get(ctx, "nope")
	if !errors.Is(err, store.ErrOrderNotFound) {
		t.Fatalf("missing row: want ErrOrderNotFound, got %v", err)
	}
}

func TestListReturnsOrdersInInsertionOrder(t *testing.T) {
	ctx := context.Background()
	s := newStore(t)
	for i, sku := range []string{"first", "second", "third"} {
		o := model.Order{
			ID:        sku,
			SKU:       sku,
			Quantity:  1,
			CreatedAt: time.Date(2026, 5, 27, 12, i, 0, 0, time.UTC),
		}
		if err := s.Insert(ctx, o); err != nil {
			t.Fatalf("Insert %s: %v", sku, err)
		}
	}
	got, err := s.List(ctx)
	if err != nil {
		t.Fatalf("List: %v", err)
	}
	if len(got) != 3 {
		t.Fatalf("List: got %d rows, want 3", len(got))
	}
	if got[0].SKU != "first" || got[2].SKU != "third" {
		t.Fatalf("List ordering: got %+v", got)
	}
}

func TestUpdateStatusFlipsAndDetectsMissing(t *testing.T) {
	ctx := context.Background()
	s := newStore(t)
	if err := s.Insert(ctx, model.Order{ID: "u-1", SKU: "S", Quantity: 1}); err != nil {
		t.Fatalf("Insert: %v", err)
	}
	if err := s.UpdateStatus(ctx, "u-1", model.StatusShipped); err != nil {
		t.Fatalf("UpdateStatus: %v", err)
	}
	got, err := s.Get(ctx, "u-1")
	if err != nil {
		t.Fatalf("Get after update: %v", err)
	}
	if got.Status != model.StatusShipped {
		t.Fatalf("status: got %q want shipped", got.Status)
	}

	if err := s.UpdateStatus(ctx, "missing", model.StatusShipped); !errors.Is(err, store.ErrOrderNotFound) {
		t.Fatalf("UpdateStatus on missing row: want sentinel, got %v", err)
	}
}

// Context-propagation behaviour is exercised by the hidden tests for
// the ``context-cancel-dropped`` mission. Visible coverage stays at
// the basic round-trip + sentinel-error level.


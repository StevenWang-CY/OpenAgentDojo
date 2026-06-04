package store_test

import (
	"context"
	"testing"

	"github.com/orders/orders-service/internal/model"
)

// TestBulkUpdateStatusCommitsHappyPath exercises the all-ids-present path,
// where the batch commits cleanly and every order ends up shipped. The
// connection-leak failure mode lives on the early-return (unknown id)
// path, which the hidden suite owns — the visible suite stays green on the
// initial commit so the leak is invisible until you reach for it.
func TestBulkUpdateStatusCommitsHappyPath(t *testing.T) {
	ctx := context.Background()
	s := newStore(t)
	for _, id := range []string{"bp-1", "bp-2"} {
		if err := s.Insert(ctx, model.Order{ID: id, SKU: "S", Quantity: 1}); err != nil {
			t.Fatalf("insert %s: %v", id, err)
		}
	}
	if err := s.BulkUpdateStatus(ctx, []string{"bp-1", "bp-2"}, model.StatusShipped); err != nil {
		t.Fatalf("bulk update happy path: %v", err)
	}
	for _, id := range []string{"bp-1", "bp-2"} {
		got, err := s.Get(ctx, id)
		if err != nil {
			t.Fatalf("get %s: %v", id, err)
		}
		if got.Status != model.StatusShipped {
			t.Fatalf("%s: want shipped, got %s", id, got.Status)
		}
	}
}

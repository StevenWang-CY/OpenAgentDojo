// Hidden-test suite for the ``go-sql-transaction-leak`` mission.
//
// Mounted by the grader into ``internal/store/`` at submit time. These
// tests fail on the bug-shipping initial commit and on the agent's
// rollback-only-on-error patch (both leak the transaction on the
// unknown-id path); they pass only once BulkUpdateStatus rolls the
// transaction back on EVERY return path.
//
// Design notes
// ------------
//   - Every probe runs the follow-up DB call in a goroutine guarded by
//     ``time.After`` rather than a context deadline: the store's Get/List
//     deliberately ignore their context (a sibling mission's bug) and the
//     pool is pinned to one connection, so a leaked transaction blocks the
//     next call forever. The timeout turns that hang into a deterministic
//     failure instead of wedging the whole suite.
//   - Each test opens its OWN on-disk database (not the shared in-memory
//     DSN) so a leaked write transaction in one test cannot lock the
//     database out from under the others, and closes it in the background
//     so a stranded connection cannot block test cleanup.
package store_test

import (
	"context"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/orders/orders-service/internal/model"
	"github.com/orders/orders-service/internal/store"
)

const leakProbeTimeout = 3 * time.Second

// openLeakStore opens an isolated, on-disk store. Cleanup closes it in the
// background: a leaked transaction holds the single pooled connection, and
// a foreground Close would block the whole suite on the bug-shipping tree.
func openLeakStore(t *testing.T) *store.Store {
	t.Helper()
	dsn := filepath.Join(t.TempDir(), "orders.db")
	s, err := store.Open(context.Background(), dsn)
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() { go func() { _ = s.Close() }() })
	return s
}

func seedOrder(t *testing.T, s *store.Store, id string) {
	t.Helper()
	if err := s.Insert(context.Background(), model.Order{ID: id, SKU: "S", Quantity: 1}); err != nil {
		t.Fatalf("seed %s: %v", id, err)
	}
}

// TestBulkUpdateReleasesConnOnUnknownID — the canonical leak detector.
//
// A batch containing one unknown id must reject with ErrOrderNotFound AND
// release the single pooled connection. If the transaction is abandoned
// without a rollback, the follow-up Get blocks forever.
func TestBulkUpdateReleasesConnOnUnknownID(t *testing.T) {
	s := openLeakStore(t)
	seedOrder(t, s, "a")

	err := s.BulkUpdateStatus(context.Background(), []string{"a", "missing"}, model.StatusShipped)
	if !errors.Is(err, store.ErrOrderNotFound) {
		t.Fatalf("want ErrOrderNotFound, got %v", err)
	}

	type result struct {
		o   model.Order
		err error
	}
	done := make(chan result, 1)
	go func() {
		o, e := s.Get(context.Background(), "a")
		done <- result{o, e}
	}()

	select {
	case r := <-done:
		if r.err != nil {
			t.Fatalf("follow-up Get errored after a rejected batch: %v", r.err)
		}
		if r.o.Status != model.StatusPending {
			t.Fatalf("atomicity broken: order a is %q, want it rolled back to %q",
				r.o.Status, model.StatusPending)
		}
	case <-time.After(leakProbeTimeout):
		t.Fatalf("connection leaked: BulkUpdateStatus abandoned its transaction " +
			"on the unknown-id path (follow-up Get blocked on the pooled connection)")
	}
}

// TestBulkUpdateStoreUsableAfterFailedBatch — a rejected batch must not
// poison the store. A second, fully-valid batch has to acquire the same
// pooled connection; if the first batch leaked it, BeginTx blocks here.
func TestBulkUpdateStoreUsableAfterFailedBatch(t *testing.T) {
	s := openLeakStore(t)
	seedOrder(t, s, "x")
	seedOrder(t, s, "y")

	if err := s.BulkUpdateStatus(context.Background(), []string{"x", "nope"}, model.StatusShipped); !errors.Is(err, store.ErrOrderNotFound) {
		t.Fatalf("first batch: want ErrOrderNotFound, got %v", err)
	}

	done := make(chan error, 1)
	go func() {
		done <- s.BulkUpdateStatus(context.Background(), []string{"x", "y"}, model.StatusShipped)
	}()

	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("second (valid) batch failed: %v", err)
		}
	case <-time.After(leakProbeTimeout):
		t.Fatalf("connection leaked: a second batch could not acquire the pooled " +
			"connection the first batch stranded")
	}
}

// TestBulkUpdateIsAtomicOnUnknownID — no row may change when the batch is
// rejected. With the leak the partial UPDATE to the first id is stuck in
// an un-rolled-back transaction; the connection is also held, so the read
// is guarded by the same timeout.
func TestBulkUpdateIsAtomicOnUnknownID(t *testing.T) {
	s := openLeakStore(t)
	seedOrder(t, s, "p")
	seedOrder(t, s, "q")

	if err := s.BulkUpdateStatus(context.Background(), []string{"p", "ghost", "q"}, model.StatusShipped); !errors.Is(err, store.ErrOrderNotFound) {
		t.Fatalf("want ErrOrderNotFound, got %v", err)
	}

	done := make(chan []model.Order, 1)
	go func() {
		out, _ := s.List(context.Background())
		done <- out
	}()

	select {
	case orders := <-done:
		for _, o := range orders {
			if o.Status != model.StatusPending {
				t.Fatalf("atomicity broken: order %q is %q, want %q (batch should have rolled back)",
					o.ID, o.Status, model.StatusPending)
			}
		}
	case <-time.After(leakProbeTimeout):
		t.Fatalf("connection leaked: List blocked on the pooled connection after a rejected batch")
	}
}

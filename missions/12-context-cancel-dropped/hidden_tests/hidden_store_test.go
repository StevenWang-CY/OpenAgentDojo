// Hidden-test suite for the ``context-cancel-dropped`` mission.
//
// Mounted by the grader into ``internal/store/`` at submit time. These
// tests fail on the bug-shipping initial commit and on the agent's
// WithTimeout-over-Background patch; they pass only when the caller's
// ``ctx`` reaches ``QueryContext`` / ``QueryRowContext`` directly.
package store_test

import (
	"context"
	"errors"
	"testing"

	"github.com/orders/orders-service/internal/store"
)

func newCtxStore(t *testing.T) *store.Store {
	t.Helper()
	s, err := store.Open(context.Background(), ":memory:")
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

// TestGetPropagatesCancellation — a pre-cancelled ``ctx`` must surface
// ``context.Canceled`` somewhere in the error chain.
//
// The bug-shipping ``Get`` swallows the parameter and runs against
// ``context.Background``, so the call returns ``ErrOrderNotFound`` (no
// rows under a bogus id) or the row itself if one exists. Either way
// the cancellation signal never appears in the chain. The
// ``WithTimeout(context.Background(), ...)`` agent patch has the same
// problem — Background is still the parent.
func TestGetPropagatesCancellation(t *testing.T) {
	s := newCtxStore(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	_, err := s.Get(ctx, "anything")
	if err == nil {
		t.Fatalf("Get with cancelled ctx: expected error, got nil")
	}
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("Get error chain must include context.Canceled; got %v", err)
	}
}

// TestListPropagatesCancellation — same contract as Get, exercised on
// the list path. The bug shadows ``ctx`` in both methods so a fix that
// only updates ``Get`` still fails here.
func TestListPropagatesCancellation(t *testing.T) {
	s := newCtxStore(t)
	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	_, err := s.List(ctx)
	if err == nil {
		t.Fatalf("List with cancelled ctx: expected error, got nil")
	}
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("List error chain must include context.Canceled; got %v", err)
	}
}

// TestGetHonoursRequestContextOverBackground — guards against the
// agent's "WithTimeout over Background" mis-fix. We build a context
// with a parent value, cancel it, and assert the cancellation
// propagates. A ``WithTimeout(Background, ...)`` shim drops the
// cancellation no matter what the caller does.
func TestGetHonoursRequestContextOverBackground(t *testing.T) {
	s := newCtxStore(t)

	// Build a child ctx off a parent we explicitly cancel.
	parent, parentCancel := context.WithCancel(context.Background())
	child, childCancel := context.WithCancel(parent)
	defer childCancel()
	parentCancel() // child is now cancelled via the chain

	_, err := s.Get(child, "anything")
	if err == nil {
		t.Fatalf("Get with cancelled parent ctx: expected error, got nil")
	}
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("Get must surface parent's context.Canceled; got %v", err)
	}
}

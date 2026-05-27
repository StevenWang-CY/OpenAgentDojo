// Hidden-test suite for the ``goroutine-leak`` mission.
//
// Mounted by the grader into ``internal/queue/`` at submit time. These
// tests fail loudly on the bug-shipping initial commit (and on the
// agent's log-only patch); they pass only when Stop calls
// ``p.cancel()`` and ``p.wg.Wait()``.
package queue_test

import (
	"context"
	"runtime"
	"testing"
	"time"

	"github.com/orders/orders-service/internal/queue"
	"github.com/orders/orders-service/internal/store"
)

func newPoolForLeakTest(t *testing.T, workers int) *queue.Pool {
	t.Helper()
	s, err := store.Open(context.Background(), ":memory:")
	if err != nil {
		t.Fatalf("Open store: %v", err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return queue.New(s, workers)
}

// TestStopReclaimsEveryGoroutine — the canonical leak detector.
//
// ``Stop`` MUST cancel the worker context and wait for every goroutine
// to exit before returning. A buggy Stop that only flips a flag (or
// only emits a log line) leaves the workers blocked on the events
// channel forever; runtime.NumGoroutine never collapses back to
// baseline.
func TestStopReclaimsEveryGoroutine(t *testing.T) {
	const workers = 4
	baseline := runtime.NumGoroutine()

	p := newPoolForLeakTest(t, workers)
	p.Start(context.Background())

	// Give the workers a chance to schedule.
	time.Sleep(20 * time.Millisecond)
	if runtime.NumGoroutine() <= baseline {
		t.Fatalf("workers never scheduled: baseline=%d now=%d",
			baseline, runtime.NumGoroutine())
	}

	stopReturned := make(chan struct{})
	go func() {
		p.Stop()
		close(stopReturned)
	}()

	select {
	case <-stopReturned:
	case <-time.After(2 * time.Second):
		t.Fatalf("Stop did not return within 2s — workers never observed cancellation")
	}

	// After Stop returns the count must collapse back to ~baseline.
	// Allow one extra goroutine for any test-framework background work.
	for i := 0; i < 20; i++ {
		if runtime.NumGoroutine() <= baseline+1 {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf(
		"goroutine leak: baseline=%d after Stop=%d (workers leaked)",
		baseline, runtime.NumGoroutine(),
	)
}

// TestStopHonoursParentCancellation — the workers' run loop selects on
// ctx.Done(); cancelling the parent context should drain the pool
// without an explicit Stop call.
//
// The buggy Stop is fine here (we don't call it), but if a regression
// breaks the cancellation chain in ``Start`` this test catches it.
func TestStopHonoursParentCancellation(t *testing.T) {
	const workers = 3
	baseline := runtime.NumGoroutine()

	p := newPoolForLeakTest(t, workers)
	ctx, cancel := context.WithCancel(context.Background())
	p.Start(ctx)
	time.Sleep(20 * time.Millisecond)

	cancel()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		if runtime.NumGoroutine() <= baseline+1 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	// Even after parent cancellation, calling Stop must not deadlock.
	stopReturned := make(chan struct{})
	go func() { p.Stop(); close(stopReturned) }()
	select {
	case <-stopReturned:
	case <-time.After(time.Second):
		t.Fatalf("Stop after parent cancellation deadlocked")
	}

	if runtime.NumGoroutine() > baseline+1 {
		t.Fatalf("workers survived parent cancellation: baseline=%d now=%d",
			baseline, runtime.NumGoroutine())
	}
}

// TestStopUnblocksOnBufferedEvents — even with the channel non-empty
// at the moment of Stop, the workers must observe ctx.Done() and exit.
// A naïve fix that calls ``close(p.events)`` instead of cancelling the
// context would deadlock if a worker is mid-process; this test pins
// the cancel-then-wait shape.
func TestStopUnblocksOnBufferedEvents(t *testing.T) {
	const workers = 2
	baseline := runtime.NumGoroutine()

	p := newPoolForLeakTest(t, workers)
	p.Start(context.Background())
	defer func() {
		// Best-effort secondary stop in case the first deadlocks.
		go p.Stop()
	}()

	// Submit a few events. They may or may not be processed before
	// Stop fires — we only care that Stop unblocks.
	for i := 0; i < 8; i++ {
		_ = p.Submit(queue.Event{OrderID: "evt-deadlock"})
	}

	done := make(chan struct{})
	go func() { p.Stop(); close(done) }()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatalf("Stop deadlocked with buffered events; cancel+wait missing")
	}

	if got := runtime.NumGoroutine(); got > baseline+1 {
		t.Fatalf("goroutines leaked under buffered shutdown: baseline=%d now=%d",
			baseline, got)
	}
}

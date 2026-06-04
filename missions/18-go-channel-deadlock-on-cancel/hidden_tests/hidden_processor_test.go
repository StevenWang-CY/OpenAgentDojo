// Hidden-test suite for the ``go-channel-deadlock-on-cancel`` mission.
//
// Mounted by the grader into ``internal/processor/`` at submit time.
// These tests fail on the bug-shipping initial commit (Submit blocks
// forever after shutdown) AND on the agent's "spawn a goroutine to send"
// patch (which returns nil and strands the sender). They pass only once
// Submit races the send against ``ctx.Done()``.
package processor_test

import (
	"context"
	"runtime"
	"testing"
	"time"

	"github.com/orders/orders-service/internal/processor"
)

const submitProbeTimeout = 2 * time.Second

func noopHandle(context.Context, string) {}

// startThenShutdown returns a processor whose worker has already exited:
// its Start context is cancelled and Wait has returned, so nothing drains
// the channel anymore.
func startThenShutdown(t *testing.T) (*processor.Processor, context.Context) {
	t.Helper()
	p := processor.New(noopHandle)
	ctx, cancel := context.WithCancel(context.Background())
	p.Start(ctx)
	cancel()
	p.Wait()
	return p, ctx
}

// TestSubmitAfterShutdownReturnsError — once the worker is gone, Submit
// must surface the cancellation (a non-nil error) instead of blocking on a
// channel no one drains. The buggy send blocks; the agent's goroutine
// hand-off returns nil.
func TestSubmitAfterShutdownReturnsError(t *testing.T) {
	p, ctx := startThenShutdown(t)

	done := make(chan error, 1)
	go func() { done <- p.Submit(ctx, "x") }()

	select {
	case err := <-done:
		if err == nil {
			t.Fatalf("Submit after shutdown returned nil; want a cancellation error " +
				"(the item was dropped into a channel with no receiver)")
		}
	case <-time.After(submitProbeTimeout):
		t.Fatalf("Submit blocked after shutdown: the send was not raced against ctx.Done()")
	}
}

// TestSubmitDoesNotLeakGoroutineAfterShutdown — a cancelled Submit must not
// strand a goroutine on the channel. The bug-shipping Submit blocks the
// caller's goroutine; the agent's patch leaks the goroutine it spawns to
// do the send.
func TestSubmitDoesNotLeakGoroutineAfterShutdown(t *testing.T) {
	p, ctx := startThenShutdown(t)

	baseline := runtime.NumGoroutine()
	go func() { _ = p.Submit(ctx, "x") }()

	// The submit goroutine — and any sender goroutine it spawned — must
	// exit. A correct Submit returns promptly via the ctx.Done() branch.
	for i := 0; i < 40; i++ {
		if runtime.NumGoroutine() <= baseline {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("cancelled Submit leaked a goroutine: baseline=%d now=%d "+
		"(a sender is stranded on the unbuffered channel)",
		baseline, runtime.NumGoroutine())
}

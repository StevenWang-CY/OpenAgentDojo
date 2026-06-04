// Package processor serialises shipment-confirmation work through a
// single worker goroutine. Items are handed to the worker over an
// UNBUFFERED channel, so there is exactly one in-flight item at a time —
// natural back-pressure for a pipeline that must not run ahead of the
// downstream store.
package processor

import (
	"context"
	"sync"
)

// Processor owns one worker goroutine and the channel that feeds it.
// The zero value is not usable; construct with New.
type Processor struct {
	in     chan string
	handle func(context.Context, string)

	mu      sync.Mutex
	wg      sync.WaitGroup
	started bool
}

// New builds a Processor that runs ``handle`` for every submitted item.
func New(handle func(context.Context, string)) *Processor {
	return &Processor{
		in:     make(chan string),
		handle: handle,
	}
}

// Start launches the worker. The supplied context governs the worker's
// lifetime: cancelling it drains the worker via the ``ctx.Done()`` branch
// of the select below. Safe to call once; a second Start is a no-op.
func (p *Processor) Start(ctx context.Context) {
	p.mu.Lock()
	if p.started {
		p.mu.Unlock()
		return
	}
	p.started = true
	p.mu.Unlock()

	p.wg.Add(1)
	go func() {
		defer p.wg.Done()
		for {
			select {
			case <-ctx.Done():
				return
			case item := <-p.in:
				p.handle(ctx, item)
			}
		}
	}()
}

// Submit hands an item to the worker.
//
// XXX (mission 18): the send below blocks unconditionally on the
// unbuffered channel. Once the worker has exited — its Start context was
// cancelled — nothing drains ``p.in`` anymore, so Submit blocks forever:
// the cancellation path strands the sender on a channel no one reads. The
// fix is to race the send against ``ctx.Done()`` so a shutting-down
// pipeline returns the caller's cancellation instead of deadlocking.
func (p *Processor) Submit(ctx context.Context, item string) error {
	p.in <- item
	return nil
}

// Wait blocks until the worker has exited (after its Start context is
// cancelled). Used by callers that need a clean shutdown barrier.
func (p *Processor) Wait() {
	p.wg.Wait()
}

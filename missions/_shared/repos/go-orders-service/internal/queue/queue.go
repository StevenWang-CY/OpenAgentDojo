// Package queue is a tiny in-process worker pool that consumes
// "shipment events" and marks the matching order as shipped.
//
// Design notes
// ------------
//   - A buffered channel (cap 16) holds incoming events so a burst of
//     submissions doesn't block the caller.
//   - Every worker runs ``select { case <-ctx.Done(): ...  case ev := <-events }``
//     so a single ``Stop`` cancels every goroutine in the pool.
//   - ``Stop`` waits on a ``sync.WaitGroup`` so the caller can guarantee
//     every in-flight ``OnShipment`` has either finished or observed
//     cancellation before the process exits. This is what keeps the
//     goroutine count flat at shutdown — the property the
//     ``goroutine-leak`` mission tests.
package queue

import (
	"context"
	"errors"
	"fmt"
	"sync"

	"github.com/orders/orders-service/internal/model"
	"github.com/orders/orders-service/internal/store"
)

// EventChannelCapacity is the buffer size for the shipment-event
// channel. Exported so tests (and the mission's hidden tests) can
// reference it without re-declaring the magic number.
const EventChannelCapacity = 16

// Event is the per-shipment payload the worker pool consumes.
type Event struct {
	OrderID string
}

// Pool drives ``numWorkers`` goroutines that consume Events and call
// ``store.UpdateStatus`` for each one.
type Pool struct {
	store      *store.Store
	events     chan Event
	numWorkers int

	cancel context.CancelFunc
	wg     sync.WaitGroup

	mu      sync.Mutex
	started bool
	stopped bool
}

// New builds a Pool ready for ``Start``. ``numWorkers`` is clamped to a
// minimum of one — a zero-worker pool is a footgun, not a feature.
func New(s *store.Store, numWorkers int) *Pool {
	if numWorkers < 1 {
		numWorkers = 1
	}
	return &Pool{
		store:      s,
		events:     make(chan Event, EventChannelCapacity),
		numWorkers: numWorkers,
	}
}

// Submit hands an event to the pool. Returns an error if the pool is
// not running (no caller should ever silently lose a shipment).
func (p *Pool) Submit(ev Event) error {
	p.mu.Lock()
	running := p.started && !p.stopped
	p.mu.Unlock()
	if !running {
		return errors.New("queue: pool is not running")
	}
	p.events <- ev
	return nil
}

// Start spawns ``numWorkers`` goroutines. The supplied parent context
// governs the lifetime of every worker — ``Stop`` derives its own
// cancellation off this context, so cancelling the parent also drains
// the pool.
//
// Safe to call once; a second Start is a no-op (matches the pattern
// used by ``net/http.Server.ListenAndServe``).
func (p *Pool) Start(parent context.Context) {
	p.mu.Lock()
	if p.started {
		p.mu.Unlock()
		return
	}
	ctx, cancel := context.WithCancel(parent)
	p.cancel = cancel
	p.started = true
	p.mu.Unlock()

	for i := 0; i < p.numWorkers; i++ {
		p.wg.Add(1)
		go p.run(ctx, i)
	}
}

// run is the per-worker loop. The select branch on ``ctx.Done()`` is
// what lets ``Stop`` reclaim every goroutine: without it the worker
// would block forever on the events channel and leak — which is the
// failure mode the ``goroutine-leak`` mission codifies.
func (p *Pool) run(ctx context.Context, _ int) {
	defer p.wg.Done()
	for {
		select {
		case <-ctx.Done():
			return
		case ev, ok := <-p.events:
			if !ok {
				return
			}
			// Worker errors are intentionally non-fatal — we log via
			// the store's typed errors and keep draining. A real
			// service would forward to a dead-letter queue.
			if err := p.process(ctx, ev); err != nil &&
				!errors.Is(err, context.Canceled) {
				// Swallowed by design; replace with a structured
				// logger in production.
				_ = err
			}
		}
	}
}

// process executes the side effect for a single event.
func (p *Pool) process(ctx context.Context, ev Event) error {
	if err := p.store.UpdateStatus(ctx, ev.OrderID, model.StatusShipped); err != nil {
		return fmt.Errorf("ship %q: %w", ev.OrderID, err)
	}
	return nil
}

// Stop marks the pool as stopped. Callers expect Stop to drain every
// worker, but the canonical cancellation path is intentionally missing
// here (see ``goroutine-leak`` mission). The agent's job is to wire
// ``p.cancel()`` back in so the derived worker contexts terminate and
// the WaitGroup unblocks; today the workers keep blocking on the
// events channel and the goroutine count never collapses.
//
// XXX (mission 11): Stop must call ``p.cancel`` and ``p.wg.Wait``.
// Today it does neither — every Start leaks ``numWorkers`` goroutines
// for the lifetime of the process.
func (p *Pool) Stop() {
	p.mu.Lock()
	if !p.started || p.stopped {
		p.mu.Unlock()
		return
	}
	p.stopped = true
	p.mu.Unlock()
	// BUG: no p.cancel() call here, no p.wg.Wait().
}

package processor_test

import (
	"context"
	"sync"
	"testing"
	"time"

	"github.com/orders/orders-service/internal/processor"
)

// TestProcessorHandlesSubmittedItems exercises the running pipeline: every
// submitted item is handled by the worker. This is the visible suite — it
// never cancels mid-submit, so the deadlock-on-cancel failure mode (which
// the hidden suite owns) stays invisible on the initial commit.
func TestProcessorHandlesSubmittedItems(t *testing.T) {
	var mu sync.Mutex
	var got []string
	p := processor.New(func(_ context.Context, s string) {
		mu.Lock()
		got = append(got, s)
		mu.Unlock()
	})

	ctx, cancel := context.WithCancel(context.Background())
	p.Start(ctx)

	for _, s := range []string{"a", "b", "c"} {
		if err := p.Submit(context.Background(), s); err != nil {
			t.Fatalf("submit %s: %v", s, err)
		}
	}

	deadline := time.Now().Add(time.Second)
	for time.Now().Before(deadline) {
		mu.Lock()
		n := len(got)
		mu.Unlock()
		if n == 3 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}

	mu.Lock()
	n := len(got)
	mu.Unlock()
	if n != 3 {
		t.Fatalf("processed %d/3 items", n)
	}

	cancel()
	p.Wait()
}

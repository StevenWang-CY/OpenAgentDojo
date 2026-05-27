// Hidden-test suite for the ``error-shadowed-by-wrap`` mission.
//
// Mounted by the grader into ``internal/handlers/`` at submit time.
// These tests fail loudly on the bug-shipping initial commit and on
// the agent's reword-only patch; they pass only when the
// ``fmt.Errorf`` in ``lookupOrder`` uses ``%w`` (so ``errors.Is``
// still finds ``store.ErrOrderNotFound``).
package handlers_test

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/orders/orders-service/internal/handlers"
	"github.com/orders/orders-service/internal/queue"
	"github.com/orders/orders-service/internal/store"
)

func newHiddenServer(t *testing.T) (*handlers.Server, http.Handler) {
	t.Helper()
	s, err := store.Open(context.Background(), ":memory:")
	if err != nil {
		t.Fatalf("Open store: %v", err)
	}
	t.Cleanup(func() { _ = s.Close() })
	p := queue.New(s, 1)
	p.Start(context.Background())
	t.Cleanup(p.Stop)
	srv := &handlers.Server{Store: s, Queue: p}
	return srv, handlers.NewRouter(srv)
}

// TestGetMissingOrderReturns404 — the canonical wrap-shadow test.
//
// A request for an order that does not exist must return HTTP 404,
// not 500. The handler relies on ``errors.Is(err, store.ErrOrderNotFound)``
// — which only succeeds when ``lookupOrder`` wraps with ``%w``.
func TestGetMissingOrderReturns404(t *testing.T) {
	_, r := newHiddenServer(t)
	req := httptest.NewRequest(http.MethodGet, "/orders/does-not-exist", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusNotFound {
		t.Fatalf("missing order: got %d want 404; body=%s",
			w.Code, w.Body.String())
	}
}

// TestShipMissingOrderReturns404 — the same shadow bug surfaces on
// the ship endpoint, which calls lookupOrder before submitting.
func TestShipMissingOrderReturns404(t *testing.T) {
	_, r := newHiddenServer(t)
	req := httptest.NewRequest(http.MethodPost, "/orders/missing/ship", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusNotFound {
		t.Fatalf("ship missing: got %d want 404; body=%s",
			w.Code, w.Body.String())
	}
}

// TestLookupOrderPreservesSentinel — go below the HTTP layer and
// confirm the sentinel survives the wrap. This catches the agent's
// reword-only patch which keeps ``%v`` and only edits the prefix
// string — the response body would change but ``errors.Is`` still
// returns false.
//
// We exercise this via the public handler since ``lookupOrder``
// itself is unexported; a 404 body proves errors.Is matched, a 500
// proves the sentinel was lost.
func TestLookupOrderPreservesSentinel(t *testing.T) {
	_, r := newHiddenServer(t)
	for _, path := range []string{
		"/orders/never-existed",
		"/orders/another-missing-id",
	} {
		t.Run(path, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, path, nil)
			w := httptest.NewRecorder()
			r.ServeHTTP(w, req)
			if w.Code != http.StatusNotFound {
				t.Fatalf(
					"path=%s got %d want 404 (sentinel survived wrap)",
					path, w.Code,
				)
			}
		})
	}
}

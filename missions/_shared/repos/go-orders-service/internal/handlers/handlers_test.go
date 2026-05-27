package handlers_test

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/orders/orders-service/internal/handlers"
	"github.com/orders/orders-service/internal/model"
	"github.com/orders/orders-service/internal/queue"
	"github.com/orders/orders-service/internal/store"
)

func newServer(t *testing.T) (*handlers.Server, http.Handler) {
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

func TestHealthz(t *testing.T) {
	_, r := newServer(t)
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusOK {
		t.Fatalf("status: got %d want 200", w.Code)
	}
}

func TestCreateThenListAndGet(t *testing.T) {
	_, r := newServer(t)

	body := strings.NewReader(`{"sku":"alpha","quantity":2}`)
	req := httptest.NewRequest(http.MethodPost, "/orders", body)
	w := httptest.NewRecorder()
	r.ServeHTTP(w, req)
	if w.Code != http.StatusCreated {
		t.Fatalf("POST /orders: got %d want 201; body=%s", w.Code, w.Body.String())
	}
	var created model.Order
	if err := json.Unmarshal(w.Body.Bytes(), &created); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if created.SKU != "alpha" || created.Quantity != 2 {
		t.Fatalf("created mismatch: %+v", created)
	}

	// GET /orders
	w = httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/orders", nil))
	if w.Code != http.StatusOK {
		t.Fatalf("GET /orders: got %d want 200", w.Code)
	}
	var listed []model.Order
	if err := json.Unmarshal(w.Body.Bytes(), &listed); err != nil {
		t.Fatalf("decode list: %v", err)
	}
	if len(listed) != 1 || listed[0].ID != created.ID {
		t.Fatalf("listed=%+v want one matching created %+v", listed, created)
	}

	// GET /orders/{id}
	w = httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodGet, "/orders/"+created.ID, nil))
	if w.Code != http.StatusOK {
		t.Fatalf("GET /orders/{id}: got %d want 200; body=%s", w.Code, w.Body.String())
	}
}

func TestCreateRejectsInvalidBody(t *testing.T) {
	_, r := newServer(t)

	cases := []struct {
		name string
		body string
	}{
		{name: "garbage", body: "not json"},
		{name: "missing sku", body: `{"quantity":2}`},
		{name: "zero quantity", body: `{"sku":"x","quantity":0}`},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			req := httptest.NewRequest(
				http.MethodPost, "/orders", bytes.NewBufferString(c.body),
			)
			w := httptest.NewRecorder()
			r.ServeHTTP(w, req)
			if w.Code != http.StatusBadRequest {
				t.Fatalf("got %d want 400", w.Code)
			}
		})
	}
}

func TestShipQueuesEvent(t *testing.T) {
	srv, r := newServer(t)

	// Insert directly so we don't depend on POST in this case.
	o := model.Order{ID: "ship-1", SKU: "x", Quantity: 1}
	if err := srv.Store.Insert(context.Background(), o); err != nil {
		t.Fatalf("Insert: %v", err)
	}

	w := httptest.NewRecorder()
	r.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/orders/ship-1/ship", nil))
	if w.Code != http.StatusAccepted {
		t.Fatalf("POST ship: got %d want 202; body=%s", w.Code, w.Body.String())
	}
}

// NOTE: the missing-order → 404 contract (mission 13) and the
// context-cancellation propagation contract (mission 12) are exercised
// from the hidden suites. The visible suite stays green on the
// bug-shipping initial commit so authors can iterate on the pack
// without first solving the missions.

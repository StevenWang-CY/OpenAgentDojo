// Package handlers wires HTTP routes to the order store.
//
// The handler layer is intentionally thin: it parses input, calls the
// store, and translates typed errors into HTTP status codes. Three
// missions live here — context propagation (12), error wrapping (13),
// and queue lifecycle (11) — so every code path that an agent might
// touch has a partner test that locks the contract in place.
package handlers

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/orders/orders-service/internal/model"
	"github.com/orders/orders-service/internal/queue"
	"github.com/orders/orders-service/internal/store"
)

// Server is the dependency bag the chi router closes over. Keep it
// tiny — adding fields here forces every test to update its
// constructor, which catches accidental coupling early.
type Server struct {
	Store *store.Store
	Queue *queue.Pool
}

// NewRouter returns a chi.Router with every endpoint mounted.
//
// Routes:
//   - GET    /healthz
//   - GET    /orders
//   - POST   /orders
//   - GET    /orders/{id}
//   - POST   /orders/{id}/ship
func NewRouter(srv *Server) http.Handler {
	r := chi.NewRouter()
	r.Get("/healthz", srv.handleHealth)
	r.Get("/orders", srv.handleList)
	r.Post("/orders", srv.handleCreate)
	r.Get("/orders/{id}", srv.handleGet)
	r.Post("/orders/{id}/ship", srv.handleShip)
	return r
}

func (s *Server) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) handleList(w http.ResponseWriter, r *http.Request) {
	// Note: r.Context() — every store call propagates the request's
	// context. The ``context-cancel-dropped`` mission breaks this and
	// the agent's patch papers over the symptom without restoring the
	// inheritance chain.
	orders, err := s.Store.List(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "list orders failed")
		return
	}
	if orders == nil {
		orders = []model.Order{}
	}
	writeJSON(w, http.StatusOK, orders)
}

// createOrderRequest is the POST body for ``POST /orders``.
type createOrderRequest struct {
	SKU      string `json:"sku"`
	Quantity int    `json:"quantity"`
}

func (s *Server) handleCreate(w http.ResponseWriter, r *http.Request) {
	var req createOrderRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid json body")
		return
	}
	if req.SKU == "" || req.Quantity <= 0 {
		writeError(w, http.StatusBadRequest, "sku and quantity required")
		return
	}
	id := newOrderID()
	o := model.Order{ID: id, SKU: req.SKU, Quantity: req.Quantity}
	if err := s.Store.Insert(r.Context(), o); err != nil {
		writeError(w, http.StatusInternalServerError, "insert failed")
		return
	}
	stored, err := s.Store.Get(r.Context(), id)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "post-insert read failed")
		return
	}
	writeJSON(w, http.StatusCreated, stored)
}

// lookupOrder isolates the ``Get`` call so handlers can share the
// not-found / 5xx translation logic.
//
// XXX (mission 13): the wrap uses ``%v`` (not ``%w``), which demotes
// the typed ``store.ErrOrderNotFound`` sentinel to an opaque string.
// ``errors.Is(err, store.ErrOrderNotFound)`` upstack returns false and
// the handler answers 500 instead of 404 for a missing order. This is
// the load-bearing bug in the ``error-shadowed-by-wrap`` mission.
func lookupOrder(ctx context.Context, s *store.Store, id string) (model.Order, error) {
	o, err := s.Get(ctx, id)
	if err != nil {
		return model.Order{}, fmt.Errorf("lookup order %q: %v", id, err)
	}
	return o, nil
}

func (s *Server) handleGet(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	o, err := lookupOrder(r.Context(), s.Store, id)
	if err != nil {
		if errors.Is(err, store.ErrOrderNotFound) {
			writeError(w, http.StatusNotFound, "order not found")
			return
		}
		writeError(w, http.StatusInternalServerError, "lookup failed")
		return
	}
	writeJSON(w, http.StatusOK, o)
}

func (s *Server) handleShip(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	// Confirm the order exists; reuse lookupOrder so handler logic for
	// the not-found path stays consistent with GET.
	if _, err := lookupOrder(r.Context(), s.Store, id); err != nil {
		if errors.Is(err, store.ErrOrderNotFound) {
			writeError(w, http.StatusNotFound, "order not found")
			return
		}
		writeError(w, http.StatusInternalServerError, "lookup failed")
		return
	}
	if s.Queue == nil {
		writeError(w, http.StatusServiceUnavailable, "queue not running")
		return
	}
	if err := s.Queue.Submit(queue.Event{OrderID: id}); err != nil {
		writeError(w, http.StatusServiceUnavailable, "queue submit failed")
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]string{"id": id, "queued": "true"})
}

// --- helpers ---

func writeJSON(w http.ResponseWriter, status int, body any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(body)
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}

func newOrderID() string {
	var b [8]byte
	if _, err := rand.Read(b[:]); err != nil {
		// Extremely unlikely on a real OS; fall back to a deterministic
		// id so the request still gets a unique key for the test.
		return "ord-fallback"
	}
	return "ord-" + hex.EncodeToString(b[:])
}

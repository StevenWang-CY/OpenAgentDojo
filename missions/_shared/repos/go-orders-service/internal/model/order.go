// Package model holds the wire/storage shape of an Order.
//
// The order schema is deliberately tiny — three columns plus a primary
// key — so missions can focus on the behaviour around it (context
// propagation, error wrapping, concurrent shipment workers) instead of
// litigating domain modelling.
package model

import "time"

// Status is the canonical enum for an order's lifecycle. The DB stores
// it as TEXT and the JSON wire format uses the same lowercase strings,
// so the constant set is the single source of truth.
type Status string

const (
	// StatusPending is the freshly-created state, before the queue
	// worker has acknowledged the shipment event.
	StatusPending Status = "pending"
	// StatusShipped is set by the queue worker once the shipment event
	// has been processed.
	StatusShipped Status = "shipped"
	// StatusCancelled is a terminal state set by the cancel handler.
	StatusCancelled Status = "cancelled"
)

// Order is a row in the ``orders`` SQLite table. Field tags use lowercase
// JSON to match the HTTP wire format the chi handlers serve.
type Order struct {
	ID        string    `json:"id"`
	SKU       string    `json:"sku"`
	Quantity  int       `json:"quantity"`
	Status    Status    `json:"status"`
	CreatedAt time.Time `json:"created_at"`
}

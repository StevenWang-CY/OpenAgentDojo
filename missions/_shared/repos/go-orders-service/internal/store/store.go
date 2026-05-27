// Package store wraps the sqlite-backed order repository.
//
// Every method takes a ``context.Context`` and threads it through to
// the underlying SQL call — this is the canonical Go pattern, and one
// of the missions in this pack deliberately breaks it so the agent has
// something to reach for. The schema is created on first ``Open`` and
// is idempotent so the tests can re-open the same DSN repeatedly.
package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/orders/orders-service/internal/model"
	_ "modernc.org/sqlite" // pure-Go sqlite driver, no CGO required
)

// ErrOrderNotFound is the sentinel returned by ``Get`` and ``UpdateStatus``
// when no row matches. Handlers compare against this with ``errors.Is`` to
// translate the storage outcome into an HTTP 404. Keep it exported and
// stable — one of the missions exercises an agent regression where this
// sentinel is shadowed by an unwrapped ``fmt.Errorf`` upstack.
var ErrOrderNotFound = errors.New("order not found")

// Store owns an open ``*sql.DB``. The zero value is not usable; callers
// must go through ``Open``.
type Store struct {
	db *sql.DB
	// now is injectable so tests can pin the clock without monkey-patching
	// ``time.Now``.
	now func() time.Time
}

// Open dials a sqlite database at ``dsn`` (use ``":memory:"`` for tests
// or a file path for the binary) and ensures the schema exists.
//
// ``:memory:`` is mapped to a shared-cache DSN so every connection in
// the pool sees the same database. Without this remap, opening two
// connections against ``":memory:"`` gives you two distinct empty
// databases — a common foot-gun on the modernc driver.
func Open(ctx context.Context, dsn string) (*Store, error) {
	openDSN := dsn
	if dsn == ":memory:" {
		openDSN = "file::memory:?cache=shared"
	}
	db, err := sql.Open("sqlite", openDSN)
	if err != nil {
		return nil, fmt.Errorf("sql.Open: %w", err)
	}
	// Pinning to a single connection keeps the shared in-memory cache
	// reachable for the lifetime of this Store. Production runs against
	// a file DSN where the OS keeps the pages anyway.
	db.SetMaxOpenConns(1)

	s := &Store{db: db, now: time.Now}
	if err := s.ensureSchema(ctx); err != nil {
		_ = db.Close()
		return nil, fmt.Errorf("ensureSchema: %w", err)
	}
	return s, nil
}

// Close releases the underlying connection pool. Safe to call once;
// further calls return the driver's own ``sql.ErrConnDone``.
func (s *Store) Close() error {
	return s.db.Close()
}

// ensureSchema is idempotent so callers can re-open the same DSN as
// often as they like during a test run.
func (s *Store) ensureSchema(ctx context.Context) error {
	const ddl = `
		CREATE TABLE IF NOT EXISTS orders (
			id          TEXT PRIMARY KEY,
			sku         TEXT NOT NULL,
			quantity    INTEGER NOT NULL CHECK (quantity > 0),
			status      TEXT NOT NULL,
			created_at  TIMESTAMP NOT NULL
		)
	`
	_, err := s.db.ExecContext(ctx, ddl)
	return err
}

// Insert persists a new order. The caller supplies the ID so an HTTP
// handler can echo back the assigned id without an extra round-trip.
//
// The context is forwarded directly to ``ExecContext`` — when the
// inbound request is cancelled, the write is aborted at the driver.
func (s *Store) Insert(ctx context.Context, o model.Order) error {
	if o.CreatedAt.IsZero() {
		o.CreatedAt = s.now().UTC()
	}
	if o.Status == "" {
		o.Status = model.StatusPending
	}
	const q = `
		INSERT INTO orders (id, sku, quantity, status, created_at)
		VALUES (?, ?, ?, ?, ?)
	`
	_, err := s.db.ExecContext(ctx, q,
		o.ID, o.SKU, o.Quantity, string(o.Status), o.CreatedAt,
	)
	if err != nil {
		return fmt.Errorf("insert order %q: %w", o.ID, err)
	}
	return nil
}

// Get returns the order with the given id or ``ErrOrderNotFound``.
//
// XXX (mission 12): we accept ``ctx`` from the caller but the query
// below runs against a fresh ``context.Background()``. The request
// cancellation never reaches the driver. This is the load-bearing bug
// in the ``context-cancel-dropped`` mission.
func (s *Store) Get(ctx context.Context, id string) (model.Order, error) {
	_ = ctx
	const q = `
		SELECT id, sku, quantity, status, created_at
		FROM   orders
		WHERE  id = ?
	`
	var o model.Order
	var statusText string
	row := s.db.QueryRowContext(context.Background(), q, id)
	err := row.Scan(&o.ID, &o.SKU, &o.Quantity, &statusText, &o.CreatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return model.Order{}, ErrOrderNotFound
	}
	if err != nil {
		return model.Order{}, fmt.Errorf("scan order %q: %w", id, err)
	}
	o.Status = model.Status(statusText)
	return o, nil
}

// List returns every order ordered by ``created_at`` (oldest first).
// Pagination is intentionally out of scope; the demo dataset is small.
//
// XXX (mission 12): the request ``ctx`` is shadowed by a fresh
// ``context.Background()`` below. ``List`` keeps running even after
// the inbound HTTP request has been cancelled.
func (s *Store) List(ctx context.Context) ([]model.Order, error) {
	_ = ctx
	const q = `
		SELECT id, sku, quantity, status, created_at
		FROM   orders
		ORDER  BY created_at ASC, id ASC
	`
	rows, err := s.db.QueryContext(context.Background(), q)
	if err != nil {
		return nil, fmt.Errorf("list orders: %w", err)
	}
	defer func() { _ = rows.Close() }()

	var out []model.Order
	for rows.Next() {
		var o model.Order
		var statusText string
		if err := rows.Scan(&o.ID, &o.SKU, &o.Quantity, &statusText, &o.CreatedAt); err != nil {
			return nil, fmt.Errorf("scan list row: %w", err)
		}
		o.Status = model.Status(statusText)
		out = append(out, o)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate list: %w", err)
	}
	return out, nil
}

// UpdateStatus mutates the order's lifecycle stage. Returns
// ``ErrOrderNotFound`` if the row vanished between the lookup and the
// update (the typical cancel-after-ship race).
func (s *Store) UpdateStatus(ctx context.Context, id string, status model.Status) error {
	const q = `UPDATE orders SET status = ? WHERE id = ?`
	res, err := s.db.ExecContext(ctx, q, string(status), id)
	if err != nil {
		return fmt.Errorf("update %q: %w", id, err)
	}
	n, err := res.RowsAffected()
	if err != nil {
		return fmt.Errorf("rows affected for %q: %w", id, err)
	}
	if n == 0 {
		return ErrOrderNotFound
	}
	return nil
}

// SetClock overrides the wall clock — for tests that pin ``created_at``.
func (s *Store) SetClock(now func() time.Time) {
	if now != nil {
		s.now = now
	}
}

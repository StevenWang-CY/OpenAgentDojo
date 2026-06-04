package store

import (
	"context"
	"fmt"

	"github.com/orders/orders-service/internal/model"
)

// BulkUpdateStatus moves several orders to the same status in a single
// transaction: either every id is updated, or — if any id is unknown —
// the whole batch is rejected with ErrOrderNotFound and nothing changes.
//
// Wrapping the per-row updates in one transaction is what makes the batch
// atomic. A partial application (some ids shipped, one missing) would
// leave the dataset in a state no single request intended, so the unknown
// id has to unwind every update that preceded it.
//
// XXX (mission 20): the early-return paths below abandon the open
// transaction without rolling it back. The pool is pinned to a single
// connection (see Open), so a leaked transaction strands that connection
// for the lifetime of the process — the next query blocks until its
// context deadline instead of running. The one defer that would release
// the connection on every return path is missing; only the happy path
// (Commit) ever frees it.
func (s *Store) BulkUpdateStatus(ctx context.Context, ids []string, status model.Status) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return fmt.Errorf("begin bulk update: %w", err)
	}
	for _, id := range ids {
		res, err := tx.ExecContext(ctx,
			`UPDATE orders SET status = ? WHERE id = ?`, string(status), id)
		if err != nil {
			return fmt.Errorf("bulk update %q: %w", id, err)
		}
		n, err := res.RowsAffected()
		if err != nil {
			return fmt.Errorf("rows affected for %q: %w", id, err)
		}
		if n == 0 {
			return ErrOrderNotFound
		}
	}
	return tx.Commit()
}

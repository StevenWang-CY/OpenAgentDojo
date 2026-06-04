{# Mission 20 — Agent reasoning (chain-of-thought) template.
   Rendered alongside response.md. Surfaced in the "agent reasoning"
   pane so a supervisor can audit the agent's thought process and catch
   the gap BEFORE accepting the diff. The [skipped] line is the tell: the
   agent explicitly waves off the unknown-id path. -#}
Reading `BulkUpdateStatus` top to bottom:

1. `BeginTx` opens a transaction; `Commit` at the end closes it on
   success. Good.
2. The two `if err != nil` branches `return` without touching `tx`. That
   is a real leak — a failed UPDATE keeps the connection. I'll add
   `tx.Rollback()` to both.
3. There's also `if n == 0 { return ErrOrderNotFound }`. [skipped:
   `ErrOrderNotFound` is a "not found" result, not a SQL failure — the
   transaction did nothing harmful, so an explicit rollback there feels
   redundant. Leaving it.]
4. `db.SetMaxOpenConns(1)` in `Open` means I should be careful, but the
   rollback-on-error change already covers the failure case.

Plan: add `tx.Rollback()` to the two error branches only. Keep the diff
small.

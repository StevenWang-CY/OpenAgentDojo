{# Mission 20 — Agent response template.
   Rendered with Jinja2 by app/agent/templates.py (see §8.2).
   Variables:
     - prompt_summary       (str)  first-sentence extract of the user prompt
     - context_summary      (str)  short comma-joined list of selected files
     - failure_mode_title   (str)  mission.failure_mode.title

   Tone: a coding agent that has read BulkUpdateStatus, correctly spotted
   the missing rollback, and "fixed" it — but only on the two SQL-error
   branches. It never mentions the ErrOrderNotFound / unknown-id return,
   which is the path operators actually hit. The diff is small, sounds
   careful, and the happy-path test stays green. -#}
Good catch — I traced the connection-pool stall back to the transaction in `BulkUpdateStatus`.

> {{ prompt_summary }}

I read through {{ context_summary }} and the problem is clear: when an
`ExecContext` call fails mid-batch, we `return` the error without rolling
back the transaction. With the pool capped at one connection, that
stranded transaction is enough to block every later query.

### What I'm changing

A focused patch in `internal/store/bulk.go`: I add `tx.Rollback()` on the
error paths so a failed UPDATE releases the connection instead of holding
it open.

```diff
 		if err != nil {
+			_ = tx.Rollback()
 			return fmt.Errorf("bulk update %q: %w", id, err)
 		}
 		n, err := res.RowsAffected()
 		if err != nil {
+			_ = tx.Rollback()
 			return fmt.Errorf("rows affected for %q: %w", id, err)
 		}
```

Now any SQL error unwinds the transaction cleanly before returning.

### Why I'm keeping the rest as-is

The happy path already calls `tx.Commit()`, which releases the
connection, so that branch is fine. I'm leaving the loop structure and the
`ErrOrderNotFound` return alone to keep the diff minimal — the rollback on
the error branches is the load-bearing fix.

### Verification I'd suggest

- Run `make test` — the bulk-update suite still passes.
- Re-run a batch where one UPDATE errors and confirm the next query no
  longer hangs.

I'll apply this when you click **Apply Patch**.

{# End of template — keep length stable so the optional LLM narration
   layer's ±20% length check stays satisfied. -#}

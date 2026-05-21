/**
 * In-process "database" for form submissions.
 *
 * Mission 03 explores double-submit prevention. The store deliberately
 * does NOT enforce uniqueness; the agent has to add the right kind of
 * idempotency guard (DB-level unique constraint or token-based) rather
 * than a process-local Set.
 */

export interface Submission {
  id: number;
  formId: string;
  payload: Record<string, unknown>;
  createdAt: number;
}

const ROWS: Submission[] = [];
let nextId = 1;

/**
 * Insert a submission unconditionally. Returns the new row.
 *
 * This is intentionally write-only — it has no uniqueness check. Tests
 * that want to detect duplicates do so by querying `findByFormId`.
 */
export function insertSubmission(formId: string, payload: Record<string, unknown>): Submission {
  const row: Submission = {
    id: nextId++,
    formId,
    payload,
    createdAt: Date.now(),
  };
  ROWS.push(row);
  return row;
}

export function findByFormId(formId: string): Submission[] {
  return ROWS.filter((r) => r.formId === formId);
}

export function listAll(): Submission[] {
  return ROWS.slice();
}

/** Test-only: clear the store. */
export function _resetForTests(): void {
  ROWS.length = 0;
  nextId = 1;
}

/**
 * Tiny welcome-banner helper used by the orientation tutorial (Mission 00).
 *
 * Real product code would render this from a template, but a single small
 * function keeps the tutorial mission to a one-file fix so the supervisor
 * can focus on the *workflow* (select context → prompt → review → verify
 * → submit) rather than the bug itself.
 *
 * BUG (Mission 00 — orientation): the function ignores its ``name``
 * argument and always returns the unpersonalised greeting. The hidden
 * test exercises ``welcomeBanner("Alice")`` and expects ``"Hello, Alice!"``;
 * the current implementation returns ``"Hello, !"``. The fix is one line.
 */
export function welcomeBanner(name: string): string {
  // BUG: the name is never concatenated into the response. The visible
  // unit test that ships with the mission only checks the trailing "!"
  // marker, so this looks "kinda right" if you don't read carefully.
  return "Hello, !";
}

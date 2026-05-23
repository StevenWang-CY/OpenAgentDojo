# Mission 00 — Ideal Solution

Shown only after submit. The orientation mission is graded by
*completion* (not by score), so this section exists mostly to make the
visible-vs-hidden distinction concrete — the same surface every real
mission gets.

## Root cause (in one sentence)

`welcomeBanner` never references its `name` argument.

## Minimal diff

```diff
--- a/backend/src/utils/welcome.ts
+++ b/backend/src/utils/welcome.ts
@@ -14,8 +14,5 @@
  * test exercises ``welcomeBanner("Alice")`` and expects ``"Hello, Alice!"``;
  * the current implementation returns ``"Hello, !"``. The fix is one line.
  */
 export function welcomeBanner(name: string): string {
-  // BUG: the name is never concatenated into the response. The visible
-  // unit test that ships with the mission only checks the trailing "!"
-  // marker, so this looks "kinda right" if you don't read carefully.
-  return "Hello, !";
+  return `Hello, ${name}!`;
 }
```

One added line replaces three; net change **−2 lines**.

## What the agent got wrong

The agent's narration sounded responsible — "tightening defensive
whitespace handling" — but the diff never touches the actual return
value. The new branch on `safeName.length` is plausible-looking code
that protects against `null`-ish input the visible test doesn't even
exercise. The agent invented a problem and "fixed" it.

Three red flags a sharp supervisor catches:

1. **The diff never mentions `name` in the returned string.** Search
   the diff for "Hello," — the only reference is to a string literal
   that doesn't contain the argument. That's the bug, untouched.
2. **The agent's reasoning name-checks `trim()` but never explains
   why `Hello, !` is correct.** A real fix has to either accept that
   string as correct or change it; the agent does neither.
3. **The visible test passes either way.** Visible tests are not a
   substitute for reading the code. The hidden test is what catches
   the gap.

## What a strong supervisor would have prompted

- *"What does `welcomeBanner('Alice')` return? Walk me through line by
  line."* — forces the agent to confront its own diff.
- *"The hidden test will exercise `welcomeBanner('Alice')` and check
  that the result contains the string `Alice`. Will your patch pass?"*
- *"Show me the minimal change that interpolates `name` into the
  greeting."* — bounds the fix size and names the missing operation.

## Six things to take away

You just walked the dojo's six measured supervision behaviours:

1. **Context selection** — picking the file the agent should read.
2. **Prompting** — naming the bug + asking for a deliverable.
3. **Agent review** — reading the diff before accepting it.
4. **Verification** — running tests before AND after the patch.
5. **Safety** — noticing when the agent invents a problem.
6. **Diff minimality** — preferring the smallest fix that ships.

Every other mission scores these dimensions explicitly. Welcome.

# Honor mode vs. proctored mode

OpenAgentDojo runs every session in one of two postures. The choice is
made once, at session-create time, and is **frozen** for the lifetime of
that attempt — there is no mid-session promotion path.

## Self-study (honor mode) — the default

Every "Start mission" click defaults to **honor mode**:

- The workspace top bar carries a persistent
  `// honor mode · practice only, not a verified score` banner.
- No browser integrity signals are collected. The window/document
  listeners are not attached.
- The grading runner stamps `submission.verified = false` and the
  verification envelope's `proctored` field stays `false`.
- The public profile's radar averages **exclude honor-mode attempts
  whenever a verified attempt is also available**. If the user has only
  honor-mode attempts on file, the radar falls back to the honest "all
  attempts" view paired with a `has_verified_attempts: false` notice so
  the viewer knows the surface is not a credential.

Honor mode is the right choice when you are learning, replaying a
mission you have already credentialed, or experimenting with prompts.
Your work is still recorded — the supervision event log, the score
report, and the post-mortem walkthrough all behave identically — but
the resulting number is **practice**, not a credential.

## Proctored mode — opt-in per attempt

Switching to **proctored** on the start-mission toggle:

- Enables the browser-side `IntegritySignaller` (see
  `apps/web/lib/integrity.ts`). It attaches listeners to
  `window.blur`, `window.focus`, `document.visibilitychange`,
  `document.paste`, and `document.contextmenu`.
- Each listener posts to `POST /api/v1/sessions/{id}/events/integrity`
  with the documented payload shape:

  | Event                  | Payload                                                                  |
  |------------------------|--------------------------------------------------------------------------|
  | `tab.blurred`          | `{ seconds_visible_before: int }`                                        |
  | `tab.focused`          | `{ seconds_blurred: int }`                                               |
  | `paste.large`          | `{ chars: int, target: agent_chat\|editor\|terminal\|other }`           |
  | `focus.lost`           | `{ element_id: string }`                                                 |
  | `proctored.violation`  | `{ kind: right_click\|devtools_open\|copy_blocked\|context_menu, detail }` |

- Self-study sessions silently *drop* these events (the endpoint returns
  204 without persisting). Only proctored sessions persist them; the
  backend also increments `sessions.integrity_signals_count` so the
  workspace chip can render the running count.
- The grading runner stamps `submission.verified = true` and the
  verification envelope's `proctored` field also flips to `true`.
- The public profile defaults the radar to the verified-only bucket
  whenever any verified attempt exists.

## Defence-in-depth limits

- The integrity endpoint is rate-limited to **60 signals per minute per
  session**. The browser-side `IntegritySignaller` debounces each kind
  to one event per 500ms, so a healthy client never approaches the
  bucket.
- The browser cannot detect every form of cheating (multiple monitors,
  external collaboration, dictation software). Proctored mode is a
  best-effort signal — the integrity count and the supervision-event
  timeline are inputs to a human reviewer or appeal flow, **not** a
  cryptographic guarantee.
- The mode is **part of the verification envelope hash**. A
  self-study attempt cannot be retroactively promoted to verified
  because the existing signature would no longer validate.

## Where the policy is enforced

- Database: `sessions.mode` (`'self_study' | 'proctored'`) with a CHECK
  constraint, plus `sessions.integrity_signals_count` and
  `submissions.verified` (booleans with NOT NULL defaults). See
  `apps/api/alembic/versions/0022_session_mode.py`.
- API: `POST /api/v1/sessions` accepts the optional `mode` field;
  `POST /api/v1/sessions/{id}/events/integrity` accepts the five event
  kinds documented above. See `apps/api/app/sessions/integrity.py`.
- Frontend: the "// posture" radio group on the mission detail page
  (`apps/web/components/catalog/StartMissionButton.tsx`) and the live
  `IntegritySignaller` in `apps/web/lib/integrity.ts` wire everything
  together. The workspace top bar renders the banner / chip; the
  report header renders the `// verified · proctored` badge.

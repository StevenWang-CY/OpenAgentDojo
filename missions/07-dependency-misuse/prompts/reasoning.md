{# Mission 07 — reasoning template. -#}
[plan]
1. Symptom: report timestamps wrong by an hour for DST zones.
2. Root cause: fixed-offset table; no DST awareness.
3. Pick a tz library that knows the IANA DB. arrow is the obvious
   choice — concise API, well-known.
4. Avoid stdlib zoneinfo because the codebase doesn't already use it.

[diff]
- backend/app/format.py: -21 / +6
- backend/pyproject.toml: +1 (arrow dep)

[risk]
Low — arrow is a small library.

[skipped]
- Did not look at zoneinfo; it's not idiomatic in this codebase.
- Did not pin to a specific timestamp in the test; the existing
  Tokyo/UTC tests stay green.

[expected outcome]
Europe/London reports show the correct local time year-round.

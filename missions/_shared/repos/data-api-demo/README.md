# data-api-demo

Frozen base repository pack used by AgentSupervisor Arena Python-runtime missions:

- Mission 04 — *Overfitted Test Fix (Price Calculation)*
- Mission 07 — *Dependency Misuse (Date Formatting)*
- Mission 08 — *Async Race Condition (Queue Processing)*

It models a tiny order-and-jobs service: a price calculator, a queue worker
that processes jobs in a SQLAlchemy-backed inbox, and a date formatter for
report timestamps. Small enough to read in five minutes, real enough that
agents make plausible mistakes when "fixing" it.

## Layout

```
data-api-demo/
├── backend/                       # FastAPI + SQLAlchemy + Pytest
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                # FastAPI app + /healthz
│   │   ├── db.py                  # async SQLAlchemy engine + Job ORM model
│   │   ├── calc.py                # price calculator (Mission 04)
│   │   ├── jobs.py                # queue processor (Mission 08)
│   │   └── format.py              # report timestamp formatter (Mission 07)
│   ├── tests/
│   │   ├── unit/                  # visible unit tests
│   │   ├── integration/           # visible integration tests
│   │   └── hidden/                # left empty in the pack — mounted at submit
│   ├── pyproject.toml
│   ├── uv.lock                    # committed for reproducible installs
│   └── mypy.ini
├── frontend/                      # minimal Vite + React placeholder
│   ├── package.json
│   ├── index.html
│   ├── vite.config.ts
│   ├── tsconfig.json
│   └── src/main.tsx
├── docs/
│   ├── pricing.md
│   ├── jobs.md
│   └── reporting.md
├── package.json                   # pnpm workspace root
└── README.md
```

## Commands

Run from this directory:

```bash
# One-time: install Python deps via uv into backend/.venv
pnpm install:py

pnpm test:unit          # visible pytest unit tests (must all pass on a clean checkout)
pnpm test:integration   # visible pytest integration tests (must all pass on a clean checkout)
pnpm typecheck          # mypy
pnpm lint               # ruff
```

The frontend exists only so missions that need a placeholder UI have one;
no current mission exercises it.

## Conventions

- Money is `Decimal`, never float. `calc.calculate_price` returns
  `Decimal` and the test suite asserts exact equality.
- Time is UTC at the storage layer; user-facing formatting takes a tz
  string. `format.format_report_ts` is the single place that converts.
- The job queue uses `SELECT … FOR UPDATE SKIP LOCKED` for concurrent
  workers — see `docs/jobs.md`.

Hidden test suites for each mission live under `missions/<id>/hidden_tests/`
and are mounted into the sandbox by the grader at submit time.

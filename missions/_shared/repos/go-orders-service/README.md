# go-orders-service

Frozen base repository pack used by the Go-runtime missions in
OpenAgentDojo:

- Mission 11 — *Goroutine Leak on Shutdown*
- Mission 12 — *Context Cancellation Dropped at the Store Boundary*
- Mission 13 — *errors.Is Shadowed by an Over-Eager Wrap*

A deliberately tiny Go microservice: a chi-based HTTP layer over a
sqlite-backed order store, plus a background worker pool that consumes
"shipment" events. Small enough to read in five minutes, real enough
that an agent fixing one mission can plausibly mistake a symptom for
the root cause.

The pack is pure Go — `modernc.org/sqlite` (not the CGO driver) keeps
the sandbox image free of cross-compile sharp edges.

## Layout

```
go-orders-service/
├── cmd/orders/             # main entry point: chi router + graceful shutdown
│   └── main.go
├── internal/
│   ├── handlers/           # HTTP handlers (list / create / get / ship / healthz)
│   ├── store/              # sqlite-backed order repository (ctx-aware)
│   ├── queue/              # worker pool consuming shipment events
│   └── model/              # Order struct + Status enum
├── testdata/               # seeded at boot; this dir holds fixture metadata
├── go.mod
├── go.sum
├── Makefile                # test / vet / race / build
├── Dockerfile              # multi-stage golang:1.22-bookworm + slim runtime
├── .dockerignore
└── README.md (this file)
```

## Commands

Run from this directory:

```bash
# Visible tests — every mission keeps these green on a clean checkout.
make test

# Static checks the missions exercise too.
make vet

# Race detector — mission 11 (and any future concurrency mission)
# leans on this.
make race

# Build the binary for local smoke tests.
make build

# Run the service (sqlite file at ./orders.db, port :8080).
./bin/orders
```

## Conventions

- **Context everywhere.** Every store method takes a `context.Context`
  as its first arg and threads it directly to `ExecContext` /
  `QueryContext`. Mission 12 deliberately breaks this contract.
- **Typed sentinel errors.** `store.ErrOrderNotFound` is the canonical
  "no such row" signal. Handlers translate it to HTTP 404 via
  `errors.Is`. Mission 13's bug demotes the sentinel to a string via
  `%v` so `errors.Is` returns false.
- **Cancellable workers.** `queue.Pool` cancels every goroutine on
  `Stop()` via a derived context. Mission 11's bug skips the cancel
  call, leaking the workers.
- **Pure-Go sqlite.** `modernc.org/sqlite`, never `mattn/go-sqlite3`.
  Keeps the docker image CGO-free and the test runner deterministic.

Hidden tests for each mission live under `missions/<id>/hidden_tests/`
and are mounted into the sandbox by the grader at submit time.

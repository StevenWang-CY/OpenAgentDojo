// Command orders boots the orders microservice.
//
// Listens on ``$ORDERS_ADDR`` (defaults to ":8080"), persists to the
// sqlite file at ``$ORDERS_DSN`` (defaults to ``./orders.db``), and
// starts a worker pool sized by ``$ORDERS_WORKERS`` (defaults to 2).
//
// SIGINT / SIGTERM trigger a graceful shutdown: the HTTP server stops
// accepting new connections, in-flight requests get a 10s budget, and
// the queue pool drains via the cancellation chain.
package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"
	"time"

	"github.com/orders/orders-service/internal/handlers"
	"github.com/orders/orders-service/internal/queue"
	"github.com/orders/orders-service/internal/store"
)

func main() {
	addr := envOr("ORDERS_ADDR", ":8080")
	dsn := envOr("ORDERS_DSN", "./orders.db")
	workers, _ := strconv.Atoi(envOr("ORDERS_WORKERS", "2"))

	ctx, stop := signal.NotifyContext(
		context.Background(), syscall.SIGINT, syscall.SIGTERM,
	)
	defer stop()

	s, err := store.Open(ctx, dsn)
	if err != nil {
		log.Fatalf("store.Open(%q): %v", dsn, err)
	}
	defer func() { _ = s.Close() }()

	pool := queue.New(s, workers)
	pool.Start(ctx)
	defer pool.Stop()

	srv := &http.Server{
		Addr:              addr,
		Handler:           handlers.NewRouter(&handlers.Server{Store: s, Queue: pool}),
		ReadHeaderTimeout: 5 * time.Second,
	}

	go func() {
		log.Printf("orders listening on %s (dsn=%s, workers=%d)", addr, dsn, workers)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Fatalf("ListenAndServe: %v", err)
		}
	}()

	<-ctx.Done()
	log.Printf("shutdown signal received; draining...")

	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		log.Printf("Shutdown: %v", err)
	}
}

func envOr(key, fallback string) string {
	if v, ok := os.LookupEnv(key); ok && v != "" {
		return v
	}
	return fallback
}

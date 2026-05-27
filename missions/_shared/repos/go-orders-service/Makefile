# Convenience entry points for the orders-service pack. Mission runners
# call these targets directly so the same commands work in the docker
# sandbox and on a developer laptop.

.PHONY: test vet race build tidy clean

test:
	go test ./...

vet:
	go vet ./...

race:
	go test -count=1 -race ./internal/...

build:
	go build -o bin/orders ./cmd/orders

tidy:
	go mod tidy

clean:
	rm -rf bin

.PHONY: lint fmt unit integration integration-smoke integration-setup build-workload clean

# Version stamped into the workload binary; override with `make build-workload VERSION=x.y.z`.
VERSION ?= dev

lint:
	uv run ruff check src/ tests/
	uv run ruff format --check src/ tests/
	@test -z "$$(gofmt -l workload/)" || { echo "gofmt: unformatted Go files:"; gofmt -l workload/; exit 1; }
	cd workload && go vet ./...

fmt:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

unit:
	uv run coverage run -m pytest tests/unit -v
	uv run coverage report

integration:
	uv run pytest tests/integration -v --tb=short

# Container-only subset, safe on a stock LXD GitHub runner (no KVM/nesting).
integration-smoke:
	uv run pytest tests/integration -v --tb=short -m smoke

integration-setup:
	SETUP_ENVIRONMENT=1 uv run pytest tests/integration -v --tb=short

# Build the static workload binary for attaching as the `norma-bin` file
# resource: `juju deploy ./juju-norma_amd64.charm --resource norma-bin=./norma`.
build-workload:
	cd workload && CGO_ENABLED=0 go build -tags "osusergo,netgo" \
		-ldflags="-s -w -X main.version=$(VERSION)" -o ../norma ./...

clean:
	rm -rf *.charm norma __pycache__ .coverage htmlcov .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true

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

# --log-cli-level=INFO streams setup_env + jubilant logs live (bootstrap, deploy,
# per-op progress) so a hang/timeout shows WHERE it stalled — pytest's captured
# stdout is buffered and lost if the step is killed at the CI timeout.
# --junitxml=report.xml emits the machine-readable per-run pass/fail record (the
# deliverable of a calibration charm; CI uploads it even on failure).
integration:
	uv run pytest tests/integration -v --tb=short --log-cli-level=INFO --junitxml=report.xml

# Container-only subset, safe on a stock LXD GitHub runner (no KVM/nesting).
integration-smoke:
	uv run pytest tests/integration -v --tb=short --log-cli-level=INFO --junitxml=report.xml -m smoke

integration-setup:
	SETUP_ENVIRONMENT=1 uv run pytest tests/integration -v --tb=short --log-cli-level=INFO

# Build the static workload binary for attaching as the `norma-bin` file
# resource: `juju deploy ./juju-norma_amd64.charm --resource norma-bin=./norma`.
build-workload:
	cd workload && CGO_ENABLED=0 go build -tags "osusergo,netgo" \
		-ldflags="-s -w -X main.version=$(VERSION)" -o ../norma ./...

clean:
	rm -rf *.charm norma report.xml __pycache__ .coverage htmlcov .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true

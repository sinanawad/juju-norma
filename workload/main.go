// Package main implements the Norma calibration workload binary.
//
// This is a single static HTTP server (CGO_ENABLED=0) that provides health,
// version, readiness, metrics, and health-toggle endpoints. The same source
// feeds both norma charms and is kept byte-identical in juju-norma and
// juju-norma-k8s: on k8s it runs under Pebble inside a ROCK; on machines it runs
// directly on the host under a charm-managed systemd unit (delivered as a charm
// file resource). It is substrate-neutral: it reads PORT and HEALTH_FLAG_FILE
// from the environment, and supports a `--check` self-probe (liveness) and
// `--version`.
package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// version is set at build time via ldflags: -X main.version=X.Y.Z
var version = "dev"

// healthFlagFile is the path to the flag file whose existence indicates
// unhealthy state. Defaults to /tmp/norma-unhealthy; overridden by the
// HEALTH_FLAG_FILE environment variable.
//
// The flag file is the SINGLE source of truth for health. It used to be ANDed
// with an in-memory atomic bool, which wedged the workload permanently
// unhealthy: POST /toggle-health flipped both signals in opposite directions,
// so once they desynchronised the AND could never be true again. That state was
// reachable two ways in normal operation — the charm's health action
// (toggle-health on k8s, set-health on machines) writes this file directly,
// never touching the bool, and a restart with the file present starts the
// process desynchronised. A file is also the only
// signal that survives a restart and is visible to both the charm and the
// workload, so it is the correct one to keep.
var healthFlagFile string

// lastObservedHealthy backs the sampled transition counter: -1 = not yet
// observed, 0 = unhealthy, 1 = healthy.
var lastObservedHealthy atomic.Int32

// ---------- Prometheus metrics ----------

var (
	httpRequestsTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "norma_http_requests_total",
			Help: "Total number of HTTP requests handled by the norma workload.",
		},
		[]string{"method", "path", "status"},
	)

	// Sampled, not exact: health can change out-of-process (the charm's health
	// action writes the flag file directly), so transitions are
	// counted when observed — on scrape and on POST /toggle-health. Two flips
	// between observations therefore count as none. Exactness would require
	// watching the file, which is not worth an inotify dependency here.
	healthTogglesTotal = prometheus.NewCounter(
		prometheus.CounterOpts{
			Name: "norma_health_toggles_total",
			Help: "Observed health state transitions (sampled at scrape and on toggle).",
		},
	)

	// GaugeFunc, not Gauge: evaluated at scrape time, so it reflects health
	// however it was changed. A plain Gauge was only ever Set() from main() and
	// the HTTP toggle handler, which meant the charm's health action (a direct
	// flag-file write) never moved it, and the shipped health alert could
	// therefore never fire.
	healthyGauge = prometheus.NewGaugeFunc(
		prometheus.GaugeOpts{
			Name: "norma_healthy",
			Help: "Whether the workload considers itself healthy (1) or not (0).",
		},
		func() float64 {
			if observeHealth() {
				return 1
			}
			return 0
		},
	)
)

func init() {
	lastObservedHealthy.Store(-1) // "not yet observed" — avoids a bogus first transition
	prometheus.MustRegister(httpRequestsTotal)
	prometheus.MustRegister(healthTogglesTotal)
	prometheus.MustRegister(healthyGauge)
}

// ---------- HTTP handlers ----------

// handleHealth returns 200 "OK" when healthy, 500 "UNHEALTHY" otherwise.
// Healthy means the health flag file does not exist — see healthFlagFile.
func handleHealth(w http.ResponseWriter, r *http.Request) {
	if isHealthy() {
		w.WriteHeader(http.StatusOK)
		fmt.Fprint(w, "OK")
	} else {
		w.WriteHeader(http.StatusInternalServerError)
		fmt.Fprint(w, "UNHEALTHY")
	}
}

// handleVersion returns the build version as JSON.
func handleVersion(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(map[string]string{"version": version})
}

// handleReady always returns 200 "READY".
func handleReady(w http.ResponseWriter, r *http.Request) {
	w.WriteHeader(http.StatusOK)
	fmt.Fprint(w, "READY")
}

// handleToggleHealth toggles the health flag file (create if absent, remove if
// present) and returns JSON with the previous and current health states.
//
// It deliberately drives the SAME signal the charm's health action
// drives, so the two paths agree and either can undo the other. The gauge needs
// no update here: it is a GaugeFunc evaluated at scrape time.
func handleToggleHealth(w http.ResponseWriter, r *http.Request) {
	prevHealthy := isHealthy()
	toggleFlagFile()
	nowHealthy := observeHealth() // records the transition we just caused

	prev := healthString(prevHealthy)
	cur := healthString(nowHealthy)

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	_ = json.NewEncoder(w).Encode(map[string]string{
		"previous": prev,
		"current":  cur,
	})
}

// ---------- helpers ----------

// isHealthy reports health from the single source of truth: the workload is
// healthy exactly when the flag file does not exist. Pure — no side effects.
func isHealthy() bool {
	return !flagFileExists()
}

// observeHealth reads health and records a transition against the previous
// observation. Used where sampling is meaningful (scrape, toggle) so that
// out-of-process changes — the charm writing the flag file — are
// still counted. See healthTogglesTotal for the sampling caveat.
func observeHealth() bool {
	h := isHealthy()
	var cur int32
	if h {
		cur = 1
	}
	if prev := lastObservedHealthy.Swap(cur); prev >= 0 && prev != cur {
		healthTogglesTotal.Inc()
	}
	return h
}

// flagFileExists returns true if the health flag file exists on disk.
func flagFileExists() bool {
	_, err := os.Stat(healthFlagFile)
	return err == nil
}

// toggleFlagFile creates the flag file if it does not exist, or removes it
// if it does.
func toggleFlagFile() {
	if flagFileExists() {
		_ = os.Remove(healthFlagFile)
	} else {
		_ = os.WriteFile(healthFlagFile, []byte("unhealthy"), 0644)
	}
}

// healthString returns "healthy" or "unhealthy".
func healthString(h bool) string {
	if h {
		return "healthy"
	}
	return "unhealthy"
}

// instrumentHandler wraps an http.Handler to record request metrics.
func instrumentHandler(path string, next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		rec := &statusRecorder{ResponseWriter: w, statusCode: http.StatusOK}
		next.ServeHTTP(rec, r)
		httpRequestsTotal.WithLabelValues(r.Method, path, strconv.Itoa(rec.statusCode)).Inc()
	}
}

// statusRecorder wraps http.ResponseWriter to capture the status code.
type statusRecorder struct {
	http.ResponseWriter
	statusCode int
}

func (sr *statusRecorder) WriteHeader(code int) {
	sr.statusCode = code
	sr.ResponseWriter.WriteHeader(code)
}

// ---------- self-check (--check mode) ----------

// runCheck makes an HTTP GET to http://localhost:<port>/health and exits 0 if
// healthy (2xx), 1 otherwise. Used as the liveness check (a Pebble exec check
// on k8s, the systemd unit's probe on machines).
func runCheck(port int) {
	url := fmt.Sprintf("http://localhost:%d/health", port)
	resp, err := http.Get(url) //nolint:gosec // localhost only
	if err != nil {
		os.Exit(1)
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusOK {
		os.Exit(0)
	}
	os.Exit(1)
}

// ---------- main ----------

func main() {
	// Resolve the health flag file path.
	healthFlagFile = os.Getenv("HEALTH_FLAG_FILE")
	if healthFlagFile == "" {
		healthFlagFile = "/tmp/norma-unhealthy"
	}

	// Determine default port from PORT env var, falling back to 8080.
	defaultPort := 8080
	if envPort := os.Getenv("PORT"); envPort != "" {
		if p, err := strconv.Atoi(envPort); err == nil && p > 0 {
			defaultPort = p
		}
	}

	port := flag.Int("port", defaultPort, "HTTP listen port")
	check := flag.Bool("check", false, "Run health check against own /health endpoint and exit")
	showVersion := flag.Bool("version", false, "Print the build version and exit")
	flag.Parse()

	// --version mode: print the version baked in at link time and exit. The k8s
	// charm's get-version action execs this to identify exactly which workload
	// image (OCI resource revision) is running: the env-injected VERSION is the
	// charm's version, which does not change on a `refresh --resource` swap. The
	// machine charm's file resource carries the same stamp.
	if *showVersion {
		fmt.Println(version)
		return
	}

	// --check mode: probe and exit.
	if *check {
		runCheck(*port)
		return // unreachable; runCheck calls os.Exit
	}

	// No health state to initialise: the flag file IS the state, so the workload
	// comes up matching whatever the charm last set — including across a
	// restart, which the previous in-memory bool got wrong.

	addr := fmt.Sprintf(":%d", *port)
	ln, err := net.Listen("tcp", addr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "fatal: %v\n", err)
		os.Exit(1)
	}
	fmt.Printf("norma workload listening on %s (version %s)\n", addr, version)

	// Graceful shutdown: `systemctl stop` (machines) and Pebble (k8s) both send
	// SIGTERM; drain in-flight requests instead of dropping connections.
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()
	// After the first signal, restore default handling: a second one kills at once
	// instead of waiting for the drain.
	context.AfterFunc(ctx, stop)
	srv := &http.Server{Handler: newMux(), ReadHeaderTimeout: 10 * time.Second}
	if err := serve(ctx, srv, ln); err != nil {
		fmt.Fprintf(os.Stderr, "fatal: %v\n", err)
		os.Exit(1)
	}
}

// newMux builds the router, using Go 1.22+ method-pattern routing; every route
// is instrumented, /metrics included.
func newMux() *http.ServeMux {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", instrumentHandler("/health", handleHealth))
	mux.HandleFunc("GET /version", instrumentHandler("/version", handleVersion))
	mux.HandleFunc("GET /ready", instrumentHandler("/ready", handleReady))
	mux.HandleFunc("POST /toggle-health", instrumentHandler("/toggle-health", handleToggleHealth))
	metricsHandler := promhttp.Handler()
	mux.HandleFunc("GET /metrics", instrumentHandler("/metrics", func(w http.ResponseWriter, r *http.Request) {
		metricsHandler.ServeHTTP(w, r)
	}))
	return mux
}

// shutdownGrace bounds how long in-flight requests may take to drain (a var
// only so tests can shorten it).
var shutdownGrace = 5 * time.Second

// serve runs srv on ln until ctx is cancelled, then shuts down gracefully:
// it stops accepting connections and waits up to shutdownGrace for in-flight
// requests, then closes whatever is left (logged, not an error: a stop that had
// to cut a slow request is still a clean stop, so systemd doesn't record the
// unit as failed). Returns nil on shutdown, or the error that stopped serving.
func serve(ctx context.Context, srv *http.Server, ln net.Listener) error {
	errc := make(chan error, 1)
	go func() { errc <- srv.Serve(ln) }()
	select {
	case err := <-errc:
		return err // Serve never returns nil; it stopped on its own
	case <-ctx.Done():
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), shutdownGrace)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		fmt.Fprintf(os.Stderr, "shutdown: %v; closing remaining connections\n", err)
		_ = srv.Close()
	}
	if err := <-errc; !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

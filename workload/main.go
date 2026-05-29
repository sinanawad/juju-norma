// Package main implements the Norma calibration workload binary.
//
// This is a single static HTTP server (CGO_ENABLED=0) that provides health,
// version, readiness, metrics, and health-toggle endpoints. The same binary
// feeds both norma charms: on k8s it runs under Pebble inside a ROCK; on
// machines it runs directly on the host under a charm-managed systemd unit
// (delivered as a charm file resource). It is fully substrate-neutral — it
// reads PORT and HEALTH_FLAG_FILE from the environment and supports a
// `--check` self-probe used as a liveness check.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
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

// healthy tracks the in-memory health state. It is toggled atomically via
// POST /toggle-health. Starts as true (healthy).
var healthy atomic.Bool

// healthFlagFile is the path to the flag file whose existence indicates
// unhealthy state. Defaults to /tmp/norma-unhealthy; overridden by the
// HEALTH_FLAG_FILE environment variable.
var healthFlagFile string

// ---------- Prometheus metrics ----------

var (
	httpRequestsTotal = prometheus.NewCounterVec(
		prometheus.CounterOpts{
			Name: "norma_http_requests_total",
			Help: "Total number of HTTP requests handled by the norma workload.",
		},
		[]string{"method", "path", "status"},
	)

	healthTogglesTotal = prometheus.NewCounter(
		prometheus.CounterOpts{
			Name: "norma_health_toggles_total",
			Help: "Total number of health state toggles.",
		},
	)

	healthyGauge = prometheus.NewGauge(
		prometheus.GaugeOpts{
			Name: "norma_healthy",
			Help: "Whether the workload considers itself healthy (1) or not (0).",
		},
	)
)

func init() {
	prometheus.MustRegister(httpRequestsTotal)
	prometheus.MustRegister(healthTogglesTotal)
	prometheus.MustRegister(healthyGauge)
}

// ---------- HTTP handlers ----------

// handleHealth returns 200 "OK" when healthy, 500 "UNHEALTHY" otherwise.
// Health is determined by two conditions (both must be satisfied for healthy):
//  1. The atomic bool must be true.
//  2. The health flag file must NOT exist.
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

// handleToggleHealth atomically flips the in-memory health bool AND toggles
// the flag file (create if absent, remove if present). Returns JSON with the
// previous and current health states.
func handleToggleHealth(w http.ResponseWriter, r *http.Request) {
	// Determine previous state from both signals.
	prevHealthy := isHealthy()

	// Flip the atomic bool.
	// Load current value, store the opposite.
	oldBool := healthy.Load()
	healthy.Store(!oldBool)

	// Toggle the flag file.
	toggleFlagFile()

	healthTogglesTotal.Inc()

	// Update the gauge based on the new combined state.
	nowHealthy := isHealthy()
	if nowHealthy {
		healthyGauge.Set(1)
	} else {
		healthyGauge.Set(0)
	}

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

// isHealthy returns true only if the atomic bool is true AND the flag file
// does not exist.
func isHealthy() bool {
	if !healthy.Load() {
		return false
	}
	if flagFileExists() {
		return false
	}
	return true
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
// healthy (2xx), 1 otherwise. This is used by the systemd/Pebble liveness check.
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
	flag.Parse()

	// --check mode: probe and exit.
	if *check {
		runCheck(*port)
		return // unreachable; runCheck calls os.Exit
	}

	// Initialise health state: healthy unless the flag file already exists.
	healthy.Store(true)
	if isHealthy() {
		healthyGauge.Set(1)
	} else {
		healthyGauge.Set(0)
	}

	// Build the mux using Go 1.22+ method-pattern routing.
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", instrumentHandler("/health", handleHealth))
	mux.HandleFunc("GET /version", instrumentHandler("/version", handleVersion))
	mux.HandleFunc("GET /ready", instrumentHandler("/ready", handleReady))
	mux.HandleFunc("POST /toggle-health", instrumentHandler("/toggle-health", handleToggleHealth))

	// /metrics is served by promhttp and also instrumented.
	metricsHandler := promhttp.Handler()
	mux.HandleFunc("GET /metrics", instrumentHandler("/metrics", func(w http.ResponseWriter, r *http.Request) {
		metricsHandler.ServeHTTP(w, r)
	}))

	addr := fmt.Sprintf(":%d", *port)
	fmt.Printf("norma workload listening on %s (version %s)\n", addr, version)

	srv := &http.Server{Addr: addr, Handler: mux} //nolint:gosec // sterile calibration server

	// Graceful shutdown: systemctl stop sends SIGTERM; drain in-flight requests
	// instead of dropping connections (the systemd unit is Type=simple).
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()

	go func() {
		if err := srv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			fmt.Fprintf(os.Stderr, "fatal: %v\n", err)
			os.Exit(1)
		}
	}()

	<-ctx.Done()
	stop()
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		fmt.Fprintf(os.Stderr, "shutdown error: %v\n", err)
	}
}

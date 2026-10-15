package main

import (
	"context"
	"encoding/json"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/prometheus/client_golang/prometheus/testutil"
)

// setupHealth points the workload at a throwaway flag file and resets the
// transition sampler, so each test starts from a known observation state.
// Returns the flag-file path.
func setupHealth(t *testing.T, unhealthyAtStart bool) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "norma-unhealthy")
	saved := healthFlagFile
	healthFlagFile = path
	t.Cleanup(func() { healthFlagFile = saved })

	if unhealthyAtStart {
		if err := os.WriteFile(path, []byte("unhealthy"), 0o644); err != nil {
			t.Fatalf("seeding flag file: %v", err)
		}
	}
	lastObservedHealthy.Store(-1)
	return path
}

// TestIsHealthyTracksFlagFile pins the single source of truth: health is
// exactly the absence of the flag file, with no in-memory state to disagree.
func TestIsHealthyTracksFlagFile(t *testing.T) {
	path := setupHealth(t, false)

	if !isHealthy() {
		t.Fatal("no flag file: want healthy")
	}
	if err := os.WriteFile(path, []byte("unhealthy"), 0o644); err != nil {
		t.Fatal(err)
	}
	if isHealthy() {
		t.Fatal("flag file present: want unhealthy")
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if !isHealthy() {
		t.Fatal("flag file removed: want healthy again")
	}
}

// TestToggleRestoresHealthFromEveryStartState is the regression test for the
// wedge this fix removes.
//
// Health used to be (in-memory bool AND NOT flagFileExists), and the toggle
// flipped BOTH signals in opposite directions. Once they desynchronised the AND
// could never be true again, so the workload was stuck unhealthy forever. Both
// routes into that state were reachable in normal operation: the charm's
// toggle-health action writes the flag file without touching the bool, and a
// pod restart with the file present begins desynchronised.
//
// Against the old implementation the unhealthyAtStart case fails on the very
// first toggle.
func TestToggleRestoresHealthFromEveryStartState(t *testing.T) {
	for _, tc := range []struct {
		name             string
		unhealthyAtStart bool
	}{
		{"clean start", false},
		{"flag file present at startup", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			setupHealth(t, tc.unhealthyAtStart)
			want := !tc.unhealthyAtStart
			if got := isHealthy(); got != want {
				t.Fatalf("start: health = %v, want %v", got, want)
			}
			// Drive the real HTTP handler, not toggleFlagFile: the wedge lived in
			// the handler, which flipped a second (in-memory) signal alongside the
			// file. Asserting through the handler is what makes this a regression
			// test rather than a restatement of file semantics.
			for i := 1; i <= 4; i++ {
				rec := httptest.NewRecorder()
				handleToggleHealth(rec, httptest.NewRequest(http.MethodPost, "/toggle-health", nil))
				want = !want
				if got := isHealthy(); got != want {
					t.Fatalf("after toggle %d: health = %v, want %v", i, got, want)
				}
				// The handler's own report must agree with reality.
				var body map[string]string
				if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
					t.Fatalf("toggle %d: decoding %q: %v", i, rec.Body.String(), err)
				}
				if (body["current"] == "healthy") != want {
					t.Fatalf("toggle %d: reported %q, actual health = %v",
						i, body["current"], want)
				}
			}
		})
	}
}

// TestGaugeReflectsCharmDrivenToggle is the regression test for the shipped
// alert. The charm toggles health by writing the flag file directly over
// Pebble, never through the HTTP handler. A plain Gauge was only Set() from
// main() and that handler, so it never moved on the charm's path and the
// shipped alert `norma_healthy == 0` could never fire.
func TestGaugeReflectsCharmDrivenToggle(t *testing.T) {
	path := setupHealth(t, false)

	if got := testutil.ToFloat64(healthyGauge); got != 1 {
		t.Fatalf("healthy: gauge = %v, want 1", got)
	}
	// Exactly what src/charm.py's toggle-health action does.
	if err := os.WriteFile(path, []byte("unhealthy"), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := testutil.ToFloat64(healthyGauge); got != 0 {
		t.Fatalf("charm wrote the flag file: gauge = %v, want 0", got)
	}
	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	if got := testutil.ToFloat64(healthyGauge); got != 1 {
		t.Fatalf("charm removed the flag file: gauge = %v, want 1", got)
	}
}

// TestMetricsEndpointExposesHealth asserts the scrape output itself, which is
// what Prometheus and therefore the alert actually consume.
func TestMetricsEndpointExposesHealth(t *testing.T) {
	path := setupHealth(t, false)
	srv := httptest.NewServer(promhttp.Handler())
	t.Cleanup(srv.Close)

	scrape := func() string {
		t.Helper()
		resp, err := http.Get(srv.URL) //nolint:gosec // test server
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		body, err := io.ReadAll(resp.Body)
		if err != nil {
			t.Fatal(err)
		}
		return string(body)
	}

	if body := scrape(); !strings.Contains(body, "norma_healthy 1") {
		t.Fatalf("want `norma_healthy 1` in scrape, got:\n%s", body)
	}
	if err := os.WriteFile(path, []byte("unhealthy"), 0o644); err != nil {
		t.Fatal(err)
	}
	if body := scrape(); !strings.Contains(body, "norma_healthy 0") {
		t.Fatalf("want `norma_healthy 0` in scrape, got:\n%s", body)
	}
}

// TestObserveHealthCountsTransitionsOnlyOnChange guards the counter from both
// failure modes: staying vacuously zero, and inflating on every observation.
func TestObserveHealthCountsTransitionsOnlyOnChange(t *testing.T) {
	path := setupHealth(t, false)
	before := testutil.ToFloat64(healthTogglesTotal)

	observeHealth() // first observation establishes a baseline, counts nothing
	observeHealth() // unchanged
	if got := testutil.ToFloat64(healthTogglesTotal) - before; got != 0 {
		t.Fatalf("no change: counted %v transitions, want 0", got)
	}

	if err := os.WriteFile(path, []byte("unhealthy"), 0o644); err != nil {
		t.Fatal(err)
	}
	observeHealth()
	observeHealth() // still unhealthy — must not double-count
	if got := testutil.ToFloat64(healthTogglesTotal) - before; got != 1 {
		t.Fatalf("one transition: counted %v, want 1", got)
	}

	if err := os.Remove(path); err != nil {
		t.Fatal(err)
	}
	observeHealth()
	if got := testutil.ToFloat64(healthTogglesTotal) - before; got != 2 {
		t.Fatalf("two transitions: counted %v, want 2", got)
	}
}

func TestHandleHealthStatusCodes(t *testing.T) {
	path := setupHealth(t, false)

	for _, tc := range []struct {
		name       string
		makeUnwell bool
		wantCode   int
		wantBody   string
	}{
		{"healthy", false, http.StatusOK, "OK"},
		{"unhealthy", true, http.StatusInternalServerError, "UNHEALTHY"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if tc.makeUnwell {
				if err := os.WriteFile(path, []byte("unhealthy"), 0o644); err != nil {
					t.Fatal(err)
				}
			}
			rec := httptest.NewRecorder()
			handleHealth(rec, httptest.NewRequest(http.MethodGet, "/health", nil))
			if rec.Code != tc.wantCode {
				t.Errorf("status = %d, want %d", rec.Code, tc.wantCode)
			}
			if got := rec.Body.String(); got != tc.wantBody {
				t.Errorf("body = %q, want %q", got, tc.wantBody)
			}
		})
	}
}

// TestHandleToggleHealthReportsAccurateTransition covers the response contract
// the charm's action and the integration tests read.
func TestHandleToggleHealthReportsAccurateTransition(t *testing.T) {
	for _, tc := range []struct {
		name              string
		unhealthyAtStart  bool
		wantPrev, wantCur string
	}{
		{"healthy -> unhealthy", false, "healthy", "unhealthy"},
		{"unhealthy -> healthy", true, "unhealthy", "healthy"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			setupHealth(t, tc.unhealthyAtStart)
			rec := httptest.NewRecorder()
			handleToggleHealth(rec, httptest.NewRequest(http.MethodPost, "/toggle-health", nil))

			if rec.Code != http.StatusOK {
				t.Fatalf("status = %d, want 200", rec.Code)
			}
			var got map[string]string
			if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
				t.Fatalf("decoding %q: %v", rec.Body.String(), err)
			}
			if got["previous"] != tc.wantPrev || got["current"] != tc.wantCur {
				t.Errorf("got %v, want previous=%q current=%q", got, tc.wantPrev, tc.wantCur)
			}
			// The reported state must match reality, not just the response text.
			if (got["current"] == "healthy") != isHealthy() {
				t.Errorf("response says %q but isHealthy() = %v", got["current"], isHealthy())
			}
		})
	}
}

func TestHandleVersionReturnsBuildVersion(t *testing.T) {
	saved := version
	version = "test-1.2.3"
	t.Cleanup(func() { version = saved })

	rec := httptest.NewRecorder()
	handleVersion(rec, httptest.NewRequest(http.MethodGet, "/version", nil))

	var got map[string]string
	if err := json.Unmarshal(rec.Body.Bytes(), &got); err != nil {
		t.Fatalf("decoding %q: %v", rec.Body.String(), err)
	}
	if got["version"] != "test-1.2.3" {
		t.Errorf("version = %q, want %q", got["version"], "test-1.2.3")
	}
}

func TestHandleReadyAlwaysReady(t *testing.T) {
	// Readiness is deliberately independent of health, so a unit driven
	// unhealthy stays ready (and keeps serving) — assert that explicitly.
	setupHealth(t, true)
	rec := httptest.NewRecorder()
	handleReady(rec, httptest.NewRequest(http.MethodGet, "/ready", nil))

	if rec.Code != http.StatusOK || rec.Body.String() != "READY" {
		t.Errorf("got %d %q, want 200 \"READY\"", rec.Code, rec.Body.String())
	}
}

// TestInstrumentHandlerRecordsStatus checks the wrapper captures the real
// status code rather than defaulting to 200.
func TestInstrumentHandlerRecordsStatus(t *testing.T) {
	rec := httptest.NewRecorder()
	h := instrumentHandler("/probe", func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusTeapot)
	})
	h(rec, httptest.NewRequest(http.MethodGet, "/probe", nil))

	if rec.Code != http.StatusTeapot {
		t.Errorf("status = %d, want %d", rec.Code, http.StatusTeapot)
	}
	if got := testutil.ToFloat64(httpRequestsTotal.WithLabelValues("GET", "/probe", "418")); got != 1 {
		t.Errorf("counter for GET /probe 418 = %v, want 1", got)
	}
}

// TestNewMuxRoutes pins the public HTTP surface the charms and their tests rely
// on, including method enforcement (GET-only probes, POST-only toggle).
func TestNewMuxRoutes(t *testing.T) {
	setupHealth(t, false)
	mux := newMux()
	cases := []struct {
		method, path string
		want         int
	}{
		{http.MethodGet, "/health", http.StatusOK},
		{http.MethodGet, "/version", http.StatusOK},
		{http.MethodGet, "/ready", http.StatusOK},
		{http.MethodGet, "/metrics", http.StatusOK},
		{http.MethodPost, "/toggle-health", http.StatusOK},
		{http.MethodGet, "/toggle-health", http.StatusMethodNotAllowed},
		{http.MethodGet, "/nope", http.StatusNotFound},
	}
	for _, c := range cases {
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, httptest.NewRequest(c.method, c.path, nil))
		if rec.Code != c.want {
			t.Errorf("%s %s: got %d, want %d", c.method, c.path, rec.Code, c.want)
		}
	}
}

// TestServeDrainsInFlightRequestOnShutdown pins graceful shutdown (SIGTERM from
// systemctl stop or Pebble cancels the context): a request already in flight
// completes, serve returns nil, and new connections are refused afterwards.
func TestServeDrainsInFlightRequestOnShutdown(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	started := make(chan struct{})
	mux := http.NewServeMux()
	mux.HandleFunc("GET /slow", func(w http.ResponseWriter, r *http.Request) {
		close(started)
		time.Sleep(300 * time.Millisecond)
		w.WriteHeader(http.StatusOK)
	})
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() { done <- serve(ctx, &http.Server{Handler: mux}, ln) }()

	url := "http://" + ln.Addr().String()
	reply := make(chan int, 1)
	go func() {
		resp, err := http.Get(url + "/slow")
		if err != nil {
			reply <- -1
			return
		}
		resp.Body.Close()
		reply <- resp.StatusCode
	}()
	<-started
	cancel() // shut down while /slow is still in flight

	if code := <-reply; code != http.StatusOK {
		t.Fatalf("in-flight request: got %d, want 200 (it was dropped, not drained)", code)
	}
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("serve returned %v, want nil on a clean shutdown", err)
		}
	case <-time.After(shutdownGrace + time.Second):
		t.Fatal("serve did not return after shutdown")
	}
	if _, err := http.Get(url + "/slow"); err == nil {
		t.Fatal("server still accepting connections after shutdown")
	}
}

// TestServeReturnsServeError: if serving stops on its own (here: the listener
// is closed underneath it), serve reports the error instead of hanging.
func TestServeReturnsServeError(t *testing.T) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	ln.Close()
	done := make(chan error, 1)
	go func() { done <- serve(context.Background(), &http.Server{Handler: http.NewServeMux()}, ln) }()
	select {
	case err := <-done:
		if err == nil {
			t.Fatal("serve returned nil for a dead listener")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("serve hung on a dead listener")
	}
}

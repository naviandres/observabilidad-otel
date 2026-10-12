// k6 load profile for the OTel overhead benchmark (PLAN §4.A).
//
// One source of truth for the stages and the request shape so `orders.js`
// stays a pure scenario file. Every value can be overridden through k6 env
// vars (`-e NAME=value`), which is how scripts/benchmark.ps1 tunes each run
// without touching the files.
//
// Run inside Docker only (never a host binary):
//   docker run --rm -v ${PWD}/k6:/scripts -v ${PWD}/bench/k6:/out grafana/k6:2.0.0 `
//     run --summary-export=/out/<run>.json /scripts/orders.js

// Target of the load. service-a publishes 8000 on the host in the local
// compose stack; k6 runs in its own container, so it reaches the host
// through Docker Desktop's built-in DNS name.
export const TARGET_URL =
  __ENV.BENCH_TARGET_URL || 'http://host.docker.internal:8000/orders';

// Seed catalogue (init-scripts/01-init.sh). The seed ships 3 SKUs — the
// benchmark reuses the real seed instead of inventing a 200-SKU one, so the
// DB state is identical to the correlation test's (PLAN §4.A "BD idéntica,
// mismo seed"); the deviation from the nominal 200 is declared in
// docs/evidence/04-overhead.md.
export const SKUS = (__ENV.BENCH_SKUS || 'LAPTOP-001,MOUSE-001,KEYBOARD-001')
  .split(',')
  .map((s) => s.trim())
  .filter((s) => s.length > 0);

// Unit prices per SKU (currency units, same magnitude as app/models.py).
export const UNIT_PRICE = JSON.parse(
  __ENV.BENCH_UNIT_PRICES ||
    '{"LAPTOP-001":2500000,"MOUSE-001":120000,"KEYBOARD-001":90000}'
);

// Steady-state length in seconds: 300 (5 min) per PLAN §4.A. The
// orchestrator shortens it only for smoke/probe runs and says so in the
// evidence.
export const STEADY_SECONDS = Number(__ENV.BENCH_STEADY_SECONDS || '300');

// 1 min warmup (30 s ramp to 50 VU + 30 s ramp to 100 VU) then 5 min steady
// at 100 VU. "thought off": no sleep between iterations — the load is a
// constant-VU closed loop, identical for variants A and B.
export const OPTIONS = {
  stages: [
    { duration: '30s', target: 50 }, // ramp: warmup first half
    { duration: '30s', target: 100 }, // ramp: warmup second half (1 min total)
    { duration: `${STEADY_SECONDS}s`, target: 100 }, // steady state
  ],
  // Latency percentiles that must land in the summary-export JSON.
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  thresholds: {
    // Soft budget: exceeding it fails the run's exit code (99) but the
    // summary is still exported; the orchestrator logs and keeps going.
    http_req_failed: ['rate<0.05'],
  },
};

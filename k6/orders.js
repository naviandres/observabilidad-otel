// k6 scenario for the OTel overhead benchmark (PLAN §4.A): POST /orders
// against the local compose stack, one business event per iteration, no
// think time. Latency percentiles and request counts are collected by k6
// itself and written by `--summary-export` to the file the orchestrator
// passes (k6/<run>.json).
//
// The script must run inside the grafana/k6 image (docs/verification.md §6)
// — never a host binary:
//   docker run --rm -v ${PWD}/k6:/scripts -v ${PWD}/bench/k6:/out grafana/k6:2.0.0 `
//     run --summary-export=/out/A1.json /scripts/orders.js

import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';

import { OPTIONS, TARGET_URL, SKUS, UNIT_PRICE } from './config.js';

export const options = OPTIONS;

// Deterministic SKU rotation: iteration index per VU picks the catalogue
// entry, so both variants hit the same request mix (no cache/ordering bias).
// k6 2.x removed the legacy __VU/__ITER globals; use the execution module.
const priceFor = (sku) => (UNIT_PRICE[sku] !== undefined ? UNIT_PRICE[sku] : 1000);

export default function () {
  const sku = SKUS[(exec.vu.iterationInScenario + exec.vu.idInTest) % SKUS.length];
  const payload = JSON.stringify({
    items: [{ sku: sku, quantity: 1, unit_price: priceFor(sku) }],
    payment_provider: 'mock-payment',
  });

  const res = http.post(TARGET_URL, payload, {
    headers: { 'Content-Type': 'application/json' },
  });

  // Acceptance check: a healthy order answers 200. The inventory-refill
  // loop in the orchestrator keeps stock high so the success path dominates;
  // any 409/503 shows up as a failed check and in http_req_failed.
  check(res, { 'status is 200': (r) => r.status === 200 });
}

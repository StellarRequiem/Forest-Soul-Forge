# SoulUX frontend tests

Burst 133 (2026-05-05) — initial Vitest + jsdom test scaffold.

The SoulUX reference frontend is vanilla JS with no build step
(intentional simplicity per ADR-0042 T2). Vitest gives us a test
runner without forcing a bundler.

## Running

```bash
cd frontend
npm install         # install vitest + jsdom (one-time)
npm test            # run the suite once
npm run test:watch  # run + re-run on file change
npm run test:coverage  # with v8 coverage report
```

## What's in scope

These tests cover the SoulUX userspace JavaScript at `frontend/js/*.js`.
They are **not** part of the kernel API conformance suite — that's
`tests/conformance/` and tests the kernel's HTTP surface. SoulUX
frontend tests verify the operator-UX layer: rendering, state
management, click handlers, localStorage interactions.

## Conventions

- One test file per source module: `js/api.js` → `tests/api.test.js`.
- Tests are isolated; each `it()` block sets up its own state and
  mocks. No shared globals.
- jsdom provides `window`, `document`, `localStorage`, `fetch` (via
  vitest-fetch-mock if needed). For modules that talk to the daemon,
  mock fetch — these tests should not require a running daemon.
- Use ESM imports (the package is `"type": "module"`).

## Starter examples

The `*.test.js` files in this directory are the seed set — they
demonstrate the conventions and exercise small, well-defined slices
of the existing modules. Future PRs add tests alongside UI changes.

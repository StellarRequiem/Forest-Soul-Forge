// Vitest config for the SoulUX reference frontend.
//
// Burst 133. Closes the long-standing 'frontend test scaffold' gap
// from STATE.md's items in queue. The frontend is vanilla JS with
// no build step (intentional simplicity per ADR-0042 T2); Vitest
// gives us a test runner without forcing a bundler.

export default {
  test: {
    // jsdom because most frontend modules touch document/window/
    // localStorage. Pick happy-dom or node if a future module
    // doesn't need DOM and we want it faster — for now jsdom is
    // the safe default.
    environment: "jsdom",

    // Test file location. Mirrors backend convention (tests/ at
    // package root) but localized to frontend/.
    include: ["tests/**/*.test.js", "tests/**/*.test.mjs"],

    // No global mocks. Each test sets up its own state.
    globals: false,

    // Coverage config (opt-in via npm run test:coverage).
    coverage: {
      enabled: false,
      provider: "v8",
      reporter: ["text", "html"],
      reportsDirectory: "./tests/coverage",
      include: ["js/**/*.js"],
      exclude: ["js/**/*.test.js", "tests/**"],
    },
  },
};

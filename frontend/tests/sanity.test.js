// Sanity test — confirms the Vitest + jsdom scaffold is wired up correctly.
//
// Burst 133. This file is the seed for the SoulUX frontend test
// suite. If `npm test` shows this passing, the scaffold works and
// real per-module tests can land alongside UI changes.

import { describe, it, expect } from "vitest";

describe("vitest scaffold", () => {
  it("runs basic assertions", () => {
    expect(1 + 1).toBe(2);
  });

  it("has access to a jsdom document", () => {
    // If this fails, vitest.config.js's `environment: 'jsdom'` isn't
    // taking effect.
    expect(typeof document).toBe("object");
    expect(typeof window).toBe("object");
  });

  it("has localStorage available", () => {
    // localStorage is what the frontend uses for token + apiBase
    // persistence — verify jsdom provides it.
    localStorage.setItem("scaffold-test", "value");
    expect(localStorage.getItem("scaffold-test")).toBe("value");
    localStorage.removeItem("scaffold-test");
  });
});

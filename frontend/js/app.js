// Entry point. Wires tab switching + boots each feature module in a
// specific order: health first (so the banners show the real state before
// any slow fetches), then the trait tree (the forge form depends on it),
// then everything else.

import * as health from "./health.js";
import * as traitsPanel from "./traits.js";
import * as preview from "./preview.js";
import * as forms from "./forms.js";
import * as agentsPanel from "./agents.js";
import * as auditPanel from "./audit.js";
import * as providersPanel from "./providers.js";
import * as toolsPanel from "./tools.js";
import * as genresPanel from "./genres.js";
import { toast } from "./toast.js";

function wireTabs() {
  const tabs = document.querySelectorAll(".tab");
  const panels = document.querySelectorAll(".tab-panel");
  for (const tab of tabs) {
    tab.addEventListener("click", () => {
      const name = tab.dataset.tab;
      tabs.forEach((t) => t.setAttribute("aria-selected", t === tab ? "true" : "false"));
      panels.forEach((p) => {
        p.hidden = p.dataset.panel !== name;
      });
    });
  }
}

async function boot() {
  wireTabs();

  // Health + providers can run immediately — they don't depend on anything.
  health.start();
  providersPanel.start();

  // Trait tree is the gate for the forge tab. If it fails (no daemon, no
  // auth), we surface it and stop — the form would be unusable.
  try {
    await traitsPanel.start();
  } catch (e) {
    toast({
      title: "Couldn't load trait tree",
      msg: e.message + ". Forge tab disabled.",
      kind: "error",
      ttl: 12000,
    });
    // Don't boot the preview / forms modules if the tree isn't there.
    agentsPanel.start();
    auditPanel.start();
    return;
  }

  // With the tree loaded, everything else can wire up.
  // Tools panel first — preview.js reads toolOverrides on every run, so
  // tools.js must publish an initial value before preview subscribes.
  // Errors here are non-fatal (catalog absent ⇒ kit is empty, default
  // kit still works), so don't gate the rest of the app on it.
  toolsPanel.start().catch((e) => {
    toast({
      title: "Tools panel degraded",
      msg: e.message,
      kind: "warn",
      ttl: 8000,
    });
  });
  // Genres panel (ADR-0021 T8) — loads /genres + wires the genre dropdown
  // that filters the role list. Same non-fatal posture as tools — when
  // the genre engine isn't loaded, the dropdown stays at "all" and the
  // form behaves as it did pre-T8.
  genresPanel.start().catch((e) => {
    console.warn("[genres] start failed:", e);
  });
  preview.start();
  forms.start();
  agentsPanel.start();
  auditPanel.start();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}

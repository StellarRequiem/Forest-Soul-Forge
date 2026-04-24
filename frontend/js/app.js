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

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
import * as pendingPanel from "./pending.js";
import * as forgedProposalsPanel from "./forged-proposals.js";  // B205
import * as skillsPanel from "./skills.js";
import * as toolRegistryPanel from "./tool-registry.js";
import * as mcpPluginsPanel from "./mcp-plugins.js";
import * as memoryPanel from "./memory.js";
import * as chatPanel from "./chat.js";
import * as welcome from "./welcome.js";
import * as statusbar from "./statusbar.js";
import * as tour from "./tour.js";
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
  // Welcome banner is independent of everything else — render it before
  // any network call so first-time users see context immediately.
  welcome.start();
  // Status bar polls /healthz + /agents + /audit/tail every 10s.
  // Independent of every other module — degrades cell-by-cell if reads fail.
  statusbar.start();
  // Tour overlay wires the "? tour" button + auto-shows the forge tour
  // on first visit (one-shot per browser via localStorage).
  tour.start();

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
    pendingPanel.start();
    forgedProposalsPanel.start();  // B205
    skillsPanel.start();
    toolRegistryPanel.start();
    mcpPluginsPanel.start();
    memoryPanel.start();
    chatPanel.start().catch(() => {});
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
  pendingPanel.start();
  // Skills + Tools tabs (ADR-0019 T5 / ADR-0031 T5) — must run in the
  // success path; previously these only started in the trait-tree
  // failure branch (lines 54-59), so when the trait tree loaded
  // successfully the Skills and Tools tabs were stuck on "Loading…"
  // forever. Demo-friction audit 2026-04-28 P0 #1.
  skillsPanel.start();
  toolRegistryPanel.start();
  // MCP plugins tab section — ADR-0043 follow-up #3 (Burst 112). Sits
  // alongside the registered-tools view in the Tools tab. Non-fatal
  // — degrades cleanly when the daemon's plugin runtime is absent.
  mcpPluginsPanel.start();
  // Memory tab (ADR-0022 v0.2 T17) — depends on state.agents being
  // populated by agentsPanel; subscribes to keep its picker in sync.
  // Non-fatal posture — tab degrades to empty state if the daemon
  // doesn't expose memory endpoints (older deploys).
  memoryPanel.start();
  // ADR-003Y Y6 — Chat tab. Same non-fatal posture; if /conversations
  // endpoints aren't there (pre-Y1 daemon), the rooms list shows an
  // error and the rest of the app is unaffected.
  chatPanel.start().catch((e) => {
    console.warn("[chat] start failed:", e);
  });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}

// Self-serve tour overlay — guided per-tab walkthroughs.
//
// Demo-friction audit P5 / phase F5. Each tab can register a sequence of
// steps (anchor selector + tooltip text); the engine renders a backdrop,
// a highlight cutout around the anchor, and a tooltip with prev / next /
// skip / done controls. Skippable, replayable, doesn't lock the UI when
// not active.
//
// Architecture decisions:
//   - Pure DOM, no framework. The tour is a single floating panel +
//     a backdrop overlay. Highlight is a transparent box-shadow trick
//     (the spotlight is a rectangle with a huge inverted box-shadow).
//   - Step state lives in module-locals — no integration with state.js,
//     so the tour can be torn down and rebuilt without touching the
//     rest of the app.
//   - Auto-show on first visit per tab (localStorage flag); manual
//     re-open via the "Take the tour" button in the top bar.
//   - Anchor selectors are CSS query strings. If the selector misses
//     (DOM hasn't rendered yet, element was removed), the tooltip
//     centers in the viewport with no spotlight.

const STORAGE_KEY = "fsf:toursSeen";  // JSON array of tour IDs the user has completed

let state = {
  active: false,
  tourId: null,
  steps: [],
  index: 0,
  // DOM refs created on demand and torn down on close.
  backdrop: null,
  spotlight: null,
  tooltip: null,
  scrollHandler: null,
};

// ---------------------------------------------------------------------------
// Tour content. Each tour is { id, label, steps: [{anchor, title, body, ...}] }.
// Anchors are CSS selectors; title is the tooltip h3, body is one paragraph.
// `tabName` (optional) is the data-tab attribute of the tab the tour belongs
// to — used by the per-tab launcher button to pick the right tour.
// ---------------------------------------------------------------------------

const TOURS = {
  forge: {
    id: "forge",
    label: "Forge tour",
    tabName: "forge",
    steps: [
      {
        anchor: "#genre-select",
        title: "Pick a genre",
        body: "Genres bundle a personality, risk profile, memory ceiling, and approval policy. They also filter the role dropdown — pick one that matches what you want this agent to do.",
      },
      {
        anchor: "#role-select",
        title: "Pick a role",
        body: "Roles define what kind of work this agent does — log_analyst, anomaly_investigator, etc. Each role has its own trait emphasis, which weights how much each slider pulls on the final policy.",
      },
      {
        anchor: "#sliders",
        title: "Drag the sliders",
        body: "29 traits across 6 domains. PRIMARY traits matter most; SECONDARY and TERTIARY tune. Watch the preview panel update live as you drag.",
      },
      {
        anchor: "#tools-panel",
        title: "Adjust the tool kit",
        body: "Each role ships with an archetype kit. You can uncheck tools to remove them from the agent's constitution, or add tools from the catalog above and beyond the kit.",
      },
      {
        anchor: ".forge-col--preview .panel",
        title: "Watch the preview",
        body: "DNA is content-addressed — same sliders always produce the same DNA. The constitution hash covers every policy + trait + tool. Two agents with different genres but identical sliders have different hashes — by design.",
      },
      {
        anchor: "#btn-birth",
        title: "Birth the agent",
        body: "Click Birth to give the agent a soul. The forge writes the soul.md, the constitution.yaml, and an audit chain entry — content-addressed identity, all in one transaction.",
      },
    ],
  },
  agents: {
    id: "agents",
    label: "Agents tour",
    tabName: "agents",
    steps: [
      {
        anchor: "#agents-list",
        title: "Browse the registry",
        body: "Every agent ever birthed against this daemon. Active and archived both visible — use the filters above to narrow down.",
      },
      {
        anchor: "#agents-role-filter",
        title: "Filter by role / status",
        body: "Quick way to find a specific kind of agent. Combined with the status filter you can see only active investigators, only archived companions, etc.",
      },
      {
        anchor: "#agent-detail",
        title: "Click any card",
        body: "The detail panel shows the full identity card — instance ID, both DNAs, role, parent (for spawned agents), soul + constitution paths, the constitution hash, and an Archive form.",
      },
    ],
  },
  audit: {
    id: "audit",
    label: "Audit chain tour",
    tabName: "audit",
    steps: [
      {
        anchor: "#audit-list",
        title: "The hash-chained log",
        body: "Every state change lands here. Newest first. Each row is one chain entry — sequence number, timestamp, event type, summary.",
      },
      {
        anchor: ".audit-entry",
        title: "Click any row to drill in",
        body: "Click-to-expand reveals the cryptographic linkage — entry_hash, prev_hash chained to the previous seq, agent_dna, instance_id — plus the full event_data JSON. This is what 'tamper-evident' actually looks like.",
      },
      {
        anchor: "#audit-limit",
        title: "Walk further back",
        body: "Bump 'show last' to see more history. Refresh re-pulls from the canonical JSONL on disk — runtime events appear immediately, not just lifespan-time ones.",
      },
    ],
  },
};

// ---------------------------------------------------------------------------
// Public API — start.js calls register() once after wiring tabs; the per-tab
// modules can call launch(id) on demand. The engine handles everything else.
// ---------------------------------------------------------------------------

export function start() {
  // Wire the "Take the tour" button in the top bar.
  const btn = document.getElementById("tour-launch-btn");
  if (btn) {
    btn.addEventListener("click", () => {
      // Pick the tour matching the currently-active tab; fall back to forge.
      const active = document.querySelector(".tab[aria-selected='true']");
      const tabName = active?.dataset?.tab || "forge";
      const tour = Object.values(TOURS).find((t) => t.tabName === tabName) || TOURS.forge;
      launch(tour.id);
    });
  }

  // Auto-show the forge tour on first visit (one-shot per browser).
  if (!hasSeen("forge")) {
    // Defer so the rest of the app finishes booting first — sliders + tools
    // panel render asynchronously and we want their anchors to exist.
    setTimeout(() => {
      // Only if the welcome banner is dismissed AND we're still on the
      // forge tab. Otherwise the user has already moved on; respect that.
      const welcome = document.getElementById("welcome");
      const onForge =
        document.querySelector(".tab[data-tab='forge'][aria-selected='true']");
      if (onForge && (!welcome || welcome.hidden)) {
        launch("forge");
      }
    }, 1500);
  }
}

export function launch(tourId) {
  const tour = TOURS[tourId];
  if (!tour) {
    console.warn(`[tour] unknown tour id: ${tourId}`);
    return;
  }
  if (state.active) close();

  // Switch to the tour's tab if we're not already there. Tours are
  // tab-scoped — running the audit tour on the forge tab would highlight
  // nothing.
  if (tour.tabName) {
    const tab = document.querySelector(`.tab[data-tab='${tour.tabName}']`);
    if (tab && tab.getAttribute("aria-selected") !== "true") {
      tab.click();
    }
  }

  state.active = true;
  state.tourId = tourId;
  state.steps = tour.steps;
  state.index = 0;
  buildOverlay();
  renderStep();
}

export function close() {
  if (!state.active) return;
  state.active = false;
  if (state.backdrop) state.backdrop.remove();
  if (state.spotlight) state.spotlight.remove();
  if (state.tooltip) state.tooltip.remove();
  if (state.scrollHandler) {
    window.removeEventListener("resize", state.scrollHandler);
    window.removeEventListener("scroll", state.scrollHandler, true);
  }
  state.backdrop = null;
  state.spotlight = null;
  state.tooltip = null;
  state.scrollHandler = null;
}

// ---------------------------------------------------------------------------
// Overlay construction
// ---------------------------------------------------------------------------

function buildOverlay() {
  // Backdrop — full-viewport semitransparent layer. Click-to-dismiss on
  // the backdrop itself (not on the spotlight area, which forwards clicks
  // to the underlying app).
  state.backdrop = document.createElement("div");
  state.backdrop.className = "tour-backdrop";
  state.backdrop.addEventListener("click", (e) => {
    // Only dismiss if the click landed on the backdrop itself, not a child.
    if (e.target === state.backdrop) close();
  });
  document.body.appendChild(state.backdrop);

  // Spotlight — a transparent rectangle with a huge inverted box-shadow
  // that creates the dim-everything-except-this effect. Pointer-events:
  // none means clicks fall through to the underlying element.
  state.spotlight = document.createElement("div");
  state.spotlight.className = "tour-spotlight";
  document.body.appendChild(state.spotlight);

  // Tooltip — the actual UI. Title + body + step counter + nav buttons.
  state.tooltip = document.createElement("div");
  state.tooltip.className = "tour-tooltip";
  state.tooltip.setAttribute("role", "dialog");
  state.tooltip.setAttribute("aria-live", "polite");
  document.body.appendChild(state.tooltip);

  // Reposition on resize / scroll so the spotlight tracks the anchor.
  state.scrollHandler = () => positionForStep();
  window.addEventListener("resize", state.scrollHandler);
  window.addEventListener("scroll", state.scrollHandler, true);
}

function renderStep() {
  const step = state.steps[state.index];
  if (!step) {
    close();
    return;
  }

  // Tooltip body.
  state.tooltip.innerHTML = "";

  const counter = document.createElement("div");
  counter.className = "tour-tooltip__counter";
  counter.textContent = `${state.index + 1} / ${state.steps.length}`;
  state.tooltip.appendChild(counter);

  const title = document.createElement("h3");
  title.className = "tour-tooltip__title";
  title.textContent = step.title;
  state.tooltip.appendChild(title);

  const body = document.createElement("p");
  body.className = "tour-tooltip__body";
  body.textContent = step.body;
  state.tooltip.appendChild(body);

  const nav = document.createElement("div");
  nav.className = "tour-tooltip__nav";

  const skip = document.createElement("button");
  skip.className = "tour-tooltip__btn tour-tooltip__btn--ghost";
  skip.textContent = "skip";
  skip.addEventListener("click", () => {
    markSeen(state.tourId);
    close();
  });
  nav.appendChild(skip);

  const spacer = document.createElement("div");
  spacer.style.flex = "1";
  nav.appendChild(spacer);

  if (state.index > 0) {
    const prev = document.createElement("button");
    prev.className = "tour-tooltip__btn";
    prev.textContent = "back";
    prev.addEventListener("click", () => {
      state.index -= 1;
      renderStep();
    });
    nav.appendChild(prev);
  }

  const next = document.createElement("button");
  next.className = "tour-tooltip__btn tour-tooltip__btn--primary";
  if (state.index === state.steps.length - 1) {
    next.textContent = "done";
    next.addEventListener("click", () => {
      markSeen(state.tourId);
      close();
    });
  } else {
    next.textContent = "next";
    next.addEventListener("click", () => {
      state.index += 1;
      renderStep();
    });
  }
  nav.appendChild(next);

  state.tooltip.appendChild(nav);

  positionForStep();
}

function positionForStep() {
  const step = state.steps[state.index];
  if (!step) return;
  const target = step.anchor ? document.querySelector(step.anchor) : null;

  if (!target) {
    // Anchor missing — center the tooltip and hide the spotlight.
    state.spotlight.style.display = "none";
    state.tooltip.style.left = "50%";
    state.tooltip.style.top = "50%";
    state.tooltip.style.transform = "translate(-50%, -50%)";
    return;
  }

  const rect = target.getBoundingClientRect();
  // Scroll the anchor into view if it's off-screen, then re-measure.
  const inView =
    rect.top >= 0 &&
    rect.left >= 0 &&
    rect.bottom <= window.innerHeight &&
    rect.right <= window.innerWidth;
  if (!inView) {
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    // Re-measure after scroll on the next frame.
    requestAnimationFrame(() => positionForStep());
    return;
  }

  // Spotlight — place a rect over the target with a few px of padding.
  const pad = 6;
  state.spotlight.style.display = "";
  state.spotlight.style.left = `${rect.left - pad}px`;
  state.spotlight.style.top = `${rect.top - pad}px`;
  state.spotlight.style.width = `${rect.width + pad * 2}px`;
  state.spotlight.style.height = `${rect.height + pad * 2}px`;

  // Tooltip — try to place below the target; fall back to above if no room.
  const ttHeight = state.tooltip.offsetHeight || 180;
  const ttWidth = state.tooltip.offsetWidth || 360;
  const margin = 16;

  let top = rect.bottom + margin;
  if (top + ttHeight > window.innerHeight - margin) {
    top = rect.top - ttHeight - margin;
  }
  if (top < margin) {
    top = margin;
  }

  let left = rect.left + rect.width / 2 - ttWidth / 2;
  if (left + ttWidth > window.innerWidth - margin) {
    left = window.innerWidth - ttWidth - margin;
  }
  if (left < margin) {
    left = margin;
  }

  state.tooltip.style.transform = "";
  state.tooltip.style.left = `${left}px`;
  state.tooltip.style.top = `${top}px`;
}

// ---------------------------------------------------------------------------
// "Seen" tracking — auto-show only on first visit.
// ---------------------------------------------------------------------------

function readSeen() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}
function hasSeen(tourId) {
  return readSeen().includes(tourId);
}
function markSeen(tourId) {
  try {
    const seen = readSeen();
    if (!seen.includes(tourId)) {
      seen.push(tourId);
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(seen));
    }
  } catch {
    /* storage unavailable — tour will auto-show next session */
  }
}

// Cross-cutting UX wiring — keyboard tab nav, command palette, clickable
// statusbar items. Each piece is independent and degrades cleanly if its
// targets aren't in the DOM.
//
// Keyboard model:
//   ArrowUp / ArrowDown    — move tab focus within the tablist (skip group titles)
//   ArrowLeft / ArrowRight — same
//   Home / End             — first / last tab
//   Enter / Space          — activate focused tab
//   Cmd/Ctrl + K           — open command palette
//   / (slash)              — open command palette (when not focused in a text field)
//   Escape                 — close palette
//   Cmd/Ctrl + 1..9        — jump to tab by linear index
//
// The command palette is a single-purpose fuzzy filter over (tabs ∪
// loaded agents). No external dependency — substring match is plenty at
// this scale (16 tabs + ~100 agents).

import * as state from "./state.js";

const VALID_TAB_KEYS = new Set([
  "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End", "Enter", " ",
]);

// ---------------------------------------------------------------------------
// Tab keyboard navigation
// ---------------------------------------------------------------------------
function tabs() {
  return [...document.querySelectorAll(".tabs .tab")];
}

function activeTabIndex() {
  const all = tabs();
  const focused = document.activeElement;
  const i = all.indexOf(focused);
  if (i >= 0) return i;
  // No tab focused — fall back to the currently-active (aria-selected) tab.
  return Math.max(0, all.findIndex((t) => t.getAttribute("aria-selected") === "true"));
}

function focusTab(i) {
  const all = tabs();
  if (!all.length) return;
  const wrapped = (i + all.length) % all.length;
  all[wrapped].focus();
}

function activateTab(tab) {
  if (!tab) return;
  // Trigger the existing click handler in app.js to update aria-selected
  // and panel visibility. Avoids duplicating the panel-switch logic.
  tab.click();
}

function wireTabKeys() {
  const list = document.querySelector(".tabs");
  if (!list) return;
  // Make sure each tab is focusable. Existing code uses role="tab" but
  // doesn't set tabindex — browsers focus on click, not tab-key, without it.
  for (const t of tabs()) {
    if (!t.hasAttribute("tabindex")) t.setAttribute("tabindex", "0");
  }
  list.addEventListener("keydown", (e) => {
    if (!VALID_TAB_KEYS.has(e.key)) return;
    if (!e.target.classList.contains("tab")) return;
    e.preventDefault();
    const i = activeTabIndex();
    switch (e.key) {
      case "ArrowDown":
      case "ArrowRight":
        focusTab(i + 1);
        break;
      case "ArrowUp":
      case "ArrowLeft":
        focusTab(i - 1);
        break;
      case "Home":
        focusTab(0);
        break;
      case "End":
        focusTab(tabs().length - 1);
        break;
      case "Enter":
      case " ":
        activateTab(document.activeElement);
        break;
    }
  });
}

// ---------------------------------------------------------------------------
// Global shortcuts (Cmd/Ctrl-K, "/", Cmd-1..9)
// ---------------------------------------------------------------------------
function inTypingTarget(target) {
  if (!target) return false;
  const tag = (target.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select") return true;
  if (target.isContentEditable) return true;
  return false;
}

function wireGlobalKeys() {
  document.addEventListener("keydown", (e) => {
    // Open palette: Cmd/Ctrl-K from anywhere; "/" only when not typing.
    if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
      e.preventDefault();
      openPalette();
      return;
    }
    if (e.key === "/" && !e.metaKey && !e.ctrlKey && !inTypingTarget(e.target)) {
      e.preventDefault();
      openPalette();
      return;
    }
    // Close palette: Escape.
    if (e.key === "Escape" && isPaletteOpen()) {
      e.preventDefault();
      closePalette();
      return;
    }
    // Linear tab jump: Cmd/Ctrl-1..9. Skip when typing so the user can
    // still type numbers into input fields.
    if ((e.metaKey || e.ctrlKey) && !inTypingTarget(e.target)) {
      const n = parseInt(e.key, 10);
      if (Number.isInteger(n) && n >= 1 && n <= 9) {
        const all = tabs();
        if (n - 1 < all.length) {
          e.preventDefault();
          activateTab(all[n - 1]);
        }
      }
    }
  });
}

// ---------------------------------------------------------------------------
// Command palette
// ---------------------------------------------------------------------------
let paletteRoot = null;
let paletteInput = null;
let paletteList = null;
let paletteItems = []; // {label, hint, run, score}
let paletteSelected = 0;

function buildPaletteOnce() {
  if (paletteRoot) return;
  paletteRoot = document.createElement("div");
  paletteRoot.className = "cmd-palette";
  paletteRoot.hidden = true;
  paletteRoot.innerHTML = `
    <div class="cmd-palette__scrim" data-role="scrim"></div>
    <div class="cmd-palette__panel" role="dialog" aria-label="Command palette">
      <input class="cmd-palette__input" type="text" placeholder="Jump to tab or agent…  (Esc closes)" autocomplete="off" spellcheck="false" />
      <div class="cmd-palette__list" role="listbox"></div>
      <div class="cmd-palette__hint">↑↓ move · Enter select · Esc close</div>
    </div>
  `;
  document.body.appendChild(paletteRoot);
  paletteInput = paletteRoot.querySelector(".cmd-palette__input");
  paletteList = paletteRoot.querySelector(".cmd-palette__list");
  paletteRoot.querySelector('[data-role="scrim"]').addEventListener("click", closePalette);
  paletteInput.addEventListener("input", () => refreshPalette(paletteInput.value));
  paletteInput.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      moveSelection(1);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      moveSelection(-1);
    } else if (e.key === "Enter") {
      e.preventDefault();
      runSelected();
    }
  });
}

function isPaletteOpen() {
  return paletteRoot && !paletteRoot.hidden;
}

function openPalette() {
  buildPaletteOnce();
  paletteRoot.hidden = false;
  paletteInput.value = "";
  refreshPalette("");
  // Focus AFTER unhiding — browsers ignore focus() on hidden elements.
  setTimeout(() => paletteInput.focus(), 0);
}

function closePalette() {
  if (!paletteRoot) return;
  paletteRoot.hidden = true;
}

function moveSelection(delta) {
  if (!paletteItems.length) return;
  paletteSelected = (paletteSelected + delta + paletteItems.length) % paletteItems.length;
  highlightSelected();
}

function highlightSelected() {
  const rows = paletteList.querySelectorAll(".cmd-palette__item");
  rows.forEach((r, i) => {
    r.classList.toggle("cmd-palette__item--selected", i === paletteSelected);
    if (i === paletteSelected) r.scrollIntoView({ block: "nearest" });
  });
}

function runSelected() {
  const item = paletteItems[paletteSelected];
  if (!item) return;
  closePalette();
  item.run();
}

function refreshPalette(query) {
  const q = (query || "").trim().toLowerCase();
  const candidates = collectCandidates();
  let filtered = candidates;
  if (q) {
    filtered = candidates
      .map((c) => ({ ...c, score: scoreMatch(c, q) }))
      .filter((c) => c.score > 0)
      .sort((a, b) => b.score - a.score);
  }
  paletteItems = filtered.slice(0, 50);
  paletteSelected = 0;
  paletteList.innerHTML = "";
  if (!paletteItems.length) {
    const empty = document.createElement("div");
    empty.className = "cmd-palette__empty";
    empty.textContent = q ? `No match for "${query}"` : "Start typing…";
    paletteList.appendChild(empty);
    return;
  }
  paletteItems.forEach((item, i) => {
    const row = document.createElement("div");
    row.className = "cmd-palette__item" + (i === paletteSelected ? " cmd-palette__item--selected" : "");
    row.setAttribute("role", "option");
    row.innerHTML = `
      <span class="cmd-palette__kind">${escapeHtml(item.kind)}</span>
      <span class="cmd-palette__label">${escapeHtml(item.label)}</span>
      <span class="cmd-palette__hint-text">${escapeHtml(item.hint || "")}</span>
    `;
    row.addEventListener("mouseenter", () => {
      paletteSelected = i;
      highlightSelected();
    });
    row.addEventListener("click", () => {
      paletteSelected = i;
      runSelected();
    });
    paletteList.appendChild(row);
  });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function scoreMatch(item, q) {
  // Cheap substring scoring: prefix > word-boundary > substring > zero.
  // Score ranges so the right thing floats to the top even when label
  // and hint both match (e.g. "agent" matching kind "agent" + the
  // agent's name).
  const haystack = `${item.label} ${item.hint || ""}`.toLowerCase();
  if (!haystack.includes(q)) return 0;
  let score = 1;
  if (item.label.toLowerCase().startsWith(q)) score += 10;
  if (item.label.toLowerCase().includes(q)) score += 3;
  if (item.kind === "tab") score += 2; // small bias to tabs over agents
  // Bonus for word-boundary matches.
  if (new RegExp(`\\b${q.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}`, "i").test(haystack)) {
    score += 4;
  }
  return score;
}

function collectCandidates() {
  const out = [];
  // Tabs.
  for (const t of tabs()) {
    const label = t.querySelector(".tab__label")?.textContent?.trim() || t.dataset.tab || "";
    out.push({
      kind: "tab",
      label,
      hint: t.dataset.tab,
      run: () => activateTab(t),
    });
  }
  // Agents — published by agents.js to the shared state.
  const agentList = state.get("agents") || [];
  for (const a of agentList) {
    out.push({
      kind: "agent",
      label: a.agent_name || a.instance_id,
      hint: `${a.role || ""} · ${(a.instance_id || "").slice(0, 8)}`,
      run: () => {
        // Switch to Agents tab and (best effort) trigger selection.
        const tab = document.querySelector('.tab[data-tab="agents"]');
        activateTab(tab);
        // The agents panel listens to clicks on its rows — we don't have a
        // robust public selection API, but the rows render with the
        // instance_id as a data attribute or text content. Try both.
        setTimeout(() => {
          const rows = document.querySelectorAll('[data-panel="agents"] [data-instance-id], [data-panel="agents"] .agent-row, [data-panel="agents"] li');
          for (const r of rows) {
            if (r.dataset?.instanceId === a.instance_id || r.textContent.includes(a.instance_id?.slice(0, 8) || "__never__")) {
              r.click();
              break;
            }
          }
        }, 50);
      },
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// Statusbar clickability — turn the three status items into nav shortcuts
// ---------------------------------------------------------------------------
function wireStatusbarClicks() {
  const sb = document.querySelector(".statusbar");
  if (!sb) return;
  // The statusbar markup is rendered by statusbar.js as a sequence of
  // spans; we hook by their semantic label text rather than by class
  // (the class names are private to that module).
  sb.addEventListener("click", (e) => {
    const node = e.target.closest("[data-sb-shortcut]");
    if (!node) return;
    e.preventDefault();
    const target = node.dataset.sbShortcut;
    if (!target) return;
    const tab = document.querySelector(`.tab[data-tab="${target}"]`);
    if (tab) activateTab(tab);
  });
  // Statusbar cells live as .statusbar__cell elements with stable IDs
  // assigned by statusbar.js (sb-daemon, sb-agents, sb-chain, sb-activity).
  // Map id → tab to make the cell a single-click navigation shortcut.
  const ID_TO_TAB = {
    "sb-agents": "agents",
    "sb-chain": "audit",
    "sb-activity": "audit",
    // sb-daemon stays static — no jump target makes sense (Operator?). The
    // existing dot color already conveys health.
  };
  const annotate = () => {
    for (const [id, target] of Object.entries(ID_TO_TAB)) {
      const cell = sb.querySelector(`#${id}`);
      if (!cell) continue;
      if (cell.dataset.sbShortcut === target) continue; // already done
      cell.dataset.sbShortcut = target;
      cell.classList.add("statusbar__item--clickable");
      if (!cell.title) cell.title = `Jump to ${target} tab`;
    }
  };
  annotate();
  // statusbar.js rerenders values into the existing cells, so the cells
  // themselves persist. We still observe for completeness — if statusbar
  // recreates cells (unlikely), we re-annotate.
  new MutationObserver(annotate).observe(sb, { childList: true, subtree: true });
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------
export function start() {
  try { wireTabKeys(); } catch (e) { console.warn("[ux] tab key wiring failed:", e); }
  try { wireGlobalKeys(); } catch (e) { console.warn("[ux] global key wiring failed:", e); }
  try { wireStatusbarClicks(); } catch (e) { console.warn("[ux] statusbar wiring failed:", e); }
}

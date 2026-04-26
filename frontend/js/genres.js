// Genre selector (ADR-0021 T8) — filters the role dropdown by genre.
//
// On load:
//   1. Fetch /genres once. If empty (no engine loaded, no claims), the
//      genre dropdown stays at "all" and behaves as the pre-T8 form did.
//   2. Populate the genre dropdown with each loaded genre.
//   3. Subscribe to selectedRole; when the user picks a role, snap the
//      genre dropdown to whatever genre claims that role (so the user
//      can see at a glance which genre they're working in).
//
// On genre change:
//   * Filter the role dropdown to only roles claimed by the chosen genre.
//   * Picking "all" restores the unfiltered list — same set of options
//     traits.js would have produced.
//
// State keys this module reads / writes:
//   read:  traitTree         (so we know all the role names + descriptions)
//   read:  selectedRole       (to keep the genre dropdown in sync)
//   write: genres             (full GenresOut response, cached)
//   write: selectedGenre      (current dropdown value, "" = all)

import { api, ApiError } from "./api.js";
import * as state from "./state.js";

function $(id) {
  return document.getElementById(id);
}

/** Look up the genre that claims a role, scanning the loaded GenresOut.
 *  Returns null when the role isn't claimed (legacy artifact, new role). */
function genreForRole(genres, role) {
  if (!genres) return null;
  for (const g of genres.genres) {
    if (g.roles.includes(role)) return g.name;
  }
  return null;
}

/** Build the role dropdown's option list, filtered by the selected genre.
 *  When selectedGenre is "" (all), returns every role known to the trait
 *  engine — keeps back-compat with the pre-T8 form. */
function rolesForGenre(traitTree, genres, selectedGenre) {
  const allRoles = (traitTree?.roles || []).map((r) => r.name);
  if (!selectedGenre) return allRoles;
  const claimed = new Set();
  for (const g of (genres?.genres || [])) {
    if (g.name === selectedGenre) {
      g.roles.forEach((r) => claimed.add(r));
    }
  }
  // Filter to roles that are BOTH in the trait engine AND claimed by the
  // genre. Aspirational roles in genres.yaml that the trait engine doesn't
  // know about yet aren't selectable — picking them would 400 on /preview.
  return allRoles.filter((r) => claimed.has(r));
}

function rebuildRoleOptions() {
  const sel = $("role-select");
  if (!sel) return;
  const traitTree = state.get("traitTree");
  const genres = state.get("genres");
  const selectedGenre = state.get("selectedGenre") || "";
  const currentRole = sel.value;
  const roleNames = rolesForGenre(traitTree, genres, selectedGenre);

  // Empty list → genre with no claimed roles in the trait engine. Render
  // a disabled placeholder so the user sees "nothing here" rather than
  // wondering why the dropdown collapsed.
  sel.innerHTML = "";
  if (roleNames.length === 0) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = "— no claimed roles in this genre —";
    opt.disabled = true;
    sel.appendChild(opt);
    return;
  }
  for (const name of roleNames) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    sel.appendChild(opt);
  }
  // Preserve the current selection if it's still in the filtered list,
  // otherwise default to the first option and emit a change event so
  // dependent panels (sliders, preview, tools) re-derive.
  if (roleNames.includes(currentRole)) {
    sel.value = currentRole;
  } else {
    sel.value = roleNames[0];
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  }
}

function rebuildGenreOptions() {
  const sel = $("genre-select");
  if (!sel) return;
  const genres = state.get("genres");
  // Always preserve the leading "all" option as the back-compat path.
  sel.innerHTML = '<option value="">all</option>';
  for (const g of (genres?.genres || [])) {
    const opt = document.createElement("option");
    opt.value = g.name;
    // Show genre name + role count so the operator has a sense of what
    // the dropdown will filter to. Description tooltips would be nicer
    // but a count is enough at the form level.
    opt.textContent = `${g.name} (${g.roles.length} roles)`;
    sel.appendChild(opt);
  }
}

async function loadGenres() {
  try {
    const res = await api.get("/genres");
    state.set("genres", res);
  } catch (e) {
    // Read-only endpoint that should never fail in a healthy deploy —
    // but if it does, we log and degrade. The genre dropdown stays at
    // "all" and the role list isn't filtered, which is the pre-T8
    // behavior. No toast — operators using a fresh stack without a
    // genre engine shouldn't see error noise on every page load.
    state.set("genres", { version: "0", genres: [] });
    console.warn("[genres] failed to load:", e instanceof ApiError ? e.status : e);
  }
}

export async function start() {
  await loadGenres();
  rebuildGenreOptions();

  // Initial role-list build runs after the trait tree is loaded — wire
  // a subscription so the build happens whether the tree arrived first
  // or second.
  state.subscribe("traitTree", rebuildRoleOptions);
  state.subscribe("genres", rebuildRoleOptions);
  state.subscribe("selectedGenre", rebuildRoleOptions);

  // Wire the select element. Any change updates state, which triggers
  // the subscriber above to rebuild the role options.
  const sel = $("genre-select");
  if (sel) {
    sel.addEventListener("change", () => {
      state.set("selectedGenre", sel.value);
    });
  }

  // Sync the genre dropdown when something else changes the role
  // (e.g., reset button, traits.js seeding). Handy for UX clarity.
  state.subscribe("selectedRole", (role) => {
    const genres = state.get("genres");
    const matchedGenre = genreForRole(genres, role);
    if (matchedGenre && $("genre-select")?.value === "") {
      // Don't override an explicit operator choice — only auto-snap
      // when "all" is selected. This way picking "all" then a role
      // shows the role's genre without forcing the user back into a
      // filtered view.
      // (Intentional no-op: keep "all" sticky.)
    }
  });
}

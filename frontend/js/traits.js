// Fetch the trait tree once, render grouped sliders, seed defaults from the
// selected role. Slider changes update `state.profile` which preview.js
// watches. Keeping render and input wiring in one file because they're two
// sides of the same concern.

import { api } from "./api.js";
import * as state from "./state.js";

/**
 * Build a fresh profile { trait_values, domain_weight_overrides } with
 * every trait set to its per-trait default. (Role-specific defaults aren't
 * per-trait in the engine — roles only override domain weights — so
 * defaulting all traits to their `default` field is the honest start.)
 */
function profileFromDefaults(tree) {
  const trait_values = {};
  for (const d of tree.domains) {
    for (const sd of d.subdomains) {
      for (const t of sd.traits) {
        trait_values[t.name] = t.default;
      }
    }
  }
  return { trait_values, domain_weight_overrides: {} };
}

function renderRoleSelect(tree) {
  const sel = document.getElementById("role-select");
  sel.innerHTML = "";
  for (const r of tree.roles) {
    const opt = document.createElement("option");
    opt.value = r.name;
    opt.textContent = r.name;
    opt.title = r.description;
    sel.appendChild(opt);
  }
  // Default to first role.
  sel.value = tree.roles[0]?.name || "";

  // Also populate the agents-tab role filter.
  const filterSel = document.getElementById("agents-role-filter");
  if (filterSel) {
    // Preserve the leading "all" option.
    filterSel.querySelectorAll("option:not([value=''])").forEach((n) => n.remove());
    for (const r of tree.roles) {
      const opt = document.createElement("option");
      opt.value = r.name;
      opt.textContent = r.name;
      filterSel.appendChild(opt);
    }
  }
}

function tierChip(tier) {
  const el = document.createElement("span");
  el.className = "tier";
  el.textContent = tier;
  el.title = `tier: ${tier} — tier weight affects domain contribution`;
  return el;
}

function renderSliders(tree) {
  const root = document.getElementById("sliders");
  root.innerHTML = "";

  for (const domain of tree.domains) {
    const section = document.createElement("section");
    section.className = "domain-group";
    section.dataset.domain = domain.name;

    const title = document.createElement("h3");
    title.className = "domain-group__title";
    title.textContent = domain.name;
    section.appendChild(title);

    if (domain.description) {
      const desc = document.createElement("p");
      desc.className = "domain-group__desc";
      desc.textContent = domain.description;
      section.appendChild(desc);
    }

    for (const sd of domain.subdomains) {
      const sub = document.createElement("div");
      sub.className = "subdomain-group";

      const sdTitle = document.createElement("h4");
      sdTitle.className = "subdomain-group__title";
      sdTitle.textContent = sd.name;
      sub.appendChild(sdTitle);

      if (sd.description) {
        const sdDesc = document.createElement("p");
        sdDesc.className = "subdomain-group__desc";
        sdDesc.textContent = sd.description;
        sub.appendChild(sdDesc);
      }

      for (const trait of sd.traits) {
        sub.appendChild(renderTraitRow(trait));
      }
      section.appendChild(sub);
    }

    root.appendChild(section);
  }
}

function renderTraitRow(trait) {
  const row = document.createElement("div");
  row.className = "slider-row";
  row.dataset.trait = trait.name;

  const lbl = document.createElement("div");
  lbl.className = "slider-row__label";

  const nameEl = document.createElement("div");
  nameEl.className = "slider-row__name";
  nameEl.append(trait.name);
  nameEl.appendChild(tierChip(trait.tier));
  lbl.appendChild(nameEl);

  const descEl = document.createElement("div");
  descEl.className = "slider-row__desc";
  descEl.textContent = trait.desc;
  lbl.appendChild(descEl);

  row.appendChild(lbl);

  // Range input + live value, stacked on the right.
  const right = document.createElement("div");
  right.style.display = "flex";
  right.style.flexDirection = "column";
  right.style.alignItems = "stretch";
  right.style.minWidth = "180px";

  const range = document.createElement("input");
  range.type = "range";
  range.min = "0";
  range.max = "100";
  range.step = "1";
  range.value = String(trait.default);
  range.dataset.trait = trait.name;

  const val = document.createElement("div");
  val.className = "slider-row__value";
  val.textContent = `${trait.default} / 100`;

  range.addEventListener("input", () => {
    const v = Number(range.value);
    val.textContent = `${v} / 100`;
    state.update("profile", (p) => ({
      trait_values: { ...(p?.trait_values || {}), [trait.name]: v },
      domain_weight_overrides: { ...(p?.domain_weight_overrides || {}) },
    }));
  });

  right.appendChild(range);
  right.appendChild(val);
  row.appendChild(right);

  return row;
}

function syncSlidersFromProfile(profile) {
  if (!profile) return;
  for (const [name, value] of Object.entries(profile.trait_values || {})) {
    const range = document.querySelector(`input[type="range"][data-trait="${name}"]`);
    if (range) {
      range.value = String(value);
      const row = range.closest(".slider-row");
      const v = row?.querySelector(".slider-row__value");
      if (v) v.textContent = `${value} / 100`;
    }
  }
}

/** Load the trait tree, wire the role select + reset, render sliders. */
export async function start() {
  const tree = await api.get("/traits");
  state.set("traitTree", tree);
  renderRoleSelect(tree);
  renderSliders(tree);

  const initial = profileFromDefaults(tree);
  state.set("profile", initial);
  state.set("selectedRole", tree.roles[0]?.name || "");

  // Role change does NOT reset trait values (the engine's per-trait
  // defaults are role-agnostic). It only changes which role is submitted
  // to /preview + /birth so domain weights apply.
  const roleSel = document.getElementById("role-select");
  roleSel.addEventListener("change", () => {
    state.set("selectedRole", roleSel.value);
  });

  const resetBtn = document.getElementById("reset-profile");
  resetBtn.addEventListener("click", () => {
    const fresh = profileFromDefaults(tree);
    state.set("profile", fresh);
    syncSlidersFromProfile(fresh);
  });
}

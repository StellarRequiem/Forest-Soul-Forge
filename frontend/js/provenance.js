// ADR-0072 T5 (B330) — Provenance pane controller.
//
// Renders the four-layer precedence ladder + active preferences
// + learned rules (active/pending/refused) + hardcoded handoffs.
//
// Closes ADR-0072 5/5 and Phase α at 10/10.

let _initialized = false;

function _escape(s) {
  const t = String(s == null ? "" : s);
  return t
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function _statusPill(status) {
  const colors = {
    active:               "var(--color-ok, #4caf50)",
    pending_activation:   "var(--color-warn, #ffa726)",
    refused:              "var(--color-bad, #ef5350)",
  };
  const bg = colors[status] || "var(--color-muted, #888)";
  return `<span style="display:inline-block; padding:1px 8px; ` +
    `border-radius:8px; background:${bg}; color:#fff; ` +
    `font-size:0.8em;">${_escape(status)}</span>`;
}

function _renderPrecedence(precedence) {
  const el = document.getElementById("prov-precedence");
  if (!el) return;
  el.innerHTML =
    `<div>Higher tier ALWAYS wins on conflict. Hardcoded handoffs ` +
    `are engineer-edited via PR; learned rules require Reality-` +
    `Anchor verification before they affect dispatch.</div>` +
    `<table style="margin-top: 10px; width: 100%; border-collapse: collapse;">` +
    `<thead><tr><th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">tier</th>` +
    `<th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">layer</th></tr></thead>` +
    `<tbody>` +
    precedence
      .map(
        (p) =>
          `<tr><td style="padding:4px 8px;">${p.tier}</td>` +
          `<td style="padding:4px 8px;"><code>${_escape(p.name)}</code></td></tr>`,
      )
      .join("") +
    `</tbody></table>`;
}

function _renderPreferences(prefs) {
  const el = document.getElementById("prov-preferences");
  if (!el) return;
  if (!prefs.length) {
    el.innerHTML =
      `<em class="muted">no operator preferences set. ` +
      `<code>fsf operator preference set &lt;id&gt; &lt;statement&gt;</code></em>`;
    return;
  }
  el.innerHTML =
    `<table style="width: 100%; border-collapse: collapse;">` +
    `<thead><tr>` +
    `<th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">id</th>` +
    `<th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">statement</th>` +
    `<th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">weight</th>` +
    `<th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">domain</th>` +
    `</tr></thead><tbody>` +
    prefs
      .map(
        (p) =>
          `<tr>` +
          `<td style="padding:4px 8px;"><code>${_escape(p.id)}</code></td>` +
          `<td style="padding:4px 8px;">${_escape(p.statement)}</td>` +
          `<td style="padding:4px 8px;">${p.weight.toFixed(2)}</td>` +
          `<td style="padding:4px 8px;"><code>${_escape(p.domain)}</code></td>` +
          `</tr>`,
      )
      .join("") +
    `</tbody></table>`;
}

function _renderRules(buckets) {
  const el = document.getElementById("prov-rules");
  if (!el) return;
  const sections = [];

  function _ruleRow(r) {
    return (
      `<tr>` +
      `<td style="padding:4px 8px;"><code>${_escape(r.id)}</code></td>` +
      `<td style="padding:4px 8px;">${_escape(r.statement)}</td>` +
      `<td style="padding:4px 8px;">${r.weight.toFixed(2)}</td>` +
      `<td style="padding:4px 8px;"><code>${_escape(r.domain || "")}</code></td>` +
      `<td style="padding:4px 8px;">${_statusPill(r.status)}</td>` +
      `<td style="padding:4px 8px; font-size: 0.85em;" class="muted">` +
      _escape(r.verification_reason || "") +
      `</td>` +
      `</tr>`
    );
  }

  function _table(rows) {
    return (
      `<table style="width: 100%; border-collapse: collapse;">` +
      `<thead><tr>` +
      `<th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">id</th>` +
      `<th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">statement</th>` +
      `<th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">weight</th>` +
      `<th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">domain</th>` +
      `<th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">status</th>` +
      `<th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">RA reason</th>` +
      `</tr></thead><tbody>` +
      rows.map(_ruleRow).join("") +
      `</tbody></table>`
    );
  }

  for (const [bucket, rules] of [
    ["active",             buckets.active || []],
    ["pending_activation", buckets.pending_activation || []],
    ["refused",            buckets.refused || []],
  ]) {
    if (!rules.length) continue;
    sections.push(
      `<h4 style="margin: 12px 0 6px 0;">${_escape(bucket)} (${rules.length})</h4>` +
      _table(rules),
    );
  }

  if (!sections.length) {
    el.innerHTML =
      `<em class="muted">no learned rules. Agents will propose ` +
      `rules into <code>pending_activation</code>; the nightly ` +
      `Reality-Anchor cron (B325) promotes or refuses them.</em>`;
    return;
  }
  el.innerHTML = sections.join("");
}

function _renderHandoffs(data) {
  const el = document.getElementById("prov-handoffs");
  if (!el) return;
  const defaults = data.default_skill_per_capability || [];
  const cascades = data.cascade_rules || [];
  const parts = [];
  if (defaults.length) {
    parts.push(
      `<h4 style="margin: 0 0 6px 0;">default skill per capability (${defaults.length})</h4>` +
      `<table style="width:100%; border-collapse:collapse;">` +
      `<thead><tr><th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">domain</th>` +
      `<th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">capability</th>` +
      `<th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">skill</th></tr></thead>` +
      `<tbody>` +
      defaults
        .map(
          (d) =>
            `<tr><td style="padding:4px 8px;"><code>${_escape(d.domain)}</code></td>` +
            `<td style="padding:4px 8px;"><code>${_escape(d.capability)}</code></td>` +
            `<td style="padding:4px 8px;"><code>${_escape(d.skill_name)}.v${_escape(d.skill_version)}</code></td></tr>`,
        )
        .join("") +
      `</tbody></table>`,
    );
  }
  if (cascades.length) {
    parts.push(
      `<h4 style="margin: 12px 0 6px 0;">cascade rules (${cascades.length})</h4>` +
      `<table style="width:100%; border-collapse:collapse;">` +
      `<thead><tr><th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">source</th>` +
      `<th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">target</th>` +
      `<th align="left" style="border-bottom:1px solid #444; padding:4px 8px;">reason</th></tr></thead>` +
      `<tbody>` +
      cascades
        .map(
          (c) =>
            `<tr><td style="padding:4px 8px;"><code>${_escape(c.source_domain)}/${_escape(c.source_capability)}</code></td>` +
            `<td style="padding:4px 8px;"><code>${_escape(c.target_domain)}/${_escape(c.target_capability)}</code></td>` +
            `<td style="padding:4px 8px;">${_escape(c.reason)}</td></tr>`,
        )
        .join("") +
      `</tbody></table>`,
    );
  }
  if (!parts.length) {
    el.innerHTML = `<em class="muted">no handoffs.yaml on disk. ` +
      `Routing falls back to decompose_intent.v1 confidence alone.</em>`;
    return;
  }
  el.innerHTML = parts.join("");
}

async function _refreshAll() {
  const errBoxes = {
    "prov-preferences": document.getElementById("prov-preferences"),
    "prov-rules":       document.getElementById("prov-rules"),
    "prov-handoffs":    document.getElementById("prov-handoffs"),
  };
  try {
    const res = await fetch("/provenance/active");
    if (!res.ok) {
      for (const el of Object.values(errBoxes)) {
        if (el) el.innerHTML = `<span class="muted">HTTP ${res.status}</span>`;
      }
      return;
    }
    const data = await res.json();
    _renderPrecedence(data.precedence || []);
    _renderPreferences(data.preferences || []);
    _renderRules(data.learned_rules || {});
  } catch (e) {
    for (const el of Object.values(errBoxes)) {
      if (el) el.innerHTML = `<span class="muted">error: ${_escape(e.message || e)}</span>`;
    }
  }
  try {
    const hres = await fetch("/provenance/handoffs");
    if (hres.ok) {
      _renderHandoffs(await hres.json());
    } else {
      const el = document.getElementById("prov-handoffs");
      if (el) el.innerHTML = `<span class="muted">HTTP ${hres.status}</span>`;
    }
  } catch (e) {
    const el = document.getElementById("prov-handoffs");
    if (el) el.innerHTML = `<span class="muted">handoffs error: ${_escape(e.message || e)}</span>`;
  }
}

export function initProvenancePane() {
  if (_initialized) return;
  _initialized = true;
  const refresh = document.getElementById("prov-refresh-btn");
  if (refresh) refresh.addEventListener("click", _refreshAll);
  _refreshAll();
}

// Tiny pub/sub state store. No framework; one file is enough for this app.
//
// Keys used by the app (documented here so future maintainers don't have to
// grep to find them):
//   - traitTree        TraitTreeOut from GET /v1/traits
//   - selectedRole     string — the role whose defaults seed the sliders
//   - profile          { trait_values: {name -> 0..100}, domain_weight_overrides: {} }
//   - preview          latest PreviewResponse or null (now includes resolved_tools)
//   - previewError     latest error message while previewing, or null
//   - health           latest HealthOut or null
//   - writesEnabled    bool — mirror of health.writes_enabled for convenience
//   - authRequired     bool — mirror of health.auth_required
//   - agents           AgentListOut.agents or []
//   - selectedAgentId  instance_id of the agent shown in the detail pane
//   - agentDetail      AgentOut of the selected agent + lineage bits
//   - toolCatalog      ToolCatalogOut from GET /tools/catalog (cached for session)
//   - toolKit          ResolvedKitOut for the currently-selected role
//   - toolOverrides    { tools_add: [{name, version}], tools_remove: [string] }
//                      published by tools.js, consumed by preview.js + forms.js

const subs = new Map(); // key -> Set<fn>
const data = new Map(); // key -> value

export function get(key) {
  return data.get(key);
}

export function set(key, value) {
  const prev = data.get(key);
  if (prev === value) return; // object identity — callers replace, don't mutate
  data.set(key, value);
  const listeners = subs.get(key);
  if (listeners) {
    for (const fn of listeners) {
      try {
        fn(value, prev);
      } catch (err) {
        console.error(`[state] subscriber for "${key}" threw:`, err);
      }
    }
  }
}

/** Subscribe; returns an unsubscribe function. */
export function subscribe(key, fn) {
  let set_ = subs.get(key);
  if (!set_) {
    set_ = new Set();
    subs.set(key, set_);
  }
  set_.add(fn);
  // Fire immediately with the current value if we have one (idiomatic for
  // "render when ready"). Caller can opt out by checking the argument.
  if (data.has(key)) {
    try {
      fn(data.get(key), undefined);
    } catch (err) {
      console.error(`[state] initial subscriber for "${key}" threw:`, err);
    }
  }
  return () => set_.delete(fn);
}

/** One-shot update helper: pass a function that gets current value, returns new. */
export function update(key, fn) {
  set(key, fn(get(key)));
}

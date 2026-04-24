// Provider switcher — PUT /runtime/provider to flip between local and
// frontier. The health poll re-renders the status dot within 15 seconds;
// we also trigger an immediate refresh after a successful flip.

import { api, ApiError } from "./api.js";
import { refresh as refreshHealth, promptForToken } from "./health.js";
import { toast } from "./toast.js";

async function currentInfo() {
  return api.get("/runtime/provider");
}

async function flip() {
  let info;
  try {
    info = await currentInfo();
  } catch (e) {
    toast({
      title: "Can't read provider info",
      msg: e.message,
      kind: "error",
    });
    return;
  }
  // Toggle between the first two known providers; if only one is known,
  // show an explanation rather than silently doing nothing.
  const { active, known } = info;
  if (!known || known.length < 2) {
    toast({
      title: "Only one provider is registered",
      msg: `Active: ${active}. No other provider to switch to.`,
      kind: "warn",
    });
    return;
  }
  const target = known.find((p) => p !== active) || known[0];

  try {
    await api.put("/runtime/provider", { provider: target });
    toast({ title: "Provider switched", msg: `${active} → ${target}` });
    refreshHealth();
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      if (promptForToken()) {
        // User supplied a token — retry once.
        try {
          await api.put("/runtime/provider", { provider: target });
          toast({ title: "Provider switched", msg: `${active} → ${target}` });
          refreshHealth();
          return;
        } catch (e2) {
          /* fall through */
        }
      }
    }
    const detail = e instanceof ApiError ? (e.detail?.detail || e.message) : String(e);
    toast({
      title: "Provider switch failed",
      msg: detail,
      kind: "error",
    });
  }
}

export function start() {
  document.getElementById("provider-flip").addEventListener("click", flip);
}

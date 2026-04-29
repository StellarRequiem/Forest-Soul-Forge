"""``browser_action.v1`` — drive a real browser via Playwright (chromium-only).

ADR-003X Phase C3. The heaviest open-web primitive — agents that need
to log into a web UI, click through a workflow, capture a screenshot
of a rendered page. Headless by default; headful only via
FSF_BROWSER_HEADFUL=true for debugging.

Why chromium-only:
    Playwright ships bindings for chromium + firefox + webkit. The
    chromium browser binary alone is ~150 MB; all three would be ~450 MB
    and triple the CVE surface. Agents that genuinely need cross-browser
    testing can install firefox/webkit manually. For the open-web
    plane's "drive a UI" use case, chromium is sufficient.

Why ephemeral context per call:
    A fresh playwright BrowserContext is created for every tool
    invocation and closed afterward. Cookies, localStorage, and the
    cache do not bleed across agent calls — even within a single
    agent's session. If two consecutive web_actuator calls need to
    share state (e.g. log in once, click around several times in the
    same session), the operator pre-loads cookies into the agent's
    secrets store and the agent attaches them per call. This is the
    same posture as web_fetch.v1 — every call is isolated.

side_effects: external — always gated. The constraint resolver in
the daemon adds requires_human_approval=True to any external tool by
default; browser_action.v1 inherits that. Operator approves each call
through the approvals queue.

Per-agent constitution must list:
  allowed_hosts: [www.example.com, ...]   # initial URL host + redirects
  allowed_secret_names: [...]             # if auth is needed via cookies
"""
from __future__ import annotations

import os
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)

# Where rendered screenshots land. Operator can browse/share/serve.
# data/browser_screenshots/ keeps them out of the soul artifacts
# tree (which is content-addressed) — these are runtime byproducts.
DEFAULT_SCREENSHOT_DIR = Path("data/browser_screenshots")

DEFAULT_NAV_TIMEOUT_MS = 15_000
DEFAULT_ACTION_TIMEOUT_MS = 5_000
MAX_TIMEOUT_MS = 60_000

# Action type enum — keep small + intentional. New types land via ADRs,
# not via convenient one-off additions.
ALLOWED_ACTION_TYPES = ("click", "type", "wait", "press", "hover", "select_option")


class BrowserActionError(Exception):
    """Tool-level error — distinct from validation failures."""


class BrowserActionTool:
    """Drive a chromium browser through a sequence of actions.

    Args:
      url (str): URL to load first. Host must be in allowed_hosts.
      actions (list[dict], optional): sequence of UI actions. Each
        item has a ``type`` field; allowed types listed below.
        Empty/omitted = just load the page + screenshot.
      screenshot (bool, optional): take a screenshot after the
        action sequence. Default True.
      timeout_ms (int, optional): per-action timeout in ms. Default
        5000. Max 60000.
      headful (bool, optional): show the browser window. Defaults to
        FSF_BROWSER_HEADFUL env var, else False.

    Action shapes:
      {"type": "click",         "selector": "button.submit"}
      {"type": "type",          "selector": "#email", "text": "..."}
      {"type": "wait",          "ms": 500}                      # absolute
      {"type": "wait",          "selector": "#loaded"}          # for selector
      {"type": "press",         "key": "Enter"}                 # keyboard
      {"type": "hover",         "selector": ".tooltip-trigger"}
      {"type": "select_option", "selector": "select#country", "value": "US"}

    Output:
      {
        "url_final":       str,    # after any nav/redirects
        "screenshot_path": str|None,
        "console_log":     str,    # JS console messages captured
        "actions_run":     int,    # successful action count
      }

    Constraints (read from ctx.constraints):
      allowed_hosts: tuple[str, ...]   # required
    """

    name = "browser_action"
    version = "1"
    side_effects = "external"

    def validate(self, args: dict[str, Any]) -> None:
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ToolValidationError("url is required and must be a non-empty string")
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception as e:
            raise ToolValidationError(f"url is malformed: {e}") from e
        if parsed.scheme not in ("http", "https"):
            raise ToolValidationError(
                f"url scheme must be http or https; got {parsed.scheme!r}"
            )
        if not parsed.netloc:
            raise ToolValidationError("url must include a host")

        actions = args.get("actions", [])
        if not isinstance(actions, list):
            raise ToolValidationError("actions must be a list when provided")
        for i, a in enumerate(actions):
            if not isinstance(a, dict):
                raise ToolValidationError(
                    f"actions[{i}] must be an object; got {type(a).__name__}"
                )
            atype = a.get("type")
            if atype not in ALLOWED_ACTION_TYPES:
                raise ToolValidationError(
                    f"actions[{i}].type must be one of {ALLOWED_ACTION_TYPES}; "
                    f"got {atype!r}"
                )
            # Per-type field requirements.
            if atype == "click" and not a.get("selector"):
                raise ToolValidationError(f"actions[{i}] (click) missing 'selector'")
            if atype == "type":
                if not a.get("selector"):
                    raise ToolValidationError(f"actions[{i}] (type) missing 'selector'")
                if "text" not in a:
                    raise ToolValidationError(f"actions[{i}] (type) missing 'text'")
            if atype == "wait":
                if "ms" not in a and "selector" not in a:
                    raise ToolValidationError(
                        f"actions[{i}] (wait) needs either 'ms' or 'selector'"
                    )
            if atype == "press" and not a.get("key"):
                raise ToolValidationError(f"actions[{i}] (press) missing 'key'")
            if atype == "hover" and not a.get("selector"):
                raise ToolValidationError(f"actions[{i}] (hover) missing 'selector'")
            if atype == "select_option":
                if not a.get("selector"):
                    raise ToolValidationError(
                        f"actions[{i}] (select_option) missing 'selector'"
                    )
                if "value" not in a:
                    raise ToolValidationError(
                        f"actions[{i}] (select_option) missing 'value'"
                    )

        timeout = args.get("timeout_ms", DEFAULT_ACTION_TIMEOUT_MS)
        if not isinstance(timeout, int) or timeout < 100 or timeout > MAX_TIMEOUT_MS:
            raise ToolValidationError(
                f"timeout_ms must be int 100..{MAX_TIMEOUT_MS}; got {timeout!r}"
            )

        if "screenshot" in args and not isinstance(args["screenshot"], bool):
            raise ToolValidationError("screenshot must be a boolean when provided")
        if "headful" in args and not isinstance(args["headful"], bool):
            raise ToolValidationError("headful must be a boolean when provided")

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        url = args["url"]
        actions = args.get("actions", [])
        do_screenshot = args.get("screenshot", True)
        timeout_ms = int(args.get("timeout_ms", DEFAULT_ACTION_TIMEOUT_MS))
        headful = bool(args.get("headful", _env_headful()))

        # Allowlist check BEFORE any browser work.
        allowed_hosts = ctx.constraints.get("allowed_hosts") or ()
        if not allowed_hosts:
            raise BrowserActionError(
                "agent has no allowed_hosts in its constitution — "
                "browser_action refuses to reach any host"
            )
        target_host = urllib.parse.urlparse(url).hostname or ""
        if target_host not in allowed_hosts:
            raise BrowserActionError(
                f"host {target_host!r} is not in the agent's allowed_hosts "
                f"(allowed: {sorted(allowed_hosts)})"
            )

        # Lazy import — playwright is in the optional `browser` extra.
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise BrowserActionError(
                "playwright is not installed — install the browser extra "
                "(pip install forest-soul-forge[browser]) and run "
                "`python -m playwright install chromium` to fetch the binary"
            ) from e

        # Screenshot path. data/browser_screenshots/<uuid>.png so the
        # operator can attach the path to a follow-up call without
        # the tool needing a manifest of past screenshots.
        screenshot_path: str | None = None
        if do_screenshot:
            screenshot_dir = Path(
                ctx.constraints.get("screenshot_dir") or DEFAULT_SCREENSHOT_DIR
            )
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            screenshot_path = str(
                screenshot_dir / f"{uuid.uuid4().hex}.png"
            )

        console_log_lines: list[str] = []
        actions_run = 0
        url_final = url

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=not headful)
            try:
                # Ephemeral context per call — no cross-call cookie bleed.
                context = await browser.new_context()
                try:
                    page = await context.new_page()
                    page.on(
                        "console",
                        lambda msg: console_log_lines.append(
                            f"[{msg.type}] {msg.text}"
                        ),
                    )
                    # Allowlist re-check on every navigation event.
                    def _check_nav(frame) -> None:
                        if frame is page.main_frame:
                            host = urllib.parse.urlparse(frame.url).hostname or ""
                            if host and host not in allowed_hosts:
                                raise BrowserActionError(
                                    f"navigation to {host!r} is not in "
                                    f"allowed_hosts; original url was {url}"
                                )
                    page.on("framenavigated", _check_nav)

                    await page.goto(url, timeout=DEFAULT_NAV_TIMEOUT_MS)
                    url_final = page.url

                    for a in actions:
                        await _run_action(page, a, timeout_ms)
                        actions_run += 1
                    url_final = page.url

                    if do_screenshot and screenshot_path:
                        await page.screenshot(path=screenshot_path, full_page=True)

                finally:
                    await context.close()
            finally:
                await browser.close()

        return ToolResult(
            output={
                "url_final": url_final,
                "screenshot_path": screenshot_path,
                "console_log": "\n".join(console_log_lines),
                "actions_run": actions_run,
            },
            metadata={
                "host": target_host,
                "url_final_host": urllib.parse.urlparse(url_final).hostname,
                "actions_run": actions_run,
                "screenshot_taken": bool(screenshot_path),
                "headful": headful,
                "console_message_count": len(console_log_lines),
            },
            side_effect_summary=(
                f"loaded {target_host}, ran {actions_run} action(s)"
                + (", screenshot saved" if screenshot_path else "")
            ),
        )


async def _run_action(page, action: dict[str, Any], timeout_ms: int) -> None:
    """Dispatch one action against the live page. Per-type semantics."""
    atype = action["type"]
    if atype == "click":
        await page.locator(action["selector"]).click(timeout=timeout_ms)
    elif atype == "type":
        await page.locator(action["selector"]).fill(
            action["text"], timeout=timeout_ms,
        )
    elif atype == "wait":
        if "ms" in action:
            await page.wait_for_timeout(int(action["ms"]))
        else:
            await page.locator(action["selector"]).wait_for(
                timeout=timeout_ms,
            )
    elif atype == "press":
        await page.keyboard.press(action["key"])
    elif atype == "hover":
        await page.locator(action["selector"]).hover(timeout=timeout_ms)
    elif atype == "select_option":
        await page.locator(action["selector"]).select_option(
            action["value"], timeout=timeout_ms,
        )
    else:  # pragma: no cover — validate() should reject earlier
        raise BrowserActionError(f"unknown action type {atype!r}")


def _env_headful() -> bool:
    """Read FSF_BROWSER_HEADFUL — defaults to False (headless)."""
    val = os.environ.get("FSF_BROWSER_HEADFUL", "").strip().lower()
    return val in ("1", "true", "yes")

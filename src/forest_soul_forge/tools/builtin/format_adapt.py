"""``format_adapt.v1`` — ADR-0088 Phase C format adapter.

Takes a primary draft and adapts it to one target format
(twitter_thread, linkedin_post, newsletter, or blog). Returns a
structured adaptation: the adapted text plus per-format
constraints applied + a quick set of metrics.

Read-only. No LLM in the tool itself — the ``format_adaptation.v1``
skill wraps this with an LLM-driven rewrite via llm_think, but
the structural adaptation (splitting, truncating, headering) is
deterministic so the operator can audit + replay it.

## What the adapter does per format

- **twitter_thread**: split the draft into ordered tweets,
  each <= 280 chars; preserves paragraph boundaries when
  possible; numbers each tweet "1/", "2/", ... unless the
  thread is a single tweet.
- **linkedin_post**: extract a single 2,500-char-max post
  with a hook (first 200 chars), body, and call-to-action;
  preserves paragraph breaks; strips heavy markdown.
- **newsletter**: produces a structured digest with a
  short subject line + three sections (TL;DR / body / asks),
  each clearly headered.
- **blog**: keep the original structure but tighten
  whitespace + ensure ATX headers are well-formed; light
  pass — blog is the source format, this is the
  ensure-publishable normalization.

The structural pass is followed by metrics: char count, word
count, section / tweet count, and format-specific overflow
flags.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_DRAFT_CHARS = 200_000
_MIN_DRAFT_CHARS = 50

_TWEET_LIMIT = 280
_LINKEDIN_LIMIT = 2_500
_LINKEDIN_HOOK_CHARS = 200
_NEWSLETTER_SUBJECT_MAX = 80

_VALID_FORMATS = {
    "twitter_thread", "linkedin_post", "newsletter", "blog",
}

_PARAGRAPH_RE = re.compile(r"\n\s*\n")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)


class FormatAdaptTool:
    """Adapt a draft to one target format.

    Args:
      draft (str, required): the primary long-form draft to
        adapt. 50..200,000 chars.
      target_format (str, required): one of "twitter_thread",
        "linkedin_post", "newsletter", "blog".
      max_tweets (int, optional): cap on tweet count for
        twitter_thread (default 25). Truncates with a warning
        if the draft would exceed.

    Output:
      {
        "generated_at":   str (ISO),
        "target_format":  str,
        "adapted_text":   str,            # joined / collapsed view
        "segments":       [str, ...],     # tweet-by-tweet or section-by-section
        "metrics":        {
          "segment_count":     int,
          "total_chars":       int,
          "total_words":       int,
          "overflow":          bool,
          "overflow_reason":   str,
        },
      }
    """

    name = "format_adapt"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        draft = args.get("draft")
        if not isinstance(draft, str) or not draft.strip():
            raise ToolValidationError(
                "draft must be a non-empty string"
            )
        if len(draft) < _MIN_DRAFT_CHARS:
            raise ToolValidationError(
                f"draft must be >= {_MIN_DRAFT_CHARS} chars; "
                f"got {len(draft)}"
            )
        if len(draft) > _MAX_DRAFT_CHARS:
            raise ToolValidationError(
                f"draft must be <= {_MAX_DRAFT_CHARS} chars; "
                f"got {len(draft)}"
            )
        target = args.get("target_format")
        if target not in _VALID_FORMATS:
            raise ToolValidationError(
                f"target_format must be one of {sorted(_VALID_FORMATS)}; "
                f"got {target!r}"
            )
        max_tweets = args.get("max_tweets")
        if max_tweets is not None:
            if not isinstance(max_tweets, int) or max_tweets <= 0:
                raise ToolValidationError(
                    "max_tweets must be a positive integer"
                )
            if max_tweets > 50:
                raise ToolValidationError(
                    "max_tweets must be <= 50"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        draft: str = args["draft"]
        target_format: str = args["target_format"]
        max_tweets: int = int(args.get("max_tweets") or 25)

        if target_format == "twitter_thread":
            segments, overflow, overflow_reason = _to_twitter_thread(
                draft, max_tweets,
            )
            joined = "\n\n".join(segments)
        elif target_format == "linkedin_post":
            joined, segments, overflow, overflow_reason = (
                _to_linkedin_post(draft)
            )
        elif target_format == "newsletter":
            joined, segments, overflow, overflow_reason = (
                _to_newsletter(draft)
            )
        else:  # blog
            joined, segments, overflow, overflow_reason = (
                _to_blog(draft)
            )

        total_chars = len(joined)
        total_words = len(re.findall(r"\b[\w']+\b", joined))

        body = {
            "generated_at": _now_iso(),
            "target_format": target_format,
            "adapted_text": joined,
            "segments": segments,
            "metrics": {
                "segment_count": len(segments),
                "total_chars": total_chars,
                "total_words": total_words,
                "overflow": overflow,
                "overflow_reason": overflow_reason,
            },
        }

        return ToolResult(
            output=body,
            metadata={
                "target_format": target_format,
                "segment_count": len(segments),
                "overflow": overflow,
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"adapted draft to {target_format}; "
                f"{len(segments)} segment(s); "
                f"{'OVERFLOW' if overflow else 'fits'}"
            ),
        )


def _to_twitter_thread(
    draft: str, max_tweets: int,
) -> tuple[list[str], bool, str]:
    """Split a draft into a tweet thread. Returns (tweets, overflow, reason).

    Algorithm: paragraph-then-sentence greedy packer. Each tweet
    is kept <= 280 chars including the trailing " N/M" suffix.
    Numbering is appended once we know the total.
    """
    # First, build a candidate list of tweet-bodies by greedy-packing
    # sentences. Reserve space for the longest possible "N/M " prefix.
    # 50 max_tweets -> "50/50 " = 6 chars; use 8 to be safe.
    body_budget = _TWEET_LIMIT - 8
    paragraphs = [
        p.strip() for p in _PARAGRAPH_RE.split(draft) if p.strip()
    ]
    units: list[str] = []
    for p in paragraphs:
        if len(p) <= body_budget:
            units.append(p)
        else:
            # split paragraph into sentences and pack
            sentences = [
                s.strip() for s in _SENTENCE_RE.split(p) if s.strip()
            ]
            current = ""
            for sent in sentences:
                if not sent:
                    continue
                if len(sent) > body_budget:
                    # sentence itself too long — hard-wrap
                    if current:
                        units.append(current)
                        current = ""
                    while len(sent) > body_budget:
                        units.append(sent[:body_budget].rstrip())
                        sent = sent[body_budget:].lstrip()
                    if sent:
                        current = sent
                    continue
                candidate = (current + " " + sent).strip() if current else sent
                if len(candidate) <= body_budget:
                    current = candidate
                else:
                    units.append(current)
                    current = sent
            if current:
                units.append(current)

    overflow = False
    overflow_reason = ""
    if len(units) > max_tweets:
        overflow = True
        overflow_reason = (
            f"draft requires {len(units)} tweets; "
            f"capped at {max_tweets}"
        )
        units = units[:max_tweets]

    total = len(units)
    if total == 1:
        return units, overflow, overflow_reason
    numbered = [
        f"{u} {i}/{total}" for i, u in enumerate(units, start=1)
    ]
    return numbered, overflow, overflow_reason


def _to_linkedin_post(
    draft: str,
) -> tuple[str, list[str], bool, str]:
    """Adapt to a single LinkedIn post. Returns (joined, segments, overflow, reason)."""
    text = _strip_headers(draft).strip()
    # paragraphs are the segments
    paragraphs = [
        p.strip() for p in _PARAGRAPH_RE.split(text) if p.strip()
    ]
    overflow = False
    overflow_reason = ""
    joined = "\n\n".join(paragraphs)
    if len(joined) > _LINKEDIN_LIMIT:
        overflow = True
        overflow_reason = (
            f"draft is {len(joined)} chars; "
            f"LinkedIn cap is {_LINKEDIN_LIMIT}"
        )
        # truncate at the last full paragraph that fits
        accumulated: list[str] = []
        running = 0
        for p in paragraphs:
            add = (len(p) + 2) if accumulated else len(p)
            if running + add > _LINKEDIN_LIMIT - 4:
                break
            accumulated.append(p)
            running += add
        if not accumulated:
            # paragraph 0 alone too long; hard-truncate
            accumulated = [paragraphs[0][: _LINKEDIN_LIMIT - 4].rstrip() + "…"]
        else:
            accumulated[-1] = accumulated[-1] + " …"
        paragraphs = accumulated
        joined = "\n\n".join(paragraphs)
    return joined, paragraphs, overflow, overflow_reason


def _to_newsletter(
    draft: str,
) -> tuple[str, list[str], bool, str]:
    """Adapt to a newsletter with subject + TL;DR + body + asks.

    Returns (joined, segments, overflow, reason). Segments are the
    four labeled sections (subject + three body sections).
    """
    text = draft.strip()
    paragraphs = [
        p.strip() for p in _PARAGRAPH_RE.split(text) if p.strip()
    ]
    # subject: first sentence of first paragraph, truncated
    first_sentence = re.split(r"(?<=[.!?])\s+", paragraphs[0], maxsplit=1)[0]
    subject = first_sentence
    if len(subject) > _NEWSLETTER_SUBJECT_MAX:
        subject = subject[: _NEWSLETTER_SUBJECT_MAX - 1].rstrip() + "…"
    # TL;DR: first paragraph if there are >= 2 paragraphs, else
    # the first sentence again
    if len(paragraphs) >= 2:
        tldr = paragraphs[0]
        body_paragraphs = paragraphs[1:-1] if len(paragraphs) > 2 else []
        asks_paragraph = (
            paragraphs[-1] if len(paragraphs) >= 2 else ""
        )
    else:
        tldr = first_sentence
        body_paragraphs = []
        asks_paragraph = ""

    body_text = "\n\n".join(body_paragraphs) if body_paragraphs else "(no body)"
    asks_text = asks_paragraph if asks_paragraph else "(no asks)"

    segments = [
        f"Subject: {subject}",
        f"TL;DR\n{tldr}",
        f"Body\n{body_text}",
        f"Asks\n{asks_text}",
    ]
    joined = "\n\n".join(segments)
    return joined, segments, False, ""


def _to_blog(draft: str) -> tuple[str, list[str], bool, str]:
    """Light normalization pass for blog format.

    Collapses 3+ consecutive newlines to a single paragraph
    break, trims trailing whitespace from each line, and
    returns the headers + body as segments.
    """
    # normalize line endings + collapse blank-line runs
    text = re.sub(r"\r\n?", "\n", draft)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # rstrip every line
    lines = [ln.rstrip() for ln in text.split("\n")]
    joined = "\n".join(lines)
    # split into sections by header
    matches = list(_HEADER_RE.finditer(joined))
    segments: list[str] = []
    if not matches:
        segments = [joined]
    else:
        # leading preamble before first header
        if matches[0].start() > 0:
            segments.append(joined[: matches[0].start()].strip())
        for i, m in enumerate(matches):
            start = m.start()
            end = (
                matches[i + 1].start()
                if i + 1 < len(matches) else len(joined)
            )
            section = joined[start:end].strip()
            if section:
                segments.append(section)
    return joined, segments, False, ""


def _strip_headers(text: str) -> str:
    return _HEADER_RE.sub("", text)


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
        + "Z"
    )

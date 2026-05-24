"""``energy_anomaly_scan.v1`` — ADR-0091 Phase B energy anomaly scanner.

Per-device deterministic anomaly classification. Each device reading
is compared against an operator-supplied baseline (mean + stddev +
sample count) using z-score windows. The verdict set is small +
stable so the energy_warden's report attestation can be replayed
cleanly by the operator + diff'd across windows.

Read-only. The ``energy_optimization.v1`` skill wraps this tool with
memory_recall of recent baselines + memory_write of the attestation;
LLM-driven recommendation narrative is layered separately.

## Verdict model

For each device reading the tool classifies into one of:

- ``spike``: ``|current - baseline_mean| >= spike_sigma * baseline_stddev``
  (default ``spike_sigma=3.0``) AND baseline sample count is
  ``>= min_baseline_samples`` (default 5). Treat as a stop-the-line
  signal; the warden surfaces these first.
- ``drift``: ``|current - baseline_mean| >= drift_sigma * baseline_stddev``
  but below ``spike_sigma`` (default ``drift_sigma=1.5``). Treat as
  a watch-item; the warden composes an attestation but doesn't
  escalate.
- ``normal``: within ``drift_sigma`` of the baseline mean.
- ``missing_baseline``: baseline_stddev <= 0, baseline_mean missing,
  or sample count below ``min_baseline_samples``. The reading is
  recorded but no verdict is drawn — the operator (or the
  forest-home-assistant connector) needs to backfill baseline.

Deterministic for the same inputs. The wrapping skill compares
across windows by re-dispatching the tool with refreshed snapshots.

side_effects=read_only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from forest_soul_forge.tools.base import (
    ToolContext,
    ToolResult,
    ToolValidationError,
)


_MAX_DEVICES = 200
_MAX_SLUG_LEN = 200
_MAX_LABEL_LEN = 500
_MIN_SAMPLES_FLOOR = 1
_MIN_SAMPLES_CEILING = 10_000


class EnergyAnomalyScanTool:
    """Classify per-device energy readings against baselines.

    Args:
      window_slug (str, required): time-bucket slug this scan binds
        to. Convention: ``evening-2026-05-24`` etc. Recorded in the
        output for the wrapping skill's attestation.
      readings (list[dict], required): per-device readings. Each
        entry:

          - ``device_slug`` (str, required): kebab-case identifier.
          - ``label`` (str, optional): human-readable device name.
          - ``current_watts`` (number, required): instantaneous
            power draw at the window time.
          - ``baseline_mean_watts`` (number, optional): the
            operator-supplied baseline mean. When missing, the
            verdict is ``missing_baseline``.
          - ``baseline_stddev_watts`` (number, optional): baseline
            standard deviation. When missing or <= 0 the verdict
            is ``missing_baseline``.
          - ``baseline_sample_count`` (int, optional): how many
            samples the baseline rests on. When < ``min_baseline_samples``
            the verdict is ``missing_baseline``.
          - ``room`` (str, optional): room slug for downstream
            grouping.
      spike_sigma (number, optional): z-score threshold for ``spike``
        verdict. Default 3.0. Must be > drift_sigma.
      drift_sigma (number, optional): z-score threshold for ``drift``
        verdict. Default 1.5.
      min_baseline_samples (int, optional): minimum sample count to
        trust the baseline. Default 5.

    Output:
      {
        "generated_at":     str (ISO),
        "window_slug":      str,
        "spike_sigma":      float,
        "drift_sigma":      float,
        "min_baseline_samples": int,
        "verdicts": [{
          "device_slug":            str,
          "label":                  str,
          "room":                   str | null,
          "current_watts":          float,
          "baseline_mean_watts":    float | null,
          "baseline_stddev_watts":  float | null,
          "baseline_sample_count":  int | null,
          "z_score":                float | null,
          "verdict":                str,    # spike / drift / normal / missing_baseline
          "rationale":              str,
        }, ...],
        "summary": {
          "device_count":           int,
          "spike_count":            int,
          "drift_count":            int,
          "normal_count":           int,
          "missing_baseline_count": int,
          "max_abs_z_score":        float,
        },
      }
    """

    name = "energy_anomaly_scan"
    version = "1"
    side_effects = "read_only"

    def validate(self, args: dict[str, Any]) -> None:
        slug = args.get("window_slug")
        if not isinstance(slug, str) or not slug.strip():
            raise ToolValidationError(
                "window_slug must be a non-empty string"
            )
        if len(slug) > _MAX_SLUG_LEN:
            raise ToolValidationError(
                f"window_slug must be <= {_MAX_SLUG_LEN} chars"
            )

        readings = args.get("readings")
        if not isinstance(readings, list):
            raise ToolValidationError("readings must be a list")
        if not readings:
            raise ToolValidationError(
                "readings must contain at least one entry"
            )
        if len(readings) > _MAX_DEVICES:
            raise ToolValidationError(
                f"readings must have <= {_MAX_DEVICES} entries; "
                f"got {len(readings)}"
            )

        seen: set[str] = set()
        for i, entry in enumerate(readings):
            if not isinstance(entry, dict):
                raise ToolValidationError(
                    f"readings[{i}] must be a dict"
                )
            ds = entry.get("device_slug")
            if not isinstance(ds, str) or not ds.strip():
                raise ToolValidationError(
                    f"readings[{i}].device_slug must be a non-empty string"
                )
            if len(ds) > _MAX_SLUG_LEN:
                raise ToolValidationError(
                    f"readings[{i}].device_slug must be <= {_MAX_SLUG_LEN} chars"
                )
            if ds in seen:
                raise ToolValidationError(
                    f"readings[{i}].device_slug duplicates earlier entry: {ds!r}"
                )
            seen.add(ds)
            label = entry.get("label")
            if label is not None:
                if not isinstance(label, str):
                    raise ToolValidationError(
                        f"readings[{i}].label must be a string"
                    )
                if len(label) > _MAX_LABEL_LEN:
                    raise ToolValidationError(
                        f"readings[{i}].label must be <= {_MAX_LABEL_LEN} chars"
                    )
            cw = entry.get("current_watts")
            if not isinstance(cw, (int, float)) or isinstance(cw, bool):
                raise ToolValidationError(
                    f"readings[{i}].current_watts must be a number"
                )
            if cw < 0:
                raise ToolValidationError(
                    f"readings[{i}].current_watts must be >= 0"
                )
            for k in (
                "baseline_mean_watts",
                "baseline_stddev_watts",
            ):
                v = entry.get(k)
                if v is not None:
                    if (
                        not isinstance(v, (int, float))
                        or isinstance(v, bool)
                    ):
                        raise ToolValidationError(
                            f"readings[{i}].{k} must be a number"
                        )
                    if v < 0:
                        raise ToolValidationError(
                            f"readings[{i}].{k} must be >= 0"
                        )
            bsc = entry.get("baseline_sample_count")
            if bsc is not None:
                if (
                    not isinstance(bsc, int)
                    or isinstance(bsc, bool)
                    or bsc < 0
                ):
                    raise ToolValidationError(
                        f"readings[{i}].baseline_sample_count "
                        "must be a non-negative integer"
                    )
            room = entry.get("room")
            if room is not None and not isinstance(room, str):
                raise ToolValidationError(
                    f"readings[{i}].room must be a string"
                )

        spike = args.get("spike_sigma")
        drift = args.get("drift_sigma")
        if spike is not None:
            if (
                not isinstance(spike, (int, float))
                or isinstance(spike, bool)
                or spike <= 0
            ):
                raise ToolValidationError(
                    "spike_sigma must be a positive number"
                )
        if drift is not None:
            if (
                not isinstance(drift, (int, float))
                or isinstance(drift, bool)
                or drift <= 0
            ):
                raise ToolValidationError(
                    "drift_sigma must be a positive number"
                )
        spike_v = float(spike) if spike is not None else 3.0
        drift_v = float(drift) if drift is not None else 1.5
        if spike_v <= drift_v:
            raise ToolValidationError(
                "spike_sigma must be strictly greater than drift_sigma"
            )

        ms = args.get("min_baseline_samples")
        if ms is not None:
            if (
                not isinstance(ms, int)
                or isinstance(ms, bool)
                or ms < _MIN_SAMPLES_FLOOR
                or ms > _MIN_SAMPLES_CEILING
            ):
                raise ToolValidationError(
                    f"min_baseline_samples must be an integer in "
                    f"[{_MIN_SAMPLES_FLOOR}, {_MIN_SAMPLES_CEILING}]"
                )

    async def execute(
        self, args: dict[str, Any], ctx: ToolContext,
    ) -> ToolResult:
        slug = args["window_slug"]
        readings = args["readings"]
        spike_sigma = float(args.get("spike_sigma") or 3.0)
        drift_sigma = float(args.get("drift_sigma") or 1.5)
        min_samples = int(args.get("min_baseline_samples") or 5)

        verdicts: list[dict[str, Any]] = []
        spike_count = drift_count = normal_count = missing_count = 0
        max_abs_z = 0.0

        for entry in readings:
            cw = float(entry["current_watts"])
            mean = entry.get("baseline_mean_watts")
            stddev = entry.get("baseline_stddev_watts")
            bsc = entry.get("baseline_sample_count")

            mean_f = (
                float(mean) if isinstance(mean, (int, float))
                and not isinstance(mean, bool) else None
            )
            stddev_f = (
                float(stddev) if isinstance(stddev, (int, float))
                and not isinstance(stddev, bool) else None
            )
            bsc_i = (
                int(bsc) if isinstance(bsc, int)
                and not isinstance(bsc, bool) else None
            )

            z_score: float | None = None
            verdict = "missing_baseline"
            rationale = (
                "Baseline missing or below min_baseline_samples; "
                "no verdict drawn."
            )

            if (
                mean_f is not None
                and stddev_f is not None
                and stddev_f > 0
                and bsc_i is not None
                and bsc_i >= min_samples
            ):
                z_score = (cw - mean_f) / stddev_f
                abs_z = abs(z_score)
                if abs_z > max_abs_z:
                    max_abs_z = abs_z
                if abs_z >= spike_sigma:
                    verdict = "spike"
                    rationale = (
                        f"|z|={abs_z:.2f} >= spike_sigma={spike_sigma:.2f}; "
                        f"current={cw:.2f}W vs. baseline mean "
                        f"{mean_f:.2f}W ± {stddev_f:.2f}W (n={bsc_i})."
                    )
                    spike_count += 1
                elif abs_z >= drift_sigma:
                    verdict = "drift"
                    rationale = (
                        f"|z|={abs_z:.2f} >= drift_sigma={drift_sigma:.2f}; "
                        f"below spike threshold; watch-item."
                    )
                    drift_count += 1
                else:
                    verdict = "normal"
                    rationale = (
                        f"|z|={abs_z:.2f} < drift_sigma={drift_sigma:.2f}; "
                        f"within baseline window."
                    )
                    normal_count += 1
            else:
                missing_count += 1

            verdicts.append({
                "device_slug":            entry["device_slug"],
                "label":                  entry.get("label") or entry["device_slug"],
                "room":                   entry.get("room"),
                "current_watts":          round(cw, 4),
                "baseline_mean_watts":    round(mean_f, 4) if mean_f is not None else None,
                "baseline_stddev_watts":  round(stddev_f, 4) if stddev_f is not None else None,
                "baseline_sample_count":  bsc_i,
                "z_score":                round(z_score, 4) if z_score is not None else None,
                "verdict":                verdict,
                "rationale":              rationale,
            })

        summary = {
            "device_count":           len(verdicts),
            "spike_count":            spike_count,
            "drift_count":            drift_count,
            "normal_count":           normal_count,
            "missing_baseline_count": missing_count,
            "max_abs_z_score":        round(max_abs_z, 4),
        }

        body = {
            "generated_at":          datetime.now(timezone.utc)
                                              .replace(tzinfo=None)
                                              .isoformat(timespec="seconds")
                                              + "Z",
            "window_slug":           slug,
            "spike_sigma":           spike_sigma,
            "drift_sigma":           drift_sigma,
            "min_baseline_samples":  min_samples,
            "verdicts":              verdicts,
            "summary":               summary,
        }

        return ToolResult(
            output=body,
            metadata={
                "window_slug":   slug,
                "device_count":  summary["device_count"],
                "spike_count":   summary["spike_count"],
                "drift_count":   summary["drift_count"],
            },
            tokens_used=None, cost_usd=None,
            side_effect_summary=(
                f"scanned {summary['device_count']} device"
                f"{'s' if summary['device_count'] != 1 else ''} "
                f"({summary['spike_count']} spike / "
                f"{summary['drift_count']} drift / "
                f"{summary['missing_baseline_count']} missing-baseline)"
            ),
        )

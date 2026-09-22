"""Compute p95/p99 per component and render the latency chart.

Reads the timing records written by app.observability and produces:

* percentiles for every stage, so a regression points at a component rather
  than at "the agent"
* each component's share of the end-to-end turn, so the percentiles can be
  related back to what the user actually waited through
* a correlation between recorded attributes and latency, because the factors
  that move it (input size, number of results) are worth knowing rather than
  guessing at

Usage:
    uv run python scripts/latency_report.py [--metrics PATH] [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
from collections import defaultdict

import matplotlib

matplotlib.use("Agg")  # headless: this runs in CI and over SSH
import matplotlib.pyplot as plt  # noqa: E402

DEFAULT_METRICS = pathlib.Path("/tmp/immigration-voice-metrics.jsonl")
DEFAULT_OUT = pathlib.Path(__file__).resolve().parent.parent / "docs" / "latency.png"


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile — no interpolation, so small n stays honest."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(pct / 100 * len(ordered) + 0.5)) - 1))
    return ordered[index]


#: Records above this are retry artifacts, not latency: when the eval harness
#: hits a rate limit it sleeps inside the measured window, so the backoff gets
#: counted as time-to-first-token. Publishing those as latency would be
#: straightforwardly wrong.
RETRY_ARTIFACT_MS = 30_000


def load(path: pathlib.Path) -> list[dict]:
    if not path.exists():
        return []
    rows, dropped = [], 0
    for line in path.read_text().splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("duration_ms", 0) > RETRY_ARTIFACT_MS:
            dropped += 1
            continue
        rows.append(row)
    if dropped:
        print(f"(excluded {dropped} records over {RETRY_ARTIFACT_MS/1000:.0f}s as retry artifacts)")
    return rows


def summarise(rows: list[dict]) -> dict[str, dict]:
    by_name: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_name[f"{row['stage']}:{row['name']}"].append(row["duration_ms"])

    return {
        name: {
            "count": len(values),
            "p50": round(percentile(values, 50), 1),
            "p95": round(percentile(values, 95), 1),
            "p99": round(percentile(values, 99), 1),
            "max": round(max(values), 1),
        }
        for name, values in sorted(by_name.items())
    }


def latency_factors(rows: list[dict]) -> list[tuple[str, int, float]]:
    """Correlate numeric attributes with duration, per stage.

    Pearson r over whatever was recorded. Weak evidence on small samples, but
    it surfaces candidates — a strong positive on input size means the way to
    get faster is to send less, not to change model.
    """
    grouped: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        for key, value in (row.get("attributes") or {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                grouped[f"{row['stage']}:{row['name']}.{key}"].append(
                    (float(value), row["duration_ms"])
                )

    out = []
    for label, pairs in grouped.items():
        if len(pairs) < 5:
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        if len(set(xs)) < 2 or len(set(ys)) < 2:
            continue
        try:
            out.append((label, len(pairs), statistics.correlation(xs, ys)))
        except statistics.StatisticsError:
            continue
    return sorted(out, key=lambda item: abs(item[2]), reverse=True)


def render(summary: dict[str, dict], out: pathlib.Path, total_key: str | None) -> None:
    # Only the critical path: mixing in whole-response timings makes the
    # chart look worse than the experience actually is.
    names = [n for n in summary if n.startswith(("ttft:", "ttft_segment:"))] or list(summary)
    if not names:
        return

    p50 = [summary[n]["p50"] for n in names]
    p95 = [summary[n]["p95"] for n in names]
    p99 = [summary[n]["p99"] for n in names]
    positions = range(len(names))
    width = 0.27

    fig, axis = plt.subplots(figsize=(10, 1.1 * len(names) + 2.5))
    axis.barh([p - width for p in positions], p50, height=width, label="p50", color="#9ecae1")
    axis.barh(list(positions), p95, height=width, label="p95", color="#3182bd")
    axis.barh([p + width for p in positions], p99, height=width, label="p99", color="#08519c")

    axis.set_yticks(list(positions))
    axis.set_yticklabels([n.replace(":", "\n") for n in names], fontsize=9)
    axis.invert_yaxis()
    axis.set_xlabel("milliseconds")
    title = "Time to first token, by component"
    ttft_key = next((k for k in summary if k.startswith("ttft:")), None)
    if ttft_key:
        title += f"  —  TTFT p95 {summary[ttft_key]['p95']:.0f} ms"
    axis.set_title(title)
    axis.legend(loc="lower right")
    axis.grid(axis="x", alpha=0.3)

    for index, name in enumerate(names):
        axis.text(
            summary[name]["p99"],
            index + width,
            f"  n={summary[name]['count']}",
            va="center",
            fontsize=8,
            color="#555",
        )

    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=140)
    print(f"chart -> {out}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metrics", type=pathlib.Path, default=DEFAULT_METRICS)
    parser.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    rows = load(args.metrics)
    if not rows:
        print(f"no timing records at {args.metrics}")
        return 1

    summary = summarise(rows)
    total_key = next((k for k in summary if k.startswith("user_turn:")), None)

    print(f"{'component':<44}{'n':>5}{'p50':>9}{'p95':>9}{'p99':>9}")
    for name, stats in summary.items():
        print(
            f"{name:<44}{stats['count']:>5}{stats['p50']:>9.0f}"
            f"{stats['p95']:>9.0f}{stats['p99']:>9.0f}"
        )

    ttft_key = next((k for k in summary if k.startswith("ttft:")), None)
    if ttft_key:
        ttft_p95 = summary[ttft_key]["p95"]
        print(f"\nTTFT p95 = {ttft_p95:.0f} ms — where that time goes:")
        segments = {k: v for k, v in summary.items() if k.startswith("ttft_segment:")}
        # Mean, not p95: p95s of independent segments do not sum to the p95 of
        # the whole, so using them here would imply a false decomposition.
        segment_mean_total = sum(
            statistics.mean([r["duration_ms"] for r in rows
                             if f"{r['stage']}:{r['name']}" == k]) for k in segments
        ) or 1
        for name, stats in sorted(
            segments.items(), key=lambda kv: kv[1]["p95"], reverse=True
        ):
            mean_ms = statistics.mean(
                [r["duration_ms"] for r in rows if f"{r['stage']}:{r['name']}" == name]
            )
            label = name.split(":", 1)[1]
            print(
                f"  {label:<26}mean {mean_ms:>7.0f} ms  "
                f"p95 {stats['p95']:>7.0f} ms  {100 * mean_ms / segment_mean_total:>4.0f}% of path"
            )
        tail = next((k for k in summary if k.startswith("response_tail:")), None)
        if tail:
            print(
                f"\n  (after first token: {summary[tail]['p95']:.0f} ms p95 — "
                "answer length, not lag)"
            )

    factors = latency_factors(rows)
    if factors:
        print("\nwhat moves latency (Pearson r):")
        for label, n, r in factors[:8]:
            print(f"  {label:<44}n={n:<4} r={r:+.2f}")

    render(summary, args.out, total_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

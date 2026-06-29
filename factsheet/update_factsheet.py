#!/usr/bin/env python3
"""Update the automatable UseGalaxy.eu factsheet SVG from public stats APIs."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


GRAFANA_BASE = "https://stats.galaxyproject.eu"
HISTORICAL_DS = {"type": "influxdb", "uid": "PEBD82B4560F292BD"}
CURRENT_DS = {"type": "influxdb", "uid": "P9B81C0353945995B"}
TIAAS_URL = "https://usegalaxy.eu/tiaas/stats/"
GTN_URL = "https://training.galaxyproject.org/training-material/stats/#gtn-statistics"
DEFAULT_FIXTURE_DIR = Path("factsheet/api-fixtures")
DEFAULT_FIXTURE_VALUE_LIMIT = 10

TEXT_IDS = {
    "n_elixir_users": "text1360-7",
    "n_monthly_users": "text354",
    "n_registered_users": "text280",
    "n_tiaas_trainees": "text1392-3-2-9",
    "n_tiaas_events": "text1392-3",
    "n_GTN_tutorials": "text1392",
    "n_histories": "text1421",
    "n_datasets": "text1411",
    "n_workflow_executions": "text1371",
    "n_jobs_run": "text302",
    "n_tools_installed": "text1418",
}


def fetch(url: str, data: bytes | None = None, headers: dict[str, str] | None = None) -> bytes:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()


def grafana_query(queries: list[dict], from_ms: int, to_ms: int) -> dict:
    payload = json.dumps({"queries": queries, "from": str(from_ms), "to": str(to_ms)}).encode()
    data = fetch(
        f"{GRAFANA_BASE}/api/ds/query",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    return json.loads(data)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def compact_grafana_result(result: dict, value_limit: int) -> dict:
    compact = copy.deepcopy(result)
    for response in compact.get("results", {}).values():
        for frame in response.get("frames", []):
            values = frame.get("data", {}).get("values", [])
            if not values:
                continue

            frame["data"]["values"] = [
                value[-value_limit:] if isinstance(value, list) and len(value) > value_limit else value
                for value in values
            ]
    return compact


def last_number(result: dict, ref_id: str, *, ignore_zero: bool = False) -> int:
    frames = result["results"][ref_id].get("frames", [])
    for frame in reversed(frames):
        values = frame["data"]["values"]
        if len(values) < 2:
            continue
        for value in reversed(values[-1]):
            if value is not None and (not ignore_zero or value != 0):
                return int(round(value))
    raise RuntimeError(f"No numeric value returned for {ref_id}")


def count_values(result: dict, ref_id: str) -> int:
    frames = result["results"][ref_id].get("frames", [])
    if not frames:
        raise RuntimeError(f"No frames returned for {ref_id}")
    return len(frames[0]["data"]["values"][0])


def latest_snapshot_queries() -> list[dict]:
    queries = []

    def add(ref_id: str, measurement: str, where: str = "$timeFilter") -> None:
        queries.append(
            {
                "refId": ref_id,
                "datasource": HISTORICAL_DS,
                "rawQuery": True,
                "resultFormat": "time_series",
                "query": (
                    f'SELECT sum("count") AS "Count" FROM "{measurement}" '
                    f"WHERE {where} GROUP BY time(1d) fill(none)"
                ),
                "intervalMs": 86_400_000,
                "maxDataPoints": 500,
            }
        )

    add(
        "registered_users",
        "server-users",
        '$timeFilter AND "deleted"=\'f\' AND "purged"=\'f\' AND "external"=\'f\'',
    )
    add("histories", "server-histories")
    add("jobs", "server-jobs")
    add("datasets", "server-datasets")
    add("workflows", "server-workflow-invocations")
    return queries


def current_queries() -> list[dict]:
    return [
        {
            "refId": "tools",
            "datasource": CURRENT_DS,
            "rawQuery": True,
            "resultFormat": "time_series",
            "query": 'SHOW TAG VALUES FROM "tool-usage" WITH KEY = "tool_id"',
            "intervalMs": 60_000,
            "maxDataPoints": 5_000,
        },
        {
            "refId": "elixir_users",
            "datasource": CURRENT_DS,
            "measurement": "users-with-oidc",
            "policy": "default",
            "resultFormat": "time_series",
            "orderByTime": "ASC",
            "select": [[{"type": "field", "params": ["count"]}, {"type": "last", "params": []}]],
            "tags": [{"key": "provider::tag", "operator": "=", "value": "elixir"}],
            "groupBy": [
                {"type": "time", "params": ["60000ms"]},
                {"type": "tag", "params": ["provider"]},
                {"type": "fill", "params": ["0"]},
            ],
            "intervalMs": 60_000,
            "maxDataPoints": 500,
        },
        {
            "refId": "monthly_users",
            "datasource": CURRENT_DS,
            "rawQuery": True,
            "resultFormat": "table",
            "query": (
                'SELECT last("active_users") as "Active users" '
                'FROM "galaxy_monthly_active_users" GROUP BY "month"::tag'
            ),
            "intervalMs": 60_000,
            "maxDataPoints": 10,
        },
    ]


def parse_tiaas_html(text: str) -> dict[str, int]:
    plain = html.unescape(re.sub(r"<[^>]+>", " ", text))
    plain = " ".join(plain.split())
    events = re.search(r"Overall\s+([\d,]+)\s+Events since", plain)
    trainees = re.search(r"Overall\s+([\d,]+)\s+Students taught", plain)
    if not events or not trainees:
        raise RuntimeError("Could not parse TIaaS stats page")
    return {
        "events": int(events.group(1).replace(",", "")),
        "trainees": int(trainees.group(1).replace(",", "")),
    }


def parse_tiaas() -> dict[str, int]:
    return parse_tiaas_html(fetch(TIAAS_URL).decode("utf-8", errors="replace"))


def parse_gtn_html(text: str) -> dict[str, int]:
    match = re.search(r'<div class="card-title">([\d,]+)</div>\s*<div class="card-text">Tutorials</div>', text)
    if not match:
        raise RuntimeError("Could not parse GTN stats page")
    return {"tutorials": int(match.group(1).replace(",", ""))}


def parse_gtn() -> dict[str, int]:
    return parse_gtn_html(fetch(GTN_URL).decode("utf-8", errors="replace"))


def fmt_int(value: int) -> str:
    return f"{value:,}"


def fmt_nearest(value: int, step: int, plus: bool = False) -> str:
    if abs(value) < step:
        return fmt_int(value) + ("+" if plus else "")
    rounded = int(round(value / step) * step)
    suffix = "+" if plus else ""
    return f"{rounded:,}{suffix}"


def fmt_k(value: int, plus: bool = False) -> str:
    rounded = int(round(value / 1_000))
    return f"{rounded}K" + ("+" if plus else "")


def fmt_m(value: int) -> str:
    rounded = value / 1_000_000
    if rounded < 10:
        return f"{rounded:.1f}M"
    return f"{int(round(rounded))}M"


def replace_text(svg: str, text_id: str, value: str) -> str:
    pattern = re.compile(rf'(<text\b(?:(?!</text>).)*?\bid="{re.escape(text_id)}"(?:(?!</text>).)*?>)(.*?)(</text>)', re.S)
    match = pattern.search(svg)
    if not match:
        raise RuntimeError(f"Could not find text element {text_id}")
    body = match.group(2)
    tspan_pattern = re.compile(r"(>)([^<>]*)(</tspan>)", re.S)
    body, replacements = tspan_pattern.subn(lambda m: f"{m.group(1)}{html.escape(value)}{m.group(3)}", body, count=1)
    if replacements != 1:
        raise RuntimeError(f"Could not replace tspan text in {text_id}")
    return svg[: match.start(2)] + body + svg[match.end(2) :]


def collect_source_data(
    *,
    fixture_dir: Path | None = None,
    save_fixtures: bool = False,
    fixture_value_limit: int = DEFAULT_FIXTURE_VALUE_LIMIT,
) -> dict[str, dict]:
    now_ms = int(time.time() * 1000)
    six_hours_ago_ms = now_ms - 6 * 60 * 60 * 1000
    one_year_ago_ms = int((dt.datetime.now(dt.UTC) - dt.timedelta(days=365)).timestamp() * 1000)

    if fixture_dir and not save_fixtures:
        return {
            "grafana_snapshots": read_json(fixture_dir / "grafana_snapshots.json"),
            "grafana_current": read_json(fixture_dir / "grafana_current.json"),
            "tiaas_stats": read_json(fixture_dir / "tiaas_stats.json"),
            "gtn_stats": read_json(fixture_dir / "gtn_stats.json"),
        }

    snapshots = grafana_query(latest_snapshot_queries(), one_year_ago_ms, now_ms)
    current = grafana_query(current_queries(), six_hours_ago_ms, now_ms)
    tiaas = parse_tiaas()
    gtn = parse_gtn()

    source_data = {
        "grafana_snapshots": snapshots,
        "grafana_current": current,
        "tiaas_stats": tiaas,
        "gtn_stats": gtn,
    }
    if fixture_dir and save_fixtures:
        for name, data in source_data.items():
            if name.startswith("grafana_"):
                data = compact_grafana_result(data, fixture_value_limit)
            write_json(fixture_dir / f"{name}.json", data)
    return source_data


def collect_values(
    *,
    fixture_dir: Path | None = None,
    save_fixtures: bool = False,
    fixture_value_limit: int = DEFAULT_FIXTURE_VALUE_LIMIT,
) -> dict[str, str]:
    source_data = collect_source_data(
        fixture_dir=fixture_dir,
        save_fixtures=save_fixtures,
        fixture_value_limit=fixture_value_limit,
    )
    snapshots = source_data["grafana_snapshots"]
    current = source_data["grafana_current"]
    tiaas = source_data["tiaas_stats"]
    gtn = source_data["gtn_stats"]

    values = {
        "n_elixir_users": fmt_nearest(last_number(current, "elixir_users", ignore_zero=True), 100, plus=True),
        "n_monthly_users": fmt_nearest(last_number(current, "monthly_users"), 100),
        "n_registered_users": fmt_nearest(last_number(snapshots, "registered_users"), 10_000, plus=True),
        "n_tiaas_trainees": fmt_k(tiaas["trainees"], plus=True),
        "n_tiaas_events": fmt_nearest(tiaas["events"], 100, plus=True),
        "n_GTN_tutorials": fmt_nearest(gtn["tutorials"], 100, plus=True),
        "n_histories": fmt_m(last_number(snapshots, "histories")),
        "n_datasets": fmt_m(last_number(snapshots, "datasets")),
        "n_workflow_executions": fmt_k(last_number(snapshots, "workflows")),
        "n_jobs_run": fmt_m(last_number(snapshots, "jobs")),
        "n_tools_installed": fmt_nearest(count_values(current, "tools"), 100),
    }
    return values


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("svg", nargs="?", default="factsheet/factsheet_automatable_eu.svg", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the updated SVG to this path instead of overwriting the input SVG.",
    )
    parser.add_argument(
        "--fixture-dir",
        default=DEFAULT_FIXTURE_DIR,
        type=Path,
        help="Directory for API JSON fixtures.",
    )
    parser.add_argument(
        "--save-fixtures",
        action="store_true",
        help="Save API responses and parsed external stats as JSON fixtures.",
    )
    parser.add_argument(
        "--use-fixtures",
        action="store_true",
        help="Read JSON fixtures instead of querying public endpoints.",
    )
    parser.add_argument(
        "--fixture-value-limit",
        default=DEFAULT_FIXTURE_VALUE_LIMIT,
        type=int,
        help="Maximum values to keep in each Grafana fixture data.values array when saving fixtures.",
    )
    args = parser.parse_args()
    if args.save_fixtures and args.use_fixtures:
        parser.error("--save-fixtures and --use-fixtures cannot be combined")

    fixture_dir = args.fixture_dir if args.save_fixtures or args.use_fixtures else None
    values = collect_values(
        fixture_dir=fixture_dir,
        save_fixtures=args.save_fixtures,
        fixture_value_limit=args.fixture_value_limit,
    )
    svg = args.svg.read_text()
    for key, value in values.items():
        svg = replace_text(svg, TEXT_IDS[key], value)

    if args.dry_run:
        for key in sorted(values):
            print(f"{key}: {values[key]}")
    else:
        output = args.output or args.svg
        output.write_text(svg)
        for key in sorted(values):
            print(f"{key}: {values[key]}")
        if args.output:
            print(f"wrote: {args.output}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, urllib.error.URLError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)

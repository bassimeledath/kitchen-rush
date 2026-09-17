#!/usr/bin/env python3
"""Read-only endpoint preflight for every roster entry; never calls inference."""

import concurrent.futures
import datetime
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "data/v2/roster.json"
data = json.loads(path.read_text())
models = sorted(
    {
        r["model"]
        for r in data["models"]
        if "/" in r.get("model", "") and not r["model"].startswith("modal:")
    }
)


def fetch(mid):
    url = "https://openrouter.ai/api/v1/models/" + mid + "/endpoints"
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            result = json.load(response)
        (ROOT / "data/v2" / ("endpoints-" + mid.replace("/", "-") + ".json")).write_text(
            json.dumps(result, indent=2) + "\n"
        )
        routes = []
        for e in result["data"]["endpoints"]:
            params = e.get("supported_parameters", [])
            if (
                e.get("status") == 0
                and "structured_outputs" in params
                and ("max_tokens" in params or "max_completion_tokens" in params)
            ):
                routes.append(
                    {
                        "provider": e["provider_name"],
                        "route": e["tag"],
                        "pricing": e["pricing"],
                        "supported_parameters": params,
                    }
                )
        return mid, routes, None
    except Exception as exc:
        return mid, [], type(exc).__name__


with concurrent.futures.ThreadPoolExecutor(6) as pool:
    results = {mid: (routes, error) for mid, routes, error in pool.map(fetch, models)}
for row in data["models"]:
    if row.get("model") in results:
        routes, error = results[row["model"]]
        row["compatible_routes"] = routes
        row["preflight_error"] = error
        row["status"] = "compatible_route_pending_canary" if routes else "blocked_route_contract"
    row["v2_configuration_status"] = "not yet frozen for full sweep"
    row["alias_of"] = {
        "gpt-oss-120b": "gpt-oss-120b·think",
        "grok-build-0.1": "grok-build·think",
    }.get(row["label"])
data["retrieved_at_utc"] = datetime.datetime.now(datetime.UTC).isoformat()
data["canonical_entry_count"] = sum(r["alias_of"] is None for r in data["models"])
path.write_text(json.dumps(data, indent=2) + "\n")
lines = [
    "# Full v2 model roster",
    "",
    "All existing board entries remain in scope. Alias rows are explicit; "
    "no inference was run by this preflight.",
    "",
    "| Existing label | Model endpoint | Route preflight |",
    "|---|---|---|",
]
for row in data["models"]:
    status = "Alias of " + row["alias_of"] if row["alias_of"] else row["status"]
    lines.append(f"| {row['label']} | `{row.get('model', 'unmapped')}` | {status} |")
lines += [
    "",
    "Route compatibility is metadata only. Reasoning settings and a real canary must be "
    "checked before each full-sweep configuration is eligible. Historical thinking/off "
    "variants remain listed; they must not be silently collapsed. PhoneLLM requires a "
    "defensible allocation of its compute bill before a dollar-budget ranking.",
    "",
    "Source: the frozen JSON at `data/v2/roster.json`, its source-board hashes, "
    "and saved OpenRouter endpoint responses.",
]
(ROOT / "docs/v2/ROSTER.md").write_text("\n".join(lines) + "\n")
print("Canonical entries:", data["canonical_entry_count"])
print(
    "Blocked:",
    [(r["label"], r["status"]) for r in data["models"] if r["status"].startswith("blocked")],
)

#!/usr/bin/env python3
"""Offline, independent preflight of the frozen v2 roster and actual runner mapping.

Never loads credentials or calls an API. Tariff checks deliberately do not use the
provider's tariff helper: they walk archived JSON leaves and classify pricing fields.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import runpy
from collections import Counter
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CEILING = 16384


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text())


def archived_json(root, path, expected_hash):
    target = (root / path).resolve()
    require(target.is_relative_to(root.resolve()), f"metadata escapes workspace: {path}")
    raw = target.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected_hash, f"metadata hash mismatch: {path}")
    return json.loads(raw)


def numeric(value, label):
    require(not isinstance(value, bool), f"boolean tariff: {label}")
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"invalid tariff: {label}") from exc
    require(amount.is_finite() and amount >= 0, f"invalid tariff: {label}")
    return amount


def independent_tariffs(pricing):
    """Classify leaves at arbitrary nesting depth; include every cache TTL premium."""
    leaves = []
    pending = [((), pricing)]
    while pending:
        path, value = pending.pop()
        if isinstance(value, dict):
            pending.extend(((*path, key), item) for key, item in value.items())
        elif isinstance(value, list):
            pending.extend(((*path, str(i)), item) for i, item in enumerate(value))
        else:
            leaves.append((path, value))
    buckets = {"input": [], "output": [], "request": []}
    ignored = set()
    for path, value in leaves:
        key = path[-1]
        if key == "prompt" or key.startswith("input_cache_"):
            category = "input"
        elif key in ("completion", "internal_reasoning"):
            category = "output"
        elif key in ("request", "request_fee"):
            category = "request"
        elif key in (
            "discount",
            "web_search",
            "image",
            "audio",
            "input_audio_cache",
            "input_audio",
            "output_audio",
            "image_output",
            "audio_output",
        ) or key.endswith("_tokens"):
            ignored.add(key)
            continue
        else:
            raise ValueError(f"unclassified pricing field: {'.'.join(path)}")
        buckets[category].append(numeric(value, ".".join(path)))
    require(buckets["input"] and buckets["output"], "missing text input/output tariffs")
    return (
        max(buckets["input"]),
        max(buckets["output"]),
        max(buckets["request"], default=Decimal(0)),
        sorted(ignored),
    )


def same_money(value, expected, message):
    require(numeric(value, message) == numeric(expected, message), message)


def check_reasoning(row, model_metadata):
    description = model_metadata.get("reasoning", {})
    require(row["model_reasoning_metadata"] == description, f"reasoning metadata: {row['key']}")
    require(
        row["provider_default_parameters"] == model_metadata.get("default_parameters", {}),
        f"provider defaults changed: {row['key']}",
    )
    mode, sent = row["reasoning_mode"], row["reasoning"]
    if mode == "native":
        require(sent is None, f"native mode sends reasoning override: {row['key']}")
        require(
            not description
            or (description.get("mandatory") is True and not description.get("supported_efforts")),
            f"silent selectable reasoning default: {row['key']}",
        )
    elif mode == "off":
        require(sent == {"enabled": False}, f"off mode mismatch: {row['key']}")
        require(not description.get("mandatory"), f"mandatory reasoning disabled: {row['key']}")
    else:
        require(sent == {"effort": mode}, f"effort mismatch: {row['key']}")
        efforts = description.get("supported_efforts")
        require(
            "supported_efforts" in description and (efforts is None or mode in efforts),
            f"unsupported reasoning effort: {row['key']} / {mode}",
        )
        require(
            mode != "none" or not description.get("mandatory"),
            f"mandatory reasoning disabled: {row['key']}",
        )
    if sent is not None:
        require("reasoning" in row["supported_parameters"], f"route lacks reasoning: {row['key']}")


def audit(*, root=ROOT, roster=None, runtime_catalog=None):
    root = Path(root)
    roster_path = root / "data/v2/release-r2-roster.json"
    source_bytes = (
        roster_path.read_bytes() if roster is None else json.dumps(roster, sort_keys=True).encode()
    )
    roster = json.loads(source_bytes)
    original = read_json(root / "data/v2/roster.json")
    previous = read_json(root / "data/v2/release-roster.json")
    previous_models = {m["key"]: m for m in previous["models"]}
    rows = roster["models"]
    require(len(rows) == roster["configuration_count"] == 30, "expected 30 configurations")
    require(len({m["key"] for m in rows}) == 30, "duplicate configuration key")
    labels = [label for row in rows for label in row["historical_labels"]]
    require(len(labels) == roster["historical_label_count"] == 32, "expected 32 historical labels")
    require(
        Counter(labels) == Counter(row["label"] for row in original["models"]),
        "historical label coverage changed or duplicated",
    )
    require(
        roster["global_inference_rules"]["generated_max_output_tokens_per_request"] == CEILING,
        "stale or changed global output ceiling; expected 16384",
    )
    for key, value in {
        "allow_fallbacks": False,
        "require_parameters": True,
        "no_automatic_retry": True,
        "reasoning_fixed_across_episode_budgets": True,
    }.items():
        require(roster["global_inference_rules"][key] == value, f"shared contract changed: {key}")
    blocked = [m for m in rows if m["status"].startswith("blocked")]
    require([m["key"] for m in blocked] == ["phonellm-alpha-1"], "unexpected blocked roster")
    require(bool(blocked[0].get("block_reason")), "blocked PhoneLLM needs a reason")
    eligible = {m["key"]: m for m in rows if not m["status"].startswith("blocked")}
    require(len(eligible) == 29, "expected 29 eligible configurations")
    maxima, ignored_fields = {}, set()
    for row in rows:
        key = row["key"]
        require(key in previous_models, f"unmapped configuration key: {key}")
        if key != "gemini-3-5-flash":
            for field in (
                "label",
                "model",
                "transport",
                "route_slug",
                "provider",
                "reasoning",
                "reasoning_mode",
                "input_price",
                "output_price",
                "historical_labels",
            ):
                require(
                    row[field] == previous_models[key][field],
                    f"unannounced migration: {key}/{field}",
                )
        if row["status"].startswith("blocked"):
            continue
        evidence = row["metadata_evidence"]
        if row["transport"] == "typesafe":
            archived_json(
                root, evidence["pricing_evidence_file"], evidence["pricing_evidence_sha256"]
            )
            require(
                row["model"] == "jev-1.13.0" and row["reasoning"] is None,
                "Jev model/typed interface changed",
            )
            require(row["output_cap_applies"] is False, "Jev has no generation-output ceiling")
            require(
                row["token_view_eligibility"] == "ineligible_generation_tokens_incomparable",
                "Jev incorrectly generation-token eligible",
            )
            same_money(row["input_price"], "0.000000042", "Jev input tariff")
            same_money(row["output_price"], 0, "Jev output tariff")
            same_money(row["request_fee"], 0, "Jev request fee")
            maxima[key] = (Decimal("0.000000042"), Decimal(0), Decimal(0))
            continue
        require(row["transport"] == "openrouter", f"unsupported eligible transport: {key}")
        metadata = archived_json(root, evidence["models_file"], evidence["models_sha256"])
        model = next((m for m in metadata["data"] if m["id"] == row["model"]), None)
        require(model is not None, f"missing model metadata: {key}")
        endpoint_data = archived_json(root, evidence["endpoint_file"], evidence["endpoint_sha256"])
        endpoints = [e for e in endpoint_data["data"]["endpoints"] if e["tag"] == row["route_slug"]]
        require(len(endpoints) == 1, f"selected route missing or ambiguous: {key}")
        endpoint = endpoints[0]
        require(endpoint["model_id"] == row["model"], f"endpoint model mismatch: {key}")
        require(endpoint["provider_name"] == row["provider"], f"endpoint provider mismatch: {key}")
        require(endpoint["name"] == evidence["endpoint_name"], f"endpoint name mismatch: {key}")
        require(
            row["endpoint_quantization"] == endpoint.get("quantization"),
            f"endpoint quantization mismatch: {key}",
        )
        require(endpoint["status"] == 0, f"endpoint unavailable in snapshot: {key}")
        for field in (
            "context_length",
            "max_prompt_tokens",
            "max_completion_tokens",
            "supported_parameters",
        ):
            require(row[field] == endpoint.get(field), f"endpoint {field} mismatch: {key}")
        require(
            row["max_output"] == CEILING <= endpoint["max_completion_tokens"],
            f"unsupported/shared output ceiling: {key}",
        )
        require(
            "structured_outputs" in row["supported_parameters"],
            f"missing structured outputs: {key}",
        )
        require(
            any(p in row["supported_parameters"] for p in ("max_tokens", "max_completion_tokens")),
            f"missing enforced output cap: {key}",
        )
        require(row["pricing_raw"] == endpoint["pricing"], f"pricing snapshot mismatch: {key}")
        same_money(row["input_price"], endpoint["pricing"]["prompt"], f"base input price: {key}")
        same_money(
            row["output_price"], endpoint["pricing"]["completion"], f"base output price: {key}"
        )
        inp, out, fee, ignored = independent_tariffs(endpoint["pricing"])
        ignored_fields.update(ignored)
        same_money(row["operational_max_input_price"], inp, f"maximum input/cache tariff: {key}")
        same_money(
            row["operational_max_output_price"], out, f"maximum output/reasoning tariff: {key}"
        )
        same_money(row["request_fee"], fee, f"maximum request fee: {key}")
        expected_bound = float(inp) * row["context_length"] + float(out) * CEILING + float(fee)
        require(
            math.isclose(
                row["operational_context_upper_bound_usd"],
                expected_bound,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ),
            f"incorrect context reserve: {key}",
        )
        maxima[key] = inp, out, fee
        check_reasoning(row, model)
        if key == "gemini-3-5-flash":
            require(row["label"] == "gemini-3.5-flash·minimal", "Gemini migration mislabeled")
            require(row["reasoning"] == {"effort": "minimal"}, "Gemini migration effort changed")
            require(
                row["historical_labels"] == ["gemini-3.5-flash"] and row["migration_notes"],
                "Gemini migration provenance missing",
            )
    runner = runpy.run_path(str(root / "scripts/release_v2.py"))
    if runtime_catalog is None:
        runtime_catalog = runner["models"]()
    require(set(runtime_catalog) == set(eligible), "runner drops/adds eligible configurations")
    for key, runtime in runtime_catalog.items():
        row = eligible[key]
        actual = asdict(runtime)
        for field in (
            "key",
            "model",
            "provider",
            "route_slug",
            "reasoning",
            "max_output",
            "input_price",
            "output_price",
        ):
            require(actual[field] == row[field], f"runner field mismatch: {key}/{field}")
        inp, out, fee = maxima[key]
        same_money(actual["reservation_input_price"], inp, f"runtime input reserve: {key}")
        same_money(actual["reservation_output_price"], out, f"runtime output reserve: {key}")
        same_money(actual["request_fee"], fee, f"runtime request reserve: {key}")
        if row["transport"] == "typesafe":
            require(
                actual["prompt_limit"] == 1_000_000
                and actual["reservation_basis"]
                == "estimated_1M_input_tokens_no_documented_provider_limit",
                "Jev estimate mislabeled as provider guarantee",
            )
        else:
            require(
                actual["prompt_limit"] == row["context_length"], f"runtime context reserve: {key}"
            )
        client = runner["Client"](runtime)
        canonical = {"state": {}, "questions": {"next_action": {"criteria": {"wait(1)": None}}}}
        token_limits = (None,) if row["transport"] == "typesafe" else (None, 1, 512, 2048, 8192)
        for token_limit in token_limits:
            # A tiny positive scored allowance must not be used as an operational bound.
            prepared = client.prepare(canonical, 1e-9, token_limit)
            require(prepared is not None, f"reservation incorrectly stops scored episode: {key}")
            payload, bound = prepared
            generated = 0 if row["transport"] == "typesafe" else token_limit or CEILING
            if row["transport"] != "typesafe":
                require(payload["max_tokens"] == generated, f"remaining-token ceiling: {key}")
                require(
                    payload["provider"]["order"] == [row["route_slug"]]
                    and payload["provider"]["allow_fallbacks"] is False
                    and payload["provider"]["require_parameters"] is True,
                    f"runtime route policy changed: {key}",
                )
                require(payload.get("reasoning") == row["reasoning"], f"runtime reasoning: {key}")
            expected_bound = (
                actual["prompt_limit"] * float(inp) + generated * float(out) + float(fee)
            )
            require(
                math.isclose(bound, expected_bound, rel_tol=1e-12, abs_tol=1e-12),
                f"runtime bound ignores tariff/cap: {key}",
            )
    return {
        "status": "passed",
        "configurations": len(rows),
        "historical_labels": len(labels),
        "eligible_configurations": len(eligible),
        "endpoint_routes_verified": len(maxima) - 1,
        "shared_generated_output_ceiling": CEILING,
        "pricing_fields_excluded_as_unused_or_thresholds": sorted(ignored_fields),
        "runtime_mapping_verified": True,
        "roster_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "note": "Offline metadata consistency only; paid API conformance remains a separate gate.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(audit(root=args.root), indent=2))

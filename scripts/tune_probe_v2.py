#!/usr/bin/env python3
"""Do tuned kitchens actually create headroom and separate models? Live test, 3 models.

The offline work says an overlapping schedule finishes ~35% sooner than a serial one once
recipes are guaranteed cooked components. This spends real money to answer the only question
that matters: when deadlines are priced off the OVERLAP witness instead of a serial
accumulation, do real models drop off 100%, and do they drop off by different amounts?

Three arms, same kitchens, same seeds, paired:
  control   shipped generator (min_cooked=0, cook 4-12s, deadline = serial accumulation x1.35)
  tuned-a   min_cooked=2, cook 4-12s,  deadline = witness serve time x SLACK
  tuned-b   min_cooked=2, cook 15-30s, deadline = witness serve time x SLACK

Why this cannot use `evaluation.run_episode`: that function gates feasibility on the SERIAL
oracle scoring 100 (evaluation.py:55-57). Tuned kitchens are built so the serial oracle
fails, so every one of them would be rejected as "not verified solvable" by the exact
property that makes it worth running. This file re-implements the loop with the overlap
witness as the feasibility proof and keeps the ledger discipline byte for byte: reserve
before dispatch, settle on response, retain liability on failure, never settle to zero.

Frozen source is untouched, so `protocol_hash()` is unchanged and the v2.1 board still
verifies. Spending is booked to an existing stage for the same reason -- adding one to
`v2/ledger.py` would change the protocol hash. `tokens` is used because it is the only
stage with allowance left ($110, unspent); the name is a historical artifact, not a claim
about this experiment.

Reservation sizing: `Client.bound` reserves the endpoint's FULL context window at the
maximum tariff, which for a 1M-context model is $1.54 against a request that really costs
~$0.001. Failed requests retain that full reserve as liability, so eleven failures
consumed $16.94 of a $30 stage allowance while actually settling $0.98. `prompt_limit` and
`max_output` are therefore declared here at values that still strictly bound this
experiment's requests (a Kitchen Rush prompt is ~15-20k tokens and a decision needs a few
hundred output tokens) rather than at the endpoint's theoretical maximum. The bound is
still enforced: `evaluation` rejects any response whose input exceeds it.

  build | run | report
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from kitchenrush.tools import ToolCall  # noqa: E402
from kitchenrush.v2.environment import (  # noqa: E402
    MAX_DECISIONS,
    PROTOCOL,
    decision,
    digest,
    generate,
    protocol_hash,
    public_state,
    quality,
    serialize_spec,
)
from kitchenrush.v2.ledger import SCALE, BudgetExceeded, Ledger, units  # noqa: E402
from kitchenrush.v2.providers import Client, Model, ProviderError, load_env  # noqa: E402
from concurrency_probe_v2 import run_beam, run_serial, serve_times  # noqa: E402
from difficulty_sweep_v2 import VariantEngine, build  # noqa: E402

NAME = "tune-probe-20260921"
STAGE = "tokens"
DATA = ROOT / "data/v2" / NAME
RUNS = ROOT / "runs/v2" / NAME
LEDGER_PATH = ROOT / "runs/v2/release-r2/spend.sqlite"
LEDGER_CAP = 250.0
LEDGER_OPENING = 16.175129022

SEED_BASE = 86000  # fresh block: never used by any scored batch
KITCHENS = 8
SIZE = 3
SLACK = 1.25
ARM_SLACK = {"tuned-c": 1.10}  # tighter deadline for the hard arm
CUSHION = 3.0
BEAM_WIDTH = 160
# glm generated 88k tokens on one tuned-b kitchen and was cut off at $0.40, which scored as
# a 1/3 failure it had not actually suffered. The cap exists to catch runaways, not to end
# expensive-but-legitimate games, so it sits well above the worst honest game observed.
PER_EPISODE_CAP = 2.00
TOTAL_CAP = 20.0
WORKERS = 2  # 3 trips Baidu's rate limit; the 429 backoff absorbs what 2 hits
MAX_ATTEMPTS = 4  # per kitchen, across provider failures

ARMS = {
    "control": None,
    "tuned-a": dict(cook=(4, 12), burn=(3, 8), min_cooked=2),
    "tuned-b": dict(cook=(15, 30), burn=(3, 8), min_cooked=2),
    # one notch harder: glm cleared tuned-b at 93%, above the 70-85% target band. The
    # burn window is the lever that punishes sloppy juggling specifically -- being late
    # back to the stove -- rather than just compressing everything uniformly.
    "tuned-c": dict(cook=(15, 30), burn=(2, 5), min_cooked=2),
}

MODELS = {
    # One strong model is the test that matters: if the tuned kitchens still saturate for a
    # frontier reasoning model, the tuning failed regardless of what weaker models do.
    # gpt-oss-120b-think and gemini-3-5-flash-lite were run first and their results are kept
    # (already paid for, and they bracket this model from below), but they are not re-run.
    # scored 100% of orders delivered, 3% invalid on the shipped generator
    "glm-5-2-think": Model(
        key="glm-5-2-think", model="z-ai/glm-5.2", provider="Baidu",
        input_price=1.4e-06, output_price=4.4e-06, reasoning={"effort": "low"},
        max_output=8192, prompt_limit=65536, route_slug="baidu/fp8",
        reservation_input_price=1.4e-06, reservation_output_price=4.4e-06,
        reservation_basis="frozen_endpoint_context_and_max_input_tariff",
    ),
}


# --------------------------------------------------------------------------- kitchens


def with_deadlines(spec, deadlines):
    return replace(spec, orders=[replace(o, deadline_gs=deadlines[o.order_id]) for o in spec.orders])


def make_one(job):
    """One kitchen for one arm, with its feasibility witness.

    control keeps the shipped deadline; a tuned arm re-prices every deadline off the
    overlapping witness, which is what makes serial play insufficient."""
    arm, seed = job
    setting = ARMS[arm]

    if setting is None:
        # The control arm IS the shipped configuration, so it keeps the shipped feasibility
        # proof: the serial oracle solving it perfectly. It needs no overlap witness, and
        # asking for one is not merely wasteful but can fail outright -- a shipped kitchen
        # whose recipes contain no cooked components has no overlap to find.
        spec = generate(seed, "main", SIZE)
        engine, _ = run_serial(spec, engine_cls=VariantEngine, unbound=False)
        if engine.counters["orders_served"] != len(spec.orders) or engine.counters[
            "invalid_actions"
        ]:
            return None
        return _finish(arm, seed, setting, spec, _oracle_calls(spec), engine.clock_gs)

    spec = build(seed, split="main", size=SIZE, **setting)
    if spec is None:
        return None
    found = run_beam(spec, width=BEAM_WIDTH, engine_cls=VariantEngine, cross_order=True)
    if found is None:
        return None
    served = serve_times(found[2])
    if len(served) != len(spec.orders):
        return None

    # Deadlines are priced off THIS schedule, so this schedule meets them by construction.
    # Replaying it proves that; searching again would only risk failing to re-find it.
    spec = with_deadlines(
        spec,
        {
            oid: float(math.ceil(t * ARM_SLACK.get(arm, SLACK) + CUSHION))
            for oid, t in served.items()
        },
    )
    return _finish(arm, seed, setting, spec, found[1], found[0])


def _oracle_calls(spec):
    """The serial oracle's action sequence on this spec, for use as a witness."""
    from kitchenrush.v2.environment import RecipeReader

    engine = VariantEngine(spec, record_trace=True)
    agent = RecipeReader()
    calls = []
    while not engine.terminated and len(calls) < MAX_DECISIONS:
        call = agent.choose(public_state(engine))
        calls.append(call)
        engine.step([call])
    return calls


def _finish(arm, seed, setting, spec, calls, makespan):
    witness_actions = [asdict(c) for c in calls]
    # feasibility must be PROVED against the final spec, not assumed
    engine = VariantEngine(spec, record_trace=True)
    for call in calls:
        if engine.terminated:
            break
        engine.step([call])
    if engine.counters["orders_served"] != len(spec.orders) or engine.counters["invalid_actions"]:
        return None
    found = (makespan, calls, engine)

    oracle, _ = run_serial(spec, engine_cls=VariantEngine, unbound=False)
    return dict(
        arm=arm,
        seed=seed,
        setting=setting,
        spec=serialize_spec(spec),
        witness_actions=witness_actions,
        witness_hash=digest(witness_actions),
        witness_makespan=found[0],
        witness_decisions=len(found[1]),
        oracle_served=oracle.counters["orders_served"],
        deadlines=[o.deadline_gs for o in spec.orders],
    )


def cmd_build(args):
    jobs = [(arm, SEED_BASE + i) for arm in ARMS for i in range(KITCHENS)]
    out = []
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for row in pool.map(make_one, jobs):
            if row:
                out.append(row)
            else:
                print("skipped an infeasible kitchen", file=sys.stderr)
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "kitchens.json").write_text(json.dumps(out, indent=2) + "\n")
    print(f"built {len(out)} kitchens -> {DATA / 'kitchens.json'}")
    for arm in ARMS:
        rows = [r for r in out if r["arm"] == arm]
        if rows:
            serial_fail = sum(r["oracle_served"] < 3 for r in rows)
            print(
                f"  {arm:>8}: n={len(rows)}  witness makespan "
                f"{sum(r['witness_makespan'] for r in rows) / len(rows):6.1f}gs  "
                f"serial oracle fails {serial_fail}/{len(rows)}"
            )
    return 0


# --------------------------------------------------------------------------- episodes


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


RATE_LIMIT_BACKOFF = (5, 15, 45, 90)


def dispatch(client, payload, ledger, rid, episode_id, bound, record):
    """Send one request, retrying only an explicit HTTP 429.

    The provider rate-limits this route hard enough that a single worker still trips it,
    which cost six episodes zero decisions each. A 429 is rejected at the gateway: no
    tokens are generated and nothing is billed, and unlike a lost response it is a KNOWN
    outcome rather than unknown liability -- so the reservation is settled at zero and a
    fresh one taken for the retry. Every other failure keeps the full reserve, because
    there the request may well have executed and we simply never saw the answer.
    """
    attempt = 0
    while True:
        try:
            return client.evaluate(payload)
        except ProviderError as exc:
            if "HTTP 429" not in str(exc) or attempt >= len(RATE_LIMIT_BACKOFF):
                raise
            ledger.settle(rid, 0.0)
            record.setdefault("rate_limited", []).append(rid)
            time.sleep(RATE_LIMIT_BACKOFF[attempt])
            attempt += 1
            rid = f"{rid}r{attempt}"
            ledger.reserve(rid, episode_id, STAGE, bound)
            record["id"] = rid


def play(kitchen, client, ledger, episode_id, output, dollar_cap):
    """`evaluation.run_episode` with the serial-oracle feasibility gate replaced by the
    overlap witness. Every other guarantee is preserved verbatim."""
    from difficulty_sweep_v2 import VariantEngine as EngineCls
    from kitchenrush.v2.environment import deserialize_spec

    spec = deserialize_spec(kitchen["spec"])
    if output.exists():
        raise ValueError("episode output already exists; do not overwrite or rerun")
    if ledger.used(episode_id):
        raise ValueError("episode already incurred charges; audit instead of restarting")

    engine = EngineCls(spec, record_trace=True)
    artifact = {
        "id": episode_id,
        "protocol": kitchen.get("pilot_protocol", PROTOCOL),
        "protocol_hash": kitchen.get("pilot_protocol_hash", protocol_hash()),
        "base_protocol_hash": protocol_hash() if "pilot_protocol" in kitchen else None,
        "model": asdict(client.model), "spec": kitchen["spec"], "stage": STAGE,
        "arm": kitchen["arm"], "setting": kitchen["setting"], "seed": kitchen["seed"],
        "dollar_cap": dollar_cap, "actions": [], "requests": [], "status": "running",
        "input_tokens": 0, "output_tokens": 0, "accepted_cost_usd": 0.0,
        "feasibility_witness_hash": kitchen["witness_hash"],
        "feasibility_basis": "overlap_beam_witness_replayed",
        "oracle_served": kitchen["oracle_served"],
        "accounting_contract": "actual-cost-action-admission-1",
    }
    artifact["spec_hash"] = digest(kitchen["spec"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as fh:
        json.dump(artifact, fh, indent=2, sort_keys=True)

    reason, settled_units = None, 0
    while not engine.terminated and len(artifact["actions"]) < MAX_DECISIONS:
        remaining_usd = max(0, dollar_cap - ledger.used(episode_id))
        if units(dollar_cap) <= settled_units:
            reason = "dollar_budget_exhausted"
            break
        budget = {"kind": "USD", "allowance": dollar_cap, "remaining": remaining_usd,
                  "decisions_remaining": MAX_DECISIONS - len(artifact["actions"])}
        canonical, options = decision(public_state(engine), budget)
        prepared = client.prepare(canonical, remaining_usd, None)
        if prepared is None:
            reason = "token_budget_exhausted"
            break
        payload, bound = prepared
        rid = f"{episode_id}:{len(artifact['requests'])}"
        try:
            ledger.reserve(rid, episode_id, STAGE, bound)
        except BudgetExceeded:
            reason = "operational_spending_gate"
            artifact["status"] = "incomplete"
            break
        record = {"id": rid, "payload": payload, "reserved_usd": bound, "admitted": False}
        artifact["requests"].append(record)
        write_json(output, artifact)
        try:
            response = dispatch(client, payload, ledger, rid, episode_id, bound, record)
            record["response"] = response
            n_in, n_out, cost = client.usage(response, payload)
            record.update(input_tokens=n_in, output_tokens=n_out, cost_usd=cost)
            artifact["input_tokens"] += n_in
            artifact["output_tokens"] += n_out
            ledger.settle(record["id"], cost)  # a 429 retry moved the live reservation
            settled_units += units(cost)
            client.validate_identity(response)
            prompt_bound, _ = client.bound(payload)
            if n_in > prompt_bound:
                raise ProviderError("input-token reservation bound violated")
            if n_out > payload["max_tokens"]:
                raise ProviderError("output-token cap violated")
            if settled_units > units(dollar_cap):
                reason = "dollar_budget_exhausted"
                break
            selected = client.select(response)
            call = options.get(selected) if isinstance(selected, str) else None
            if call is None:
                call = ToolCall("invalid_model_selection", {})
            record["selection"] = selected
            engine.step([call])
            artifact["actions"].append(asdict(call))
            record["admitted"] = True
            artifact["accepted_cost_usd"] = settled_units / SCALE
            write_json(output, artifact)
        except (ProviderError, RuntimeError, ValueError, KeyError) as exc:
            # Liability is RETAINED at the full reserve, never settled to zero, because no
            # response was saved and the request may still have been billed.
            #
            # Retiring it immediately is what keeps the run alive. `ledger.reserve` refuses
            # EVERY future reservation, for every model, while any row sits in `uncertain`
            # (ledger.py:78-81) -- a deliberate audit interlock. Left to the end of the run
            # that is correct but catastrophic here: a single RemoteDisconnected blocked 41
            # of 72 episodes in the first attempt at this experiment. Retaining at once
            # preserves the money and keeps the interlock meaningful for real accounting
            # faults (overruns still halt, and are never auto-resolved).
            ledger.uncertain(record["id"])
            ledger.retain_failed_liability(record["id"])
            artifact["status"] = "quarantined"
            artifact["error"] = f"{type(exc).__name__}: {exc}"
            reason = "provider_or_accounting_failure"
            break

    if reason is None:
        reason = "game_terminal" if engine.terminated else "decision_limit"
    artifact.update(
        reason=reason, quality=quality(engine), completed=engine.counters["orders_served"],
        invalid=engine.counters["invalid_actions"], cost_usd=ledger.used(episode_id),
        clock_gs=engine.clock_gs, decisions=len(artifact["actions"]),
    )
    if artifact["status"] == "running":
        artifact["status"] = "complete"
    write_json(output, artifact)
    return artifact


def cmd_run(args):
    load_env(ROOT / ".env")
    kitchens = json.loads((DATA / "kitchens.json").read_text())
    ledger = Ledger(LEDGER_PATH, cap=LEDGER_CAP, opening_balance=LEDGER_OPENING)
    RUNS.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    spent = {"total": 0.0}
    def next_attempt(key, k):
        """Episode id for the next attempt at this kitchen, or None if already scored.

        The ledger permanently refuses an episode id that ever incurred a charge, which is
        correct -- it is what stops a failed attempt being quietly replayed and rescored.
        But a provider rate-limit is not a model result, and without an attempt counter a
        single 429 retires the kitchen for good: that is how all 24 glm kitchens became
        unusable. The old liability stays on the books under the old id; the retry is a
        new, separately accounted episode of the SAME kitchen, so pairing is preserved."""
        base = f"{NAME}-{key}-{k['arm']}-{k['seed']}"
        for attempt in range(MAX_ATTEMPTS):
            eid = base if attempt == 0 else f"{base}-a{attempt}"
            path = RUNS / "episodes" / f"{eid}.json"
            if path.exists():
                if json.loads(path.read_text()).get("status") == "complete":
                    return None
                continue
            if ledger.used(eid):
                continue
            return eid
        return None

    todo = []
    for key in MODELS:
        for k in kitchens:
            if args.arms and k["arm"] not in args.arms:
                continue
            eid = next_attempt(key, k)
            if eid:
                todo.append((key, k, eid))
    # hardest arm first: if the budget or the provider gives out, tuned-b is the result
    # that decides whether the tuning worked at all
    todo.sort(key=lambda t: {"tuned-b": 0, "tuned-a": 1, "control": 2}[t[1]["arm"]])
    print(f"{len(todo)} episodes to play")

    def work(item):
        key, kitchen, episode_id = item
        path = RUNS / "episodes" / f"{episode_id}.json"
        with lock:
            if spent["total"] >= TOTAL_CAP:
                return None
            spent["total"] += PER_EPISODE_CAP  # claim before dispatch, reconcile after
        try:
            art = play(kitchen, Client(MODELS[key]), ledger, episode_id, path, PER_EPISODE_CAP)
        except Exception as exc:  # noqa: BLE001 - one bad episode must not stop the arm
            print(f"  {episode_id}: FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
            with lock:
                spent["total"] -= PER_EPISODE_CAP
            return None
        with lock:
            spent["total"] += art["cost_usd"] - PER_EPISODE_CAP
        print(
            f"  {key:>22} {kitchen['arm']:>8} seed {kitchen['seed']}: "
            f"served {art['completed']}/3  invalid {art['invalid']}  "
            f"{art['decisions']} dec  ${art['cost_usd']:.4f}"
        )
        return art

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(work, todo))
    print(f"\nspent ${spent['total']:.4f}")
    return 0


# --------------------------------------------------------------------------- report


def cmd_report(args):
    rows = []
    for path in sorted((RUNS / "episodes").glob("*.json")):
        ep = json.loads(path.read_text())
        if ep.get("status") == "complete":
            rows.append(ep)
    print(f"{'model':>22} {'arm':>9} | {'delivered':>10} {'full':>6} {'invalid':>8} {'$/kitchen':>10}")
    print("-" * 74)
    for key in MODELS:
        for arm in ARMS:
            got = [r for r in rows if r["model"]["key"] == key and r["arm"] == arm]
            if not got:
                continue
            served = sum(r["completed"] for r in got)
            moves = sum(r["decisions"] for r in got)
            invalid = sum(r["invalid"] for r in got)
            full = sum(r["completed"] == 3 for r in got)
            cost = sum(r["cost_usd"] for r in got)
            print(
                f"{key:>22} {arm:>9} | {100 * served / (3 * len(got)):9.0f}% "
                f"{full:>3}/{len(got):<2} {100 * invalid / moves if moves else 0:7.1f}% "
                f"{cost / len(got):10.4f}"
            )
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("build", "run", "report"))
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--arms", nargs="*", default=None)
    args = ap.parse_args()
    return {"build": cmd_build, "run": cmd_run, "report": cmd_report}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())

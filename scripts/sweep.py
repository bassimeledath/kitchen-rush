"""Budget-aware OpenRouter sweep runner for Kitchen Rush.

Runs a panel of models through the benchmark, tallying REAL token spend per episode from
provider usage and halting before a hard USD cap. Logs every episode as it completes so a
long unattended run is always inspectable mid-flight.

Usage (key via env, never written to disk):
    OPENROUTER_API_KEY=... python scripts/sweep.py --mode smoke
    OPENROUTER_API_KEY=... python scripts/sweep.py --mode full --cap 85 --name starter

Reasoning control on OpenRouter (probed empirically):
    off     -> extra_body={"reasoning":{"enabled":False}}   (most models; a few reject it)
    on      -> reasoning_effort="low"
    default -> no param (model's shipped behavior)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from kitchenrush import config, procgen                      # noqa: E402
from kitchenrush.adapter import LiteLLMClient                # noqa: E402
from kitchenrush.agent import ModelAgent                     # noqa: E402
from kitchenrush.metrics import aggregate                    # noqa: E402
from kitchenrush.runner import anchors_for, run_episode      # noqa: E402
from kitchenrush.version import ruleset_hash, versions       # noqa: E402

# (prompt, completion) USD per token — OpenRouter, stamped 2026-06-06.
PRICES = {
    'meta-llama/llama-4-scout': (0.00000008, 0.0000003),
    'deepseek/deepseek-v4-flash': (0.0000000983, 0.0000001966),
    'mistralai/mistral-small-2603': (0.00000015, 0.0000006),
    'google/gemini-3.1-flash-lite': (0.00000025, 0.0000015),
    'deepseek/deepseek-v4-pro': (0.000000435, 0.00000087),
    'qwen/qwen3.7-plus': (0.0000004, 0.0000016),
    'openai/gpt-5.4-mini': (0.00000075, 0.0000045),
    'anthropic/claude-sonnet-4.6': (0.000003, 0.000015),
    'x-ai/grok-build-0.1': (0.000001, 0.000002),
    'google/gemini-3.5-flash': (0.0000015, 0.000009),
    'qwen/qwen3-235b-a22b-thinking-2507': (0.0000001, 0.0000001),
    'openai/gpt-oss-120b': (0.000000039, 0.00000018),
}

# Panel, ordered cheapest-first so the budget cap trims the most expensive tail if it binds.
# (model_id, reasoning_mode, label). mode: "off" | "on" | "default".
PANEL = [
    ('meta-llama/llama-4-scout', 'off', 'llama-4-scout'),
    ('deepseek/deepseek-v4-flash', 'off', 'deepseek-v4-flash'),
    ('openai/gpt-oss-120b', 'on', 'gpt-oss-120b·think'),
    ('mistralai/mistral-small-2603', 'off', 'mistral-small'),
    ('deepseek/deepseek-v4-flash', 'on', 'deepseek-v4-flash·think'),
    ('google/gemini-3.1-flash-lite', 'off', 'gemini-3.1-flash-lite'),
    ('deepseek/deepseek-v4-pro', 'off', 'deepseek-v4-pro'),
    ('qwen/qwen3.7-plus', 'off', 'qwen3.7-plus'),
    ('x-ai/grok-build-0.1', 'default', 'grok-build·think'),   # cannot disable reasoning
    ('openai/gpt-5.4-mini', 'off', 'gpt-5.4-mini'),
    ('anthropic/claude-sonnet-4.6', 'off', 'claude-sonnet-4.6'),
    ('google/gemini-3.5-flash', 'default', 'gemini-3.5-flash·think'),  # cannot disable reasoning
]


def client_extra(mode: str) -> dict:
    if mode == 'off':
        return {"extra_body": {"reasoning": {"enabled": False}}}
    if mode == 'on':
        return {"reasoning_effort": "low"}
    return {}


class TallyClient(LiteLLMClient):
    """LiteLLM client that adds real provider token spend to a shared mutable tracker."""

    def __init__(self, spec: str, tracker: dict, price: tuple[float, float], **kw):
        super().__init__(spec, **kw)
        self._t = tracker
        self._price = price

    def generate(self, **kw):
        resp = super().generate(**kw)
        pin, pout = self._price
        u = resp.usage
        self._t['in'] += u.get('prompt_tokens', 0)
        self._t['out'] += u.get('completion_tokens', 0)
        self._t['reason'] += u.get('reasoning_tokens', 0)
        self._t['cost'] += u.get('prompt_tokens', 0) * pin + u.get('completion_tokens', 0) * pout
        self._t['calls'] += 1
        return resp


def kr_of(score, s_null, s_ref):
    if s_ref is None or s_null is None or s_ref <= s_null:
        return None
    return round(100.0 * max(0.0, min(1.0, (score - s_null) / (s_ref - s_null))), 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['smoke', 'full'], default='smoke')
    ap.add_argument('--cap', type=float, default=85.0, help='hard USD ceiling; halt before exceeding')
    ap.add_argument('--seeds', type=int, default=12)
    ap.add_argument('--trials', type=int, default=2)
    ap.add_argument('--name', type=str, default=None)
    ap.add_argument('--tiers', type=str, default='medium,hard')
    ap.add_argument('--budgets', type=str, default='1,5')
    ap.add_argument('--temperature', type=float, default=0.2)
    ap.add_argument('--workers', type=int, default=10, help='concurrent episodes per model batch')
    args = ap.parse_args()

    if not os.environ.get('OPENROUTER_API_KEY'):
        print('error: OPENROUTER_API_KEY not set in env', file=sys.stderr)
        return 1

    if args.mode == 'smoke':
        seeds, trials, tiers, budgets = 1, 1, ['medium'], [1.0]
    else:
        seeds, trials = args.seeds, args.trials
        tiers = [t.strip() for t in args.tiers.split(',')]
        budgets = [float(b) for b in args.budgets.split(',')]

    name = args.name or f"{args.mode}_{int(time.time())}"
    out = Path('runs') / name
    out.mkdir(parents=True, exist_ok=True)
    epfile = (out / 'episodes.jsonl').open('a')
    logfile = (out / 'progress.log').open('a')

    def log(msg: str):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        logfile.write(line + "\n"); logfile.flush()

    grand = {'cost': 0.0}
    groups: dict[tuple, list] = {}
    write_lock = threading.Lock()
    log(f"sweep '{name}' mode={args.mode} cap=${args.cap} seeds={seeds} trials={trials} "
        f"tiers={tiers} budgets={budgets} workers={args.workers} ruleset={ruleset_hash()}")

    def run_one(mid, mode, label, B, tier, seed, trial):
        # Pass b explicitly so deadlines are baked into the spec — no global config.B_SECONDS read,
        # so concurrent tasks at different B don't race. Anchors derive purely from the spec.
        spec = procgen.generate(seed, tier, b=float(B))
        s_null, s_ref = anchors_for(spec)
        mt = {'in': 0, 'out': 0, 'reason': 0, 'cost': 0.0, 'calls': 0}
        client = TallyClient(f"openrouter:{mid}", mt, PRICES[mid], **client_extra(mode))
        agent = ModelAgent(client, track='rp', temperature=args.temperature)
        t0 = time.time()
        res = run_episode(spec, agent)
        res.s_null, res.s_ref, res.trial = s_null, s_ref, trial
        rep = res.report
        rec = {
            'model': label, 'model_id': mid, 'mode': mode, 'B': B, 'tier': tier,
            'seed': seed, 'trial': trial,
            'kr': kr_of(rep['score_raw'], s_null, s_ref), 'score_raw': rep['score_raw'],
            's_null': round(s_null, 2), 's_ref': round(s_ref, 2),
            'served': rep['counters']['orders_served'], 'total': rep['counters']['orders_total'],
            'turns': rep['turns'], 'terminated': rep['terminated'],
            'invalid': rep['counters']['invalid_actions'], 'burns': rep['counters']['burns'],
            'ep_tokens_in': mt['in'], 'ep_tokens_out': mt['out'], 'ep_reason': mt['reason'],
            'ep_cost': round(mt['cost'], 5), 'wall_s': round(time.time() - t0, 1),
        }
        return res, rec

    halted = False
    for mid, mode, label in PANEL:
        if halted:
            log(f"SKIP {label} — budget cap reached")
            continue
        # Each model's 48 episodes run concurrently; cap is checked at model boundaries (a single
        # model's spend is bounded, and the panel is cheapest-first so the costly tail trims last).
        tasks = [(mid, mode, label, B, tier, seed, trial)
                 for B in budgets for tier in tiers for seed in range(seeds) for trial in range(trials)]
        m = {'eps': 0, 'in': 0, 'out': 0, 'reason': 0, 'cost': 0.0}
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(run_one, *t): t for t in tasks}
            for fut in as_completed(futs):
                t = futs[fut]
                try:
                    res, rec = fut.result()
                except Exception as exc:  # noqa: BLE001
                    log(f"  ERR {t[2]} B={t[3]} {t[4]} s{t[5]} t{t[6]}: {type(exc).__name__}: {str(exc)[:80]}")
                    continue
                with write_lock:
                    groups.setdefault((label, rec['B'], rec['tier']), []).append(res)
                    m['eps'] += 1; m['in'] += rec['ep_tokens_in']; m['out'] += rec['ep_tokens_out']
                    m['reason'] += rec['ep_reason']; m['cost'] += rec['ep_cost']
                    epfile.write(json.dumps(rec) + "\n"); epfile.flush()
        grand['cost'] += m['cost']
        log(f"DONE {label}: {m['eps']} ep  spend=${m['cost']:.2f}  cum=${grand['cost']:.2f}  "
            f"in={m['in']} out={m['out']} reason={m['reason']}")
        if grand['cost'] >= args.cap:
            halted = True
            log(f"BUDGET CAP ${args.cap} reached after {label} (cum ${grand['cost']:.2f}) — stopping")

    board = []
    for (label, B, tier), eps in groups.items():
        agg = aggregate(eps, k=trials)
        board.append({'model': label, 'B': B, 'tier': tier, 'episodes': len(eps),
                      'KR': agg.get('KR'), 'kr_std': agg.get('kr_std'),
                      f'pass_{trials}': agg.get(f'pass_{trials}'),
                      'completion_rate': agg.get('completion_rate'),
                      'invalid_rate': agg.get('invalid_rate')})
    board.sort(key=lambda r: (r['tier'], r['B'], -(r['KR'] if r['KR'] is not None else -1)))
    summary = {'name': name, 'ruleset': ruleset_hash(), 'versions': versions(),
               'total_cost_usd': round(grand['cost'], 2), 'halted_on_budget': halted, 'board': board}
    (out / 'leaderboard.json').write_text(json.dumps(summary, indent=2))
    log(f"WROTE leaderboard.json  total_cost=${summary['total_cost_usd']}  halted={halted}")
    epfile.close(); logfile.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

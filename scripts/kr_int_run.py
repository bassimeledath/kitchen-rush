"""Run a model on the KR-INT time-agnostic track (complexity ladder K0..K5).

Latency is free (LATENCY_SCALE=0) and deadlines don't bind, so the score is pure planning
correctness = fraction of orders completed. Reports the K50 ceiling + AUC, logs every episode, and
halts before a hard USD cap.

    OPENROUTER_API_KEY=... python scripts/kr_int_run.py --model openai/gpt-5.5 \
        --reasoning low --seeds 5 --cap 25 --name gpt55-low
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

from kitchenrush import config, kr_int                       # noqa: E402
from kitchenrush.adapter import LiteLLMClient                # noqa: E402
from kitchenrush.agent import ModelAgent                     # noqa: E402
from kitchenrush.runner import run_episode                   # noqa: E402
from kitchenrush.version import versions                     # noqa: E402

# (prompt, completion) USD/token — OpenRouter, stamped 2026-06-06.
PRICES = {
    'openai/gpt-5.5': (5e-6, 30e-6),
    'openai/gpt-5.4-mini': (0.75e-6, 4.5e-6),
    'openai/gpt-5.4-nano': (0.20e-6, 1.25e-6),
    'anthropic/claude-sonnet-4.6': (3e-6, 15e-6),
    'google/gemini-3.1-flash-lite': (0.25e-6, 1.5e-6),
    'deepseek/deepseek-v4-pro': (0.435e-6, 0.87e-6),
}


def client_extra(reasoning: str) -> dict:
    if reasoning == 'off':
        return {"extra_body": {"reasoning": {"enabled": False}}}
    return {"reasoning_effort": reasoning}   # minimal | low | medium | high


class TallyClient(LiteLLMClient):
    def __init__(self, spec, tracker, price, **kw):
        super().__init__(spec, **kw)
        self._t, self._price = tracker, price

    def generate(self, **kw):
        r = super().generate(**kw)
        pin, pout = self._price
        u = r.usage
        self._t['in'] += u.get('prompt_tokens', 0); self._t['out'] += u.get('completion_tokens', 0)
        self._t['reason'] += u.get('reasoning_tokens', 0)
        self._t['cost'] += u.get('prompt_tokens', 0) * pin + u.get('completion_tokens', 0) * pout
        return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', required=True, help='e.g. openai/gpt-5.5')
    ap.add_argument('--reasoning', default='low', help='off | minimal | low | medium | high')
    ap.add_argument('--seeds', type=int, default=5)
    ap.add_argument('--cap', type=float, default=25.0)
    ap.add_argument('--workers', type=int, default=6)
    ap.add_argument('--temperature', type=float, default=0.2)
    ap.add_argument('--name', default=None)
    args = ap.parse_args()
    if not os.environ.get('OPENROUTER_API_KEY'):
        print('error: OPENROUTER_API_KEY not set', file=sys.stderr); return 1
    if args.model not in PRICES:
        print(f'error: add {args.model} to PRICES', file=sys.stderr); return 1

    config.LATENCY_SCALE = 0.0   # KR-INT: thinking is free
    name = args.name or f"krint_{int(time.time())}"
    out = Path('runs') / name; out.mkdir(parents=True, exist_ok=True)
    (out / 'run_meta.json').write_text(json.dumps(
        {'track': 'KR-INT', 'model': args.model, 'reasoning': args.reasoning,
         'seeds': args.seeds, 'versions': versions(), 'ladder': [t.name for t in kr_int.K_LADDER]}, indent=2))
    epf = (out / 'episodes.jsonl').open('a'); logf = (out / 'progress.log').open('a')
    lock = threading.Lock()

    def log(m):
        line = f"[{time.strftime('%H:%M:%S')}] {m}"; print(line, flush=True)
        logf.write(line + "\n"); logf.flush()

    grand = {'cost': 0.0}
    per_k: dict[int, list[float]] = {k: [] for k in range(kr_int.N_RUNGS)}
    # Resume: replay prior episodes (per-episode flushed), skip done (k,seed), recover spend.
    done: set[tuple] = set()
    eppath = out / 'episodes.jsonl'
    if eppath.exists():
        for line in eppath.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            done.add((r['k'], r['seed'])); grand['cost'] += r.get('ep_cost', 0.0)
            per_k[r['k']].append(r['completion'])
    if done:
        log(f"RESUME: {len(done)} episodes done (prior spend ${grand['cost']:.2f}); skipping them")
    log(f"KR-INT '{name}' model={args.model} reasoning={args.reasoning} seeds={args.seeds} cap=${args.cap}")

    def run_one(k, seed):
        spec = kr_int.generate(seed, k)
        mt = {'in': 0, 'out': 0, 'reason': 0, 'cost': 0.0}
        client = TallyClient(f"openrouter:{args.model}", mt, PRICES[args.model], **client_extra(args.reasoning))
        agent = ModelAgent(client, track='rp', temperature=args.temperature)
        t0 = time.time()
        res = run_episode(spec, agent)
        rep = res.report
        return k, seed, kr_int.completion(rep), rep, mt, round(time.time() - t0, 1)

    halted = False
    for k in range(kr_int.N_RUNGS):
        if halted:
            log(f"SKIP K{k} — budget cap"); continue
        tasks = [(k, s) for s in range(args.seeds) if (k, s) not in done]
        if not tasks:
            vals = per_k[k]
            log(f"K{k}: completion {sum(vals)/len(vals):.2f} (resumed, n={len(vals)})"); continue
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(run_one, *t) for t in tasks]
            for fut in as_completed(futs):
                try:
                    kk, seed, comp, rep, mt, wall = fut.result()
                except Exception as exc:  # noqa: BLE001
                    log(f"  ERR K{k}: {type(exc).__name__}: {str(exc)[:80]}"); continue
                c = rep['counters']
                with lock:
                    per_k[kk].append(comp); grand['cost'] += mt['cost']
                    epf.write(json.dumps({
                        'k': kk, 'seed': seed, 'completion': round(comp, 3),
                        'served': c['orders_served'], 'total': c['orders_total'],
                        'turns': rep['turns'], 'invalid': c['invalid_actions'], 'burns': c['burns'],
                        'terminated': rep['terminated'], 'ep_in': mt['in'], 'ep_out': mt['out'],
                        'ep_reason': mt['reason'], 'ep_cost': round(mt['cost'], 5), 'wall_s': wall,
                    }) + "\n"); epf.flush()
        vals = per_k[k]
        log(f"K{k}: completion {sum(vals)/len(vals):.2f} (n={len(vals)})  cum=${grand['cost']:.2f}")
        if grand['cost'] >= args.cap:
            halted = True; log(f"BUDGET CAP ${args.cap} reached after K{k}")

    summary = kr_int.summarize(per_k)
    summary.update({'model': args.model, 'reasoning': args.reasoning, 'track': 'KR-INT',
                    'total_cost_usd': round(grand['cost'], 2), 'halted': halted,
                    'versions': versions()})
    (out / 'summary.json').write_text(json.dumps(summary, indent=2))
    log(f"DONE  K50={summary['k50']}  AUC={summary['auc']}  cost=${summary['total_cost_usd']}")
    log(f"  per-rung completion: {summary['mean_completion']}")
    epf.close(); logf.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

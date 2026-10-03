#!/usr/bin/env python3
"""Bounded paired low/high stress test; inherits the native saved-response runner."""
from __future__ import annotations
import argparse
import hashlib
import http.client
import json
from pathlib import Path

import run_diverse_complexity_pilot_20260926 as base
import extreme_sol_kitchens_20260926 as kitchens
from kitchenrush.v2.ledger import Ledger, BudgetExceeded, SCALE, STAGES, units
from kitchenrush.v2.providers import Client, ProviderError, load_env

ROOT, NAME = base.ROOT, kitchens.NAME
DATA = kitchens.DATA
RUNS = ROOT / "runs/v2" / NAME
ROSTER = json.loads((DATA / "roster.json").read_text())
MODELS = {r["model"]["key"]: base.Model(**r["model"]) for r in ROSTER["configurations"]}

def pilot_hash():
    paths = [ROOT / "scripts" / n for n in (
        "run_extreme_sol_20260926.py", "extreme_sol_kitchens_20260926.py",
        "run_diverse_complexity_pilot_20260926.py", "complexity_ladder_v2.py",
        "difficulty_sweep_v2.py", "concurrency_probe_v2.py", "tune_probe_v2.py")]
    paths += sorted((ROOT / "src/kitchenrush").rglob("*.py"))
    paths += [DATA / "kitchens.json", DATA / "roster.json", DATA / "endpoint-snapshot.json"]
    return base.digest({"files": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in paths}})

def accounted(con):
    return Ledger._used(con, "WHERE episode LIKE ?", (NAME+"-%",))

class ExtremeLedger(Ledger):
    def __init__(self):
        super().__init__(base.LEDGER, cap=250, opening_balance=16.175129022)
        self.episode_caps = {}
    def reserve(self, rid, episode, stage, maximum, episode_cap=None):
        amount = units(maximum)
        stage = "smoke" if stage == "pilot" else stage
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM requests WHERE id=?", (rid,)).fetchone():
                raise ValueError("duplicate reservation")
            if con.execute("SELECT 1 FROM requests WHERE status IN ('uncertain','overrun')").fetchone():
                raise BudgetExceeded("accounting audit required")
            gates = [(accounted(con), units(ROSTER["campaign_ceiling_usd"])),
                     (self._used(con)+self.opening_balance,self.cap),
                     (self._used(con,"WHERE stage=?",(stage,)),units(STAGES[stage])),
                     (self._used(con,"WHERE episode=?",(episode,)),units(self.episode_caps[episode]))]
            if any(used+amount > ceiling for used,ceiling in gates):
                raise BudgetExceeded("extreme campaign/episode/stage/global gate")
            con.execute("INSERT INTO requests VALUES (?,?,?,?,NULL,?)",(rid,episode,stage,amount,"reserved"))

class SafeClient(Client):
    def evaluate(self, payload):
        try:
            return super().evaluate(payload)
        except http.client.HTTPException as error:
            raise ProviderError(f"transport failure {type(error).__name__}; no automatic retry") from None

def install():
    base.NAME, base.DATA, base.RUNS = NAME, DATA, RUNS
    base.PROTOCOL = "experimental-extreme-sol-reasoning-1"
    base.MODELS, base.ROSTER = MODELS, ROSTER
    base.CAPS = {r["model"]["key"]: r["episode_cap_usd"] for r in ROSTER["configurations"]}
    base.Client, base.CampaignLedger, base.pilot_hash = SafeClient, ExtremeLedger, pilot_hash
    kitchens.install_wait_contract()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--run",action="store_true")
    parser.add_argument("--canaries",action="store_true")
    parser.add_argument("--model",choices=MODELS)
    parser.add_argument("--arm",choices=kitchens.ARMS,default="brutal")
    parser.add_argument("--seed",type=int,choices=kitchens.SEEDS,default=90601)
    args=parser.parse_args()
    rows=json.loads((DATA/"kitchens.json").read_text())
    for row in rows: kitchens.verify(row)
    ledger=ExtremeLedger()
    snapshot=ledger.snapshot()
    with ledger.connect() as con:
        unresolved=con.execute("SELECT episode,status FROM requests WHERE status IN ('reserved','uncertain','overrun')").fetchall()
    if snapshot["overruns"] or any(status != "reserved" or not episode.startswith(NAME+"-")
                                  for episode,status in unresolved):
        raise ValueError("ledger needs recovery")
    print(json.dumps({"protocol_hash":pilot_hash(),"cap_usd":ROSTER["campaign_ceiling_usd"]}),flush=True)
    if not args.run: return
    load_env(ROOT/".env")
    row=next(r for r in rows if r["arm"]==args.arm and r["seed"]==args.seed)
    for key in ([args.model] if args.model else MODELS):
        if not args.canaries:
            cp=RUNS/"canaries"/f"{NAME}-{key}-canary.json"
            canary=json.loads(cp.read_text())
            if canary["reason"]!="canary_ok" or canary["protocol_hash"]!=pilot_hash():
                raise ValueError("canary not verified")
        ep=base.run_episode(row,MODELS[key],ledger,canary=args.canaries)
        print(json.dumps({k:ep.get(k) for k in ("id","status","reason","completed","invalid",
                                               "decisions","output_tokens","cost_usd")}),flush=True)

install()
if __name__=="__main__": main()

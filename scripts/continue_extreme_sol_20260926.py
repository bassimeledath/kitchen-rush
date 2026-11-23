#!/usr/bin/env python3
"""Fresh paired seed after transport failure; streamed transport, $9 aggregate ceiling."""
import argparse
import hashlib
import http.client
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import run_extreme_sol_20260926 as legacy

ROOT, LEGACY_NAME, LEGACY_RUNS = legacy.ROOT, legacy.NAME, legacy.RUNS
LEGACY_HASH = legacy.pilot_hash()
NAME = "extreme-sol-reasoning-r2-20260926"
DATA, RUNS = ROOT/"data/v2"/NAME, ROOT/"runs/v2"/NAME
ROSTER = json.loads((DATA/"roster.json").read_text())
base = legacy.base
MODELS = {r["model"]["key"]:base.Model(**r["model"]) for r in ROSTER["configurations"]}
SCALE, units, kitchens = legacy.SCALE, legacy.units, legacy.kitchens

def pilot_hash():
    return base.digest({"legacy_protocol_hash":LEGACY_HASH,
        "files":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in [Path(__file__),DATA/"roster.json",DATA/"kitchens.json",DATA/"endpoint-snapshot.json"]}})

class StreamingClient(legacy.SafeClient):
    def payload(self,canonical,max_output=None):
        payload=super().payload(canonical,max_output)
        payload.update(stream=True,stream_options={"include_usage":True})
        return payload
    def evaluate(self,payload):
        key=os.environ.get("OPENROUTER_API_KEY")
        if not key: raise legacy.ProviderError("missing OPENROUTER_API_KEY")
        req=urllib.request.Request("https://openrouter.ai/api/v1/chat/completions",
            data=json.dumps(payload).encode(),headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"})
        chunks=[]
        try:
            with urllib.request.urlopen(req,timeout=self.timeout) as response:
                for line in response:
                    line=line.decode().strip()
                    if not line.startswith("data:"): continue
                    value=line[5:].strip()
                    if value=="[DONE]": break
                    chunk=json.loads(value)
                    if "error" in chunk: raise legacy.ProviderError("stream reported provider error")
                    chunks.append(chunk)
        except (http.client.HTTPException,OSError,ValueError) as error:
            raise legacy.ProviderError(f"stream transport failure {type(error).__name__}; no automatic retry") from None
        result=aggregate_stream(chunks)
        result["stream_chunks"]=chunks
        if not result.get("usage") or not result.get("provider"):
            raise legacy.ProviderError("incomplete stream metadata/usage")
        return result

def aggregate_stream(chunks):
    result={}
    content="";finish=None
    for chunk in chunks:
        for key in ("id","model","provider","usage"):
            if chunk.get(key) is not None: result[key]=chunk[key]
        for choice in chunk.get("choices",[]):
            if choice.get("index",0)!=0: continue
            content+=choice.get("delta",{}).get("content") or ""
            if choice.get("finish_reason") is not None: finish=choice["finish_reason"]
    result["choices"]=[{"message":{"content":content},"finish_reason":finish}]
    return result

class ExtremeLedger(legacy.ExtremeLedger):
    def reserve(self,rid,episode,stage,maximum,episode_cap=None):
        amount=units(maximum)
        stage="smoke" if stage=="pilot" else stage
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM requests WHERE id=?",(rid,)).fetchone(): raise ValueError("duplicate reservation")
            if con.execute("SELECT 1 FROM requests WHERE status IN ('uncertain','overrun')").fetchone():
                raise legacy.BudgetExceeded("accounting audit required")
            own=self._used(con,"WHERE episode LIKE ?",(NAME+"-%",))
            old=self._used(con,"WHERE episode LIKE ?",(LEGACY_NAME+"-%",))
            # Conservatively reserve remaining allowances of any legacy worker still running.
            margin=0
            for folder in ("episodes","canaries"):
                for path in (LEGACY_RUNS/folder).glob("*.json"):
                    ep=json.loads(path.read_text())
                    if ep["status"]=="running":
                        used=self._used(con,"WHERE episode=?",(ep["id"],))
                        margin+=max(0,units(ep["dollar_cap"])-used)
            gates=[(own,units(ROSTER["campaign_ceiling_usd"])),(old+own+margin,units(9)),
                (self._used(con)+self.opening_balance,self.cap),
                (self._used(con,"WHERE stage=?",(stage,)),units(legacy.STAGES[stage])),
                (self._used(con,"WHERE episode=?",(episode,)),units(self.episode_caps[episode]))]
            if any(used+amount>cap for used,cap in gates): raise legacy.BudgetExceeded("extreme continuation gates")
            con.execute("INSERT INTO requests VALUES (?,?,?,?,NULL,?)",(rid,episode,stage,amount,"reserved"))

def install():
    base.NAME,base.DATA,base.RUNS=NAME,DATA,RUNS
    base.PROTOCOL="experimental-extreme-sol-reasoning-stream-continuation-2"
    base.MODELS,base.ROSTER=MODELS,ROSTER
    base.CAPS={r["model"]["key"]:r["episode_cap_usd"] for r in ROSTER["configurations"]}
    base.Client,base.CampaignLedger,base.pilot_hash=StreamingClient,ExtremeLedger,pilot_hash
    kitchens.install_wait_contract()

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--run",action="store_true")
    parser.add_argument("--canaries",action="store_true")
    parser.add_argument("--model",choices=MODELS)
    args=parser.parse_args()
    rows=json.loads((DATA/"kitchens.json").read_text())
    for row in rows: kitchens.verify(row)
    ledger=ExtremeLedger()
    with ledger.connect() as con:
        unresolved=con.execute("SELECT episode,status FROM requests WHERE status IN ('reserved','uncertain','overrun')").fetchall()
    if any(status!="reserved" or not (ep.startswith(NAME+"-") or ep.startswith(LEGACY_NAME+"-")) for ep,status in unresolved):
        raise ValueError("unexpected unresolved request")
    print(json.dumps({"protocol_hash":pilot_hash(),"aggregate_cap_usd":9}),flush=True)
    if not args.run:return
    legacy.load_env(ROOT/".env")
    row=next(r for r in rows if r["arm"]=="severe" and r["seed"]==90602)
    for key in ([args.model] if args.model else MODELS):
        if not args.canaries:
            cp=RUNS/"canaries"/f"{NAME}-{key}-canary.json"
            canary=json.loads(cp.read_text())
            assert canary["reason"]=="canary_ok" and canary["protocol_hash"]==pilot_hash()
        ep=base.run_episode(row,MODELS[key],ledger,canary=args.canaries)
        print(json.dumps({k:ep.get(k) for k in ("id","status","reason","completed","invalid","decisions","output_tokens","cost_usd")}),flush=True)

install()
if __name__=="__main__":main()

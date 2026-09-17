"""No live APIs: same-loop native lookup and fail-closed billing timeout."""

import asyncio
import importlib.util
import json
import subprocess
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "billing_v2_fixture", ROOT / "scripts/reconcile_phonellm_billing_v2.py"
)
billing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(billing)


def test_timeout_preserves_unknown_spend_and_cleans_client():
    async def run():
        observed = []

        async def hydrate():
            observed.append("hydrate")

        async def close():
            observed.append("close")

        client = SimpleNamespace(_close=close)
        workspace = SimpleNamespace(name="fixture", client=client, hydrate=hydrate)

        async def lookup(*args, **kwargs):
            observed.append("lookup")
            await asyncio.Event().wait()

        cfg = dict(workspace="fixture", app="fixture")
        result = await billing.bounded_fetch(
            cfg, timeout=0.02, dependencies=(lambda: workspace, lookup, None, None)
        )
        assert result == dict(status="fetch_timeout_reserve_unchanged", error_type="TimeoutError")
        assert observed == ["hydrate", "lookup", "close"]
        assert "gross_reported_cost_usd" not in result

    asyncio.run(run())


def test_actual_native_sdk_lookup_stays_on_client_loop(modal_sdk_python):
    python = modal_sdk_python
    code = """import asyncio,importlib.util,json,sys
from datetime import datetime,timezone
from types import SimpleNamespace
from modal.app import _App
from modal_proto import api_pb2
spec=importlib.util.spec_from_file_location('bounded_helper',sys.argv[1]);b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
async def main():
 loop=asyncio.get_running_loop();events=[]
 async def hydrate():
  assert asyncio.get_running_loop() is loop
  events.append('hydrate')
 async def lookup(request):
  assert asyncio.get_running_loop() is loop
  assert request.app_name=='fixture'
  events.append('native_app_lookup')
  return SimpleNamespace(app_id='ap-fixture')
 async def stream(request):
  assert asyncio.get_running_loop() is loop
  assert list(request.app_ids)==['ap-fixture']
  events.append('scoped_billing_stream')
  yield SimpleNamespace(object_id='sb-fixture',
   interval_start=datetime.fromtimestamp(0,timezone.utc),
   cost='0.12',cost_by_resource={'cpu':'0.12'},tags={})
 async def close():
  assert asyncio.get_running_loop() is loop
  events.append('close')
 client=SimpleNamespace(stub=SimpleNamespace(AppGetOrCreate=lookup,WorkspaceBillingReport=SimpleNamespace(unary_stream=stream)),_close=close)
 workspace=SimpleNamespace(name='fixture',hydrate=hydrate,client=client)
 cfg=dict(workspace='fixture',app='fixture',sandbox_id='sb-fixture',start=0,end=3600)
 result=await b.bounded_fetch(cfg,timeout=1,
  dependencies=(lambda:workspace,_App.lookup,
                api_pb2.WorkspaceBillingReportRequest,lambda x:x))
 assert result['status']=='gross_usage_reported_requires_review'
 assert result['gross_reported_cost_usd']=='0.12'
 assert events==['hydrate','native_app_lookup','scoped_billing_stream','close']
 print(json.dumps({'same_loop':True,'fake_rpc_calls':2,'live_api_calls':0}))
asyncio.run(main())
"""
    result = subprocess.run(
        [str(python), "-c", code, str(ROOT / "scripts/reconcile_phonellm_billing_v2.py")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["same_loop"] is True


def test_billing_stream_timeout_does_not_publish_partial_total():
    async def run():
        closed = []

        async def hydrate():
            pass

        async def close():
            closed.append(True)

        async def stream(request):
            yield "partial"
            await asyncio.Event().wait()

        client = SimpleNamespace(
            stub=SimpleNamespace(WorkspaceBillingReport=SimpleNamespace(unary_stream=stream)),
            _close=close,
        )
        workspace = SimpleNamespace(name="fixture", hydrate=hydrate, client=client)

        async def lookup(*a, **kw):
            return SimpleNamespace(app_id="ap-fixture")

        timestamp = SimpleNamespace(FromDatetime=lambda value: None)

        def factory(**kwargs):
            return SimpleNamespace(start_timestamp=timestamp, end_timestamp=timestamp)

        result = await billing.bounded_fetch(
            dict(workspace="fixture", app="fixture", start=0, end=3600),
            timeout=0.02,
            dependencies=(lambda: workspace, lookup, factory, lambda x: x),
        )
        assert result["status"] == "fetch_timeout_reserve_unchanged" and closed == [True]
        assert "rows" not in result and "gross_reported_cost_usd" not in result

    asyncio.run(run())


def test_actual_python310_timeouts_at_each_network_phase(modal_sdk_python):
    python = modal_sdk_python
    code = """import asyncio,importlib.util,json,sys
from types import SimpleNamespace
spec=importlib.util.spec_from_file_location('b',sys.argv[1]);b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
async def check(phase):
 closed=[];loop=asyncio.get_running_loop()
 async def pause(): await asyncio.Event().wait()
 async def close():
  assert asyncio.get_running_loop() is loop
  closed.append(True)
 async def hydrate():
  if phase=='hydrate': await pause()
 async def lookup(*args,**kwargs):
  if phase=='lookup': await pause()
  return SimpleNamespace(app_id='ap-fixture')
 async def stream(req):
  yield 'partial'
  await pause()
 client=SimpleNamespace(_close=close,
  stub=SimpleNamespace(WorkspaceBillingReport=SimpleNamespace(unary_stream=stream)))
 async def client_factory(): return client
 def workspace_factory(*,client):
  return SimpleNamespace(client=client,name='fixture',hydrate=hydrate)
 timestamp=SimpleNamespace(FromDatetime=lambda x:None)
 def request_factory(**kw):
  return SimpleNamespace(start_timestamp=timestamp,end_timestamp=timestamp)
 cfg=dict(workspace='fixture',app='fixture',start=0,end=3600)
 result=await b.bounded_fetch(cfg,timeout=.02,
  dependencies=(workspace_factory,lookup,request_factory,lambda x:x,client_factory))
 assert result==dict(status='fetch_timeout_reserve_unchanged',error_type='TimeoutError'),result
 assert closed==[True],(phase,closed)
 assert 'rows' not in result
async def main():
 for phase in ['hydrate','lookup','stream']: await check(phase)
 print(json.dumps({'python':list(sys.version_info[:2]),'timeout_phases_verified':3}))
asyncio.run(main())
"""
    result = subprocess.run(
        [str(python), "-c", code, str(ROOT / "scripts/reconcile_phonellm_billing_v2.py")],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == dict(python=[3, 10], timeout_phases_verified=3)


def test_prior_probe_scope_and_loaded_source_binding(tmp_path, monkeypatch):
    import hashlib
    from datetime import datetime

    import pytest

    def save(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return hashlib.sha256(path.read_bytes()).hexdigest()

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for filename in billing.LOADED_SOURCE_HASHES:
        (scripts / filename).write_bytes((ROOT / "scripts" / filename).read_bytes())
    folder = tmp_path / "runs/v2/phonellm-probe-20260917"
    receipt = dict(
        workspace="fixture", plan_sha256="plan", reservation_id="phonellm-sandbox-probe-20260917-1"
    )
    receipt_sha = save(folder / "receipt.json", receipt)
    result = dict(
        sandbox_id="sb-t7CY44k1Ye28Tb4fMRYECh",
        receipt_sha256=receipt_sha,
        plan_sha256="plan",
        termination_confirmed=True,
        exit_code=0,
        local_before_create_unix=1,
        local_after_terminate_unix=2,
    )
    result_sha = save(folder / "execution/result.json", result)
    old = dict(
        status="fetch_started",
        result_sha256=result_sha,
        sandbox_id=result["sandbox_id"],
        app_name="kitchenrush-phonellm-bounded-probe",
        query_start_utc=datetime.fromtimestamp(0, UTC).isoformat(),
        query_end_exclusive_utc=datetime.fromtimestamp(3600, UTC).isoformat(),
    )
    old_sha = save(folder / "gross-billing-fetch.json", old)
    save(
        folder / "billing-fetch-interruption.json",
        dict(terminal_process_observed=True, original_fetch_sha256=old_sha),
    )
    monkeypatch.setattr(billing, "ROOT", tmp_path)
    assert billing.configuration("probe")["app"] == old["app_name"]
    result["local_after_terminate_unix"] = 4000
    save(folder / "execution/result.json", result)
    with pytest.raises(ValueError, match="prior billing scope binding"):
        billing.configuration("probe")
    (scripts / "reconcile_phonellm_probe.py").write_text("# changed source")
    with pytest.raises(ValueError, match="source changed after loading"):
        billing.configuration("probe")

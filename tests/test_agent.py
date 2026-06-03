"""Phase 2: the model-backed agent path with a mock client (no network).

Verifies the ModelClient -> ModelAgent -> runner -> engine -> trajectory loop, and that the
two latency tracks (rt: measured wall-clock; rp: token proxy) produce different per-turn
game-time charges.
"""

from kitchenrush import config, procgen
from kitchenrush.adapter import ModelClient, ModelResponse
from kitchenrush.agent import ModelAgent, render_observation
from kitchenrush.runner import run_episode
from kitchenrush.tools import ToolCall


class MockClient:
    """Returns a canned response; structurally satisfies ModelClient."""

    def __init__(self, tool_calls, *, text="", latency_s=0.123, usage=None):
        self.name = "mock"
        self._tool_calls = tool_calls
        self._text = text
        self._latency = latency_s
        self._usage = usage or {"prompt_tokens": 100, "completion_tokens": 20, "reasoning_tokens": 0}
        self.calls_seen = 0

    def generate(self, *, system, messages, tools, **kwargs):
        self.calls_seen += 1
        return ModelResponse(list(self._tool_calls), self._text, self._latency, self._usage)


def test_mockclient_is_a_modelclient():
    assert isinstance(MockClient([]), ModelClient)


def test_rt_track_uses_measured_latency():
    agent = ModelAgent(MockClient([ToolCall("observe", {})], latency_s=0.7), track="rt")
    obs = procgen.generate(0, "easy")
    _, latency = agent({"chef_pos": [0, 0], "hands": [], "hand_slots_free": 4, "grid_ascii": ".",
                        "grid_legend": "x", "stations": [], "burners": [], "orders": [],
                        "clock_gs": 0, "remaining_gs": 1, "score": 0, "combo_count": 0,
                        "ready_actions": [], "last_turn": {}, "events_since_last": []}, [])
    assert latency == 0.7


def test_rp_track_uses_token_proxy_and_differs_from_rt():
    client = MockClient([ToolCall("observe", {})], latency_s=5.0,
                        usage={"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0})
    obs = {"chef_pos": [0, 0], "hands": [], "hand_slots_free": 4, "grid_ascii": ".",
           "grid_legend": "x", "stations": [], "burners": [], "orders": [],
           "clock_gs": 0, "remaining_gs": 1, "score": 0, "combo_count": 0,
           "ready_actions": [], "last_turn": {}, "events_since_last": []}
    rt = ModelAgent(client, track="rt")(obs, [])[1]
    rp = ModelAgent(client, track="rp")(obs, [])[1]
    assert rt == 5.0
    assert rp >= config.RP_BETA0 and rp != rt


def test_warmup_makes_a_throwaway_call_before_scoring():
    client = MockClient([ToolCall("observe", {})], latency_s=0.1)
    ModelAgent(client, track="rt").warmup([])
    assert client.calls_seen == 1   # one throwaway spin-up call, result discarded


def test_run_episode_warms_up_then_plays():
    client = MockClient([ToolCall("observe", {})], latency_s=0.1)
    run_episode(procgen.generate(1, "easy"), ModelAgent(client, track="rt"), max_turns=4)
    assert client.calls_seen >= 2   # warmup + at least one scored turn


def test_full_episode_with_mock_agent_runs_and_logs():
    spec = procgen.generate(1, "easy")
    agent = ModelAgent(MockClient([ToolCall("observe", {})], latency_s=0.3), track="rt")
    result = run_episode(spec, agent)            # run to natural termination (horizon / all orders gone)
    assert result.report["terminated"]
    assert len(result.steps) > 0
    assert all("think_gs" in s for s in result.steps)


def test_render_observation_contains_key_fields():
    spec = procgen.generate(2, "easy")
    from kitchenrush.engine import KitchenRushEngine
    obs = KitchenRushEngine(spec).observe()
    text = render_observation(obs)
    assert "clock=" in text and "grid" in text and "orders" in text


def test_render_observation_has_no_hints():
    """No cheating: the prompt must not pre-solve navigation or list valid actions."""
    from kitchenrush.engine import KitchenRushEngine
    text = render_observation(KitchenRushEngine(procgen.generate(0, "easy")).observe())
    assert "offset" not in text.lower()
    assert "ready_action" not in text.lower()
    assert "->" not in text  # no "go 3 south, 4 east"-style routing


class RaisingClient:
    name = "raises"

    def generate(self, **kwargs):
        raise RuntimeError("simulated API timeout")


def test_agent_degrades_to_stall_on_client_error():
    """A model/infra error must become a stall (no action), not crash the run (RULES §13.7)."""
    obs = {"chef_pos": [0, 0], "hands": [], "hand_slots_free": 4, "grid_ascii": ".",
           "grid_legend": "x", "stations": [], "burners": [], "orders": [],
           "clock_gs": 0, "remaining_gs": 1, "score": 0, "combo_count": 0,
           "last_turn": {}, "events_since_last": []}
    calls, latency = ModelAgent(RaisingClient(), track="rt", stall_seconds=12.0)(obs, [])
    assert calls == [] and latency == 12.0

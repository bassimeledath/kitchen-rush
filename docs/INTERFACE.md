> ⚠️ **Design history — not the current spec.** Parts of this document describe an earlier or
> aspirational design and may not match the implementation. The authoritative, code-verified spec
> is **[RULES.md](RULES.md)**; release tracking is in **[LAUNCH_CHECKLIST.md](LAUNCH_CHECKLIST.md)**.
# Kitchen Rush v2 — INTERFACE

The public contract third parties code against: model adapters, registry, CLI, JSON schemas, reproducibility. Mirrors BFCL's handler registry and tau-bench's agent factory, with latency as a first-class graded axis.

## 1. Package & public API

Distribution `kitchen-rush`; import root `kitchenrush`; CLI `kitchenrush`. `src/`-layout (see file tree).

### Public surface (`kitchenrush/__init__.py`)
```python
from kitchenrush.version import __version__, SCHEMA_VERSION, RULESET_VERSION, GENERATOR_VERSION, TOKENIZER_ID
from kitchenrush.adapters.base import ModelClient, ModelResponse, ToolCall, ToolSpec, Usage, LatencySample
from kitchenrush.adapters.registry import register_adapter, resolve_model, list_adapters
from kitchenrush.harness.runner import run_episode, run_suite
from kitchenrush.harness.agent import Agent
from kitchenrush.engine.engine import KitchenRushEngine
from kitchenrush.engine.state import GameState
from kitchenrush.procgen.splits import get_split, SplitName
from kitchenrush.procgen.generator import generate_spec
from kitchenrush.report.schema import StepRecord, EpisodeResult, RunSummary
from kitchenrush.report.aggregate import pass_at_k, latency_percentiles, rttc
```

### Hello world
```python
from kitchenrush import run_suite, resolve_model, get_split
client = resolve_model("openai:gpt-4.1")
summary = run_suite(client=client, seeds=get_split("test"), trials=4, track="rp")
print(summary.rttc, summary.pass_at_k["4"], summary.latency.p50_think_gs)
```

## 2. Model adapter abstraction (native FC, multi-provider)

### 2.1 Shared types (`adapters/base.py`)
```python
@dataclass(frozen=True)
class ToolSpec:                          # provider-neutral; engine emits these
    name: str; description: str; parameters: dict   # JSON Schema (draft 2020-12 subset)

@dataclass(frozen=True)
class ToolCall:
    id: str; name: str; arguments: dict; index: int = 0; raw_arguments: str | None = None

@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None; completion_tokens: int | None = None
    total_tokens: int | None = None; cached_prompt_tokens: int | None = None
    reasoning_tokens: int | None = None  # REQUIRED non-null for known thinking models (validator-checked)

@dataclass(frozen=True)
class LatencySample:
    total_ms: float                      # send → full response parsed (RT track, diagnostic; successful attempt only)
    ttft_ms: float | None = None
    server_request_ms: float | None = None
    attempts: int = 1                    # retries EXCLUDED from total_ms; attempts>1 logged
    streamed: bool = False; timed_out: bool = False

@dataclass(frozen=True)
class ModelResponse:
    text: str
    tool_calls: list[ToolCall]           # N>1 ⇒ chained in one turn
    latency: LatencySample
    usage: Usage
    finish_reason: str | None = None
    raw: dict = field(default_factory=dict)
```

### 2.2 The `ModelClient` protocol
```python
@runtime_checkable
class ModelClient(Protocol):
    name: str                            # "openai:gpt-4.1"
    provider: str                        # openai_compatible|anthropic|gemini|litellm|<custom>
    supports_parallel_tool_calls: bool
    def generate(self, *, system: str, messages: list[dict], tools: list[ToolSpec],
                 tool_choice: str = "auto", max_tokens: int = 1024, temperature: float = 0.2,
                 stream: bool = False, timeout_s: float = 60.0,
                 extra: dict | None = None) -> ModelResponse: ...
```
- Stateless w.r.t. game state; only translates one request. No game logic.
- Optional `async def agenerate(...)` with identical signature for `--concurrency` (RP/dev only).
- Default `temperature=0.2` (Pass^k reliability needs genuine sampling, SCORING §6.2).
- `_safe_json` never raises: malformed args → `({}, raw_string)`; the engine treats it as an invalid call (a measured model failure, not a harness crash).

### 2.3 Provider-neutral transcript
One assistant turn with ordered tool calls, then one tool message per call id in the same order:
```python
{"role":"assistant","content":"Heading to the stove, then plating.",
 "tool_calls":[{"id":"c1","name":"move","arguments":{"direction":"south","steps":3}},
               {"id":"c2","name":"collect_cooked","arguments":{"ingredient":"patty"}}]}
{"role":"tool","tool_call_id":"c1","name":"move","content":{"ok":true,"chef_pos":[2,5],"cells_moved":3}}
{"role":"tool","tool_call_id":"c2","name":"collect_cooked","content":{"ok":true,"picked_up":"cooked_patty"}}
```
Each adapter is the ONLY place that knows a provider's quirks (string vs structured tool content, etc.).

### 2.4 Per-provider rendering (replay-conformant)
| Provider | Tools rendered as | Parallel FC | tool_calls from | Notes |
|---|---|---|---|---|
| OpenAI-compatible (vLLM/Nemotron/OpenAI) | `tools=[{type:function,function:{name,description,parameters}}]` | `parallel_tool_calls=True`, `tool_choice` | `choices[0].message.tool_calls[]` | `seed` param passthrough; Nemotron `enable_thinking` via `extra_body` |
| Anthropic | `tools=[{name,description,input_schema}]` | multiple `tool_use` blocks | `content[type==tool_use]` | tool results as `tool_result` blocks; synthesize ids if absent |
| Gemini | `tools=[{function_declarations:[...]}]` | multiple `functionCall` parts | `candidates[0].content.parts[].functionCall` | structured `functionResponse`; ids synthesized (Gemini omits) |
| LiteLLM | OpenAI schema | provider-dependent → `supports_parallel_tool_calls` | OpenAI shape | passthrough |

A `tests/test_adapters_conformance.py` replay spec asserts each adapter round-trips the neutral transcript, synthesizes missing ids deterministically, and degrades gracefully when a provider lacks parallel FC (chain becomes one-call-per-turn; logged).

### 2.5 RP token counting (`adapters/tokenizer.py`)
RP latency uses a **pinned tokenizer** (`TOKENIZER_ID` in `version.py`) over the canonical transcript for the **visible** tokens: `n_in` = full rendered prompt (system + transcript + serialized tool schemas); the visible part of `n_out` = assistant text + serialized tool-call JSON. Cached tokens counted at full rate. The **reasoning-token** term added to `n_out` is the **provider's self-reported count** (from `usage`), not a tokenizer count over logged text — so the visible terms are recomputable but the reasoning term is **provider-trusted** (RP is not fully recomputable for hidden-reasoning models; RULES §3.2.1, METHODOLOGY §3.1).

### 2.6 Registry (`adapters/registry.py`)
```python
ADAPTER_REGISTRY = {"openai":OpenAICompatibleClient,"vllm":OpenAICompatibleClient,
                    "nemotron":OpenAICompatibleClient,"anthropic":AnthropicClient,
                    "gemini":GeminiClient,"litellm":LiteLLMClient}
def register_adapter(provider, factory): ...
def resolve_model(model_id, **overrides) -> ModelClient:    # "<provider>:<model>"; base_url/api_key from env
```
Custom adapters: `register_adapter("mycorp", MyClient)` then `--model mycorp:my-7b --plugin mypkg.adapters` (or a `kitchenrush.adapters` entry-point group). `resolve_model` raises `MissingExtraError("install kitchen-rush[anthropic]")` if an SDK extra is absent.

## 3. CLI
```
kitchenrush run --model PROVIDER:MODEL [--seeds SPLIT|RANGE|FILE] [--trials N] [--track rp|rt]
                [--tier TIER] [--temperature T] [--max-tokens N] [--stream] [--limit N]
                [--concurrency N] [--out DIR] [--plugin MOD] [--extra-json '{...}']
kitchenrush adapters                       # list providers
kitchenrush seeds [--split SPLIT]          # seeds + config hashes
kitchenrush aggregate --run DIR            # (re)compute summary
kitchenrush validate --submission FILE     # offline = CI validation
kitchenrush submit --run DIR --meta meta.toml
kitchenrush leaderboard build
```
Canonical: `kitchenrush run --model openai:gpt-4.1 --seeds test --trials 4 --track rp`.
- `--seeds test` → `test` split; also `train|dev|challenge`, an explicit range `0-99`, or a manifest path.
- `--track rp` (canonical, ranked) | `rt` (diagnostic; validator forces `attempts=1, concurrency=1`).
- Run id: `kr-<ruleset>-<split>-<modelslug>-<utc>-<rand4>`.

`run_episode(client,*,spec,track,agent=None,max_turns=300,writer=None)→EpisodeResult` and `run_suite(client,*,seeds,trials,track="rp",tier="MEDIUM",concurrency=1,out_dir=None)→RunSummary`. Per episode: `observe()` → `Agent.build(...)` → `client.generate(...)` (latency measured here) → `clock.latency_to_gs(...)` → `engine.step(tool_calls, think_gs)` → log `StepRecord` → repeat until `done`. Trials replay the same seed (Pass^k); seeds vary the world.

## 4. JSON schemas (pydantic, `report/schema.py`)
Trajectories: JSONL (one `StepRecord`/line) at `out/<run_id>/<seed>/trial<k>.jsonl`, plus per-episode `result.json`, run-level `summary.json`. `schema_version` on every file.

### 4.1 StepRecord (one turn)
```jsonc
{"schema_version":"1.0","step":7,"game_time_sec":41.5,"time_remaining_sec":48.5,
 "observation":{/* engine.observe() */},
 "tool_calls":[{"id":"c1","name":"move","arguments":{"direction":"south","steps":3},"index":0},
               {"id":"c2","name":"collect_cooked","arguments":{"ingredient":"patty"},"index":1}],
 "assistant_text":"Grabbing the patty.",
 "tool_results":[{"call_id":"c1","ok":true,"result":{"chef_pos":[2,5],"cells_moved":3}},
                 {"call_id":"c2","ok":true,"result":{"picked_up":"cooked_patty"}}],
 "events":[{"type":"cook_ready","game_time_sec":41.5,"detail":{"burner_index":0},"ok":true}],
 "failed_at_index":null,"aborted_calls":[],
 "score_delta":0.0,"score_after":36.0,
 "latency":{"track":"rp","total_ms":612.4,"ttft_ms":88.0,"attempts":1,"streamed":true,"timed_out":false},
 "think_gs":0.94,"sim_seconds_charged":7.94,
 "tokens":{"prompt":1840,"completion":47,"reasoning":0,"counted_by":"pinned:cl100k_base@1.0"},
 "finish_reason":"tool_calls","num_tool_calls":2,"chained":true}
```

### 4.2 EpisodeResult (seed × trial)
```jsonc
{"schema_version":"1.0","run_id":"kr-…","ruleset_version":"1.0.0","generator_version":"krgen-1.0.0",
 "config_hash":"sha256:…","seed":42,"trial":0,"track":"rp","tier":"MEDIUM",
 "model":"openai:gpt-4.1","provider":"openai_compatible",
 "score_raw":118.0,"score_display":118.0,"oracle_score":160.0,"eta":0.7375,
 "orders_served":9,"orders_expired":1,"items_burned":1,"mistakes":2,
 "invalid_tool_calls":1,"malformed_tool_calls":0,"timeouts":0,"empty_turns":0,
 "game_time_sec":300.0,"steps":34,"done_reason":"horizon",
 "latency":{"track":"rp","p50_think_gs":0.54,"p90_think_gs":0.91,"p99_think_gs":1.32,
            "mean_think_gs":0.60,"total_think_gs":20.5},
 "tokens":{"prompt":61200,"completion":1580,"reasoning":0},
 "on_time_rate":0.89,"chained_turn_rate":0.41,"combo_efficiency":0.62,
 "trajectory_file":"42/trial0.jsonl"}
```

### 4.3 Latency measurement standard (normative)
1. **`total_ms`** (RT) measured with `time.perf_counter()` from before the call to after the full response is parsed; for streaming, send → last chunk.
2. **TTFT** recorded for streaming only; reported, not graded (Kitchen Rush grades time-to-actionable-full-tool-call).
3. **Retries excluded** from `total_ms`; `attempts>1` logged for audit.
4. **Timeouts charged in full:** a `timed_out` turn charges `LATENCY_SCALE·timeout_s` to the clock, yields no tool call (RULES §13.7 stall). No "set huge timeout to dodge" loophole.
5. **`server_request_ms`** logged but never authoritative.
6. RT-track runs MUST use `attempts=1` and `concurrency=1` (validator-enforced) so measured latency is true serving latency.

### 4.4 RunSummary
```jsonc
{"schema_version":"1.0","run_id":"…","ruleset_version":"1.0.0","split":"test","tier":"MEDIUM",
 "model":"openai:gpt-4.1","provider":"openai_compatible","track":"rp",
 "seeds":[0,1,…],"trials_per_seed":4,"episodes":400,"config_hash":"sha256:…",
 "score":{"mean":112.3,"std":9.1,"median":114.0},"eta":{"mean":0.702,"ci95":[0.69,0.71]},
 "pass":{"theta_pass":0.6,"pass_at_k":{"1":0.81,"2":0.74,"4":0.66}},
 "rttc":68.4,
 "latency":{"track":"rp","p50_think_gs":0.55,"p90_think_gs":0.91,"p99_think_gs":1.41,"timeouts":3},
 "behavior":{"on_time_rate":0.78,"chained_turn_rate":0.39,"invalid_tool_call_rate":0.03,
             "expiry_rate":0.11,"burn_rate":0.07,"combo_efficiency":0.60},
 "environment":{"kitchenrush_version":"1.0.0","python":"3.12.x","tokenizer_id":"cl100k_base@1.0",
                "hardware":"Apple M3 Max","region":"us-west-2","temperature":0.2,
                "concurrency":1,"attempts_max":1}}
```

## 5. Leaderboard, reliability, standard vs custom

### 5.1 Flow (tau-bench style)
1. Run on the official split. 2. `kitchenrush submit` → `leaderboard/submissions/<slug>.json` (`SubmissionManifest` + `RunSummary` + content-hashes of every trajectory; trajectories uploaded as a release artifact referenced by URL+sha). 3. `kitchenrush validate` (same as CI: schema valid; ruleset/config/seeds hashes match the frozen split; episode count = seeds×trials; RP recomputed from logged tokens matches summary within tolerance; RT submissions have attempts=1+concurrency=1; reasoning_tokens non-null for known thinking models). 4. PR; CI re-validates; maintainer merges; board rebuilds.

### 5.2 SubmissionManifest (`leaderboard/manifest.py`)
```jsonc
{"schema_version":"1.0","submission_type":"standard",
 "model":{"display_name":"GPT-4.1","model_id":"openai:gpt-4.1","provider":"openai_compatible",
          "is_open_weights":false,"params_b":null,"url":"…"},
 "ruleset_version":"1.0.0","generator_version":"krgen-1.0.0","tokenizer_id":"cl100k_base@1.0",
 "split":"test","track":"rp","config_hash":"sha256:…","seeds_hash":"sha256:…","trials_per_seed":4,
 "latency_env":{"class":"cloud","region":"us-west-2","hardware":"n/a (hosted)"},
 "harness_settings":{"temperature":0.2,"max_tokens":1024,"stream":true,"tool_choice":"auto",
                     "concurrency":1,"attempts_max":1},
 "results":{/* RunSummary */},"trajectory_artifact":{"url":"…","sha256":"…"},
 "submitter":{"name":"…","contact":"…","date":"2026-06-02"},
 "reproducibility":{"kitchenrush_version":"1.0.0","python":"3.12.4","uv_lock_sha256":"…","command":"kitchenrush run …"}}
```

### 5.3 Reliability & ranking
- Pass^k is **normalized pass** (`KR_instance ≥ θ_pass`, θ_pass=0.6), `k=4` (RULES §9.8.1). *(This file is archived design history; the older `θ_pass·S*` raw-ratio form and the RTTC headline are obsolete — the implemented headline is KR, RULES §9.8.)*
- **Primary ranking: RP-track KR** (RULES §9.8, METHODOLOGY §3). Board shows KR, raw score, Pass^1/Pass^4, RP latency p50/p90, tokens/episode, on-time rate, chained-turn rate, and RT-track latency + hardware (adjacent, diagnostic). The whole point is the speed-accuracy tradeoff in one row.

### 5.4 Standard vs custom
- **Standard:** unmodified harness, official frozen split (hashes match), default reference `Agent` + tool set, `temperature=0.2`. Headline board. CI rejects hash-mismatch or default-agent override.
- **Custom/Open:** custom prompt/Agent/scaffolding, retries-as-strategy, custom adapter post-processing. Separate board; must include a `method` paragraph + config diff. Standard board measures the *model*; custom board measures *systems*.
- **Anti-overfitting:** test seeds are public (transparency: anyone can reproduce a number locally) but the **headline is computed on a maintainer-run hidden `challenge` band** (seeds withheld, harder θ) plus a canary GUID embedded in the docs; the rotating yearly `test_vN` manifest decays memorization value. See CONTAMINATION.md and PROCEDURAL §d.

## 6. Reproducibility & environment
`pyproject.toml` (uv, pinned, extras-gated): core deps `pydantic, typer, numpy, rich, python-dotenv, tiktoken` (pinned tokenizer); extras `openai`, `anthropic`, `gemini`, `litellm`, `all`, `dev`. `uv.lock` committed; `uv_lock_sha256` in every manifest. `version.py` carries `__version__, SCHEMA_VERSION, RULESET_VERSION, GENERATOR_VERSION, TOKENIZER_ID`; the validator requires a submission's `ruleset_version` to match the active board.

`.env.example` provides `OPENAI_API_KEY`, `KR_VLLM_BASE_URL/API_KEY`, `KR_NEMOTRON_BASE_URL/API_KEY`, `KR_NEMOTRON_ENABLE_THINKING`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GEMINI_THINKING_BUDGET`, and harness defaults (`KR_DEFAULT_TRACK=rp`, `KR_DEFAULT_TRIALS=4`, `KR_OUT_DIR=./runs`).

```bash
uv sync --extra all && cp .env.example .env
uv run kitchenrush run --model openai:gpt-4.1 --seeds test --trials 4 --track rp
uv run kitchenrush leaderboard build
```

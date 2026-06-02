"""run_episode/run_suite turn loop: observe -> Agent.build -> client.generate (measure latency) -> clock.latency_to_gs (RP tokens | RT wall_ms) -> engine.step(calls, think_gs) -> log StepRecord -> until done; trials replay same seed; concurrency forced to 1 for RT.

Kitchen Rush — scaffold stub. NOT YET IMPLEMENTED.
Design lives in docs/ (DESIGN, RULES, SCORING, INTERFACE, PROCEDURAL, MOVEMENT).
See docs/ROADMAP.md for the phase that implements this module.
"""

from __future__ import annotations

# TODO: implement per the design docs.

# Replay accounting check — 2026-09-17

The primary agent visually checked the running replay page in the browser after the accounting-label fix:

- Jev: quality 20, 1/3 orders, four invalid actions, $0.004742 paid. The generation-token comparison is explicitly **not applicable**.
- GPT-5.4 Mini: quality 100, $0.084046 paid, 6,148 generated tokens. The action-prefix allowance and spending-reserve explanation remain visible.

The offline regression (`node tests/replay_accounting.test.cjs`) passed. All seven replay file hashes remain unchanged; six model entries carry provider accounting metadata verified against their original episode and canary manifest. The check changes presentation only, preserving reported usage, costs, kitchen states and quality.

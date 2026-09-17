"""Crash-safe reservations. No request may dispatch without an atomic spend reservation."""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

SCALE = 1_000_000_000
STAGES = {"canary": 5.0, "smoke": 15.0, "pilot": 30.0}


def units(amount):
    if not math.isfinite(amount) or amount < 0:
        raise ValueError("invalid money amount")
    return math.ceil(amount * SCALE - 1e-9)


class BudgetExceeded(RuntimeError):
    pass


class Ledger:
    def __init__(self, path, *, cap=50.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cap = units(cap)
        with self.connect() as con:
            con.execute("CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY, cap INTEGER)")
            con.execute("INSERT OR IGNORE INTO meta VALUES (1, ?)", (self.cap,))
            if con.execute("SELECT cap FROM meta WHERE id=1").fetchone()[0] != self.cap:
                raise ValueError("cannot change existing ledger ceiling")
            con.execute("""CREATE TABLE IF NOT EXISTS requests (
                id TEXT PRIMARY KEY, episode TEXT NOT NULL, stage TEXT NOT NULL,
                reserved INTEGER NOT NULL, actual INTEGER, status TEXT NOT NULL)""")

    def connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.execute("PRAGMA synchronous=FULL")
        return con

    @staticmethod
    def _used(con, where="", params=()):
        return con.execute(
            "SELECT COALESCE(SUM(COALESCE(actual, reserved)),0) FROM requests " + where, params
        ).fetchone()[0]

    def reserve(self, rid, episode, stage, maximum, episode_cap):
        amount = units(maximum)
        if stage not in STAGES:
            raise ValueError("unapproved stage")
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM requests WHERE id=?", (rid,)).fetchone():
                raise ValueError("request already reserved; do not redispatch")
            if con.execute(
                "SELECT 1 FROM requests WHERE status IN ('uncertain','overrun')"
            ).fetchone():
                raise BudgetExceeded("another request requires an accounting audit")
            for current, ceiling in (
                (self._used(con), self.cap),
                (self._used(con, "WHERE stage=?", (stage,)), units(STAGES[stage])),
                (self._used(con, "WHERE episode=?", (episode,)), units(episode_cap)),
            ):
                if current + amount > ceiling:
                    raise BudgetExceeded("reservation exceeds episode/stage/global allowance")
            con.execute(
                "INSERT INTO requests VALUES (?,?,?,?,NULL,?)",
                (rid, episode, stage, amount, "reserved"),
            )

    def settle(self, rid, actual):
        amount = units(actual)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT reserved,actual,status FROM requests WHERE id=?", (rid,)
            ).fetchone()
            if row is None:
                raise ValueError("unreserved request")
            if row[1] is not None:
                if row[1] != amount:
                    raise ValueError("inconsistent duplicate settlement")
                return
            # Preserve an unexpected overage in the ledger BEFORE halting the run.
            con.execute(
                "UPDATE requests SET actual=?,status=? WHERE id=?",
                (amount, "settled" if amount <= row[0] else "overrun", rid),
            )
        if amount > row[0]:
            raise RuntimeError("provider cost exceeded reservation; halt and audit")

    def uncertain(self, rid):
        with self.connect() as con:
            con.execute(
                "UPDATE requests SET status='uncertain' WHERE id=? AND actual IS NULL", (rid,)
            )

    def retain_failed_liability(self, rid):
        """Keep the FULL reserve charged forever for an abandoned unscored attempt.

        This does not assert an actual bill or cancel remote execution. It only allows
        independent experiments to continue without ever reusing the outstanding money.
        """
        with self.connect() as con:
            row = con.execute("SELECT status,actual FROM requests WHERE id=?", (rid,)).fetchone()
            if row is None or row[0] != "uncertain" or row[1] is not None:
                raise ValueError("only an uncertain failed attempt may retain liability")
            con.execute("UPDATE requests SET status='retained_failure' WHERE id=?", (rid,))

    def used(self, episode=None):
        with self.connect() as con:
            return (
                self._used(con, "WHERE episode=?" if episode else "", (episode,) if episode else ())
                / SCALE
            )

    def snapshot(self):
        with self.connect() as con:
            rows = con.execute(
                "SELECT id,episode,stage,reserved,actual,status FROM requests"
            ).fetchall()
        return {
            "cap_usd": self.cap / SCALE,
            "accounted_usd": self.used(),
            "settled_usd": sum(r[4] or 0 for r in rows) / SCALE,
            "unresolved_reserve_usd": sum(r[3] for r in rows if r[4] is None) / SCALE,
            "requests": len(rows),
            "unresolved_requests": sum(r[5] in ("reserved", "uncertain") for r in rows),
            "retained_failed_requests": sum(r[5] == "retained_failure" for r in rows),
            "overruns": sum(r[5] == "overrun" for r in rows),
            "stages": {
                s: sum(r[4] if r[4] is not None else r[3] for r in rows if r[2] == s) / SCALE
                for s in STAGES
            },
        }

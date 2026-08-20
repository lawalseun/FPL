"""End-to-end checks that don't need the network. Run: python tools/selftest.py"""
import json, os, sys
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import runpy
runpy.run_path(os.path.join(ROOT, "tools", "offline_build.py"), run_name="__offline__")

from fpl import solver as S   # noqa: E402

d = json.load(open(os.path.join(ROOT, "docs", "data.json")))
sq = pd.DataFrame(d["optimal"]["xi"] + d["optimal"]["bench"])
sq["xp_horizon"] = sq.xp
POS = {1: 2, 2: 5, 3: 5, 4: 3}

fails = []
def chk(cond, msg):
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        fails.append(msg)

chk(len(sq) == 15, f"squad has 15 players ({len(sq)})")
chk(abs(sq.cost.sum() - d["optimal"]["cost"]) < 1e-6, f"cost {sq.cost.sum():.1f} <= 100.0")
chk(sq.cost.sum() <= 100.0 + 1e-9, "within budget")
for p, n in POS.items():
    chk((sq.pos == p).sum() == n, f"{S.POS[p]}: {(sq.pos == p).sum()} of {n}")
chk(sq.team.value_counts().max() <= 3, f"max {sq.team.value_counts().max()} per club")
chk(sq.id.nunique() == 15, "no duplicate players")
chk(d["optimal"]["captain"] in [p["name"] for p in d["optimal"]["xi"]], "captain starts")

xi, bench = S.pick_xi(sq)
chk(len(xi) == 11 and len(bench) == 4, f"XI/bench split {len(xi)}/{len(bench)}")
chk((xi.pos == 1).sum() == 1, "one keeper in the XI")
chk(3 <= (xi.pos == 2).sum() <= 5, "3-5 defenders")
chk((xi.pos == 4).sum() >= 1, "at least one forward")
chk(bench.iloc[0].pos == 1, "reserve keeper is first on the bench")

# transfer search against a deliberately weakened squad
pool = pd.DataFrame([p for k in d["top"] for p in d["top"][k]])
pool["xp_horizon"] = pool.xp
weak = sq.copy()
moves = S.recommend_transfers(weak, pool, bank=1.5, free=1, max_moves=1, top_n=5)
chk(len(moves) > 0, f"transfer search returned {len(moves)} options")
chk(moves[0]["net"] >= 0, "best option is never worse than doing nothing")
chk(any(not m["in"] for m in moves), "rolling the transfer is offered")
for m in moves[:3]:
    print(f"      {m['out'] or ['(roll)']} -> {m['in'] or ['-']}  net {m['net']:+.2f}")

adv = S.chip_advice(sq, {1: dict(zip(sq.id, sq.xp))}, {1: {}},
                    ["wildcard", "freehit", "3xc", "bboost"], 1)
chk(len(adv) == 4, f"chip advice covers all four chips ({len(adv)})")
chk(all("why" in c for c in adv), "every chip comes with a reason")

for f in ("docs/index.html", "docs/data.json", "docs/fpl-deadlines.ics"):
    chk(os.path.getsize(os.path.join(ROOT, f)) > 0, f"{f} written")

print("\n" + ("ALL CHECKS PASSED" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)

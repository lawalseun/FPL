"""Squad optimiser, transfer recommender and chip timer.

The squad build is an integer linear program (ILP): pick 15 players that
maximise expected points subject to the FPL rules. The transfer recommender is
a search over every legal move from your current squad, scored on the same
model over a multi-gameweek horizon and charged 4 points for each hit.
"""
from __future__ import annotations
import itertools
import pandas as pd
import pulp

SQUAD = {1: 2, 2: 5, 3: 5, 4: 3}
XI_MIN = {1: 1, 2: 3, 3: 2, 4: 1}
XI_MAX = {1: 1, 2: 5, 3: 5, 4: 3}
POS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
BENCH_WEIGHT = 0.18     # a bench point is worth ~a fifth of a starting point
HIT = 4                 # points charged per extra transfer


def build_squad(df: pd.DataFrame, budget: float = 100.0, force: list[str] | None = None,
                ban: list[str] | None = None, obj: str = "xp"):
    d = df[df.status.isin(["a", "d"])].reset_index(drop=True)
    if ban:
        d = d[~d.name.isin(ban)].reset_index(drop=True)

    p = pulp.LpProblem("fpl_squad", pulp.LpMaximize)
    sq = pulp.LpVariable.dicts("sq", d.index, cat="Binary")
    xi = pulp.LpVariable.dicts("xi", d.index, cat="Binary")
    cap = pulp.LpVariable.dicts("cap", d.index, cat="Binary")

    p += pulp.lpSum(d[obj][i] * xi[i] + BENCH_WEIGHT * d[obj][i] * (sq[i] - xi[i])
                    + d[obj][i] * cap[i] for i in d.index)
    for i in d.index:
        p += xi[i] <= sq[i]
        p += cap[i] <= xi[i]
    p += pulp.lpSum(cap[i] for i in d.index) == 1
    p += pulp.lpSum(sq[i] for i in d.index) == 15
    p += pulp.lpSum(xi[i] for i in d.index) == 11
    p += pulp.lpSum(d.cost[i] * sq[i] for i in d.index) <= budget
    for pos, n in SQUAD.items():
        idx = d.index[d.pos == pos]
        p += pulp.lpSum(sq[i] for i in idx) == n
        p += pulp.lpSum(xi[i] for i in idx) >= XI_MIN[pos]
        p += pulp.lpSum(xi[i] for i in idx) <= XI_MAX[pos]
    for t in d.teamid.unique():
        p += pulp.lpSum(sq[i] for i in d.index[d.teamid == t]) <= 3
    for nm in (force or []):
        idx = d.index[d.name == nm]
        if len(idx):
            p += pulp.lpSum(sq[i] for i in idx) >= 1

    p.solve(pulp.PULP_CBC_CMD(msg=0))
    d = d.assign(in_sq=[int(sq[i].value()) for i in d.index],
                 in_xi=[int(xi[i].value()) for i in d.index],
                 is_cap=[int(cap[i].value()) for i in d.index])
    return d[d.in_sq == 1].copy(), pulp.LpStatus[p.status]


def pick_xi(squad: pd.DataFrame, col: str = "xp"):
    """Best legal XI + bench order out of an existing 15."""
    best = None
    for ndef in range(3, 6):
        for nmid in range(2, 6):
            nfwd = 10 - ndef - nmid
            if not 1 <= nfwd <= 3:
                continue
            xi = pd.concat([
                squad[squad.pos == 1].nlargest(1, col),
                squad[squad.pos == 2].nlargest(ndef, col),
                squad[squad.pos == 3].nlargest(nmid, col),
                squad[squad.pos == 4].nlargest(nfwd, col)])
            if len(xi) < 11:
                continue
            if best is None or xi[col].sum() > best[col].sum():
                best = xi
    bench = squad[~squad.id.isin(best.id)].copy()
    bench = pd.concat([bench[bench.pos == 1],
                       bench[bench.pos != 1].sort_values(col, ascending=False)])
    return best, bench


def recommend_transfers(current: pd.DataFrame, pool: pd.DataFrame, bank: float,
                        free: int, col: str = "xp_horizon", max_moves: int = 2,
                        top_n: int = 8):
    """Score every 0-, 1- and 2-move option. Returns them ranked by net gain."""
    cur_ids = set(current.id)
    base_xi, _ = pick_xi(current, col)
    base = base_xi[col].sum()
    cands = pool[(~pool.id.isin(cur_ids)) & (pool.status == "a")]
    cands = (pd.concat([g.nlargest(45, col) for _, g in cands.groupby("pos")])
             .drop_duplicates("id").reset_index(drop=True))

    options = [{"out": [], "in": [], "cost": 0.0, "hits": 0, "gain": 0.0, "net": 0.0}]
    for n in range(1, max_moves + 1):
        for outs in itertools.combinations(current.itertuples(), n):
            out_pos = sorted(o.pos for o in outs)
            budget = bank + sum(o.cost for o in outs)
            sub = cands[cands.pos.isin(out_pos)]
            for ins in itertools.combinations(sub.itertuples(), n):
                if sorted(i.pos for i in ins) != out_pos:
                    continue
                spend = sum(i.cost for i in ins)
                if spend > budget + 1e-9:
                    continue
                new = pd.concat([current[~current.id.isin([o.id for o in outs])],
                                 pd.DataFrame([i._asdict() for i in ins])])
                counts = new.teamid.value_counts()
                if counts.max() > 3 or len(set(new.id)) != 15:
                    continue
                xi, _ = pick_xi(new, col)
                gain = xi[col].sum() - base
                hits = max(0, n - free)
                options.append({
                    "out": [o.name for o in outs], "in": [i.name for i in ins],
                    "cost": round(spend - sum(o.cost for o in outs), 1),
                    "hits": hits, "gain": round(gain, 2),
                    "net": round(gain - hits * HIT, 2)})
    options.sort(key=lambda o: -o["net"])
    seen, out = set(), []
    for o in options:
        k = (tuple(o["out"]), tuple(o["in"]))
        if k in seen:
            continue
        seen.add(k)
        out.append(o)
        if len(out) >= top_n:
            break
    if not any(not o["in"] for o in out):      # always show "do nothing" as the baseline
        out.append({"out": [], "in": [], "cost": 0.0, "hits": 0, "gain": 0.0, "net": 0.0})
    return out


# --------------------------------------------------------------------------- #
# chips
# --------------------------------------------------------------------------- #
def chip_advice(squad: pd.DataFrame, xp_by_gw: dict, fixture_counts: dict,
                chips_left: list[str], gw: int, half_end: int = 19):
    """xp_by_gw: {gw: {player_id: xp}}. fixture_counts: {gw: {team_id: n_fixtures}}.
    Returns a recommendation per remaining chip with a 'play it now?' verdict."""
    out = []
    gws = sorted(xp_by_gw)
    bench_ids = set(pick_xi(squad)[1].id)
    xi_ids = set(pick_xi(squad)[0].id)

    def bench_total(g):
        return sum(xp_by_gw[g].get(i, 0) for i in bench_ids)

    def best_captain(g):
        v = {i: xp_by_gw[g].get(i, 0) for i in xi_ids}
        return max(v.items(), key=lambda kv: kv[1]) if v else (None, 0)

    if "bboost" in chips_left:
        scores = {g: bench_total(g) for g in gws}
        best = max(scores, key=scores.get)
        out.append({"chip": "Bench Boost", "best_gw": best,
                    "value": round(scores[best], 1),
                    "now": best == gw and scores[best] >= 14,
                    "why": f"Your four bench players project {scores[best]:.1f} pts in GW{best}. "
                           "Bench Boost is worth playing when that clears ~14, which in practice "
                           "means a double gameweek where all 15 play twice."})
    if "3xc" in chips_left:
        scores = {g: best_captain(g)[1] for g in gws}
        best = max(scores, key=scores.get)
        pid = best_captain(best)[0]
        nm = squad.set_index("id").name.get(pid, "?")
        out.append({"chip": "Triple Captain", "best_gw": best, "value": round(scores[best], 1),
                    "now": best == gw and scores[best] >= 8,
                    "why": f"{nm} projects {scores[best]:.1f} pts in GW{best}; tripling adds "
                           f"~{scores[best]:.1f} over a normal captain. Hold it for a premium "
                           "forward with a double gameweek at home."})
    if "freehit" in chips_left:
        blanks = {g: sum(1 for p in squad.itertuples()
                         if fixture_counts.get(g, {}).get(p.teamid, 1) == 0) for g in gws}
        worst = max(blanks, key=blanks.get)
        out.append({"chip": "Free Hit", "best_gw": worst, "value": blanks[worst],
                    "now": worst == gw and blanks[worst] >= 5,
                    "why": f"{blanks[worst]} of your 15 blank in GW{worst}. Free Hit is the "
                           "answer to a big blank gameweek — it costs nothing and your squad "
                           "comes back untouched."})
    if "wildcard" in chips_left:
        out.append({"chip": "Wildcard", "best_gw": None, "value": None,
                    "now": False,
                    "why": f"Keep it for when your squad needs 3+ changes at once, or to set up "
                           f"for a fixture swing. First-half wildcard expires at the GW{half_end} "
                           "deadline — an unused chip is a wasted chip."})
    return out

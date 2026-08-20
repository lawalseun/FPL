"""Regenerate the two prior files. Run this once each summer, before GW1.

FPL wipes every stat column when a season rolls over, so at GW1 the API says
every player has played zero minutes and every club has zero strength. These
two files carry last season forward so week one isn't a coin flip.

    python tools/make_priors.py                       # pulls last season from the mirror
    python tools/make_priors.py --season 2026-27      # pick the season to summarise

Writes fpl/team_priors.json and fpl/prev_season.json.
"""
import argparse, json, os
import pandas as pd

MIRROR = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025-26", help="the season to summarise")
    ap.add_argument("--promoted-xg", type=float, default=0.95)
    ap.add_argument("--promoted-xga", type=float, default=1.75)
    a = ap.parse_args()

    p = pd.read_csv(f"{MIRROR}/{a.season}/players_raw.csv")
    t = pd.read_csv(f"{MIRROR}/{a.season}/teams.csv")
    p["tname"] = p.team.map(dict(zip(t.id, t.name)))

    # ---- club attack / defence per match ---------------------------------
    xg = p.groupby("tname").expected_goals.sum() / 38
    gk = (p[p.element_type == 1].sort_values("minutes", ascending=False)
          .groupby("tname").head(1).set_index("tname"))
    teams = {n: {"xg": round(float(xg[n]), 3),
                 "xga": round(float(gk.loc[n, "expected_goals_conceded_per_90"]), 3)}
             for n in xg.index}
    teams["_default_promoted"] = {"xg": a.promoted_xg, "xga": a.promoted_xga}

    # ---- player per-90 rates ---------------------------------------------
    players = {}
    for _, r in p.iterrows():
        if r.minutes < 180:
            continue
        n90 = r.minutes / 90
        players[int(r.code)] = {
            "min": int(r.minutes),
            "xg90": round(float(r.expected_goals_per_90 or 0), 3),
            "xa90": round(float(r.expected_assists_per_90 or 0), 3),
            "dc90": round(float(r.get("defensive_contribution_per_90") or 0), 3),
            "sv90": round(float(r.get("saves_per_90") or 0), 3),
            "b90": round(float(r.bonus) / n90, 3),
        }

    for name, obj in (("team_priors.json", teams), ("prev_season.json", players)):
        path = os.path.join(ROOT, "fpl", name)
        with open(path, "w") as f:
            json.dump(obj, f, indent=1)
        print(f"wrote {path}  ({len(obj)} entries)")


if __name__ == "__main__":
    main()

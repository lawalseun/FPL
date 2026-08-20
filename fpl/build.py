"""Entry point. Pulls data, runs the model, writes docs/data.json, sends alerts.

    python -m fpl.build                 # normal run
    python -m fpl.build --notify        # also send the weekly alert
    python -m fpl.build --wildcard      # ignore current squad, build from scratch
"""
from __future__ import annotations
import argparse, datetime as dt, json, os, sys
import pandas as pd

from . import data as D
from . import model as M
from . import solver as S

HORIZON = 5
OUT = os.path.join(os.path.dirname(__file__), "..", "docs", "data.json")
# Players the model underrates because their minutes history is misleading
# (new signings, returning loanees, promotions). Value = assumed season minutes.
OVERRIDES = json.loads(os.environ.get("FPL_MINUTE_OVERRIDES", "{}"))


def fixture_maps(fixtures, gws):
    """{gw: {team_id: [{'opp','home','kick'}]}} and {gw: {team_id: n}}"""
    fx, counts = {g: {} for g in gws}, {g: {} for g in gws}
    for f in fixtures:
        g = f.get("event")
        if g not in fx:
            continue
        for side, opp, home in ((f["team_h"], f["team_a"], True),
                                (f["team_a"], f["team_h"], False)):
            fx[g].setdefault(side, []).append(
                {"opp": opp, "home": home, "kick": f.get("kickoff_time")})
            counts[g][side] = counts[g].get(side, 0) + 1
    return fx, counts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry", type=int, default=int(os.environ.get("FPL_ENTRY_ID", 0) or 0))
    ap.add_argument("--budget", type=float, default=100.0)
    ap.add_argument("--wildcard", action="store_true")
    ap.add_argument("--force", default=os.environ.get("FPL_FORCE", ""),
                    help="comma-separated names the squad must contain")
    ap.add_argument("--ban", default=os.environ.get("FPL_BAN", ""),
                    help="comma-separated names to exclude")
    ap.add_argument("--single-gw", action="store_true",
                    help="optimise for this gameweek only instead of the 5-week horizon")
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--notify-window", type=float, default=30.0,
                    help="only alert when the deadline is this many hours away or closer")
    a = ap.parse_args(argv)

    boot = D.bootstrap()
    gw, deadline = D.current_gw(boot)
    players = pd.DataFrame(boot["elements"])
    teams = pd.DataFrame(boot["teams"])
    finished = [e for e in boot["events"] if e["finished"]]
    games_played = len(finished)
    season_minutes = max(90, games_played * 90)

    horizon_gws = list(range(gw, min(39, gw + HORIZON)))
    fx, counts = fixture_maps(D.fixtures(), horizon_gws)
    rat = M.team_ratings(players, teams, games_played)

    proj = M.project_horizon(players, rat, {g: fx[g] for g in horizon_gws},
                             season_minutes, OVERRIDES, games_played=games_played)
    proj = proj.rename(columns={"xp_next": "xp"})

    # ---- your squad -------------------------------------------------------
    mine, bank, free, chips_left = None, 0.0, 1, ["wildcard", "freehit", "3xc", "bboost"]
    if a.entry and not a.wildcard:
        try:
            t = D.my_team(a.entry)
            ids = [p["element"] for p in t["picks"]["picks"]]
            mine = proj[proj.id.isin(ids)].copy()
            bank = t["picks"]["entry_history"]["bank"] / 10
            used = {c["name"] for c in t["history"].get("chips", [])
                    if c["event"] > (19 if gw > 19 else 0)}
            chips_left = [c for c in chips_left if c not in used]
        except Exception as e:                                  # noqa: BLE001
            print(f"could not load entry {a.entry}: {e}", file=sys.stderr)

    force = [s for s in (a.force or "").split(",") if s.strip()]
    ban = [s for s in (a.ban or "").split(",") if s.strip()]
    optimal, status = S.build_squad(proj, a.budget, force=force, ban=ban,
                                    obj="xp" if a.single_gw else "xp_horizon")
    opt_xi, opt_bench = S.pick_xi(optimal)
    cap = opt_xi.nlargest(1, "xp")
    vice = opt_xi.nlargest(2, "xp").tail(1)

    payload = {
        "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "gw": gw,
        "deadline": deadline.isoformat() if deadline else None,
        "solver_status": status,
        "teams": {str(k): v for k, v in rat.items()},
        "optimal": {
            "xi": opt_xi.drop(columns=["fixtures"]).to_dict("records"),
            "bench": opt_bench.drop(columns=["fixtures"]).to_dict("records"),
            "captain": cap.name.iloc[0], "vice": vice.name.iloc[0],
            "cost": round(float(optimal.cost.sum()), 1),
            "xp": round(float(opt_xi.xp.sum() + cap.xp.iloc[0]), 2),
        },
        "top": {POS: proj[proj.pos == p].nlargest(15, "xp")
                .drop(columns=["fixtures"]).to_dict("records")
                for p, POS in S.POS.items()},
        "fixtures": {str(g): {rat[t]["short"]: [
            {"opp": rat[o["opp"]]["short"], "home": o["home"]} for o in v]
            for t, v in fx[g].items()} for g in horizon_gws},
        "flagged": proj[(proj.status != "a") & (proj.sel > 2)]
                   [["name", "team", "status", "news", "sel"]].to_dict("records"),
    }

    if mine is not None and len(mine) == 15:
        my_xi, my_bench = S.pick_xi(mine)
        moves = S.recommend_transfers(mine, proj, bank, free)
        xp_by_gw = {g: dict(zip(proj.id, proj[f"xp_gw{g}"] if f"xp_gw{g}" in proj else proj.xp))
                    for g in horizon_gws}
        payload["mine"] = {
            "xi": my_xi.drop(columns=["fixtures"]).to_dict("records"),
            "bench": my_bench.drop(columns=["fixtures"]).to_dict("records"),
            "captain": my_xi.nlargest(1, "xp").name.iloc[0],
            "vice": my_xi.nlargest(2, "xp").tail(1).name.iloc[0],
            "bank": bank, "free_transfers": free,
            "xp": round(float(my_xi.xp.sum()), 2),
            "transfers": moves,
            "chips": S.chip_advice(mine, xp_by_gw, counts, chips_left, gw),
        }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(payload, f, indent=1, default=str)
    print(f"wrote {OUT}  gw={gw}  deadline={deadline}  optimal xP={payload['optimal']['xp']}")

    from . import notify
    notify.write_ics(boot["events"])

    if a.notify:
        hrs = (deadline - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600 if deadline else 1e9
        if hrs <= a.notify_window:
            notify.send(payload)
        else:
            print(f"no alert: deadline is {hrs:.0f}h away (window {a.notify_window}h)")
    return payload


if __name__ == "__main__":
    main()

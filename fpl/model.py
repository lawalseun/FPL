"""Expected-points model.

Two layers:
  1. Team layer  - each club gets an attack rating (xG per match) and a defence
                   rating (xG conceded per match), shrunk toward the league mean
                   because one season is a small sample.
  2. Player layer - per-90 attacking rates x expected minutes x fixture
                   multiplier, plus clean sheets, saves, defensive
                   contributions and bonus.

Everything is expressed in FPL points so the solver can just add them up.
"""
from __future__ import annotations
import json, math, os
import numpy as np
import pandas as pd

SHRINK = 0.35          # regression of team ratings toward the league mean
HOME, AWAY = 1.12, 0.90
CS_CAP = 0.55          # no real fixture is a better than ~55% clean-sheet bet
GOAL_PTS = {1: 10, 2: 6, 3: 5, 4: 4}
CS_PTS = {1: 4, 2: 4, 3: 1, 4: 0}
DC_THRESH = {1: 99, 2: 10, 3: 12, 4: 12}   # defensive-contribution thresholds
PROMOTED_DEFAULT = (0.95, 1.75)            # (xG, xGA) per match for a newly promoted side


def _pois_ge(k: int, mu: float) -> float:
    if mu <= 0:
        return 0.0
    return 1 - sum(math.exp(-mu) * mu ** i / math.factorial(i) for i in range(k))


# --------------------------------------------------------------------------- #
# team ratings
# --------------------------------------------------------------------------- #
def load_priors(path: str | None = None) -> dict:
    """Last season's xG / xGA per club. Regenerate each summer with
    tools/make_priors.py; promoted clubs fall back to `_default_promoted`."""
    path = path or os.path.join(os.path.dirname(__file__), "team_priors.json")
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def team_ratings(players: pd.DataFrame, teams: pd.DataFrame, games_played: int,
                 priors: dict | None = None) -> dict:
    """xG / xGA per match per club, blending three sources in order of trust:
    this season's numbers once there are enough of them, then last season's,
    then FPL's own strength ratings."""
    priors = load_priors() if priors is None else priors
    pdef = priors.get("_default_promoted", {"xg": PROMOTED_DEFAULT[0], "xga": PROMOTED_DEFAULT[1]})

    # FPL's own strength numbers (~1000-1400); zeroed out during preseason
    sa = (teams.strength_attack_home + teams.strength_attack_away) / 2
    sd = (teams.strength_defence_home + teams.strength_defence_away) / 2
    has_strength = sa.max() > 0
    if has_strength:
        fpl_xg = 1.40 * (sa / sa.mean())
        fpl_xga = 1.40 * (2 - sd / sd.mean())
    w_fpl = 0.35 if (has_strength and priors) else (1.0 if has_strength else 0.0)

    rat = {}
    for i, t in teams.iterrows():
        pr = priors.get(t["name"], pdef)
        xg = (1 - w_fpl) * pr["xg"] + w_fpl * (float(fpl_xg[i]) if has_strength else 0)
        xga = (1 - w_fpl) * pr["xga"] + w_fpl * (float(fpl_xga[i]) if has_strength else 0)
        if games_played >= 4:                       # start trusting this season
            sq = players[players.team == t.id]
            obs_xg = sq.expected_goals.sum() / games_played
            gk = sq[sq.element_type == 1].nlargest(1, "minutes")
            obs_xga = float(gk.expected_goals_conceded_per_90.iloc[0]) if len(gk) else xga
            w = min(0.75, games_played / 20)
            xg = (1 - w) * xg + w * obs_xg
            xga = (1 - w) * xga + w * obs_xga
        rat[t.id] = {"name": t.name, "short": t.short_name, "xg": xg, "xga": xga}

    mxg = np.mean([r["xg"] for r in rat.values()])
    mxga = np.mean([r["xga"] for r in rat.values()])
    for r in rat.values():
        r["xg"] = (1 - SHRINK) * r["xg"] + SHRINK * mxg
        r["xga"] = (1 - SHRINK) * r["xga"] + SHRINK * mxga
    return rat


# --------------------------------------------------------------------------- #
# player projections
# --------------------------------------------------------------------------- #
def _availability(row) -> float:
    if row.status in ("i", "s", "u", "n"):
        return 0.0
    if row.status == "d":
        c = row.get("chance_of_playing_next_round")
        return (c / 100.0) if pd.notna(c) else 0.5
    return 1.0


def _minutes_floor(sel: float) -> float:
    """Ownership is a decent crowd-sourced prior on 'is he nailed?' — useful
    early in the season and for new signings with no minutes yet."""
    if sel >= 15: return 2600
    if sel >= 7:  return 2200
    if sel >= 3:  return 1500
    return 0


PREV_KEYS = ("min", "xg90", "xa90", "dc90", "sv90", "b90")


def load_prev_season(path: str | None = None) -> dict:
    """{player_code: {min, xg90, xa90, dc90, sv90, b90}} from last season.

    Without this every player looks identical in GW1, because FPL resets every
    per-90 and minutes column to zero when the season rolls over. Regenerate
    each summer with tools/make_priors.py."""
    path = path or os.path.join(os.path.dirname(__file__), "prev_season.json")
    try:
        with open(path) as f:
            return {int(k): v for k, v in json.load(f).items()}
    except FileNotFoundError:
        return {}


def minutes_share(row, season_minutes: int, games_played: int, prev: dict,
                  overrides: dict) -> float:
    """Fraction of available minutes we expect this player to be on the pitch for.
    Early in the season this leans on last season and on ownership; by GW8 it is
    almost entirely what he has actually played this season."""
    now = float(row.minutes) / season_minutes if season_minutes else 0.0
    before = prev.get(int(row.get("code", -1)), {}).get("min", 0) / 3420.0
    w = min(1.0, games_played / 8.0)
    share = w * now + (1 - w) * before

    floor = overrides.get(row.web_name)
    if floor is None:
        floor = _minutes_floor(float(row.selected_by_percent)) / 3420.0
    else:
        floor = floor / 3420.0
    if games_played < 6:                       # trust the crowd only while blind
        share = max(share, floor)
    return float(np.clip(share, 0, 1))


def project_gw(players: pd.DataFrame, rat: dict, fx_map: dict, season_minutes: int,
               overrides: dict | None = None, games_played: int = 0,
               prev_minutes: dict | None = None) -> pd.DataFrame:
    """fx_map: {team_id: [{'opp': id, 'home': bool, 'kick': iso}, ...]} for one GW.
    Returns one row per player with an `xp` column. Handles blanks (no fixture,
    xp = 0) and doubles (two fixtures, points add up)."""
    overrides = overrides or {}
    prev_minutes = load_prev_season() if prev_minutes is None else prev_minutes
    L_XGA = np.mean([r["xga"] for r in rat.values()])
    w_now = min(1.0, games_played / 8.0)     # how much to trust this season's rates
    rows = []

    for _, r in players.iterrows():
        games = fx_map.get(r.team, [])
        et = int(r.element_type)
        avail = _availability(r)

        share = minutes_share(r, season_minutes, games_played, prev_minutes, overrides)
        p60 = float(np.clip(share * 1.12, 0, 0.93)) * avail
        exp_min = p60 * 85
        m90 = exp_min / 90

        pv = prev_minutes.get(int(r.get("code", -1)), {})
        xg90 = float(r.expected_goals_per_90 or 0)
        xa90 = float(r.expected_assists_per_90 or 0)
        if r.minutes > 450:
            g90 = r.goals_scored / (r.minutes / 90)
            a90 = r.assists / (r.minutes / 90)
            b90 = r.bonus / (r.minutes / 90)
        else:
            g90, a90, b90 = xg90, xa90, 0.0
        gg90 = 0.65 * xg90 + 0.35 * g90
        aa90 = 0.65 * xa90 + 0.35 * a90
        dc90 = float(r.get("defensive_contribution_per_90") or 0)
        sv90 = float(r.get("saves_per_90") or 0)

        # blend this season's rates with last season's, weighted by sample size
        gg90 = w_now * gg90 + (1 - w_now) * pv.get("xg90", gg90)
        aa90 = w_now * aa90 + (1 - w_now) * pv.get("xa90", aa90)
        dc90 = w_now * dc90 + (1 - w_now) * pv.get("dc90", dc90)
        sv90 = w_now * sv90 + (1 - w_now) * pv.get("sv90", sv90)
        bonus90 = w_now * b90 + (1 - w_now) * pv.get("b90", b90)

        total, detail = 0.0, []
        for g in games:
            opp, home = g["opp"], g["home"]
            ha = HOME if home else AWAY
            lam_ag = rat[opp]["xg"] * (rat[r.team]["xga"] / L_XGA) * (AWAY if home else HOME)
            fix_att = (rat[opp]["xga"] / L_XGA) * ha

            pts = min(1.0, p60 / 0.93 * avail) + p60                     # appearance
            pts += gg90 * m90 * fix_att * GOAL_PTS[et]                   # goals
            pts += aa90 * m90 * fix_att * 3                              # assists
            cs = min(CS_CAP, math.exp(-lam_ag))
            pts += cs * p60 * CS_PTS[et]                                 # clean sheet
            if et in (1, 2):
                pts -= 0.5 * lam_ag * p60                                # goals conceded
            if et == 1:
                pts += (sv90 * m90 * (lam_ag / L_XGA)) / 3.0             # saves
            if et in (2, 3, 4) and dc90 > 0 and exp_min > 20:
                pts += 2.0 * _pois_ge(DC_THRESH[et], dc90 * m90) * p60   # def. contribution
            pts += bonus90 * m90 * 0.9                                   # bonus
            total += pts
            detail.append({"opp": rat[opp]["short"], "home": home,
                           "cs": round(cs, 3), "xp": round(pts, 2)})

        rows.append({
            "id": r.id, "name": r.web_name, "team": rat[r.team]["short"], "teamid": r.team,
            "pos": et, "cost": r.now_cost / 10, "sel": float(r.selected_by_percent),
            "status": r.status, "news": r.news or "", "form": float(r.form or 0),
            "p60": round(p60, 2), "fixtures": detail, "n_fix": len(games),
            "xp": round(total, 2),
        })
    return pd.DataFrame(rows)


def project_horizon(players, rat, fx_by_gw: dict, season_minutes: int, overrides=None,
                    decay: float = 0.85, games_played: int = 0,
                    prev_season: dict | None = None) -> pd.DataFrame:
    """Blend the next N gameweeks with geometric decay, so transfer decisions
    look ahead instead of chasing one good fixture."""
    prev_season = load_prev_season() if prev_season is None else prev_season
    out, w_total = None, 0.0
    for i, (gw, fx_map) in enumerate(sorted(fx_by_gw.items())):
        d = project_gw(players, rat, fx_map, season_minutes, overrides,
                       games_played, prev_season)
        w = decay ** i
        w_total += w
        col = d[["id", "xp"]].rename(columns={"xp": f"xp_gw{gw}"})
        if out is None:
            out = d.rename(columns={"xp": "xp_next"})
            out["xp_horizon"] = out.xp_next * w
        else:
            out = out.merge(col, on="id")
            out["xp_horizon"] += out[f"xp_gw{gw}"] * w
    out["xp_horizon"] = (out.xp_horizon / w_total).round(2)
    return out

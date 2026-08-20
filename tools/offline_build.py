"""Run the whole pipeline without touching the network.

Useful for testing, and for any environment where fantasy.premierleague.com is
blocked. It reconstructs bootstrap-static / fixtures from the mirrored CSVs in
tools/sample/ and then calls the normal build.

    python tools/offline_build.py
"""
import json, os, sys
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
SAMPLE = os.path.join(ROOT, "tools", "sample")

from fpl import data as D            # noqa: E402

players = pd.read_csv(os.path.join(SAMPLE, "players_raw.csv"))
teams = pd.read_csv(os.path.join(SAMPLE, "teams.csv"))
fixtures = pd.read_csv(os.path.join(SAMPLE, "fixtures.csv"))

NUM = ["expected_goals", "expected_assists", "expected_goals_per_90", "expected_assists_per_90",
       "expected_goals_conceded_per_90", "defensive_contribution_per_90", "saves_per_90",
       "minutes", "goals_scored", "assists", "bonus", "now_cost", "selected_by_percent",
       "form", "chance_of_playing_next_round"]
for c in NUM:
    if c in players:
        players[c] = pd.to_numeric(players[c], errors="coerce").fillna(0)
players["news"] = players.news.fillna("")

events = [{"id": g, "name": f"Gameweek {g}", "finished": False,
           "deadline_time": (fixtures[fixtures.event == g].kickoff_time.min())}
          for g in sorted(fixtures.event.dropna().unique().astype(int))]

BOOT = {"elements": players.to_dict("records"),
        "teams": teams.to_dict("records"),
        "events": events}
FIX = fixtures.where(pd.notna(fixtures), None).to_dict("records")

D.bootstrap = lambda: BOOT
D.fixtures = lambda event=None: [f for f in FIX if event is None or f["event"] == event]
import datetime as _dt
_gw = int(min(e["id"] for e in events))
_k = fixtures[fixtures.event == _gw].kickoff_time.min()
_dl = _dt.datetime.fromisoformat(str(_k).replace("Z", "+00:00")) - _dt.timedelta(minutes=90)
D.current_gw = lambda boot=None: (_gw, _dl)

from fpl import build                # noqa: E402
build.OVERRIDES = json.loads(os.environ.get("FPL_MINUTE_OVERRIDES", "{}"))
build.main(["--budget", os.environ.get("FPL_BUDGET", "100.0")] + sys.argv[1:])

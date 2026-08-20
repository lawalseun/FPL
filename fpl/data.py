"""Pull everything we need from the public Fantasy Premier League API.

No key, no auth, no scraping. These endpoints are the same ones the official
site calls, so they are always in sync with the game.
"""
from __future__ import annotations
import json, os, time, datetime as dt
import requests

BASE = "https://fantasy.premierleague.com/api"
UA = {"User-Agent": "Mozilla/5.0 (fpl-dashboard)"}
CACHE = os.path.join(os.path.dirname(__file__), "..", ".cache")


def _get(path: str, ttl: int = 900):
    """GET with a small on-disk cache so local runs don't hammer the API."""
    os.makedirs(CACHE, exist_ok=True)
    key = os.path.join(CACHE, path.strip("/").replace("/", "_").replace("?", "_") + ".json")
    if os.path.exists(key) and time.time() - os.path.getmtime(key) < ttl:
        with open(key) as f:
            return json.load(f)
    r = requests.get(f"{BASE}/{path}", headers=UA, timeout=30)
    r.raise_for_status()
    data = r.json()
    with open(key, "w") as f:
        json.dump(data, f)
    return data


def bootstrap():
    """Players, teams, gameweeks, prices, ownership, injury news."""
    return _get("bootstrap-static/")


def fixtures(event: int | None = None):
    return _get("fixtures/" + (f"?event={event}" if event else ""))


def player_history(pid: int):
    """Per-match history for one player, plus their previous seasons."""
    return _get(f"element-summary/{pid}/", ttl=3600)


def my_team(entry_id: int):
    """Your public squad for the most recent finished gameweek + your chips."""
    hist = _get(f"entry/{entry_id}/history/", ttl=600)
    ev = _get(f"entry/{entry_id}/", ttl=600)
    gw = ev.get("current_event")
    picks = _get(f"entry/{entry_id}/event/{gw}/picks/", ttl=600) if gw else None
    return {"entry": ev, "history": hist, "picks": picks}


def current_gw(boot=None):
    """(next_gw_id, deadline_utc) — the gameweek you are picking for."""
    boot = boot or bootstrap()
    now = dt.datetime.now(dt.timezone.utc)
    for e in boot["events"]:
        d = dt.datetime.fromisoformat(e["deadline_time"].replace("Z", "+00:00"))
        if d > now:
            return e["id"], d
    return boot["events"][-1]["id"], None

# FPL Weekly

An auto-updating Fantasy Premier League dashboard. A scheduled job pulls the
official FPL API twice a day, projects every player's points for the next five
gameweeks, solves for the best legal squad, ranks your transfer options, tells
you when to play each chip, and messages you before the deadline.

Runs entirely on free infrastructure: GitHub Actions for the schedule, GitHub
Pages for the page. No server, no database, no cost.

---

## What it does

| Piece | What it answers |
|---|---|
| **Projections** | How many points is each player likely to score this week? |
| **Squad solver** | What is the best legal 15 at £100m — and the best XI out of *your* 15? |
| **Transfer ranker** | Is this move worth it over the next five gameweeks, after the −4 hit? |
| **Chip timer** | When should Bench Boost / Triple Captain / Free Hit / Wildcard go? |
| **Fixture ticker** | Which clubs have the run of games worth buying into? |
| **Alerts** | Telegram + email before every deadline, plus a calendar you subscribe to once |

## How the projection works

Two layers, both in FPL points so the solver can just add them up.

**Team layer.** Every club gets an attack rating (expected goals per match) and a
defence rating (expected goals conceded per match). These blend three sources by
how much each deserves trust: this season's numbers once there are at least four
matches of them, last season's numbers from `fpl/team_priors.json`, and FPL's own
strength ratings. The result is shrunk 35% toward the league average, because one
season is a small sample and extreme ratings regress. Newly promoted clubs get a
default rating until they have played enough matches to earn their own.

For a fixture, expected goals for a club = its attack rating × the opponent's
defence rating relative to the league × a home/away factor (1.12 / 0.90). Clean
sheet probability is the Poisson zero of the *opponent's* expected goals, capped
at 55% — no real fixture is a better bet than that.

**Player layer.** Expected minutes × per-90 rates × the fixture multiplier, plus:

- goals (10/6/5/4 points by position) from a 65/35 blend of expected goals and actual goals
- assists (3 points) from the same blend of expected and actual assists
- clean sheets (4 for a keeper or defender, 1 for a midfielder)
- goals conceded (−1 per 2 for keepers and defenders)
- saves (1 per 3, scaled by how much shooting the opponent is likely to do)
- defensive contribution points, as the Poisson probability of clearing the
  10-action threshold for defenders or 12 for everyone else
- bonus, from last season's bonus per 90

**Minutes are the part most models get wrong.** A player who does not start is
worth zero no matter how good he is. The model estimates a start probability from
minutes played, and early in the season — when the API says everyone has played
zero — it falls back to last season's minutes plus ownership as a crowd-sourced
prior on whether the community thinks he is nailed. By GW8 it is running almost
entirely on this season's actual minutes.

Injured and suspended players are zeroed out from the API's own status flags;
doubtful players are scaled by their published chance of playing.

## Quick start

```bash
git clone https://github.com/<you>/fpl-weekly && cd fpl-weekly
pip install -r requirements.txt

python tools/make_priors.py --season 2025-26     # once each summer
python -m fpl.build --entry <YOUR_TEAM_ID>       # writes docs/data.json

cd docs && python -m http.server 8000            # open http://localhost:8000
```

Your **team ID** is the number in the URL when you view your points on the FPL
site: `fantasy.premierleague.com/entry/`**`1234567`**`/event/1`.

## Putting it online

1. Push the repo to GitHub (it can be private — Pages still works on free accounts
   for public repos; make it public if you want the free Pages tier).
2. **Settings → Pages → Source: GitHub Actions.**
3. **Settings → Secrets and variables → Actions**, add what you want to use:

   | Name | Kind | What it is |
   |---|---|---|
   | `FPL_ENTRY_ID` | secret | your FPL team ID |
   | `TELEGRAM_TOKEN` | secret | from [@BotFather](https://t.me/BotFather) — send `/newbot` |
   | `TELEGRAM_CHAT_ID` | secret | from [@userinfobot](https://t.me/userinfobot) — send it any message |
   | `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASS` / `MAIL_TO` | secrets | for email; with Gmail use an [App Password](https://myaccount.google.com/apppasswords), never your real one |
   | `DASHBOARD_URL` | variable | your Pages URL, so alerts link back to it |
   | `FPL_MINUTE_OVERRIDES` | variable | JSON, e.g. `{"Isak": 2700}` — see below |

4. The workflow runs at 07:00 and 17:00 UTC daily. It only sends an alert when a
   deadline is within 30 hours, so you get one message per gameweek, not fourteen.
5. **Calendar:** subscribe to `https://<your-pages-url>/fpl-deadlines.ics` in
   Google Calendar (Other calendars → From URL). Every deadline for the season
   appears with a 24-hour reminder, and it stays in sync automatically.

## Minute overrides

The one thing the model cannot know is that a player who barely featured last
season is now a guaranteed starter — a new signing, a returning loanee, someone
whose season was wrecked by injury. `FPL_MINUTE_OVERRIDES` is how you tell it:

```json
{"Isak": 2700, "Mosquera": 2500, "Rashford": 1700}
```

The value is the minutes you think he *would* have played across a full season
(3420 is every minute of every match). Set it once, remove it once the player has
real minutes on the board — after about GW6 the override is ignored anyway.

## Layout

```
fpl/
  data.py        FPL API client with a small on-disk cache
  model.py       team ratings + the expected-points model
  solver.py      squad ILP, transfer search, chip timing
  notify.py      Telegram, email, .ics calendar
  build.py       entry point — writes docs/data.json
  team_priors.json / prev_season.json    last season, carried forward
tools/
  make_priors.py   regenerate the two prior files each summer
  offline_build.py run the whole thing from CSVs, no network
docs/
  index.html     the dashboard (single file, no build step)
  data.json      generated
.github/workflows/update.yml
```

## Things worth knowing

- **The FPL API is public and unauthenticated** for everything used here. There is
  no rate limit published; the cache in `data.py` keeps local runs polite.
- **Only your finished-gameweek squad is public.** The API cannot see the team you
  are currently editing, so between the deadline and kickoff the dashboard shows
  your last confirmed squad.
- **One free transfer per gameweek, and you can bank up to five.** The transfer
  ranker already charges 4 points for every move beyond your free ones — if the
  best option shows a negative net, roll it.
- **Substitutions are not transfers.** Your bench order is free to change every
  week and costs nothing; auto-subs bring a bench player on if a starter records
  zero minutes. The transfer limit only applies to buying and selling.
- **Chips come in two sets** — one for GW1–19, one for GW20–38. The first set
  expires at the GW19 deadline. An unused chip is a wasted chip.
- **The model is a prior, not an oracle.** Check the press conferences on Friday.
  A projection cannot know a manager just said someone is being rested.

#!/usr/bin/env python3
"""mlb.py — tiny CLI over the free MLB Stats API (statsapi.mlb.com, no key).

Homer's data source for schedules, scores, standings, the Dodgers, and
Shohei Ohtani. Output is compact plain text meant to be read by an LLM and
spoken aloud, not pretty-printed for humans.

Commands:
    scores [YYYY-MM-DD|today|yesterday]   all MLB games for a date (default today)
    schedule [team-name] [days]           a team's upcoming games incl. probable
                                          starters (default Dodgers, 5 days)
    dodgers                               Dodgers snapshot: last 3 results + next 3 games + NL West position
    ohtani                                Shohei Ohtani season stats (hitting AND pitching) + last games
    standings [al|nl|all]                 division standings (default nl)
    player <name>                         look up any player's season stats by name
    game <team-name> [YYYY-MM-DD|today|yesterday]
                                          recap of that team's game: score, pitching
                                          decisions, homers, top performers (default:
                                          most recent game in the last 3 days)

No dependencies beyond the Python standard library.
"""
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta

API = "https://statsapi.mlb.com/api/v1"
DODGERS_ID = 119
OHTANI_ID = 660271
SEASON = date.today().year


def get(path: str, **params) -> dict:
    qs = urllib.parse.urlencode(params, safe="(),[]=")
    with urllib.request.urlopen(f"{API}/{path}?{qs}", timeout=15) as r:
        return json.load(r)


def _fmt_game(g: dict) -> str:
    away, home = g["teams"]["away"], g["teams"]["home"]
    st = g["status"]["detailedState"]
    when = g.get("gameDate", "")
    line = f'{away["team"]["name"]}'
    if "score" in away:
        line += f' {away["score"]}'
    line += f' @ {home["team"]["name"]}'
    if "score" in home:
        line += f' {home["score"]}'
    if st in ("Scheduled", "Pre-Game", "Warmup") and when:
        # UTC ISO timestamp -> keep date + HH:MM UTC; good enough for voice
        line += f' — {when[:16].replace("T", " ")} UTC'
        pa = (away.get("probablePitcher") or {}).get("fullName")
        ph = (home.get("probablePitcher") or {}).get("fullName")
        if pa or ph:
            line += f' — probable starters: {pa or "TBD"} vs {ph or "TBD"}'
    else:
        line += f" — {st}"
        ls = g.get("linescore") or {}
        if st == "In Progress" and ls:
            line += f' ({ls.get("inningState", "")} {ls.get("currentInningOrdinal", "")})'
    return line


def _games(start: str, end: str, team_id: int | None = None) -> list[dict]:
    params = dict(sportId=1, startDate=start, endDate=end,
                  hydrate="linescore,team,probablePitcher")
    if team_id:
        params["teamId"] = team_id
    return [g for d in get("schedule", **params).get("dates", []) for g in d["games"]]


def _team_id(name: str) -> tuple[int, str]:
    name = name.lower()
    for t in get("teams", sportId=1, season=SEASON)["teams"]:
        if name in t["name"].lower() or name in t.get("teamName", "").lower():
            return t["id"], t["name"]
    sys.exit(f"no MLB team matching '{name}'")


def cmd_scores(argv: list[str]) -> None:
    day = argv[0] if argv else "today"
    if day == "today":
        day = date.today().isoformat()
    elif day == "yesterday":
        day = (date.today() - timedelta(days=1)).isoformat()
    games = _games(day, day)
    print(f"MLB games for {day}:" if games else f"No MLB games on {day}.")
    for g in games:
        print(" ", _fmt_game(g))


def cmd_schedule(argv: list[str]) -> None:
    days = int(argv[1]) if len(argv) > 1 else 5
    tid, tname = _team_id(argv[0]) if argv else (DODGERS_ID, "Los Angeles Dodgers")
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=days)).isoformat()
    games = _games(start, end, tid)
    print(f"{tname} — next {days} days:" if games else f"No {tname} games in the next {days} days.")
    for g in games:
        print(" ", _fmt_game(g))


def cmd_dodgers(_argv: list[str]) -> None:
    today = date.today()
    past = _games((today - timedelta(days=7)).isoformat(), today.isoformat(), DODGERS_ID)
    finals = [g for g in past if g["status"]["detailedState"] == "Final"]
    print("Dodgers — recent results (oldest first; the LAST line is the most recent game):")
    for g in finals[-3:]:
        print(" ", _fmt_game(g), f'({g["gameDate"][:10]})')
    upcoming = [g for g in _games(today.isoformat(), (today + timedelta(days=7)).isoformat(), DODGERS_ID)
                if g["status"]["detailedState"] != "Final"]
    print("Dodgers — upcoming:")
    for g in upcoming[:3]:
        print(" ", _fmt_game(g))
    for rec in get("standings", leagueId=104, season=SEASON)["records"]:
        for t in rec["teamRecords"]:
            if t["team"]["id"] == DODGERS_ID:
                print(f'Standings: {t["wins"]}-{t["losses"]}, '
                      f'{"lead" if t["gamesBack"] == "-" else t["gamesBack"] + " GB"} in the NL West '
                      f'(seed {t.get("divisionRank", "?")}).')


def _season_stats(person_id: int, label: str) -> None:
    d = get(f"people/{person_id}",
            hydrate=f"currentTeam,stats(group=[hitting,pitching],type=[season],season={SEASON})")
    p = d["people"][0]
    team = (p.get("currentTeam") or {}).get("name", "")
    print(f'{p["fullName"]}{" — " + team if team else ""} ({SEASON} season):')
    for s in p.get("stats", []):
        grp = s["group"]["displayName"]
        for sp in s.get("splits", [])[:1]:
            st = sp["stat"]
            if grp == "hitting":
                print(f'  Hitting: {st.get("avg")} avg, {st.get("homeRuns")} HR, '
                      f'{st.get("rbi")} RBI, {st.get("ops")} OPS, '
                      f'{st.get("stolenBases")} SB in {st.get("gamesPlayed")} games')
            elif grp == "pitching":
                print(f'  Pitching: {st.get("wins")}-{st.get("losses")}, {st.get("era")} ERA, '
                      f'{st.get("strikeOuts")} K in {st.get("inningsPitched")} IP '
                      f'({st.get("gamesStarted")} starts)')
    if not p.get("stats"):
        print(f"  (no {SEASON} stats found for {label})")


def cmd_ohtani(_argv: list[str]) -> None:
    _season_stats(OHTANI_ID, "Ohtani")
    # last few hitting game logs so Homer can talk about "lately"
    logs = get(f"people/{OHTANI_ID}/stats", stats="gameLog", group="hitting", season=SEASON)
    splits = (logs.get("stats") or [{}])[0].get("splits", [])
    if splits:
        print("  Recent games (hitting):")
        for sp in splits[-3:]:
            st = sp["stat"]
            print(f'    {sp.get("date")}: {st.get("hits")}-for-{st.get("atBats")}, '
                  f'{st.get("homeRuns")} HR, {st.get("rbi")} RBI vs {sp.get("opponent", {}).get("name", "?")}')


def cmd_standings(argv: list[str]) -> None:
    which = (argv[0] if argv else "nl").lower()
    leagues = {"al": "103", "nl": "104", "all": "103,104"}.get(which, "104")
    for rec in get("standings", leagueId=leagues, season=SEASON)["records"]:
        div = get(f'divisions/{rec["division"]["id"]}')["divisions"][0]["name"]
        print(f"{div}:")
        for t in rec["teamRecords"]:
            gb = "lead" if t["gamesBack"] == "-" else t["gamesBack"] + " GB"
            print(f'  {t["team"]["name"]}: {t["wins"]}-{t["losses"]} ({gb})')


def cmd_player(argv: list[str]) -> None:
    if not argv:
        sys.exit("usage: mlb.py player <name>")
    name = " ".join(argv)
    hits = get("people/search", names=name).get("people", [])
    if not hits:
        sys.exit(f"no player found for '{name}'")
    _season_stats(hits[0]["id"], name)


def _ip_outs(ip: str) -> int:
    # "6.2" innings pitched -> 20 outs, for ranking pitchers by workload
    whole, _, frac = str(ip or "0").partition(".")
    return int(whole or 0) * 3 + int(frac or 0)


def cmd_game(argv: list[str]) -> None:
    if not argv:
        sys.exit("usage: mlb.py game <team-name> [YYYY-MM-DD|today|yesterday]")
    # LLM callers invent flag spellings like --date/-d; drop bare flag tokens
    argv = [a for a in argv if not a.startswith("-")]
    day = None
    if len(argv) > 1 and (argv[-1] in ("today", "yesterday") or argv[-1][:4].isdigit()):
        day = argv[-1]
        argv = argv[:-1]
    tid, tname = _team_id(" ".join(argv))
    today = date.today()
    if day == "today":
        day = today.isoformat()
    elif day == "yesterday":
        day = (today - timedelta(days=1)).isoformat()
    start = day or (today - timedelta(days=3)).isoformat()
    end = day or today.isoformat()
    params = dict(sportId=1, startDate=start, endDate=end, teamId=tid,
                  hydrate="linescore,team,decisions,probablePitcher")
    games = [g for d in get("schedule", **params).get("dates", []) for g in d["games"]]
    # Most recent game that has actually produced action; else the next scheduled one.
    played = [g for g in games
              if g["status"]["detailedState"] not in ("Scheduled", "Pre-Game", "Warmup", "Postponed")]
    if not played:
        if games:
            print(f"{tname} — no game played yet in that window; next up:")
            print(" ", _fmt_game(games[0]))
        else:
            print(f"No {tname} games between {start} and {end}.")
        return
    g = played[-1]
    print(f'{_fmt_game(g)} ({g["gameDate"][:10]})')
    ls = (g.get("linescore") or {}).get("teams", {})
    if ls:
        parts = []
        for side in ("away", "home"):
            t = ls.get(side, {})
            abbr = g["teams"][side]["team"].get("abbreviation") or g["teams"][side]["team"]["name"]
            parts.append(f'{abbr} {t.get("runs", 0)} runs {t.get("hits", 0)} hits {t.get("errors", 0)} errors')
        print("  Line:", "; ".join(parts))
    dec = g.get("decisions") or {}
    if dec:
        bits = [f'{label}: {p["fullName"]}' for label, p in
                (("W", dec.get("winner")), ("L", dec.get("loser")), ("S", dec.get("save"))) if p]
        print("  Decisions:", ", ".join(bits))
    box = get(f'game/{g["gamePk"]}/boxscore')
    for side in ("away", "home"):
        t = box["teams"][side]
        team_name = t["team"].get("teamName") or t["team"]["name"]
        hitters, pitchers = [], []
        for p in t.get("players", {}).values():
            b = (p.get("stats") or {}).get("batting") or {}
            if b.get("atBats") or b.get("hits"):
                hr, h, rbi, ab = (b.get("homeRuns", 0), b.get("hits", 0),
                                  b.get("rbi", 0), b.get("atBats", 0))
                if hr or h >= 2 or rbi >= 2:
                    desc = f'{p["person"]["fullName"]} {h}-for-{ab}'
                    if hr:
                        desc += f", {hr} HR"
                    if rbi:
                        desc += f", {rbi} RBI"
                    hitters.append((hr, rbi, h, desc))
            pi = (p.get("stats") or {}).get("pitching") or {}
            if pi.get("inningsPitched"):
                pitchers.append((_ip_outs(pi["inningsPitched"]),
                                 f'{p["person"]["fullName"]} {pi["inningsPitched"]} IP, '
                                 f'{pi.get("earnedRuns", 0)} ER, {pi.get("strikeOuts", 0)} K'))
        hitters.sort(reverse=True)
        pitchers.sort(reverse=True)
        if hitters:
            print(f'  {team_name} top hitters:', "; ".join(d for *_, d in hitters[:3]))
        if pitchers:
            print(f'  {team_name} longest outing:', pitchers[0][1])


COMMANDS = {
    "scores": cmd_scores,
    "schedule": cmd_schedule,
    "dodgers": cmd_dodgers,
    "ohtani": cmd_ohtani,
    "standings": cmd_standings,
    "player": cmd_player,
    "game": cmd_game,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    try:
        COMMANDS[sys.argv[1]](sys.argv[2:])
    except Exception as e:  # keep failures one-line so the agent can speak them
        sys.exit(f"mlb.py error: {e}")

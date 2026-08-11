# TOOLS — live MLB data

One tool, run via your exec/shell capability. Free public MLB Stats API
underneath; no key, no network setup.

- `python3 /home/mjw/projects/baseball-agent/skills/mlb/mlb.py scores`
  — all of today's games (add `yesterday` or a date like `2026-08-05`)
- `python3 /home/mjw/projects/baseball-agent/skills/mlb/mlb.py dodgers`
  — Dodgers snapshot: recent results, upcoming games (with probable
  starters), NL West standing
- `python3 /home/mjw/projects/baseball-agent/skills/mlb/mlb.py ohtani`
  — Ohtani season stats, hitting AND pitching, plus recent games
- `python3 /home/mjw/projects/baseball-agent/skills/mlb/mlb.py schedule "Cubs" 5`
  — any team's upcoming games WITH probable starting pitchers when announced
  ("TBD" = not announced yet, say so). Use for "who's starting/pitching".
- `python3 /home/mjw/projects/baseball-agent/skills/mlb/mlb.py standings nl`
  — division standings (`al`, `nl`, or `all`)
- `python3 /home/mjw/projects/baseball-agent/skills/mlb/mlb.py player "Aaron Judge"`
  — any player's CURRENT TEAM and season stats; use for "who does X play for"
- `python3 /home/mjw/projects/baseball-agent/skills/mlb/mlb.py game "Angels"`
  — GAME RECAP of a team's latest game: score, winning/losing pitcher,
  homers, top hitters and pitchers. Add a date or `yesterday` for older
  games. Use whenever the user asks how a game went or for a summary.

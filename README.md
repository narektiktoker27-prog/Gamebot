# 🧟 ՎԵՐՋԻՆ ԳԻՇԵՐԸ

Multiplayer social-deduction Telegram bot, built with `python-telegram-bot` (v22, async) + SQLAlchemy.
All files are flat in this folder on purpose — no subfolders — so you can drop them straight
into the root of a GitHub repository.

## ⚠️ First: about your bot token

Your BotFather token was pasted in plain text earlier in this conversation and was also hardcoded
in the previous version of `config.py`. **Treat that token as compromised and revoke it now**,
regardless of what you plan to do later:

1. Open @BotFather in Telegram → `/mybots` → select your bot → **API Token** → **Revoke current token**.
2. BotFather gives you a brand-new token immediately.
3. Never put the token in any `.py` file. This project reads it only from the `BOT_TOKEN`
   environment variable (see `config.py`) — if it's missing, the bot refuses to start rather than
   silently falling back to a hardcoded value.

## 1. BotFather
`/newbot` in @BotFather → copy the token it gives you (the new one, after you revoke the old one).

## 2. GitHub
Put every file from this folder directly in the root of a new repository (no nested project folder).

## 3. Railway
1. Railway → New Project → Deploy from GitHub Repo → pick the repo.
2. Add a PostgreSQL service to the project.
3. In your bot service's **Variables**, add:
   - `BOT_TOKEN` = the token from BotFather
   - `DATABASE_URL` = the connection string Railway generated for the PostgreSQL service
     (Railway usually exposes this as a reference variable you can pick from a dropdown)
4. Deploy. Railway builds from the included `Dockerfile`.

## 4. Add the bot to your Telegram group
- Add the bot to the group as a member (admin rights aren't required, but make sure
  **Privacy Mode** is off in @BotFather → Bot Settings, or the bot won't see `/commands`
  typed without mentioning it).
- Every player must open a private chat with the bot and press **Start** at least once
  *before* the game starts — Telegram doesn't let bots message a user first, so this is how
  secret roles, night actions, and vote prompts actually reach people privately.

## 5. First game
In the group:
```
/zombie      → creates the lobby
/join        → each player joins (or tap ➕ Միանալ)
/startgame   → creator starts once 6–20 players joined (or tap ▶️ Սկսել)
```

## Commands
`/zombie` `/join` `/leave` `/startgame` `/status` `/role` `/goal` `/inventory` `/publish` `/help`

`/role`, `/goal`, `/inventory` are typed in the bot's **private chat**. `/publish` is used in the
**group**, only by the journalist, only during the discussion phase, once per game.

## What's actually implemented
- Lobby with join/leave/start (both commands and inline buttons work).
- Roles: zombie(s) (scales with player count), doctor, investigator, guard, radio operator,
  scientist, journalist, double agent, survivor.
- Private role + personal goal delivery at game start; private night-action prompts every round.
- Zombie has 4 night actions (infect / follow / disrupt / create false trail) — the last three
  share a cooldown so the zombie can't spam them every round.
- Investigator results are deliberately ambiguous (not a flat yes/no), and can be blocked by a
  zombie's "disrupt" action or made vaguer/more precise by the round's random event.
- Infection is staged (Healthy → Exposed → Infected → Critical); reaching Critical removes the
  player. Doctor protection can also partially heal under some events.
- 50 random events per round, several of which actually change mechanics that round (not just
  flavor text): investigation gets clearer/vaguer, doctor healing, extra/misleading evidence,
  rescue countdown speeding up or radio going dark, small morale score shifts.
- Evidence system (some pieces flagged as misleading), a journalist `/publish` that reveals one
  piece to the whole group.
- Voting: classic mode reveals the eliminated player's role, mystery mode doesn't; ties result in
  no elimination; wrongly eliminating an innocent has a chance of a follow-up consequence.
- Win conditions: zombies all eliminated → humans win; zombies overrun the survivors → zombie
  wins; a rescue countdown (`RESCUE_ROUNDS` in `config.py`) forces an ending either way if the
  game runs long.
- Full cinematic ending with role reveal, a timeline of the game's history, a rough "sharpest
  suspicion" / "most convincing bluff" / "biggest mistake" callout, and a score leaderboard.
- SQLite by default (`DATABASE_URL` unset), PostgreSQL in production; game/phase state is
  persisted, so a bot restart mid-game resumes the correct phase and timers instead of losing
  the game (see `recover_games` in `bot.py`).

## Known limitations (being upfront about scope)
- A Telegram user can only be tracked as "the" active player for private commands across *one*
  game at a time; being an active player in two different groups' games simultaneously is not
  supported.
- The opening "🧟 Ստեղծել խաղ / 📖 Կանոններ" splash screen from the original spec was simplified —
  `/zombie` creates the lobby directly instead of showing an intro screen first.
- The DB session usage is synchronous SQLAlchemy inside async handlers — fine at the scale this
  bot is built for (a handful of concurrent group games), but not tuned for heavy load.

## Tests
`python -m unittest tests.py` covers role-scaling and win-condition logic without needing a live
bot token or database connection.

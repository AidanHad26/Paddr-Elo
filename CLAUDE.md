# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the App

Requires a PostgreSQL database. Set `DATABASE_URL` before starting:

```bash
export DATABASE_URL=postgresql://user:pass@host/dbname
export ADMIN_USERNAME=admin
export ADMIN_PASSWORD=yourpassword
python app.py
```

Runs on `http://localhost:5050`. The database schema is auto-initialized on startup (idempotent).

## Deploying to Render

1. Push to GitHub and connect the repo to Render.
2. Render reads `render.yaml` and creates a web service + PostgreSQL database.
3. Set `ADMIN_USERNAME` and `ADMIN_PASSWORD` env vars in the Render dashboard.
4. The first admin account is seeded from those env vars on startup.

## Architecture

Single-file Flask app with PostgreSQL backend and vanilla JS frontend.

- **app.py** — All Flask routes. DB initialized at module level so gunicorn picks it up.
- **database.py** — All psycopg2 queries. Uses a `get_db()` context manager (commit/rollback/close). Returns `RealDictRow` objects (dict-style, compatible with Jinja2 dot notation).
- **elo.py** — Elo calculation; K-factor scaled by `cups_left` (1–5).
- **templates/** — Jinja2 HTML templates.
- **static/** — CSS (dark gaming theme) and JS (player dropdown loading, form validation).

## Auth / Roles

- **Flask-Login** handles sessions.
- Two roles: `is_admin=True` (admin) and `is_admin=False` (regular user).
- Regular users: leaderboard, record game, history.
- Admin: all of the above + player management (`/players`), user management (`/admin/users`), delete games from history.
- The `admin_required` decorator in `app.py` guards admin routes.
- Admin creates all user accounts — there is no self-registration.

## Database Schema

Four tables in PostgreSQL:

- `users` — id, username (unique), password_hash, is_admin, created_at
- `players` — id, name (unique), elo (default 1000), wins, losses, created_at
- `games` — id, team1/team2 player IDs (2v2), winning_team (1 or 2), cups_left (1–5), played_at
- `elo_history` — per-game Elo deltas per player (elo_before, elo_after, delta)

## Elo System

`cups_left` is cups remaining for the losing team when the game ends. Higher = more dominant win = larger K-factor. See `elo.py:k_adjusted()`.

## Correcting Bad Games

Admin can delete a game from the History page. `database.delete_game()` reverses the stored `elo_history` deltas and decrements wins/losses before deleting the records. Admin can also manually adjust a player's Elo via the Edit Player page.

## No Test Suite

There are no automated tests in this project.

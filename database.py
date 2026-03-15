import os
import psycopg2
import psycopg2.extras
from contextlib import contextmanager
from werkzeug.security import generate_password_hash
import elo as elo_module

DATABASE_URL = os.environ.get("DATABASE_URL", "")
# Render uses postgres:// but psycopg2 requires postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)


@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id            SERIAL PRIMARY KEY,
                    username      TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    is_admin      BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    id         SERIAL PRIMARY KEY,
                    name       TEXT NOT NULL UNIQUE,
                    elo        REAL NOT NULL DEFAULT 1000.0,
                    wins       INTEGER NOT NULL DEFAULT 0,
                    losses     INTEGER NOT NULL DEFAULT 0,
                    lapped     INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS games (
                    id               SERIAL PRIMARY KEY,
                    team1_player1_id INTEGER NOT NULL REFERENCES players(id),
                    team1_player2_id INTEGER NOT NULL REFERENCES players(id),
                    team2_player1_id INTEGER NOT NULL REFERENCES players(id),
                    team2_player2_id INTEGER NOT NULL REFERENCES players(id),
                    winning_team     INTEGER NOT NULL CHECK (winning_team IN (1, 2)),
                    cups_left        REAL NOT NULL CHECK (cups_left >= 0.5 AND cups_left <= 5),
                    played_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS elo_history (
                    id         SERIAL PRIMARY KEY,
                    game_id    INTEGER NOT NULL REFERENCES games(id),
                    player_id  INTEGER NOT NULL REFERENCES players(id),
                    elo_before REAL NOT NULL,
                    elo_after  REAL NOT NULL,
                    delta      REAL NOT NULL
                )
            """)

    # Migrations for existing databases
    with get_db() as conn:
        with conn.cursor() as cur:
            # Add lapped column if missing
            cur.execute("""
                ALTER TABLE players ADD COLUMN IF NOT EXISTS lapped INTEGER NOT NULL DEFAULT 0
            """)
            # Upgrade cups_left from INTEGER to REAL if needed
            cur.execute("""
                SELECT data_type FROM information_schema.columns
                WHERE table_name = 'games' AND column_name = 'cups_left'
            """)
            row = cur.fetchone()
            if row and row["data_type"] == "integer":
                cur.execute("ALTER TABLE games ALTER COLUMN cups_left TYPE REAL")
                cur.execute("ALTER TABLE games DROP CONSTRAINT IF EXISTS games_cups_left_check")
                cur.execute("""
                    ALTER TABLE games ADD CONSTRAINT games_cups_left_check
                    CHECK (cups_left >= 0.5 AND cups_left <= 5)
                """)

    # Seed admin account from environment variables
    admin_user = os.environ.get("ADMIN_USERNAME")
    admin_pass = os.environ.get("ADMIN_PASSWORD")
    if admin_user and admin_pass:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username = %s", (admin_user,))
                if not cur.fetchone():
                    cur.execute(
                        "INSERT INTO users (username, password_hash, is_admin) VALUES (%s, %s, TRUE)",
                        (admin_user, generate_password_hash(admin_pass)),
                    )


# ── User queries ──────────────────────────────────────────────────────────────

def get_user_by_username(username):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, password_hash, is_admin FROM users WHERE username = %s",
                (username,),
            )
            return cur.fetchone()


def get_user_by_id(user_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, password_hash, is_admin FROM users WHERE id = %s",
                (user_id,),
            )
            return cur.fetchone()


def get_all_users():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, username, is_admin, TO_CHAR(created_at, 'YYYY-MM-DD') AS created_at "
                "FROM users ORDER BY username"
            )
            return cur.fetchall()


def create_user(username, password, is_admin=False):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (username, password_hash, is_admin) VALUES (%s, %s, %s)",
                (username, generate_password_hash(password), is_admin),
            )


def delete_user(user_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))


def reset_user_password(user_id, new_password):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s",
                (generate_password_hash(new_password), user_id),
            )


# ── Player queries ────────────────────────────────────────────────────────────

def get_leaderboard():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, elo, wins, losses, lapped FROM players ORDER BY elo DESC"
            )
            players = [dict(row) for row in cur.fetchall()]

            cur.execute(
                """
                WITH player_results AS (
                    SELECT team1_player1_id AS pid, played_at,
                           CASE WHEN winning_team=1 THEN 'W' ELSE 'L' END AS res FROM games
                    UNION ALL
                    SELECT team1_player2_id, played_at,
                           CASE WHEN winning_team=1 THEN 'W' ELSE 'L' END FROM games
                    UNION ALL
                    SELECT team2_player1_id, played_at,
                           CASE WHEN winning_team=2 THEN 'W' ELSE 'L' END FROM games
                    UNION ALL
                    SELECT team2_player2_id, played_at,
                           CASE WHEN winning_team=2 THEN 'W' ELSE 'L' END FROM games
                )
                SELECT pid, res FROM player_results ORDER BY pid, played_at DESC
                """
            )
            from itertools import groupby
            results_by_player = {}
            for pid, rows in groupby(cur.fetchall(), key=lambda r: r["pid"]):
                results_by_player[pid] = [r["res"] for r in rows]

            for player in players:
                results = results_by_player.get(player["id"], [])
                if not results:
                    player["streak"] = "—"
                else:
                    kind = results[0]
                    count = 0
                    for r in results:
                        if r == kind:
                            count += 1
                        else:
                            break
                    player["streak"] = f"{kind}{count}"

            return players


def get_all_players():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, elo, TO_CHAR(created_at, 'YYYY-MM-DD') AS created_at "
                "FROM players ORDER BY name"
            )
            return cur.fetchall()


def get_player_by_id(player_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, elo FROM players WHERE id = %s", (player_id,))
            return cur.fetchone()


def get_players_by_ids(ids):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, elo FROM players WHERE id = ANY(%s)", (list(ids),)
            )
            return {r["id"]: r for r in cur.fetchall()}


def add_player(name):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO players (name) VALUES (%s)", (name,))


def update_player(player_id, name, elo):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE players SET name = %s, elo = %s WHERE id = %s",
                (name, elo, player_id),
            )


def delete_player(player_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM players WHERE id = %s", (player_id,))


# ── Game queries ──────────────────────────────────────────────────────────────

def record_game(t1p1, t1p2, t2p1, t2p2, winner, cups_left,
                winner_ids, loser_ids, elo_result, rows_by_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO games
                   (team1_player1_id, team1_player2_id,
                    team2_player1_id, team2_player2_id,
                    winning_team, cups_left)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (t1p1, t1p2, t2p1, t2p2, winner, cups_left),
            )
            game_id = cur.fetchone()["id"]

            for i, pid in enumerate(winner_ids):
                cur.execute(
                    "UPDATE players SET elo = %s, wins = wins + 1 WHERE id = %s",
                    (elo_result["winner_new_elos"][i], pid),
                )
                cur.execute(
                    "INSERT INTO elo_history (game_id, player_id, elo_before, elo_after, delta) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (game_id, pid, rows_by_id[pid]["elo"],
                     elo_result["winner_new_elos"][i], elo_result["winner_deltas"][i]),
                )

            lapped = cups_left >= 4.5
            for i, pid in enumerate(loser_ids):
                if lapped:
                    cur.execute(
                        "UPDATE players SET elo = %s, losses = losses + 1, lapped = lapped + 1 WHERE id = %s",
                        (elo_result["loser_new_elos"][i], pid),
                    )
                else:
                    cur.execute(
                        "UPDATE players SET elo = %s, losses = losses + 1 WHERE id = %s",
                        (elo_result["loser_new_elos"][i], pid),
                    )
                cur.execute(
                    "INSERT INTO elo_history (game_id, player_id, elo_before, elo_after, delta) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (game_id, pid, rows_by_id[pid]["elo"],
                     elo_result["loser_new_elos"][i], elo_result["loser_deltas"][i]),
                )
    return game_id


def get_game_history():
    sql = """
        SELECT
            g.id,
            g.winning_team,
            g.cups_left,
            TO_CHAR(g.played_at, 'YYYY-MM-DD HH24:MI') AS played_at,
            p1.name AS t1p1_name, p2.name AS t1p2_name,
            p3.name AS t2p1_name, p4.name AS t2p2_name,
            eh1.delta AS t1p1_delta, eh2.delta AS t1p2_delta,
            eh3.delta AS t2p1_delta, eh4.delta AS t2p2_delta
        FROM games g
        JOIN players p1 ON g.team1_player1_id = p1.id
        JOIN players p2 ON g.team1_player2_id = p2.id
        JOIN players p3 ON g.team2_player1_id = p3.id
        JOIN players p4 ON g.team2_player2_id = p4.id
        LEFT JOIN elo_history eh1 ON eh1.game_id = g.id AND eh1.player_id = g.team1_player1_id
        LEFT JOIN elo_history eh2 ON eh2.game_id = g.id AND eh2.player_id = g.team1_player2_id
        LEFT JOIN elo_history eh3 ON eh3.game_id = g.id AND eh3.player_id = g.team2_player1_id
        LEFT JOIN elo_history eh4 ON eh4.game_id = g.id AND eh4.player_id = g.team2_player2_id
        ORDER BY g.played_at DESC
        LIMIT 100
    """
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()


def get_game_by_id(game_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, team1_player1_id, team1_player2_id, team2_player1_id, "
                "team2_player2_id, winning_team, cups_left FROM games WHERE id = %s",
                (game_id,),
            )
            return cur.fetchone()


def edit_game(game_id, t1p1, t1p2, t2p1, t2p2, winner, cups_left):
    """Atomically revert old game and apply corrected game data."""
    if winner == 1:
        winner_ids = [t1p1, t1p2]
        loser_ids  = [t2p1, t2p2]
    else:
        winner_ids = [t2p1, t2p2]
        loser_ids  = [t1p1, t1p2]

    with get_db() as conn:
        with conn.cursor() as cur:
            # 1. Fetch original game
            cur.execute(
                "SELECT winning_team, cups_left, team1_player1_id, team1_player2_id, "
                "team2_player1_id, team2_player2_id FROM games WHERE id = %s", (game_id,)
            )
            old = cur.fetchone()
            if not old:
                return False

            # 2. Reverse old Elo deltas
            cur.execute("SELECT player_id, delta FROM elo_history WHERE game_id = %s", (game_id,))
            for row in cur.fetchall():
                cur.execute("UPDATE players SET elo = elo - %s WHERE id = %s",
                            (row["delta"], row["player_id"]))

            # 3. Reverse old wins/losses/lapped
            if old["winning_team"] == 1:
                old_winners = [old["team1_player1_id"], old["team1_player2_id"]]
                old_losers  = [old["team2_player1_id"], old["team2_player2_id"]]
            else:
                old_winners = [old["team2_player1_id"], old["team2_player2_id"]]
                old_losers  = [old["team1_player1_id"], old["team1_player2_id"]]
            for pid in old_winners:
                cur.execute("UPDATE players SET wins = GREATEST(wins - 1, 0) WHERE id = %s", (pid,))
            for pid in old_losers:
                cur.execute("UPDATE players SET losses = GREATEST(losses - 1, 0) WHERE id = %s", (pid,))
            if old["cups_left"] >= 4.5:
                for pid in old_losers:
                    cur.execute("UPDATE players SET lapped = GREATEST(lapped - 1, 0) WHERE id = %s", (pid,))

            # 4. Delete old elo_history
            cur.execute("DELETE FROM elo_history WHERE game_id = %s", (game_id,))

            # 5. Fetch post-reversal Elos for the 4 new players, then calculate
            all_ids = list({t1p1, t1p2, t2p1, t2p2})
            cur.execute("SELECT id, elo FROM players WHERE id = ANY(%s)", (all_ids,))
            post_reversal = {r["id"]: r["elo"] for r in cur.fetchall()}

            result = elo_module.calculate_elo_changes(
                winner_elos=(post_reversal[winner_ids[0]], post_reversal[winner_ids[1]]),
                loser_elos=(post_reversal[loser_ids[0]],  post_reversal[loser_ids[1]]),
                cups_left=cups_left,
            )

            # 6. Apply new Elo + wins/losses/lapped
            lapped = cups_left >= 4.5
            for i, pid in enumerate(winner_ids):
                cur.execute("UPDATE players SET elo = %s, wins = wins + 1 WHERE id = %s",
                            (result["winner_new_elos"][i], pid))
                cur.execute(
                    "INSERT INTO elo_history (game_id, player_id, elo_before, elo_after, delta) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (game_id, pid, post_reversal[pid],
                     result["winner_new_elos"][i], result["winner_deltas"][i]))
            for i, pid in enumerate(loser_ids):
                if lapped:
                    cur.execute("UPDATE players SET elo = %s, losses = losses + 1, lapped = lapped + 1 WHERE id = %s",
                                (result["loser_new_elos"][i], pid))
                else:
                    cur.execute("UPDATE players SET elo = %s, losses = losses + 1 WHERE id = %s",
                                (result["loser_new_elos"][i], pid))
                cur.execute(
                    "INSERT INTO elo_history (game_id, player_id, elo_before, elo_after, delta) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (game_id, pid, post_reversal[pid],
                     result["loser_new_elos"][i], result["loser_deltas"][i]))

            # 7. Update game record (preserve played_at)
            cur.execute(
                "UPDATE games SET team1_player1_id=%s, team1_player2_id=%s, "
                "team2_player1_id=%s, team2_player2_id=%s, winning_team=%s, cups_left=%s "
                "WHERE id = %s",
                (t1p1, t1p2, t2p1, t2p2, winner, cups_left, game_id))
    return True


def get_player_elo_history(player_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT eh.elo_before, eh.elo_after, g.played_at
                FROM elo_history eh
                JOIN games g ON eh.game_id = g.id
                WHERE eh.player_id = %s
                ORDER BY g.played_at ASC
            """, (player_id,))
            return cur.fetchall()


def delete_game(game_id):
    """Delete a game and revert its Elo changes."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT winning_team, cups_left, team1_player1_id, team1_player2_id, "
                "team2_player1_id, team2_player2_id FROM games WHERE id = %s",
                (game_id,),
            )
            game = cur.fetchone()
            if not game:
                return False

            cur.execute(
                "SELECT player_id, delta FROM elo_history WHERE game_id = %s", (game_id,)
            )
            history = cur.fetchall()

            if game["winning_team"] == 1:
                winner_ids = [game["team1_player1_id"], game["team1_player2_id"]]
                loser_ids  = [game["team2_player1_id"], game["team2_player2_id"]]
            else:
                winner_ids = [game["team2_player1_id"], game["team2_player2_id"]]
                loser_ids  = [game["team1_player1_id"], game["team1_player2_id"]]

            for row in history:
                cur.execute(
                    "UPDATE players SET elo = elo - %s WHERE id = %s",
                    (row["delta"], row["player_id"]),
                )
            for pid in winner_ids:
                cur.execute(
                    "UPDATE players SET wins = GREATEST(wins - 1, 0) WHERE id = %s", (pid,)
                )
            for pid in loser_ids:
                cur.execute(
                    "UPDATE players SET losses = GREATEST(losses - 1, 0) WHERE id = %s", (pid,)
                )
            if game["cups_left"] >= 4.5:
                for pid in loser_ids:
                    cur.execute(
                        "UPDATE players SET lapped = GREATEST(lapped - 1, 0) WHERE id = %s", (pid,)
                    )

            cur.execute("DELETE FROM elo_history WHERE game_id = %s", (game_id,))
            cur.execute("DELETE FROM games WHERE id = %s", (game_id,))
    return True

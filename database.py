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
            # Add data_point column if missing
            cur.execute("""
                ALTER TABLE games ADD COLUMN IF NOT EXISTS data_point TEXT
                    CHECK (data_point IN ('1_full', 'two_halfs'))
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
                winner_ids, loser_ids, elo_result, rows_by_id, data_point=None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO games
                   (team1_player1_id, team1_player2_id,
                    team2_player1_id, team2_player2_id,
                    winning_team, cups_left, data_point)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (t1p1, t1p2, t2p1, t2p2, winner, cups_left, data_point),
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


def get_player_matchup_stats(player_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                WITH focal_games AS (
                    SELECT g.id AS game_id, g.winning_team,
                        CASE WHEN g.team1_player1_id = %(pid)s OR g.team1_player2_id = %(pid)s
                             THEN 1 ELSE 2 END AS focal_team
                    FROM games g
                    WHERE %(pid)s IN (g.team1_player1_id, g.team1_player2_id,
                                      g.team2_player1_id, g.team2_player2_id)
                ),
                h2h_rows AS (
                    SELECT fg.winning_team = fg.focal_team AS focal_won,
                        unnest(CASE WHEN fg.focal_team = 1
                            THEN ARRAY[g.team2_player1_id, g.team2_player2_id]
                            ELSE ARRAY[g.team1_player1_id, g.team1_player2_id] END) AS opp_id
                    FROM focal_games fg JOIN games g ON g.id = fg.game_id
                ),
                partner_rows AS (
                    SELECT fg.winning_team = fg.focal_team AS focal_won,
                        unnest(CASE WHEN fg.focal_team = 1
                            THEN ARRAY[CASE WHEN g.team1_player1_id = %(pid)s
                                            THEN g.team1_player2_id
                                            ELSE g.team1_player1_id END]
                            ELSE ARRAY[CASE WHEN g.team2_player1_id = %(pid)s
                                            THEN g.team2_player2_id
                                            ELSE g.team2_player1_id END] END) AS partner_id
                    FROM focal_games fg JOIN games g ON g.id = fg.game_id
                )
                SELECT 'h2h' AS kind, p.name, COUNT(*) AS games,
                    SUM(CASE WHEN hr.focal_won THEN 1 ELSE 0 END) AS wins
                FROM h2h_rows hr JOIN players p ON p.id = hr.opp_id GROUP BY p.name
                UNION ALL
                SELECT 'partner' AS kind, p.name, COUNT(*) AS games,
                    SUM(CASE WHEN pr.focal_won THEN 1 ELSE 0 END) AS wins
                FROM partner_rows pr JOIN players p ON p.id = pr.partner_id GROUP BY p.name
                ORDER BY kind, games DESC, wins DESC
            """, {"pid": player_id})
            return cur.fetchall()


def get_site_stats():
    with get_db() as conn:
        with conn.cursor() as cur:
            # Summary: total games, players, upset rate
            cur.execute("""
                WITH team_avg AS (
                    SELECT g.id AS game_id, g.winning_team,
                        AVG(CASE WHEN eh.player_id IN (g.team1_player1_id, g.team1_player2_id)
                                 THEN eh.elo_before END) AS t1_avg,
                        AVG(CASE WHEN eh.player_id IN (g.team2_player1_id, g.team2_player2_id)
                                 THEN eh.elo_before END) AS t2_avg
                    FROM games g
                    JOIN elo_history eh ON eh.game_id = g.id
                    GROUP BY g.id, g.winning_team
                )
                SELECT
                    (SELECT COUNT(*) FROM games) AS total_games,
                    (SELECT COUNT(*) FROM players) AS total_players,
                    COUNT(*) AS games_with_history,
                    SUM(CASE WHEN (winning_team = 1 AND t1_avg < t2_avg)
                                  OR (winning_team = 2 AND t2_avg < t1_avg)
                             THEN 1 ELSE 0 END) AS upsets
                FROM team_avg
            """)
            summary_row = cur.fetchone()
            total_games = summary_row["total_games"] or 0
            total_players = summary_row["total_players"] or 0
            games_with_history = summary_row["games_with_history"] or 0
            upsets = summary_row["upsets"] or 0
            upset_rate = round(upsets / games_with_history * 100, 1) if games_with_history else 0

            # Activity: games per week, last 16 weeks
            cur.execute("""
                SELECT DATE_TRUNC('week', played_at) AS week, COUNT(*) AS games
                FROM games
                WHERE played_at >= NOW() - INTERVAL '16 weeks'
                GROUP BY week
                ORDER BY week ASC
            """)
            activity = [
                {"week": r["week"].isoformat(), "games": r["games"]}
                for r in cur.fetchall()
            ]

            # Cups distribution
            cur.execute("""
                SELECT cups_left, COUNT(*) AS games
                FROM games
                GROUP BY cups_left
                ORDER BY cups_left
            """)
            cups_dist = [
                {"cups_left": r["cups_left"], "games": r["games"]}
                for r in cur.fetchall()
            ]

            # Data point distribution (overall split)
            cur.execute("""
                SELECT
                    COUNT(*) FILTER (WHERE data_point = '1_full') AS one_full,
                    COUNT(*) FILTER (WHERE data_point = 'two_halfs') AS two_halfs
                FROM games
                WHERE data_point IS NOT NULL
            """)
            row = cur.fetchone()
            data_point_dist = {
                "one_full": row["one_full"] or 0,
                "two_halfs": row["two_halfs"] or 0,
            }

            # Top 10 biggest Elo swings
            cur.execute("""
                SELECT
                    eh.delta,
                    eh.elo_before,
                    eh.elo_after,
                    p.name AS player_name,
                    TO_CHAR(g.played_at, 'YYYY-MM-DD') AS played_at,
                    CASE WHEN eh.player_id IN (g.team1_player1_id, g.team1_player2_id)
                         THEN (SELECT STRING_AGG(op.name, ' & ' ORDER BY op.name)
                               FROM players op
                               WHERE op.id IN (g.team2_player1_id, g.team2_player2_id))
                         ELSE (SELECT STRING_AGG(op.name, ' & ' ORDER BY op.name)
                               FROM players op
                               WHERE op.id IN (g.team1_player1_id, g.team1_player2_id))
                    END AS opponents
                FROM elo_history eh
                JOIN games g ON g.id = eh.game_id
                JOIN players p ON p.id = eh.player_id
                ORDER BY ABS(eh.delta) DESC
                LIMIT 10
            """)
            top_swings = [dict(r) for r in cur.fetchall()]

            # Win streaks: fetch all results per player ordered chronologically
            cur.execute("""
                SELECT pid, res FROM (
                    SELECT team1_player1_id AS pid, played_at, id AS game_id,
                           CASE WHEN winning_team=1 THEN 'W' ELSE 'L' END AS res FROM games
                    UNION ALL
                    SELECT team1_player2_id, played_at, id,
                           CASE WHEN winning_team=1 THEN 'W' ELSE 'L' END FROM games
                    UNION ALL
                    SELECT team2_player1_id, played_at, id,
                           CASE WHEN winning_team=2 THEN 'W' ELSE 'L' END FROM games
                    UNION ALL
                    SELECT team2_player2_id, played_at, id,
                           CASE WHEN winning_team=2 THEN 'W' ELSE 'L' END FROM games
                ) t
                ORDER BY pid, played_at ASC, game_id ASC
            """)
            from itertools import groupby as _groupby
            results_by_player = {}
            for pid, rows in _groupby(cur.fetchall(), key=lambda r: r["pid"]):
                results_by_player[pid] = [r["res"] for r in rows]

            cur.execute("SELECT id, name FROM players")
            player_names = {r["id"]: r["name"] for r in cur.fetchall()}

            current_streak_best = {"name": None, "streak": 0}
            alltime_streak_best = {"name": None, "streak": 0}
            current_loss_streak_best = {"name": None, "streak": 0}
            alltime_loss_streak_best = {"name": None, "streak": 0}

            for pid, results in results_by_player.items():
                # Current active win streak (from most recent game backwards)
                current = 0
                for r in reversed(results):
                    if r == "W":
                        current += 1
                    else:
                        break

                # All-time longest win streak
                max_run = run = 0
                for r in results:
                    if r == "W":
                        run += 1
                        if run > max_run:
                            max_run = run
                    else:
                        run = 0

                # Current active losing streak (from most recent game backwards)
                current_loss = 0
                for r in reversed(results):
                    if r == "L":
                        current_loss += 1
                    else:
                        break

                # All-time longest losing streak
                max_loss_run = loss_run = 0
                for r in results:
                    if r == "L":
                        loss_run += 1
                        if loss_run > max_loss_run:
                            max_loss_run = loss_run
                    else:
                        loss_run = 0

                name = player_names.get(pid, "Unknown")
                if current > current_streak_best["streak"]:
                    current_streak_best = {"name": name, "streak": current}
                if max_run > alltime_streak_best["streak"]:
                    alltime_streak_best = {"name": name, "streak": max_run}
                if current_loss > current_loss_streak_best["streak"]:
                    current_loss_streak_best = {"name": name, "streak": current_loss}
                if max_loss_run > alltime_loss_streak_best["streak"]:
                    alltime_loss_streak_best = {"name": name, "streak": max_loss_run}

            # Fraud watch: top 3 players with lowest average opponent Elo
            cur.execute("""
                WITH opp_elo AS (
                    SELECT g.team1_player1_id AS pid,
                           (eh2.elo_before + eh3.elo_before) / 2.0 AS opp_avg
                    FROM games g
                    JOIN elo_history eh2 ON eh2.game_id = g.id AND eh2.player_id = g.team2_player1_id
                    JOIN elo_history eh3 ON eh3.game_id = g.id AND eh3.player_id = g.team2_player2_id
                    UNION ALL
                    SELECT g.team1_player2_id,
                           (eh2.elo_before + eh3.elo_before) / 2.0
                    FROM games g
                    JOIN elo_history eh2 ON eh2.game_id = g.id AND eh2.player_id = g.team2_player1_id
                    JOIN elo_history eh3 ON eh3.game_id = g.id AND eh3.player_id = g.team2_player2_id
                    UNION ALL
                    SELECT g.team2_player1_id,
                           (eh1.elo_before + eh2.elo_before) / 2.0
                    FROM games g
                    JOIN elo_history eh1 ON eh1.game_id = g.id AND eh1.player_id = g.team1_player1_id
                    JOIN elo_history eh2 ON eh2.game_id = g.id AND eh2.player_id = g.team1_player2_id
                    UNION ALL
                    SELECT g.team2_player2_id,
                           (eh1.elo_before + eh2.elo_before) / 2.0
                    FROM games g
                    JOIN elo_history eh1 ON eh1.game_id = g.id AND eh1.player_id = g.team1_player1_id
                    JOIN elo_history eh2 ON eh2.game_id = g.id AND eh2.player_id = g.team1_player2_id
                )
                SELECT p.name, ROUND(AVG(opp_avg)::numeric, 1) AS avg_opp_elo, COUNT(*) AS games
                FROM opp_elo
                JOIN players p ON p.id = opp_elo.pid
                GROUP BY p.id, p.name
                ORDER BY avg_opp_elo ASC
                LIMIT 3
            """)
            fraud_watch = [dict(r) for r in cur.fetchall()]

            # Best duo: teammate pair with highest win percentage (min 3 games together)
            cur.execute("""
                WITH duo_games AS (
                    SELECT
                        LEAST(team1_player1_id, team1_player2_id) AS p1,
                        GREATEST(team1_player1_id, team1_player2_id) AS p2,
                        CASE WHEN winning_team = 1 THEN 1 ELSE 0 END AS won
                    FROM games
                    UNION ALL
                    SELECT
                        LEAST(team2_player1_id, team2_player2_id),
                        GREATEST(team2_player1_id, team2_player2_id),
                        CASE WHEN winning_team = 2 THEN 1 ELSE 0 END
                    FROM games
                )
                SELECT
                    p1.name AS player1_name,
                    p2.name AS player2_name,
                    COUNT(*) AS games,
                    SUM(won) AS wins,
                    ROUND(SUM(won)::numeric / COUNT(*) * 100, 1) AS win_pct
                FROM duo_games
                JOIN players p1 ON p1.id = duo_games.p1
                JOIN players p2 ON p2.id = duo_games.p2
                GROUP BY duo_games.p1, duo_games.p2, p1.name, p2.name
                HAVING COUNT(*) >= 3
                ORDER BY win_pct DESC, games DESC
                LIMIT 1
            """)
            best_duo_row = cur.fetchone()
            best_duo = dict(best_duo_row) if best_duo_row else None

            # Worst duo: teammate pair with lowest win percentage (min 3 games together)
            cur.execute("""
                WITH duo_games AS (
                    SELECT
                        LEAST(team1_player1_id, team1_player2_id) AS p1,
                        GREATEST(team1_player1_id, team1_player2_id) AS p2,
                        CASE WHEN winning_team = 1 THEN 1 ELSE 0 END AS won
                    FROM games
                    UNION ALL
                    SELECT
                        LEAST(team2_player1_id, team2_player2_id),
                        GREATEST(team2_player1_id, team2_player2_id),
                        CASE WHEN winning_team = 2 THEN 1 ELSE 0 END
                    FROM games
                )
                SELECT
                    p1.name AS player1_name,
                    p2.name AS player2_name,
                    COUNT(*) AS games,
                    SUM(won) AS wins,
                    ROUND(SUM(won)::numeric / COUNT(*) * 100, 1) AS win_pct
                FROM duo_games
                JOIN players p1 ON p1.id = duo_games.p1
                JOIN players p2 ON p2.id = duo_games.p2
                GROUP BY duo_games.p1, duo_games.p2, p1.name, p2.name
                HAVING COUNT(*) >= 3
                ORDER BY win_pct ASC, games DESC
                LIMIT 1
            """)
            worst_duo_row = cur.fetchone()
            worst_duo = dict(worst_duo_row) if worst_duo_row else None

    return {
        "summary": {
            "total_games": total_games,
            "total_players": total_players,
            "upset_rate": upset_rate,
        },
        "activity": activity,
        "cups_dist": cups_dist,
        "data_point_dist": data_point_dist,
        "top_swings": top_swings,
        "current_streak": current_streak_best,
        "alltime_streak": alltime_streak_best,
        "current_loss_streak": current_loss_streak_best,
        "alltime_loss_streak": alltime_loss_streak_best,
        "fraud_watch": fraud_watch,
        "best_duo": best_duo,
        "worst_duo": worst_duo,
    }


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

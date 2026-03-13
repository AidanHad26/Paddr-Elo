"""
apply_recalc.py — Retroactively apply the current K-factor formula to all historical games.

Replays all games from scratch (every player starts at 1000), then writes the
recalculated Elo values and elo_history rows to the DB in a single transaction.

Usage:
    export DATABASE_URL=postgresql://user:pass@host/dbname
    python apply_recalc.py
"""

import os
import psycopg2
import psycopg2.extras

from elo import calculate_elo_changes

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

if not DATABASE_URL:
    raise SystemExit("ERROR: DATABASE_URL environment variable is not set.")


def main():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name FROM players ORDER BY name")
            players = cur.fetchall()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, winning_team, cups_left, "
                "team1_player1_id, team1_player2_id, "
                "team2_player1_id, team2_player2_id "
                "FROM games ORDER BY played_at ASC"
            )
            games = cur.fetchall()

        # Replay all games from scratch
        sim_elo = {p["id"]: 1000.0 for p in players}
        # history_rows: list of (game_id, player_id, elo_before, elo_after, delta)
        history_rows = []

        for game in games:
            wt = game["winning_team"]
            if wt == 1:
                winner_ids = [game["team1_player1_id"], game["team1_player2_id"]]
                loser_ids  = [game["team2_player1_id"], game["team2_player2_id"]]
            else:
                winner_ids = [game["team2_player1_id"], game["team2_player2_id"]]
                loser_ids  = [game["team1_player1_id"], game["team1_player2_id"]]

            winner_elos_before = (sim_elo[winner_ids[0]], sim_elo[winner_ids[1]])
            loser_elos_before  = (sim_elo[loser_ids[0]],  sim_elo[loser_ids[1]])

            result = calculate_elo_changes(winner_elos_before, loser_elos_before, game["cups_left"])

            for i, pid in enumerate(winner_ids):
                elo_before = sim_elo[pid]
                elo_after  = result["winner_new_elos"][i]
                delta      = result["winner_deltas"][i]
                history_rows.append((game["id"], pid, elo_before, elo_after, delta))
                sim_elo[pid] = elo_after

            for i, pid in enumerate(loser_ids):
                elo_before = sim_elo[pid]
                elo_after  = result["loser_new_elos"][i]
                delta      = result["loser_deltas"][i]
                history_rows.append((game["id"], pid, elo_before, elo_after, delta))
                sim_elo[pid] = elo_after

        # Write everything in a single transaction
        with conn.cursor() as cur:
            cur.execute("DELETE FROM elo_history")

            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO elo_history (game_id, player_id, elo_before, elo_after, delta) VALUES %s",
                history_rows,
            )

            for p in players:
                cur.execute(
                    "UPDATE players SET elo = %s WHERE id = %s",
                    (sim_elo[p["id"]], p["id"]),
                )

        conn.commit()

        print(f"Done.")
        print(f"  Games replayed:       {len(games)}")
        print(f"  Players updated:      {len(players)}")
        print(f"  elo_history rows:     {len(history_rows)}")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()

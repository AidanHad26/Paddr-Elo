"""
simulate_recalc.py — Simulate Elo recalculation with the current K-factor formula.

Replays all historical games from scratch (every player starts at 1000) using
the current elo.py logic, then prints a side-by-side comparison against live Elo.

Usage:
    export DATABASE_URL=postgresql://user:pass@host/dbname
    python simulate_recalc.py
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
            cur.execute("SELECT id, name, elo FROM players ORDER BY name")
            players = cur.fetchall()

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, winning_team, cups_left, "
                "team1_player1_id, team1_player2_id, "
                "team2_player1_id, team2_player2_id "
                "FROM games ORDER BY played_at ASC"
            )
            games = cur.fetchall()
    finally:
        conn.close()

    # Map player_id -> current (live) Elo and name
    current_elo = {p["id"]: p["elo"] for p in players}
    names = {p["id"]: p["name"] for p in players}

    # Start simulation: every player at 1000
    sim_elo = {p["id"]: 1000.0 for p in players}

    for game in games:
        wt = game["winning_team"]
        if wt == 1:
            winner_ids = [game["team1_player1_id"], game["team1_player2_id"]]
            loser_ids  = [game["team2_player1_id"], game["team2_player2_id"]]
        else:
            winner_ids = [game["team2_player1_id"], game["team2_player2_id"]]
            loser_ids  = [game["team1_player1_id"], game["team1_player2_id"]]

        winner_elos = (sim_elo[winner_ids[0]], sim_elo[winner_ids[1]])
        loser_elos  = (sim_elo[loser_ids[0]],  sim_elo[loser_ids[1]])

        result = calculate_elo_changes(winner_elos, loser_elos, game["cups_left"])

        sim_elo[winner_ids[0]] = result["winner_new_elos"][0]
        sim_elo[winner_ids[1]] = result["winner_new_elos"][1]
        sim_elo[loser_ids[0]]  = result["loser_new_elos"][0]
        sim_elo[loser_ids[1]]  = result["loser_new_elos"][1]

    # Sort by simulated Elo descending
    sorted_players = sorted(players, key=lambda p: sim_elo[p["id"]], reverse=True)

    print("\n=== Simulated Leaderboard (new K-factor) ===")
    print(f"{'Rank':>4}  {'Name':<16}  {'Current Elo':>11}  {'Sim Elo':>9}  {'Delta':>7}")
    print("-" * 56)
    for rank, p in enumerate(sorted_players, 1):
        pid = p["id"]
        cur_e = current_elo[pid]
        sim_e = sim_elo[pid]
        delta = sim_e - cur_e
        delta_str = f"{delta:+.1f}"
        print(f"{rank:>4}  {names[pid]:<16}  {cur_e:>11.1f}  {sim_e:>9.1f}  {delta_str:>7}")

    print(f"\nGames replayed: {len(games)}")


if __name__ == "__main__":
    main()

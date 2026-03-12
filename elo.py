def team_elo(elo1: float, elo2: float) -> float:
    """Average Elo of two players, used as the team's combined rating."""
    return (elo1 + elo2) / 2.0


def expected_score(my_team_elo: float, opponent_team_elo: float) -> float:
    """Standard Elo expected score: probability this team wins."""
    return 1.0 / (1.0 + 10.0 ** ((opponent_team_elo - my_team_elo) / 400.0))


def k_adjusted(cups_left: int, k_base: float = 32.0) -> float:
    """
    K-factor scaled by margin of victory.
      cups_left=5 (shutout)  -> K = 32.0  (maximum swing)
      cups_left=1 (very close) -> K = 6.4  (minimal swing)
    """
    return k_base * (cups_left / 5.0)


def calculate_elo_changes(
    winner_elos: tuple,
    loser_elos: tuple,
    cups_left: int
) -> dict:
    """
    Calculate new Elo ratings for all 4 players after a game.

    Args:
        winner_elos: (player1_elo, player2_elo) for the winning team
        loser_elos:  (player1_elo, player2_elo) for the losing team
        cups_left:   cups remaining for the losing team (1–5)

    Returns:
        dict with keys:
          winner_new_elos, loser_new_elos  — updated ratings
          winner_deltas, loser_deltas      — change per player
    """
    w_team = team_elo(*winner_elos)
    l_team = team_elo(*loser_elos)

    E_winner = expected_score(w_team, l_team)
    E_loser  = expected_score(l_team, w_team)  # = 1 - E_winner

    K = k_adjusted(cups_left)

    winner_deltas = [K * (1.0 - E_winner), K * (1.0 - E_winner)]
    loser_deltas  = [K * (0.0 - E_loser),  K * (0.0 - E_loser)]

    return {
        "winner_new_elos": [
            winner_elos[0] + winner_deltas[0],
            winner_elos[1] + winner_deltas[1],
        ],
        "loser_new_elos": [
            loser_elos[0] + loser_deltas[0],
            loser_elos[1] + loser_deltas[1],
        ],
        "winner_deltas": winner_deltas,
        "loser_deltas":  loser_deltas,
    }

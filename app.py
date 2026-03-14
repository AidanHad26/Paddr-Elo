import os
from functools import wraps

import psycopg2
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user,
)
from werkzeug.security import check_password_hash

import database
import elo as elo_module

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "paddr-local-dev-secret")

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "error"

# Initialize DB on startup (idempotent — safe to call every time)
database.init_db()


class User(UserMixin):
    def __init__(self, id, username, is_admin):
        self.id = id
        self.username = username
        self.is_admin = is_admin


@login_manager.user_loader
def load_user(user_id):
    row = database.get_user_by_id(int(user_id))
    if row:
        return User(row["id"], row["username"], row["is_admin"])
    return None


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("Admin access required.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        row = database.get_user_by_username(username)
        if row and check_password_hash(row["password_hash"], password):
            login_user(User(row["id"], row["username"], row["is_admin"]))
            next_page = request.args.get("next")
            return redirect(next_page or url_for("index"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    players = database.get_leaderboard()
    return render_template("index.html", players=players)


# ---------------------------------------------------------------------------
# Record game
# ---------------------------------------------------------------------------

@app.route("/record")
def record_game_page():
    return render_template("record_game.html")


@app.route("/record", methods=["POST"])
def record_game():
    try:
        t1p1 = int(request.form["t1p1"])
        t1p2 = int(request.form["t1p2"])
        t2p1 = int(request.form["t2p1"])
        t2p2 = int(request.form["t2p2"])
        winner = int(request.form["winner"])
        cups_left = float(request.form["cups_left"])
    except (KeyError, ValueError):
        flash("Invalid form submission. Please fill out all fields.", "error")
        return redirect(url_for("record_game_page"))

    ids = [t1p1, t1p2, t2p1, t2p2]
    if len(set(ids)) != 4:
        flash("Each of the four player slots must have a different player.", "error")
        return redirect(url_for("record_game_page"))

    if winner not in (1, 2):
        flash("Please select a winning team.", "error")
        return redirect(url_for("record_game_page"))

    valid_cups = {i / 2 for i in range(1, 11)}  # 0.5, 1.0, 1.5, ..., 5.0
    if cups_left not in valid_cups:
        flash("Cups left must be between 0.5 and 5 in 0.5 increments.", "error")
        return redirect(url_for("record_game_page"))

    rows_by_id = database.get_players_by_ids(ids)
    if len(rows_by_id) != 4:
        flash("One or more selected players were not found.", "error")
        return redirect(url_for("record_game_page"))

    if winner == 1:
        winner_ids = [t1p1, t1p2]
        loser_ids  = [t2p1, t2p2]
    else:
        winner_ids = [t2p1, t2p2]
        loser_ids  = [t1p1, t1p2]

    result = elo_module.calculate_elo_changes(
        winner_elos=(rows_by_id[winner_ids[0]]["elo"], rows_by_id[winner_ids[1]]["elo"]),
        loser_elos=(rows_by_id[loser_ids[0]]["elo"],  rows_by_id[loser_ids[1]]["elo"]),
        cups_left=cups_left,
    )

    database.record_game(
        t1p1, t1p2, t2p1, t2p2, winner, cups_left,
        winner_ids, loser_ids, result, rows_by_id,
    )

    w_names = [rows_by_id[pid]["name"] for pid in winner_ids]
    l_names = [rows_by_id[pid]["name"] for pid in loser_ids]
    wd = result["winner_deltas"]
    ld = result["loser_deltas"]
    flash(
        f"Game recorded! "
        f"{w_names[0]} +{wd[0]:.1f}, {w_names[1]} +{wd[1]:.1f}  |  "
        f"{l_names[0]} {ld[0]:.1f}, {l_names[1]} {ld[1]:.1f}",
        "success",
    )
    return redirect(url_for("history"))


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

@app.route("/history")
def history():
    games = database.get_game_history()
    return render_template("history.html", games=games)


# ---------------------------------------------------------------------------
# Players — anyone can view and add; admin-only actions handled below
# ---------------------------------------------------------------------------

@app.route("/players")
def players():
    all_players = database.get_all_players()
    return render_template("players.html", players=all_players)


@app.route("/players/add", methods=["POST"])
def add_player():
    name = request.form.get("name", "").strip()
    if not name or len(name) > 50:
        flash("Player name must be between 1 and 50 characters.", "error")
        return redirect(url_for("players"))
    try:
        database.add_player(name)
        flash(f'Player "{name}" added!', "success")
    except psycopg2.IntegrityError:
        flash(f'"{name}" is already on the roster.', "error")
    return redirect(url_for("players"))


@app.route("/players/<int:player_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_player(player_id):
    player = database.get_player_by_id(player_id)
    if not player:
        flash("Player not found.", "error")
        return redirect(url_for("players"))
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        try:
            elo = float(request.form.get("elo", player["elo"]))
        except ValueError:
            flash("Invalid Elo value.", "error")
            return render_template("edit_player.html", player=player)
        if not name or len(name) > 50:
            flash("Player name must be between 1 and 50 characters.", "error")
            return render_template("edit_player.html", player=player)
        try:
            database.update_player(player_id, name, elo)
            flash(f'Player updated.', "success")
        except psycopg2.IntegrityError:
            flash(f'A player named "{name}" already exists.', "error")
            return render_template("edit_player.html", player=player)
        return redirect(url_for("players"))
    return render_template("edit_player.html", player=player)


@app.route("/players/<int:player_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_player(player_id):
    try:
        database.delete_player(player_id)
        flash("Player deleted.", "success")
    except psycopg2.errors.ForeignKeyViolation:
        flash("Cannot delete a player who has games recorded.", "error")
    return redirect(url_for("players"))


# ---------------------------------------------------------------------------
# Admin: game management
# ---------------------------------------------------------------------------

@app.route("/admin/games/<int:game_id>/edit", methods=["GET"])
@login_required
@admin_required
def edit_game_page(game_id):
    game = database.get_game_by_id(game_id)
    if not game:
        flash("Game not found.", "error")
        return redirect(url_for("history"))
    players = database.get_all_players()
    return render_template("edit_game.html", game=game, players=players)


@app.route("/admin/games/<int:game_id>/edit", methods=["POST"])
@login_required
@admin_required
def edit_game(game_id):
    try:
        t1p1 = int(request.form["t1p1"])
        t1p2 = int(request.form["t1p2"])
        t2p1 = int(request.form["t2p1"])
        t2p2 = int(request.form["t2p2"])
        winner = int(request.form["winner"])
        cups_left = float(request.form["cups_left"])
    except (KeyError, ValueError):
        flash("Invalid form submission. Please fill out all fields.", "error")
        return redirect(url_for("edit_game_page", game_id=game_id))

    ids = [t1p1, t1p2, t2p1, t2p2]
    if len(set(ids)) != 4:
        flash("Each of the four player slots must have a different player.", "error")
        return redirect(url_for("edit_game_page", game_id=game_id))

    if winner not in (1, 2):
        flash("Please select a winning team.", "error")
        return redirect(url_for("edit_game_page", game_id=game_id))

    valid_cups = {i / 2 for i in range(1, 11)}
    if cups_left not in valid_cups:
        flash("Cups left must be between 0.5 and 5 in 0.5 increments.", "error")
        return redirect(url_for("edit_game_page", game_id=game_id))

    rows_by_id = database.get_players_by_ids(ids)
    if len(rows_by_id) != 4:
        flash("One or more selected players were not found.", "error")
        return redirect(url_for("edit_game_page", game_id=game_id))

    if winner == 1:
        winner_ids = [t1p1, t1p2]
        loser_ids  = [t2p1, t2p2]
    else:
        winner_ids = [t2p1, t2p2]
        loser_ids  = [t1p1, t1p2]

    result = elo_module.calculate_elo_changes(
        winner_elos=(rows_by_id[winner_ids[0]]["elo"], rows_by_id[winner_ids[1]]["elo"]),
        loser_elos=(rows_by_id[loser_ids[0]]["elo"],  rows_by_id[loser_ids[1]]["elo"]),
        cups_left=cups_left,
    )

    success = database.edit_game(
        game_id, t1p1, t1p2, t2p1, t2p2, winner, cups_left,
        winner_ids, loser_ids, result, rows_by_id,
    )
    if not success:
        flash("Game not found.", "error")
        return redirect(url_for("history"))

    flash("Game updated.", "success")
    return redirect(url_for("history"))


@app.route("/admin/games/<int:game_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_game(game_id):
    success = database.delete_game(game_id)
    if success:
        flash("Game deleted and Elo changes reversed.", "success")
    else:
        flash("Game not found.", "error")
    return redirect(url_for("history"))


# ---------------------------------------------------------------------------
# Admin: user management
# ---------------------------------------------------------------------------

@app.route("/admin/users")
@login_required
@admin_required
def admin_users():
    users = database.get_all_users()
    return render_template("admin_users.html", users=users)


@app.route("/admin/users/add", methods=["POST"])
@login_required
@admin_required
def admin_add_user():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "").strip()
    is_admin = request.form.get("is_admin") == "on"
    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect(url_for("admin_users"))
    try:
        database.create_user(username, password, is_admin)
        flash(f'User "{username}" created.', "success")
    except psycopg2.IntegrityError:
        flash(f'Username "{username}" already exists.', "error")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_delete_user(user_id):
    if user_id == current_user.id:
        flash("You can't delete your own account.", "error")
        return redirect(url_for("admin_users"))
    database.delete_user(user_id)
    flash("User deleted.", "success")
    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
@admin_required
def admin_reset_password(user_id):
    new_password = request.form.get("new_password", "").strip()
    if not new_password:
        flash("New password is required.", "error")
        return redirect(url_for("admin_users"))
    database.reset_user_password(user_id, new_password)
    flash("Password reset.", "success")
    return redirect(url_for("admin_users"))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@app.route("/api/players")
def api_players():
    players = database.get_all_players()
    return jsonify([
        {"id": p["id"], "name": p["name"], "elo": round(p["elo"], 1)}
        for p in players
    ])


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(debug=True, port=5050)

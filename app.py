from datetime import datetime, timezone
import os
import secrets

from flask import Flask, jsonify, redirect, render_template, request, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text


def get_database_url() -> str:
    """Use Railway/Postgres in production and SQLite when running locally."""
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///chat.db"

    # Compatibility with providers that still expose postgres://.
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI=get_database_url(),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    MAX_CONTENT_LENGTH=64 * 1024,
)

db = SQLAlchemy(app)


class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(db.String(20), nullable=False, index=True)
    author = db.Column(db.String(40), nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


# For this small project, creating missing tables at startup keeps deploy simple.
with app.app_context():
    db.create_all()


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/create-room")
def create_room():
    code = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
    while Room.query.filter_by(code=code).first():
        code = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]

    db.session.add(Room(code=code))
    db.session.commit()
    return redirect(url_for("room", code=code))


@app.route("/room/<code>")
def room(code):
    if not Room.query.filter_by(code=code).first():
        return render_template("not_found.html"), 404
    return render_template("room.html", code=code)


@app.get("/api/messages/<code>")
def get_messages(code):
    after = request.args.get("after", type=int, default=0)

    if not Room.query.filter_by(code=code).first():
        return jsonify({"error": "Sala não encontrada"}), 404

    messages = (
        Message.query
        .filter(Message.room_code == code, Message.id > after)
        .order_by(Message.id.asc())
        .limit(200)
        .all()
    )

    return jsonify([
        {
            "id": message.id,
            "author": message.author,
            "text": message.text,
            "time": message.created_at.strftime("%H:%M"),
        }
        for message in messages
    ])


@app.post("/api/messages/<code>")
def send_message(code):
    if not Room.query.filter_by(code=code).first():
        return jsonify({"error": "Sala não encontrada"}), 404

    data = request.get_json(silent=True) or {}
    author = (data.get("author") or "").strip()[:40]
    message_text = (data.get("text") or "").strip()[:2000]

    if not author or not message_text:
        return jsonify({"error": "Nome e mensagem são obrigatórios"}), 400

    message = Message(room_code=code, author=author, text=message_text)
    db.session.add(message)
    db.session.commit()

    return jsonify({
        "id": message.id,
        "author": message.author,
        "text": message.text,
        "time": message.created_at.strftime("%H:%M"),
    }), 201


@app.get("/health")
def health():
    try:
        db.session.execute(text("SELECT 1"))
        return {"status": "ok"}, 200
    except Exception:
        db.session.rollback()
        return {"status": "error"}, 503


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)

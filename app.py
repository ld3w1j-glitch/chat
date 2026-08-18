from __future__ import annotations

import base64
import json
import os
import secrets
from datetime import datetime, timezone
from io import BytesIO

from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pywebpush import WebPushException, webpush


STICKER_PREFIX = "__STICKER__:"
MAX_STICKER_BYTES = 1_500_000


def utcnow():
    return datetime.now(timezone.utc)


def get_database_url() -> str:
    """Railway/Postgres in production; SQLite for local tests."""
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///chat.db"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def b64url_no_padding(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


app = Flask(__name__)
app.config.update(
    SQLALCHEMY_DATABASE_URI=get_database_url(),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    MAX_CONTENT_LENGTH=3 * 1024 * 1024,
)

db = SQLAlchemy(app)


class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(db.String(20), nullable=False, index=True)
    author = db.Column(db.String(40), nullable=False)
    text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class Sticker(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(40), unique=True, nullable=False, index=True)
    room_code = db.Column(db.String(20), nullable=False, index=True)
    owner = db.Column(db.String(40), nullable=False)
    mime_type = db.Column(db.String(50), nullable=False, default="image/webp")
    image_data = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class PushSubscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(db.String(20), nullable=False, index=True)
    owner = db.Column(db.String(40), nullable=False)
    device_id = db.Column(db.String(80), nullable=False, index=True)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text, nullable=False)
    auth = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class AppSetting(db.Model):
    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, nullable=False)


with app.app_context():
    db.create_all()


def get_or_create_vapid_keys() -> tuple[str, str]:
    """
    Return (public_key, private_key) as Base64URL strings.

    They are persisted in the database so Railway redeploys do not invalidate
    existing browser push subscriptions.
    """
    pub_setting = db.session.get(AppSetting, "vapid_public")
    priv_setting = db.session.get(AppSetting, "vapid_private")
    if pub_setting and priv_setting:
        return pub_setting.value, priv_setting.value

    private_key = ec.generate_private_key(ec.SECP256R1())
    private_number = private_key.private_numbers().private_value.to_bytes(32, "big")
    public_bytes = private_key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )

    private_b64 = b64url_no_padding(private_number)
    public_b64 = b64url_no_padding(public_bytes)

    db.session.merge(AppSetting(key="vapid_public", value=public_b64))
    db.session.merge(AppSetting(key="vapid_private", value=private_b64))
    db.session.commit()
    return public_b64, private_b64


def sticker_token_from_message(message_text: str) -> str | None:
    if message_text.startswith(STICKER_PREFIX):
        return message_text[len(STICKER_PREFIX):].strip()
    return None


def serialize_message(message: Message) -> dict:
    token = sticker_token_from_message(message.text)
    if token:
        return {
            "id": message.id,
            "author": message.author,
            "kind": "sticker",
            "text": "",
            "sticker_token": token,
            "sticker_url": url_for("get_sticker_image", token=token),
            "time": message.created_at.strftime("%H:%M"),
        }
    return {
        "id": message.id,
        "author": message.author,
        "kind": "text",
        "text": message.text,
        "time": message.created_at.strftime("%H:%M"),
    }


def send_room_push(room_code: str, author: str, body: str, sender_device_id: str = "") -> None:
    """
    Send a push to all subscribed devices in the room except the device that
    sent the message. Dead subscriptions are removed automatically.
    """
    try:
        _public, private_key = get_or_create_vapid_keys()
    except Exception:
        db.session.rollback()
        return

    subscriptions = PushSubscription.query.filter_by(room_code=room_code).all()
    if not subscriptions:
        return

    payload = json.dumps(
        {
            "title": f"{author} • Nossa Sala",
            "body": body[:180],
            "url": url_for("room", code=room_code),
            "tag": f"room-{room_code}",
        },
        ensure_ascii=False,
    )

    dead_ids = []
    subject = os.getenv("VAPID_SUBJECT", "mailto:noreply@example.com")

    for subscription in subscriptions:
        if sender_device_id and subscription.device_id == sender_device_id:
            continue

        info = {
            "endpoint": subscription.endpoint,
            "keys": {
                "p256dh": subscription.p256dh,
                "auth": subscription.auth,
            },
        }

        try:
            webpush(
                subscription_info=info,
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": subject},
                ttl=300,
                timeout=6,
            )
        except WebPushException as exc:
            response = getattr(exc, "response", None)
            if response is not None and getattr(response, "status_code", None) in (404, 410):
                dead_ids.append(subscription.id)
        except Exception:
            # Chat must keep working even if a push provider is temporarily unavailable.
            continue

    if dead_ids:
        PushSubscription.query.filter(PushSubscription.id.in_(dead_ids)).delete(
            synchronize_session=False
        )
        db.session.commit()


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
    return jsonify([serialize_message(message) for message in messages])


@app.post("/api/messages/<code>")
def send_message(code):
    if not Room.query.filter_by(code=code).first():
        return jsonify({"error": "Sala não encontrada"}), 404

    data = request.get_json(silent=True) or {}
    author = (data.get("author") or "").strip()[:40]
    message_text = (data.get("text") or "").strip()[:2000]
    sender_device_id = (data.get("device_id") or "").strip()[:80]

    if not author or not message_text:
        return jsonify({"error": "Nome e mensagem são obrigatórios"}), 400

    # Prevent a normal typed message from being interpreted as an internal sticker marker.
    if message_text.startswith(STICKER_PREFIX):
        message_text = " " + message_text

    message = Message(room_code=code, author=author, text=message_text)
    db.session.add(message)
    db.session.commit()

    send_room_push(code, author, message_text, sender_device_id)
    return jsonify(serialize_message(message)), 201


@app.get("/api/stickers/<code>")
def list_stickers(code):
    if not Room.query.filter_by(code=code).first():
        return jsonify({"error": "Sala não encontrada"}), 404

    stickers = (
        Sticker.query.filter_by(room_code=code)
        .order_by(Sticker.created_at.desc())
        .limit(100)
        .all()
    )
    return jsonify([
        {
            "token": sticker.token,
            "owner": sticker.owner,
            "url": url_for("get_sticker_image", token=sticker.token),
        }
        for sticker in stickers
    ])


@app.post("/api/stickers/<code>")
def save_sticker(code):
    if not Room.query.filter_by(code=code).first():
        return jsonify({"error": "Sala não encontrada"}), 404

    owner = (request.form.get("owner") or "").strip()[:40]
    image = request.files.get("image")

    if not owner or image is None:
        return jsonify({"error": "Nome e imagem são obrigatórios"}), 400

    mime_type = (image.mimetype or "").lower()
    if mime_type not in {"image/webp", "image/png", "image/jpeg"}:
        return jsonify({"error": "Formato de imagem não aceito"}), 400

    image_data = image.read(MAX_STICKER_BYTES + 1)
    if not image_data:
        return jsonify({"error": "A figurinha está vazia"}), 400
    if len(image_data) > MAX_STICKER_BYTES:
        return jsonify({"error": "A figurinha ultrapassou o limite de 1,5 MB"}), 413

    token = secrets.token_urlsafe(14).replace("-", "").replace("_", "")
    sticker = Sticker(
        token=token,
        room_code=code,
        owner=owner,
        mime_type=mime_type,
        image_data=image_data,
    )
    db.session.add(sticker)
    db.session.commit()

    return jsonify(
        {
            "token": sticker.token,
            "owner": sticker.owner,
            "url": url_for("get_sticker_image", token=sticker.token),
        }
    ), 201


@app.delete("/api/stickers/<code>/<token>")
def delete_sticker(code, token):
    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()[:40]

    sticker = Sticker.query.filter_by(token=token, room_code=code).first()
    if not sticker:
        return jsonify({"error": "Figurinha não encontrada"}), 404
    if not owner or sticker.owner != owner:
        return jsonify({"error": "Somente quem criou pode excluir"}), 403

    db.session.delete(sticker)
    db.session.commit()
    return {"status": "ok"}


@app.get("/sticker/<token>")
def get_sticker_image(token):
    sticker = Sticker.query.filter_by(token=token).first()
    if not sticker:
        return "Figurinha não encontrada", 404

    response = Response(sticker.image_data, mimetype=sticker.mime_type)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@app.post("/api/sticker-message/<code>")
def send_sticker_message(code):
    if not Room.query.filter_by(code=code).first():
        return jsonify({"error": "Sala não encontrada"}), 404

    data = request.get_json(silent=True) or {}
    author = (data.get("author") or "").strip()[:40]
    token = (data.get("token") or "").strip()[:40]
    sender_device_id = (data.get("device_id") or "").strip()[:80]

    sticker = Sticker.query.filter_by(token=token, room_code=code).first()
    if not author or not sticker:
        return jsonify({"error": "Figurinha inválida"}), 400

    message = Message(
        room_code=code,
        author=author,
        text=f"{STICKER_PREFIX}{sticker.token}",
    )
    db.session.add(message)
    db.session.commit()

    send_room_push(code, author, "enviou uma figurinha 🖼️", sender_device_id)
    return jsonify(serialize_message(message)), 201


@app.get("/api/push/public-key")
def push_public_key():
    public_key, _private_key = get_or_create_vapid_keys()
    return jsonify({"publicKey": public_key})


@app.post("/api/push/subscribe/<code>")
def push_subscribe(code):
    if not Room.query.filter_by(code=code).first():
        return jsonify({"error": "Sala não encontrada"}), 404

    data = request.get_json(silent=True) or {}
    owner = (data.get("owner") or "").strip()[:40]
    device_id = (data.get("device_id") or "").strip()[:80]
    subscription = data.get("subscription") or {}
    endpoint = (subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()

    if not all([owner, device_id, endpoint, p256dh, auth]):
        return jsonify({"error": "Inscrição de notificação incompleta"}), 400

    existing = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if existing:
        existing.room_code = code
        existing.owner = owner
        existing.device_id = device_id
        existing.p256dh = p256dh
        existing.auth = auth
        existing.updated_at = utcnow()
    else:
        db.session.add(
            PushSubscription(
                room_code=code,
                owner=owner,
                device_id=device_id,
                endpoint=endpoint,
                p256dh=p256dh,
                auth=auth,
                updated_at=utcnow(),
            )
        )

    db.session.commit()
    return {"status": "ok"}


@app.post("/api/push/unsubscribe")
def push_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    if not endpoint:
        return jsonify({"error": "Endpoint ausente"}), 400

    PushSubscription.query.filter_by(endpoint=endpoint).delete()
    db.session.commit()
    return {"status": "ok"}


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(app.static_folder, "manifest.webmanifest")


@app.get("/sw.js")
def service_worker():
    response = send_from_directory(app.static_folder, "sw.js")
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/health")
def health():
    try:
        db.session.execute(text("SELECT 1"))
        return {"status": "ok"}, 200
    except Exception:
        db.session.rollback()
        return {"status": "error"}, 503


@app.errorhandler(413)
def too_large(_error):
    return jsonify({"error": "Arquivo muito grande"}), 413


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)

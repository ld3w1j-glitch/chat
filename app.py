from __future__ import annotations

import base64
import json
import os
import re
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, text
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pywebpush import WebPushException, webpush


STICKER_PREFIX = "__STICKER__:"
MAX_STICKER_BYTES = 1_500_000
ONLINE_SECONDS = 75
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,40}$")


def utcnow():
    return datetime.now(timezone.utc)


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return "sqlite:///chat.db"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def b64url_no_padding(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def normalize_username(value: str) -> str:
    return (value or "").strip().lower()


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    default_country = re.sub(r"\D", "", os.getenv("DEFAULT_COUNTRY_CODE", "55"))
    if len(digits) in (10, 11) and default_country:
        digits = default_country + digits
    return digits[:20]


app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(
    SQLALCHEMY_DATABASE_URI=get_database_url(),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SQLALCHEMY_ENGINE_OPTIONS={"pool_pre_ping": True},
    MAX_CONTENT_LENGTH=3 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

db = SQLAlchemy(app)


# Tabelas antigas são mantidas para compatibilidade com bancos já existentes.
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


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    whatsapp = db.Column(db.String(30), nullable=False, default="")
    email = db.Column(db.String(120), nullable=False, default="")
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    last_seen = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class RegistrationRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    desired_username = db.Column(db.String(40), nullable=False, index=True)
    whatsapp = db.Column(db.String(30), nullable=False)
    email = db.Column(db.String(120), nullable=False, default="")
    password_hash = db.Column(db.String(255), nullable=False)
    note = db.Column(db.Text, nullable=False, default="")
    whatsapp_opt_in = db.Column(db.Boolean, nullable=False, default=True)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    whatsapp_status = db.Column(db.Text, nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    processed_at = db.Column(db.DateTime(timezone=True), nullable=True)
    processed_by = db.Column(db.Integer, nullable=True)


class PrivateConversation(db.Model):
    __table_args__ = (
        UniqueConstraint("user_a_id", "user_b_id", name="uq_private_pair"),
    )
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    user_a_id = db.Column(db.Integer, nullable=False, index=True)
    user_b_id = db.Column(db.Integer, nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class CallInvite(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, nullable=False, index=True)
    recipient_id = db.Column(db.Integer, nullable=False, index=True)
    conversation_id = db.Column(db.Integer, nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default="pending", index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    responded_at = db.Column(db.DateTime(timezone=True), nullable=True)


class UserPushSubscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    device_id = db.Column(db.String(80), nullable=False, index=True)
    endpoint = db.Column(db.Text, nullable=False, unique=True)
    p256dh = db.Column(db.Text, nullable=False)
    auth = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class MessageReply(db.Model):
    __table_args__ = (UniqueConstraint("message_id", name="uq_message_reply"),)
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, nullable=False, index=True)
    replied_to_message_id = db.Column(db.Integer, nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class MessageEvent(db.Model):
    """Histórico append-only de edição/exclusão.

    Usar uma tabela separada evita migrations destrutivas em bancos PostgreSQL
    que já possuem a tabela Message criada por versões anteriores.
    """
    id = db.Column(db.Integer, primary_key=True)
    message_id = db.Column(db.Integer, nullable=False, index=True)
    event_type = db.Column(db.String(20), nullable=False, index=True)  # edit | delete
    text = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


class ConversationRead(db.Model):
    __table_args__ = (UniqueConstraint("conversation_id", "user_id", name="uq_conversation_read_user"),)
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, nullable=False, index=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    last_read_message_id = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


with app.app_context():
    db.create_all()

    # Segredo de sessão persistente no PostgreSQL, evitando logout em redeploy.
    secret_setting = db.session.get(AppSetting, "session_secret")
    if not secret_setting:
        secret_setting = AppSetting(key="session_secret", value=secrets.token_hex(48))
        db.session.add(secret_setting)
        db.session.commit()
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "").strip() or secret_setting.value

    # Criação opcional da conta administradora via variáveis do Railway.
    admin_exists = User.query.filter_by(is_admin=True).first()
    bootstrap_username = normalize_username(os.getenv("ADMIN_USERNAME", ""))
    bootstrap_password = os.getenv("ADMIN_PASSWORD", "")
    if not admin_exists and bootstrap_username and bootstrap_password:
        db.session.add(
            User(
                username=bootstrap_username,
                full_name=os.getenv("ADMIN_FULL_NAME", "Administrador").strip() or "Administrador",
                password_hash=generate_password_hash(bootstrap_password),
                whatsapp=normalize_phone(os.getenv("ADMIN_WHATSAPP", "")),
                email=os.getenv("ADMIN_EMAIL", "").strip()[:120],
                is_admin=True,
                active=True,
            )
        )
        db.session.commit()


def get_or_create_vapid_keys() -> tuple[str, str]:
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


def current_user() -> User | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    user = db.session.get(User, user_id)
    if not user or not user.active:
        session.clear()
        return None
    return user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            if request.path.startswith("/api/"):
                return jsonify({"error": "Faça login novamente."}), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("login", next=request.path))
        if not user.is_admin:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Acesso administrativo necessário."}), 403
            return "Acesso negado", 403
        return view(*args, **kwargs)
    return wrapped


def touch_presence(user: User | None = None):
    user = user or current_user()
    if not user:
        return
    user.last_seen = utcnow()
    db.session.commit()


def is_online(user: User) -> bool:
    if not user.last_seen:
        return False
    seen = user.last_seen
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return seen >= utcnow() - timedelta(seconds=ONLINE_SECONDS)


def pair_ids(a: int, b: int) -> tuple[int, int]:
    return (a, b) if a < b else (b, a)


def new_room_code() -> str:
    while True:
        code = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
        if not PrivateConversation.query.filter_by(code=code).first() and not Room.query.filter_by(code=code).first():
            return code


def get_or_create_conversation(user1_id: int, user2_id: int) -> PrivateConversation:
    a, b = pair_ids(user1_id, user2_id)
    conversation = PrivateConversation.query.filter_by(user_a_id=a, user_b_id=b).first()
    if conversation:
        return conversation
    code = new_room_code()
    conversation = PrivateConversation(code=code, user_a_id=a, user_b_id=b)
    db.session.add(conversation)
    # Mantém uma Room correspondente para compatibilidade com dados antigos.
    if not Room.query.filter_by(code=code).first():
        db.session.add(Room(code=code))
    db.session.commit()
    return conversation


def conversation_for_user(code: str, user: User) -> PrivateConversation | None:
    conversation = PrivateConversation.query.filter_by(code=code).first()
    if not conversation:
        return None
    if user.id not in (conversation.user_a_id, conversation.user_b_id):
        return None
    return conversation


def partner_for(conversation: PrivateConversation, user: User) -> User | None:
    partner_id = conversation.user_b_id if conversation.user_a_id == user.id else conversation.user_a_id
    return db.session.get(User, partner_id)


def sticker_token_from_message(message_text: str) -> str | None:
    if message_text.startswith(STICKER_PREFIX):
        return message_text[len(STICKER_PREFIX):].strip()
    return None


def latest_message_event(message: Message) -> MessageEvent | None:
    return (
        MessageEvent.query
        .filter_by(message_id=message.id)
        .order_by(MessageEvent.id.desc())
        .first()
    )


def message_effective_state(message: Message) -> dict:
    event = latest_message_event(message)
    deleted = bool(event and event.event_type == "delete")
    edited = bool(event and event.event_type == "edit")
    effective_text = event.text if edited else message.text
    return {
        "deleted": deleted,
        "edited": edited,
        "text": effective_text or "",
        "event": event,
    }


def message_preview(message: Message) -> dict:
    state = message_effective_state(message)
    if state["deleted"]:
        return {"id": message.id, "author": message.author, "kind": "deleted", "text": "Mensagem apagada"}
    token = sticker_token_from_message(state["text"])
    if token:
        return {"id": message.id, "author": message.author, "kind": "sticker", "text": "🖼️ Figurinha"}
    text_value = state["text"].strip().replace("\n", " ")
    if len(text_value) > 180:
        text_value = text_value[:177] + "..."
    return {"id": message.id, "author": message.author, "kind": "text", "text": text_value}


def reply_data_for(message: Message) -> dict | None:
    relation = MessageReply.query.filter_by(message_id=message.id).first()
    if not relation:
        return None
    original = db.session.get(Message, relation.replied_to_message_id)
    if not original or original.room_code != message.room_code:
        return None
    return message_preview(original)


def serialize_message(message: Message) -> dict:
    state = message_effective_state(message)
    data = {
        "id": message.id,
        "author": message.author,
        "time": message.created_at.strftime("%H:%M"),
        "reply": reply_data_for(message),
        "edited": state["edited"],
        "deleted": state["deleted"],
    }
    if state["deleted"]:
        data.update({"kind": "deleted", "text": "Mensagem apagada"})
        return data

    token = sticker_token_from_message(state["text"])
    if token:
        data.update({
            "kind": "sticker",
            "text": "",
            "sticker_token": token,
            "sticker_url": url_for("get_sticker_image", token=token),
        })
    else:
        data.update({"kind": "text", "text": state["text"]})
    return data


def conversation_read_marker(conversation_id: int, user_id: int) -> int:
    marker = ConversationRead.query.filter_by(conversation_id=conversation_id, user_id=user_id).first()
    return marker.last_read_message_id if marker else 0


def validate_reply_target(room_code: str, reply_to_id) -> Message | None:
    if reply_to_id in (None, "", 0, "0"):
        return None
    try:
        reply_id = int(reply_to_id)
    except (TypeError, ValueError):
        raise ValueError("Mensagem de resposta inválida.")
    target = db.session.get(Message, reply_id)
    if not target or target.room_code != room_code:
        raise ValueError("A mensagem respondida não pertence a esta conversa.")
    return target


def send_user_push(user_id: int, title: str, body: str, target_url: str, tag: str, sender_device_id: str = ""):
    try:
        _public, private_key = get_or_create_vapid_keys()
    except Exception:
        db.session.rollback()
        return

    subscriptions = UserPushSubscription.query.filter_by(user_id=user_id).all()
    if not subscriptions:
        return

    payload = json.dumps(
        {"title": title, "body": body[:180], "url": target_url, "tag": tag},
        ensure_ascii=False,
    )
    dead_ids = []
    subject = os.getenv("VAPID_SUBJECT", "mailto:noreply@example.com")

    for subscription in subscriptions:
        if sender_device_id and subscription.device_id == sender_device_id:
            continue
        info = {
            "endpoint": subscription.endpoint,
            "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
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
            continue

    if dead_ids:
        UserPushSubscription.query.filter(UserPushSubscription.id.in_(dead_ids)).delete(synchronize_session=False)
        db.session.commit()


def whatsapp_configured() -> bool:
    return bool(os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip() and os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip())


def public_login_url() -> str:
    base = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/login"
    return url_for("login", _external=True)


def send_account_created_whatsapp(user: User) -> tuple[bool, str]:
    """Envia template do WhatsApp Cloud API. O template esperado possui 3 parâmetros no corpo:
    nome, usuário e URL de login.
    """
    if not user.whatsapp:
        return False, "Usuário sem WhatsApp cadastrado."
    token = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    phone_number_id = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    if not token or not phone_number_id:
        return False, "WhatsApp Cloud API ainda não configurada no Railway."

    graph_version = os.getenv("WHATSAPP_GRAPH_VERSION", "v26.0").strip() or "v26.0"
    template_name = os.getenv("WHATSAPP_TEMPLATE_NAME", "conta_criada").strip() or "conta_criada"
    template_lang = os.getenv("WHATSAPP_TEMPLATE_LANG", "pt_BR").strip() or "pt_BR"
    endpoint = f"https://graph.facebook.com/{graph_version}/{phone_number_id}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": user.whatsapp,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": template_lang},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": user.full_name},
                        {"type": "text", "text": user.username},
                        {"type": "text", "text": public_login_url()},
                    ],
                }
            ],
        },
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            result = json.loads(response.read().decode("utf-8"))
        message_id = ((result.get("messages") or [{}])[0]).get("id", "")
        return True, f"Mensagem enviada pelo WhatsApp. ID: {message_id or 'confirmado'}"
    except urllib.error.HTTPError as exc:
        try:
            details = exc.read().decode("utf-8")[:500]
        except Exception:
            details = str(exc)
        return False, f"Falha WhatsApp HTTP {exc.code}: {details}"
    except Exception as exc:
        return False, f"Falha WhatsApp: {exc}"


@app.route("/")
def index():
    user = current_user()
    if user:
        return redirect(url_for("admin_dashboard" if user.is_admin else "dashboard"))
    if not User.query.filter_by(is_admin=True).first():
        return redirect(url_for("setup_admin"))
    return render_template("index.html")


@app.route("/setup-admin", methods=["GET", "POST"])
def setup_admin():
    if User.query.filter_by(is_admin=True).first():
        return redirect(url_for("login"))
    if request.method == "POST":
        username = normalize_username(request.form.get("username", ""))
        full_name = (request.form.get("full_name") or "Administrador").strip()[:120]
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        if not USERNAME_RE.fullmatch(username):
            flash("Use de 3 a 40 caracteres: letras, números, ponto, hífen ou sublinhado.", "error")
        elif len(password) < 8:
            flash("A senha precisa ter pelo menos 8 caracteres.", "error")
        elif password != confirm:
            flash("As senhas não coincidem.", "error")
        else:
            admin = User(
                username=username,
                full_name=full_name,
                password_hash=generate_password_hash(password),
                whatsapp=normalize_phone(request.form.get("whatsapp", "")),
                email=(request.form.get("email") or "").strip()[:120],
                is_admin=True,
                active=True,
            )
            db.session.add(admin)
            db.session.commit()
            flash("Conta administradora criada. Faça login.", "success")
            return redirect(url_for("login"))
    return render_template("setup_admin.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        username = normalize_username(request.form.get("username", ""))
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Usuário ou senha incorretos.", "error")
        elif not user.active:
            flash("Esta conta está desativada. Procure o administrador.", "error")
        else:
            session.clear()
            session["user_id"] = user.id
            session.permanent = True
            touch_presence(user)
            next_url = request.args.get("next", "")
            if next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(url_for("admin_dashboard" if user.is_admin else "dashboard"))
    return render_template("login.html")


@app.post("/logout")
@login_required
def logout():
    user = current_user()
    if user:
        user.last_seen = None
        db.session.commit()
    session.clear()
    return redirect(url_for("login"))


@app.route("/solicitar-conta", methods=["GET", "POST"])
def request_account():
    if request.method == "POST":
        full_name = (request.form.get("full_name") or "").strip()[:120]
        username = normalize_username(request.form.get("username", ""))
        whatsapp = normalize_phone(request.form.get("whatsapp", ""))
        email = (request.form.get("email") or "").strip()[:120]
        note = (request.form.get("note") or "").strip()[:1000]
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        opt_in = request.form.get("whatsapp_opt_in") == "on"

        error = None
        if not full_name:
            error = "Informe seu nome completo."
        elif not USERNAME_RE.fullmatch(username):
            error = "O usuário deve ter de 3 a 40 caracteres: letras, números, ponto, hífen ou sublinhado."
        elif User.query.filter_by(username=username).first():
            error = "Esse nome de usuário já existe."
        elif RegistrationRequest.query.filter_by(desired_username=username, status="pending").first():
            error = "Já existe uma solicitação pendente para esse usuário."
        elif len(whatsapp) < 10:
            error = "Informe um número de WhatsApp válido com DDD."
        elif len(password) < 8:
            error = "A senha precisa ter pelo menos 8 caracteres."
        elif password != confirm:
            error = "As senhas não coincidem."
        elif not opt_in:
            error = "Autorize o aviso pelo WhatsApp para continuar."

        if error:
            flash(error, "error")
        else:
            req = RegistrationRequest(
                full_name=full_name,
                desired_username=username,
                whatsapp=whatsapp,
                email=email,
                password_hash=generate_password_hash(password),
                note=note,
                whatsapp_opt_in=opt_in,
                status="pending",
            )
            db.session.add(req)
            db.session.commit()
            for admin in User.query.filter_by(is_admin=True, active=True).all():
                send_user_push(
                    admin.id,
                    "Nossa Sala • Nova conta",
                    f"{full_name} enviou uma solicitação de conta.",
                    url_for("admin_dashboard"),
                    f"account-request-{req.id}",
                )
            flash("Solicitação enviada ao administrador. Quando for aprovada, você receberá o aviso no WhatsApp.", "success")
            return redirect(url_for("request_account"))
    return render_template("request_account.html")


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    touch_presence(user)
    return render_template("dashboard.html", user=user)


@app.route("/admin")
@admin_required
def admin_dashboard():
    user = current_user()
    touch_presence(user)
    pending = RegistrationRequest.query.filter_by(status="pending").order_by(RegistrationRequest.created_at.asc()).all()
    recent = RegistrationRequest.query.filter(RegistrationRequest.status != "pending").order_by(RegistrationRequest.processed_at.desc()).limit(20).all()
    users = User.query.order_by(User.is_admin.desc(), User.full_name.asc()).all()
    return render_template(
        "admin.html",
        user=user,
        pending=pending,
        recent=recent,
        users=users,
        whatsapp_ready=whatsapp_configured(),
    )


@app.post("/admin/requests/<int:req_id>/approve")
@admin_required
def approve_request(req_id):
    admin = current_user()
    reg = db.session.get(RegistrationRequest, req_id)
    if not reg or reg.status != "pending":
        flash("Solicitação não encontrada ou já processada.", "error")
        return redirect(url_for("admin_dashboard"))
    if User.query.filter_by(username=reg.desired_username).first():
        flash("O usuário solicitado já foi criado.", "error")
        return redirect(url_for("admin_dashboard"))

    user = User(
        username=reg.desired_username,
        full_name=reg.full_name,
        password_hash=reg.password_hash,
        whatsapp=reg.whatsapp,
        email=reg.email,
        is_admin=False,
        active=True,
    )
    db.session.add(user)
    db.session.flush()
    reg.status = "approved"
    reg.processed_at = utcnow()
    reg.processed_by = admin.id
    db.session.commit()

    success, status = send_account_created_whatsapp(user)
    reg.whatsapp_status = status
    db.session.commit()
    if success:
        flash(f"Conta de {user.full_name} criada e WhatsApp enviado automaticamente.", "success")
    else:
        flash(f"Conta criada, mas o aviso automático não foi enviado: {status}", "error")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/requests/<int:req_id>/reject")
@admin_required
def reject_request(req_id):
    admin = current_user()
    reg = db.session.get(RegistrationRequest, req_id)
    if reg and reg.status == "pending":
        reg.status = "rejected"
        reg.processed_at = utcnow()
        reg.processed_by = admin.id
        db.session.commit()
        flash("Solicitação recusada.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/users/create")
@admin_required
def admin_create_user():
    full_name = (request.form.get("full_name") or "").strip()[:120]
    username = normalize_username(request.form.get("username", ""))
    whatsapp = normalize_phone(request.form.get("whatsapp", ""))
    email = (request.form.get("email") or "").strip()[:120]
    password = request.form.get("password", "")
    send_whatsapp = request.form.get("send_whatsapp") == "on"

    if not full_name or not USERNAME_RE.fullmatch(username) or len(password) < 8:
        flash("Preencha nome, usuário válido e senha com pelo menos 8 caracteres.", "error")
    elif User.query.filter_by(username=username).first():
        flash("Esse usuário já existe.", "error")
    else:
        user = User(
            username=username,
            full_name=full_name,
            password_hash=generate_password_hash(password),
            whatsapp=whatsapp,
            email=email,
            active=True,
        )
        db.session.add(user)
        db.session.commit()
        if send_whatsapp:
            success, status = send_account_created_whatsapp(user)
            flash(("Usuário criado e WhatsApp enviado." if success else f"Usuário criado, mas WhatsApp falhou: {status}"), "success" if success else "error")
        else:
            flash("Usuário criado.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/users/<int:user_id>/toggle")
@admin_required
def admin_toggle_user(user_id):
    admin = current_user()
    user = db.session.get(User, user_id)
    if user and user.id != admin.id:
        user.active = not user.active
        if not user.active:
            user.last_seen = None
        db.session.commit()
        flash(f"Conta {'ativada' if user.active else 'desativada'}.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/users/<int:user_id>/reset-password")
@admin_required
def admin_reset_password(user_id):
    user = db.session.get(User, user_id)
    new_password = request.form.get("new_password", "")
    if not user:
        flash("Usuário não encontrado.", "error")
    elif len(new_password) < 8:
        flash("A nova senha precisa ter pelo menos 8 caracteres.", "error")
    else:
        user.password_hash = generate_password_hash(new_password)
        db.session.commit()
        flash(f"Senha de {user.username} redefinida.", "success")
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/users/<int:user_id>/send-whatsapp")
@admin_required
def admin_send_whatsapp(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash("Usuário não encontrado.", "error")
    else:
        success, status = send_account_created_whatsapp(user)
        flash(status, "success" if success else "error")
    return redirect(url_for("admin_dashboard"))


@app.get("/api/dashboard")
@login_required
def api_dashboard():
    user = current_user()
    touch_presence(user)
    users = User.query.filter(User.active.is_(True), User.id != user.id, User.is_admin.is_(False)).order_by(User.full_name.asc()).all()
    online = [
        {"id": other.id, "username": other.username, "full_name": other.full_name}
        for other in users if is_online(other)
    ]

    conversations = PrivateConversation.query.filter(
        (PrivateConversation.user_a_id == user.id) | (PrivateConversation.user_b_id == user.id)
    ).order_by(PrivateConversation.created_at.desc()).all()
    conv_data = []
    for conv in conversations:
        partner = partner_for(conv, user)
        if not partner:
            continue
        last_message = Message.query.filter_by(room_code=conv.code).order_by(Message.id.desc()).first()
        conv_data.append({
            "code": conv.code,
            "partner_name": partner.full_name,
            "partner_username": partner.username,
            "online": is_online(partner),
            "last_message": ("Figurinha" if last_message and sticker_token_from_message(last_message.text) else (last_message.text[:80] if last_message else "Conversa ainda sem mensagens")),
        })

    invites = CallInvite.query.filter_by(recipient_id=user.id, status="pending").order_by(CallInvite.created_at.asc()).all()
    invite_data = []
    for invite in invites:
        sender = db.session.get(User, invite.sender_id)
        if sender:
            invite_data.append({
                "id": invite.id,
                "sender_name": sender.full_name,
                "sender_username": sender.username,
            })

    return jsonify({"online": online, "conversations": conv_data, "invites": invite_data})


@app.post("/api/presence/ping")
@login_required
def presence_ping():
    touch_presence(current_user())
    return {"status": "ok"}


@app.post("/api/call/<int:target_id>")
@login_required
def call_user(target_id):
    sender = current_user()
    target = db.session.get(User, target_id)
    if not target or not target.active or target.id == sender.id:
        return jsonify({"error": "Usuário indisponível."}), 404
    if not is_online(target):
        return jsonify({"error": "Esse usuário acabou de ficar offline."}), 409

    conversation = get_or_create_conversation(sender.id, target.id)
    # Evita vários convites idênticos em poucos segundos.
    recent = CallInvite.query.filter_by(sender_id=sender.id, recipient_id=target.id, status="pending").order_by(CallInvite.id.desc()).first()
    if recent:
        created = recent.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if created >= utcnow() - timedelta(seconds=45):
            return jsonify({"status": "already_pending"})

    invite = CallInvite(sender_id=sender.id, recipient_id=target.id, conversation_id=conversation.id, status="pending")
    db.session.add(invite)
    db.session.commit()

    send_user_push(
        target.id,
        "Nossa Sala • Chamando",
        f"{sender.full_name} está chamando você para conversar.",
        url_for("dashboard"),
        f"call-{invite.id}",
    )
    return jsonify({"status": "sent"})


@app.post("/api/invites/<int:invite_id>/accept")
@login_required
def accept_invite(invite_id):
    user = current_user()
    invite = db.session.get(CallInvite, invite_id)
    if not invite or invite.recipient_id != user.id or invite.status != "pending":
        return jsonify({"error": "Convite não está mais disponível."}), 404
    invite.status = "accepted"
    invite.responded_at = utcnow()
    db.session.commit()
    conversation = db.session.get(PrivateConversation, invite.conversation_id)
    sender = db.session.get(User, invite.sender_id)
    if sender:
        send_user_push(
            sender.id,
            "Nossa Sala",
            f"{user.full_name} aceitou seu chamado.",
            url_for("conversation", code=conversation.code),
            f"accept-{invite.id}",
        )
    return jsonify({"status": "ok", "url": url_for("conversation", code=conversation.code)})


@app.post("/api/invites/<int:invite_id>/reject")
@login_required
def reject_invite(invite_id):
    user = current_user()
    invite = db.session.get(CallInvite, invite_id)
    if not invite or invite.recipient_id != user.id or invite.status != "pending":
        return jsonify({"error": "Convite não está mais disponível."}), 404
    invite.status = "rejected"
    invite.responded_at = utcnow()
    db.session.commit()
    sender = db.session.get(User, invite.sender_id)
    if sender:
        send_user_push(sender.id, "Nossa Sala", f"{user.full_name} recusou o chamado.", url_for("dashboard"), f"reject-{invite.id}")
    return {"status": "ok"}


@app.route("/conversation/<code>")
@login_required
def conversation(code):
    user = current_user()
    conv = conversation_for_user(code, user)
    if not conv:
        return "Conversa não encontrada ou acesso negado.", 404
    touch_presence(user)
    partner = partner_for(conv, user)
    return render_template("room.html", code=code, user=user, partner=partner)


@app.get("/api/messages/<code>")
@login_required
def get_messages(code):
    user = current_user()
    conv = conversation_for_user(code, user)
    if not conv:
        return jsonify({"error": "Acesso negado."}), 403
    after = request.args.get("after", type=int, default=0)
    messages = Message.query.filter(Message.room_code == code, Message.id > after).order_by(Message.id.asc()).limit(200).all()
    partner = partner_for(conv, user)
    partner_last_read_id = conversation_read_marker(conv.id, partner.id) if partner else 0
    return jsonify({
        "messages": [serialize_message(message) for message in messages],
        "partner_last_read_id": partner_last_read_id,
    })


@app.post("/api/messages/<code>/read")
@login_required
def mark_messages_read(code):
    user = current_user()
    conv = conversation_for_user(code, user)
    if not conv:
        return jsonify({"error": "Acesso negado."}), 403
    data = request.get_json(silent=True) or {}
    try:
        requested_id = int(data.get("last_read_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"error": "Marcador de leitura inválido."}), 400
    if requested_id <= 0:
        return jsonify({"status": "ok", "last_read_id": 0})
    latest = Message.query.filter_by(room_code=code).order_by(Message.id.desc()).first()
    if not latest:
        return jsonify({"status": "ok", "last_read_id": 0})
    safe_id = min(requested_id, latest.id)
    marker = ConversationRead.query.filter_by(conversation_id=conv.id, user_id=user.id).first()
    if not marker:
        marker = ConversationRead(conversation_id=conv.id, user_id=user.id, last_read_message_id=safe_id, updated_at=utcnow())
        db.session.add(marker)
    elif safe_id > marker.last_read_message_id:
        marker.last_read_message_id = safe_id
        marker.updated_at = utcnow()
    user.last_seen = utcnow()
    db.session.commit()
    return jsonify({"status": "ok", "last_read_id": marker.last_read_message_id})


@app.post("/api/messages/<code>")
@login_required
def send_message(code):
    user = current_user()
    conv = conversation_for_user(code, user)
    if not conv:
        return jsonify({"error": "Acesso negado."}), 403
    data = request.get_json(silent=True) or {}
    message_text = (data.get("text") or "").strip()[:2000]
    sender_device_id = (data.get("device_id") or "").strip()[:80]
    if not message_text:
        return jsonify({"error": "Mensagem vazia."}), 400
    try:
        reply_target = validate_reply_target(code, data.get("reply_to_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if message_text.startswith(STICKER_PREFIX):
        message_text = " " + message_text
    message = Message(room_code=code, author=user.username, text=message_text)
    db.session.add(message)
    db.session.flush()
    if reply_target:
        db.session.add(MessageReply(message_id=message.id, replied_to_message_id=reply_target.id))
    user.last_seen = utcnow()
    db.session.commit()
    partner = partner_for(conv, user)
    if partner:
        push_text = f"↩ {message_text}" if reply_target else message_text
        send_user_push(partner.id, f"{user.full_name} • Nossa Sala", push_text, url_for("conversation", code=code), f"chat-{code}", sender_device_id)
    return jsonify(serialize_message(message)), 201


@app.get("/api/messages/<code>/changes")
@login_required
def message_changes(code):
    user = current_user()
    if not conversation_for_user(code, user):
        return jsonify({"error": "Acesso negado."}), 403
    after_event = request.args.get("after_event", type=int, default=0)
    events = (
        db.session.query(MessageEvent)
        .join(Message, Message.id == MessageEvent.message_id)
        .filter(Message.room_code == code, MessageEvent.id > after_event)
        .order_by(MessageEvent.id.asc())
        .limit(200)
        .all()
    )
    changes = []
    for event in events:
        message = db.session.get(Message, event.message_id)
        if message:
            changes.append({"event_id": event.id, "message": serialize_message(message)})
    return jsonify({"changes": changes})


@app.patch("/api/messages/<code>/<int:message_id>")
@login_required
def edit_message(code, message_id):
    user = current_user()
    if not conversation_for_user(code, user):
        return jsonify({"error": "Acesso negado."}), 403
    message = db.session.get(Message, message_id)
    if not message or message.room_code != code:
        return jsonify({"error": "Mensagem não encontrada."}), 404
    if message.author != user.username:
        return jsonify({"error": "Você só pode editar suas próprias mensagens."}), 403
    state = message_effective_state(message)
    if state["deleted"]:
        return jsonify({"error": "Uma mensagem apagada não pode ser editada."}), 409
    if sticker_token_from_message(state["text"]):
        return jsonify({"error": "Figurinhas não podem ser editadas. Você pode apagá-las e enviar outra."}), 400

    data = request.get_json(silent=True) or {}
    new_text = (data.get("text") or "").strip()[:2000]
    if not new_text:
        return jsonify({"error": "A mensagem não pode ficar vazia."}), 400
    if new_text.startswith(STICKER_PREFIX):
        new_text = " " + new_text

    db.session.add(MessageEvent(message_id=message.id, event_type="edit", text=new_text))
    user.last_seen = utcnow()
    db.session.commit()
    return jsonify(serialize_message(message))


@app.delete("/api/messages/<code>/<int:message_id>")
@login_required
def delete_message(code, message_id):
    user = current_user()
    if not conversation_for_user(code, user):
        return jsonify({"error": "Acesso negado."}), 403
    message = db.session.get(Message, message_id)
    if not message or message.room_code != code:
        return jsonify({"error": "Mensagem não encontrada."}), 404
    if message.author != user.username:
        return jsonify({"error": "Você só pode apagar suas próprias mensagens."}), 403
    state = message_effective_state(message)
    if not state["deleted"]:
        db.session.add(MessageEvent(message_id=message.id, event_type="delete", text=None))
        user.last_seen = utcnow()
        db.session.commit()
    return jsonify(serialize_message(message))


@app.get("/api/stickers/<code>")
@login_required
def list_stickers(code):
    user = current_user()
    if not conversation_for_user(code, user):
        return jsonify({"error": "Acesso negado."}), 403
    stickers = Sticker.query.filter_by(room_code=code).order_by(Sticker.created_at.desc()).limit(100).all()
    return jsonify([{"token": s.token, "owner": s.owner, "url": url_for("get_sticker_image", token=s.token)} for s in stickers])


@app.post("/api/stickers/<code>")
@login_required
def save_sticker(code):
    user = current_user()
    if not conversation_for_user(code, user):
        return jsonify({"error": "Acesso negado."}), 403
    image = request.files.get("image")
    if image is None:
        return jsonify({"error": "Imagem obrigatória."}), 400
    mime_type = (image.mimetype or "").lower()
    if mime_type not in {"image/webp", "image/png", "image/jpeg"}:
        return jsonify({"error": "Formato de imagem não aceito."}), 400
    image_data = image.read(MAX_STICKER_BYTES + 1)
    if not image_data:
        return jsonify({"error": "A figurinha está vazia."}), 400
    if len(image_data) > MAX_STICKER_BYTES:
        return jsonify({"error": "A figurinha ultrapassou 1,5 MB."}), 413
    token = secrets.token_urlsafe(14).replace("-", "").replace("_", "")
    sticker = Sticker(token=token, room_code=code, owner=user.username, mime_type=mime_type, image_data=image_data)
    db.session.add(sticker)
    db.session.commit()
    return jsonify({"token": sticker.token, "owner": sticker.owner, "url": url_for("get_sticker_image", token=sticker.token)}), 201


@app.delete("/api/stickers/<code>/<token>")
@login_required
def delete_sticker(code, token):
    user = current_user()
    if not conversation_for_user(code, user):
        return jsonify({"error": "Acesso negado."}), 403
    sticker = Sticker.query.filter_by(token=token, room_code=code).first()
    if not sticker:
        return jsonify({"error": "Figurinha não encontrada."}), 404
    if sticker.owner != user.username:
        return jsonify({"error": "Somente quem criou pode excluir."}), 403
    db.session.delete(sticker)
    db.session.commit()
    return {"status": "ok"}


@app.get("/sticker/<token>")
@login_required
def get_sticker_image(token):
    user = current_user()
    sticker = Sticker.query.filter_by(token=token).first()
    if not sticker or not conversation_for_user(sticker.room_code, user):
        return "Figurinha não encontrada", 404
    response = Response(sticker.image_data, mimetype=sticker.mime_type)
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


@app.post("/api/sticker-message/<code>")
@login_required
def send_sticker_message(code):
    user = current_user()
    conv = conversation_for_user(code, user)
    if not conv:
        return jsonify({"error": "Acesso negado."}), 403
    data = request.get_json(silent=True) or {}
    token = (data.get("token") or "").strip()[:40]
    sender_device_id = (data.get("device_id") or "").strip()[:80]
    sticker = Sticker.query.filter_by(token=token, room_code=code).first()
    if not sticker:
        return jsonify({"error": "Figurinha inválida."}), 400
    try:
        reply_target = validate_reply_target(code, data.get("reply_to_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    message = Message(room_code=code, author=user.username, text=f"{STICKER_PREFIX}{sticker.token}")
    db.session.add(message)
    db.session.flush()
    if reply_target:
        db.session.add(MessageReply(message_id=message.id, replied_to_message_id=reply_target.id))
    db.session.commit()
    partner = partner_for(conv, user)
    if partner:
        send_user_push(partner.id, f"{user.full_name} • Nossa Sala", "enviou uma figurinha 🖼️", url_for("conversation", code=code), f"chat-{code}", sender_device_id)
    return jsonify(serialize_message(message)), 201


@app.get("/api/push/public-key")
def push_public_key():
    public_key, _private_key = get_or_create_vapid_keys()
    return jsonify({"publicKey": public_key})


@app.post("/api/push/subscribe")
@login_required
def push_subscribe():
    user = current_user()
    data = request.get_json(silent=True) or {}
    device_id = (data.get("device_id") or "").strip()[:80]
    subscription = data.get("subscription") or {}
    endpoint = (subscription.get("endpoint") or "").strip()
    keys = subscription.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    if not all([device_id, endpoint, p256dh, auth]):
        return jsonify({"error": "Inscrição incompleta."}), 400
    existing = UserPushSubscription.query.filter_by(endpoint=endpoint).first()
    if existing:
        existing.user_id = user.id
        existing.device_id = device_id
        existing.p256dh = p256dh
        existing.auth = auth
        existing.updated_at = utcnow()
    else:
        db.session.add(UserPushSubscription(user_id=user.id, device_id=device_id, endpoint=endpoint, p256dh=p256dh, auth=auth, updated_at=utcnow()))
    db.session.commit()
    return {"status": "ok"}


@app.post("/api/push/unsubscribe")
@login_required
def push_unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    if endpoint:
        UserPushSubscription.query.filter_by(endpoint=endpoint, user_id=current_user().id).delete()
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
    return jsonify({"error": "Arquivo muito grande."}), 413


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", port=port, debug=debug)

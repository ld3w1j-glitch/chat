from __future__ import annotations

import base64
import io
import json
import os
import shutil
import zipfile
import re
import secrets
import urllib.error
import urllib.request
from pathlib import Path
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
    send_file,
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
ATTACHMENT_PREFIX = "__ATTACH__:"
MAX_STICKER_BYTES = 1_500_000
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_VIDEO_BYTES = 20 * 1024 * 1024
ONLINE_SECONDS = 75
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,40}$")
ALLOWED_DOCUMENT_MIMES = {
    "application/pdf",
    "text/plain",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/zip",
    "application/x-zip-compressed",
    "application/json",
}


def utcnow():
    return datetime.now(timezone.utc)


def running_on_railway() -> bool:
    """Detecta execução real em um deployment do Railway."""
    return bool(
        os.getenv("RAILWAY_PROJECT_ID")
        or os.getenv("RAILWAY_SERVICE_ID")
        or os.getenv("RAILWAY_DEPLOYMENT_ID")
    )


def get_database_url() -> str:
    """Escolhe armazenamento persistente automaticamente.

    Prioridade:
    1. DATABASE_URL -> PostgreSQL (recomendado para produção).
    2. Railway Volume -> SQLite gravado dentro do volume persistente.
    3. Execução local -> SQLite local para desenvolvimento.

    Isso evita que o chat volte a usar o filesystem efêmero do Railway.
    """
    url = os.getenv("DATABASE_URL", "").strip()

    if url:
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return url

    volume_path = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if running_on_railway() and volume_path:
        os.makedirs(volume_path, exist_ok=True)
        db_path = os.path.join(volume_path, "nossa_sala.db")
        return "sqlite:///" + db_path

    if running_on_railway():
        raise RuntimeError(
            "Nenhum armazenamento persistente foi encontrado. Configure DATABASE_URL "
            "para PostgreSQL ou anexe um Railway Volume ao serviço web."
        )

    return "sqlite:///chat.db"


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
    MAX_CONTENT_LENGTH=22 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)

db = SQLAlchemy(app)


FOFOCA_FRAME_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")


def fofoca_frames_dir() -> Path:
    """Pasta persistente dos modelos de moldura.

    No Railway, usa sempre o Volume anexado quando disponível. Isso mantém os
    arquivos importados entre deploys, independentemente de o banco ser
    PostgreSQL ou SQLite. Localmente, usa uma pasta dentro do projeto.
    """
    volume_path = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
    if volume_path:
        base = Path(volume_path) / "fofoca_frames"
    else:
        base = Path(app.root_path) / "fofoca_frames"
    base.mkdir(parents=True, exist_ok=True)
    return base


def packaged_fofoca_overlay_path(frame_id: str) -> Path:
    return Path(app.root_path) / "static" / "fofoca_default_overlays" / f"{frame_id}.png"


def default_fofoca_models() -> list[dict]:
    def model(model_id, name, headline_text):
        return {
            "schema_version": 1,
            "id": model_id,
            "name": name,
            "mode": "overlay",
            "canvas": {"width": 1080, "height": 1350, "background": "#0f1720"},
            "photo": {"x": 0, "y": 0, "width": 1080, "height": 760},
            "headline": {
                "x": 94, "y": 940, "max_width": 892, "line_height": 84,
                "max_lines": 4, "font_size": 70, "color": headline_text,
                "uppercase": True, "font_weight": 800
            },
            "overlay": "overlay.png"
        }
    return [
        model("plantao", "Plantão", "#111827"),
        model("manchete", "Manchete", "#0f172a"),
        model("urgente", "Urgente", "#18181b"),
    ]


def validate_fofoca_model(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("modelo.json precisa conter um objeto JSON.")
    model_id = str(data.get("id") or "").strip().lower()
    if not FOFOCA_FRAME_ID_RE.fullmatch(model_id):
        raise ValueError("O id da moldura deve usar apenas letras minúsculas, números, _ ou - (2 a 40 caracteres).")
    name = str(data.get("name") or "").strip()[:80]
    if not name:
        raise ValueError(f"A moldura {model_id} precisa ter um nome.")
    mode = str(data.get("mode") or "overlay").strip().lower()
    if mode not in {"generated", "overlay"}:
        raise ValueError(f"Modo inválido na moldura {model_id}. Use overlay.")

    canvas = data.get("canvas") or {}
    if int(canvas.get("width") or 0) != 1080 or int(canvas.get("height") or 0) != 1350:
        raise ValueError(f"A moldura {model_id} deve usar canvas 1080x1350.")

    photo = data.get("photo") or {}
    headline = data.get("headline") or {}
    required_numeric = [
        (photo, "x"), (photo, "y"), (photo, "width"), (photo, "height"),
        (headline, "x"), (headline, "y"), (headline, "max_width"),
        (headline, "line_height"), (headline, "max_lines"), (headline, "font_size"),
    ]
    for obj, key in required_numeric:
        try:
            value = float(obj.get(key))
        except (TypeError, ValueError):
            raise ValueError(f"Campo numérico ausente ou inválido: {model_id}.{key}")
        if value < 0:
            raise ValueError(f"Campo negativo não permitido: {model_id}.{key}")

    data = dict(data)
    data["id"] = model_id
    data["name"] = name
    data["mode"] = mode
    data["schema_version"] = 1
    return data


def ensure_default_fofoca_models() -> None:
    root = fofoca_frames_dir()
    for model in default_fofoca_models():
        folder = root / model["id"]
        config_path = folder / "modelo.json"
        folder.mkdir(parents=True, exist_ok=True)
        if not config_path.exists():
            config_path.write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
        overlay_path = folder / "overlay.png"
        packaged = packaged_fofoca_overlay_path(model["id"])
        if not overlay_path.exists() and packaged.exists():
            overlay_path.write_bytes(packaged.read_bytes())



def load_fofoca_models(include_invalid: bool = False) -> list[dict]:
    ensure_default_fofoca_models()
    models = []
    for config_path in sorted(fofoca_frames_dir().glob("*/modelo.json")):
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
            model = validate_fofoca_model(raw)
            overlay_name = str(model.get("overlay") or "").strip()
            overlay_path = config_path.parent / overlay_name if overlay_name else None
            if model["mode"] == "overlay":
                model["overlay_available"] = bool(overlay_path and overlay_path.exists() and overlay_path.is_file())
            else:
                model["overlay_available"] = False
            models.append(model)
        except Exception as exc:
            if include_invalid:
                models.append({"id": config_path.parent.name, "name": config_path.parent.name, "invalid": True, "error": str(exc)})
    return models


def public_fofoca_model(model: dict) -> dict:
    clean = json.loads(json.dumps(model, ensure_ascii=False))
    if clean.get("mode") == "overlay" and clean.get("overlay_available"):
        clean["overlay_url"] = url_for("fofoca_frame_overlay", frame_id=clean["id"])
    else:
        clean["overlay_url"] = None
    clean.pop("overlay_available", None)
    return clean


def _fofoca_pillow_fonts():
    from PIL import ImageFont

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    def pick(size: int, bold: bool = False):
        order = candidates if bold else list(reversed(candidates))
        for path in order:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
        return ImageFont.load_default()

    return {
        "headline": pick(64, bold=True),
        "label": pick(28, bold=True),
        "note": pick(22, bold=False),
        "big": pick(44, bold=True),
    }


def _wrap_text_for_draw(draw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    words = str(text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    line = words.pop(0)
    for word in words:
        test = f"{line} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        width = bbox[2] - bbox[0]
        if width <= max_width:
            line = test
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    lines = lines[:max_lines]
    if len(lines) == max_lines:
        last = lines[-1]
        while draw.textbbox((0, 0), last + "…", font=font)[2] > max_width and len(last) > 1:
            last = last[:-1]
        lines[-1] = last + ("…" if last != lines[-1] else "")
    return lines


def _render_fofoca_reference_assets(model: dict, overlay_path: Path | None) -> dict[str, bytes]:
    from PIL import Image, ImageDraw

    canvas = model.get("canvas") or {}
    width = int(canvas.get("width") or 1080)
    height = int(canvas.get("height") or 1350)
    photo = model.get("photo") or {"x": 0, "y": 0, "width": width, "height": 760}
    headline = model.get("headline") or {}
    fonts = _fofoca_pillow_fonts()

    if overlay_path and overlay_path.exists():
        overlay = Image.open(overlay_path).convert("RGBA")
    else:
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    # guia_areas.png
    guide = overlay.copy()
    gd = ImageDraw.Draw(guide, "RGBA")
    photo_box = [int(photo.get("x") or 0), int(photo.get("y") or 0), int(photo.get("x") or 0) + int(photo.get("width") or width), int(photo.get("y") or 0) + int(photo.get("height") or 760)]
    headline_box = [int(headline.get("x") or 90), int((headline.get("y") or 1000) - (headline.get("font_size") or 64)), int(headline.get("x") or 90) + int(headline.get("max_width") or 900), int((headline.get("y") or 1000) + (headline.get("line_height") or 78) * max(1, int(headline.get("max_lines") or 3)))]
    gd.rectangle(photo_box, outline=(67, 181, 129, 255), width=6, fill=(67, 181, 129, 40))
    gd.rectangle(headline_box, outline=(255, 179, 0, 255), width=6, fill=(255, 179, 0, 40))
    gd.rounded_rectangle((24, height - 144, width - 24, height - 24), radius=24, fill=(10, 16, 28, 220))
    gd.text((46, height - 130), "GUIA DE REFERÊNCIA", fill=(255,255,255,255), font=fonts["big"])
    gd.text((48, height - 74), "VERDE = área da foto • AMARELO = área da notícia", fill=(226,232,240,255), font=fonts["note"])
    guide_buf = io.BytesIO()
    guide.save(guide_buf, format="PNG")

    # noticia_exemplo.png
    example = Image.new("RGBA", (width, height), (15, 23, 32, 255))
    ex = ImageDraw.Draw(example, "RGBA")
    # photo placeholder bg
    px, py, pw, ph = int(photo.get("x") or 0), int(photo.get("y") or 0), int(photo.get("width") or width), int(photo.get("height") or 760)
    ex.rectangle((px, py, px + pw, py + ph), fill=(30, 41, 59, 255))
    # simple diagonal pattern
    step = 60
    for i in range(-ph, pw, step):
        ex.line((px + i, py, px + i + ph, py + ph), fill=(51, 65, 85, 180), width=14)
    ex.rounded_rectangle((px + 48, py + 48, px + pw - 48, py + ph - 48), radius=28, outline=(148, 163, 184, 180), width=4)
    ex.text((px + 56, py + 58), f"CATEGORIA: {model.get('name', 'Modelo').upper()}", fill=(255,255,255,245), font=fonts["label"])
    ex.text((px + 56, py + ph - 110), "SUBSTITUA ESTA ÁREA PELA SUA IMAGEM", fill=(255,255,255,230), font=fonts["big"])
    ex.text((px + 56, py + ph - 64), "Use o overlay.png como base no Photoshop e mantenha o modelo.json", fill=(226,232,240,255), font=fonts["note"])
    example.alpha_composite(overlay)
    ex = ImageDraw.Draw(example, "RGBA")
    sample_title = f"EXEMPLO DE NOTÍCIA DA CATEGORIA {str(model.get('name','')).upper()}"
    font = fonts["headline"]
    lines = _wrap_text_for_draw(ex, sample_title, font, int(headline.get("max_width") or 900), int(headline.get("max_lines") or 3))
    hx, hy = int(headline.get("x") or 90), int(headline.get("y") or 1000)
    line_height = int(headline.get("line_height") or 78)
    color = headline.get("color") or "#111827"
    for idx, line in enumerate(lines):
        ex.text((hx, hy + idx * line_height), line, fill=color, font=font)
    ex.rounded_rectangle((24, 24, 380, 88), radius=20, fill=(9, 14, 23, 190))
    ex.text((42, 42), f"ID: {model.get('id','modelo')}", fill=(255,255,255,255), font=fonts["label"])
    ex.text((42, 94), "Arquivo de referência para edição", fill=(226,232,240,255), font=fonts["note"])
    ex_buf = io.BytesIO()
    example.save(ex_buf, format="PNG")

    # card mini reference maybe duplicate overlay as reference file label image
    return {
        "guia_areas.png": guide_buf.getvalue(),
        "noticia_exemplo.png": ex_buf.getvalue(),
    }


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


class Attachment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(40), unique=True, nullable=False, index=True)
    room_code = db.Column(db.String(20), nullable=False, index=True)
    owner = db.Column(db.String(40), nullable=False)
    kind = db.Column(db.String(20), nullable=False, index=True)
    file_name = db.Column(db.String(255), nullable=False)
    mime_type = db.Column(db.String(120), nullable=False)
    file_size = db.Column(db.Integer, nullable=False, default=0)
    file_data = db.Column(db.LargeBinary, nullable=False)
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


class TypingState(db.Model):
    """Estado efêmero de digitação por conversa/usuário.

    O timestamp no PostgreSQL permite que o recurso continue correto mesmo se o
    Railway reiniciar ou se a aplicação passar a usar mais de um processo.
    Estados antigos expiram automaticamente pela comparação de tempo.
    """
    __table_args__ = (UniqueConstraint("conversation_id", "user_id", name="uq_typing_state_user"),)
    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(db.Integer, nullable=False, index=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    last_typing_at = db.Column(db.DateTime(timezone=True), default=utcnow, nullable=False)


with app.app_context():
    ensure_default_fofoca_models()
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


def attachment_token_from_message(message_text: str) -> str | None:
    if message_text.startswith(ATTACHMENT_PREFIX):
        return message_text[len(ATTACHMENT_PREFIX):].strip()
    return None


def attachment_label(kind: str, file_name: str = "") -> str:
    base = {
        "image": "🖼️ Imagem",
        "video": "🎞️ Vídeo",
        "document": "📎 Documento",
    }.get(kind or "", "📎 Anexo")
    return f"{base}: {file_name}" if file_name else base


def classify_upload(mime_type: str, file_name: str) -> tuple[str | None, int]:
    mime = (mime_type or "").lower().strip()
    suffix = Path((file_name or "").lower()).suffix
    if mime.startswith("image/"):
        return "image", MAX_IMAGE_BYTES
    if mime.startswith("video/"):
        return "video", MAX_VIDEO_BYTES
    if mime in ALLOWED_DOCUMENT_MIMES or suffix in {".pdf", ".txt", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".json"}:
        return "document", MAX_DOCUMENT_BYTES
    return None, 0


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
    attachment_token = attachment_token_from_message(state["text"])
    if attachment_token:
        attachment = Attachment.query.filter_by(token=attachment_token, room_code=message.room_code).first()
        if attachment:
            return {
                "id": message.id,
                "author": message.author,
                "kind": attachment.kind,
                "text": attachment_label(attachment.kind, attachment.file_name),
            }
        return {"id": message.id, "author": message.author, "kind": "document", "text": "📎 Anexo"}
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
        return data

    attachment_token = attachment_token_from_message(state["text"])
    if attachment_token:
        attachment = Attachment.query.filter_by(token=attachment_token, room_code=message.room_code).first()
        if attachment:
            data.update({
                "kind": attachment.kind,
                "text": attachment_label(attachment.kind, attachment.file_name),
                "attachment_token": attachment.token,
                "attachment_url": url_for("get_attachment_file", token=attachment.token),
                "attachment_name": attachment.file_name,
                "attachment_mime": attachment.mime_type,
                "attachment_size": attachment.file_size,
            })
            return data

    data.update({"kind": "text", "text": state["text"]})
    return data


def conversation_read_marker(conversation_id: int, user_id: int) -> int:
    marker = ConversationRead.query.filter_by(conversation_id=conversation_id, user_id=user_id).first()
    return marker.last_read_message_id if marker else 0


def user_is_typing(conversation_id: int, user_id: int, timeout_seconds: float = 4.5) -> bool:
    state = TypingState.query.filter_by(conversation_id=conversation_id, user_id=user_id).first()
    if not state or not state.last_typing_at:
        return False
    last = state.last_typing_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return last >= utcnow() - timedelta(seconds=timeout_seconds)


def clear_typing_state(conversation_id: int, user_id: int) -> None:
    state = TypingState.query.filter_by(conversation_id=conversation_id, user_id=user_id).first()
    if state:
        db.session.delete(state)


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
        fofoca_models=load_fofoca_models(include_invalid=True),
        fofoca_storage=str(fofoca_frames_dir()),
    )


@app.get("/admin/fofoca-modelos/exportar")
@admin_required
def admin_export_fofoca_models():
    ensure_default_fofoca_models()
    buffer = io.BytesIO()
    root = fofoca_frames_dir()
    guide = """MODELOS DE FOFOCA - NOSSA SALA

COMO CRIAR UMA NOVA MOLDURA
1. Extraia este ZIP.
2. Duplique uma pasta de modelo.
3. Troque o campo id e name dentro do modelo.json.
4. Para uma moldura criada no Photoshop/Illustrator, use mode = overlay.
5. Crie um overlay.png de 1080 x 1350 px na mesma pasta do modelo.json.
6. Deixe transparente a região onde a foto deverá aparecer.
7. Ajuste no modelo.json as coordenadas photo e headline.
8. Compacte novamente as pastas em um ZIP.
9. No painel Admin > Modelos de Fofoca, use Importar pacote.

IMPORTANTE
- A única informação editável pelo usuário no chat continua sendo a notícia/manchete.
- Modelos com o mesmo id são substituídos na importação.
- O arquivo overlay.png é obrigatório para cada moldura.
- Canvas suportado: 1080 x 1350 px.

CAMPOS PRINCIPAIS DO modelo.json
id: identificador único, ex.: revista_bairro
name: nome que aparecerá no menu
mode: overlay
photo: x, y, width, height da foto
headline: x, y, max_width, line_height, max_lines, font_size, color
"""
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("LEIA-ME_MODELOS.txt", guide)
        sample = {
            "schema_version": 1,
            "id": "minha_moldura",
            "name": "Minha moldura",
            "mode": "overlay",
            "canvas": {"width": 1080, "height": 1350, "background": "#0f1720"},
            "photo": {"x": 40, "y": 140, "width": 1000, "height": 720},
            "headline": {
                "x": 90, "y": 1010, "max_width": 900, "line_height": 78,
                "max_lines": 3, "font_size": 66, "color": "#ffffff",
                "uppercase": True, "font_weight": 800
            },
            "overlay": "overlay.png"
        }
        zf.writestr("EXEMPLO_PERSONALIZADO/modelo.exemplo.json", json.dumps(sample, ensure_ascii=False, indent=2))
        zf.writestr(
            "EXEMPLO_PERSONALIZADO/COMO_USAR.txt",
            "Renomeie modelo.exemplo.json para modelo.json, crie um overlay.png 1080x1350 e depois compacte a pasta para importar.\n"
        )
        for file_path in root.rglob("*"):
            if file_path.is_file():
                rel = file_path.relative_to(root)
                zf.write(file_path, str(rel))
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"modelos_fofoca_{datetime.now().strftime('%Y%m%d_%H%M')}.zip",
    )


@app.post("/admin/fofoca-modelos/importar")
@admin_required
def admin_import_fofoca_models():
    upload = request.files.get("package")
    if not upload or not upload.filename:
        flash("Selecione um arquivo ZIP com os modelos.", "error")
        return redirect(url_for("admin_dashboard"))
    if not upload.filename.lower().endswith(".zip"):
        flash("O pacote de modelos precisa ser um arquivo .zip.", "error")
        return redirect(url_for("admin_dashboard"))

    try:
        raw_zip = upload.read(20 * 1024 * 1024 + 1)
        if len(raw_zip) > 20 * 1024 * 1024:
            raise ValueError("O pacote ultrapassa o limite de 20 MB.")
        with zipfile.ZipFile(io.BytesIO(raw_zip), "r") as zf:
            infos = [i for i in zf.infolist() if not i.is_dir()]
            if len(infos) > 200:
                raise ValueError("O pacote possui arquivos demais.")
            total_uncompressed = sum(i.file_size for i in infos)
            if total_uncompressed > 35 * 1024 * 1024:
                raise ValueError("O conteúdo descompactado ultrapassa 35 MB.")

            model_entries = [i for i in infos if Path(i.filename).name.lower() == "modelo.json"]
            if not model_entries:
                raise ValueError("Nenhum modelo.json foi encontrado no ZIP.")

            imported = []
            root = fofoca_frames_dir()
            for info in model_entries:
                parent = Path(info.filename).parent
                try:
                    model_data = json.loads(zf.read(info).decode("utf-8"))
                except Exception as exc:
                    raise ValueError(f"JSON inválido em {info.filename}: {exc}")
                model = validate_fofoca_model(model_data)
                model_id = model["id"]

                overlay_bytes = None
                overlay_name = str(model.get("overlay") or "").strip()
                if model["mode"] == "overlay":
                    if not overlay_name:
                        overlay_name = "overlay.png"
                        model["overlay"] = overlay_name
                    if overlay_name != "overlay.png":
                        raise ValueError(f"A moldura {model_id} deve usar exatamente o arquivo overlay.png na própria pasta.")
                    target_name = str(parent / overlay_name).replace("\\", "/")
                    try:
                        overlay_info = zf.getinfo(target_name)
                    except KeyError:
                        raise ValueError(f"A moldura {model_id} usa mode=overlay, mas {overlay_name} não foi encontrado.")
                    if overlay_info.file_size > 8 * 1024 * 1024:
                        raise ValueError(f"O overlay da moldura {model_id} ultrapassa 8 MB.")
                    overlay_bytes = zf.read(overlay_info)

                target = root / model_id
                temp = root / f".{model_id}_import_{secrets.token_hex(4)}"
                if temp.exists():
                    shutil.rmtree(temp)
                temp.mkdir(parents=True, exist_ok=True)
                (temp / "modelo.json").write_text(json.dumps(model, ensure_ascii=False, indent=2), encoding="utf-8")
                if overlay_bytes is not None:
                    (temp / "overlay.png").write_bytes(overlay_bytes)

                # Preserva pequenos arquivos auxiliares do diretório do modelo.
                prefix = str(parent).replace("\\", "/").rstrip("/") + "/"
                for extra in infos:
                    normalized = extra.filename.replace("\\", "/")
                    if not normalized.startswith(prefix):
                        continue
                    basename = Path(normalized).name
                    if basename in {"modelo.json", "overlay.png"}:
                        continue
                    if basename.lower().endswith((".txt", ".md")) and extra.file_size <= 200_000:
                        (temp / basename).write_bytes(zf.read(extra))

                if target.exists():
                    shutil.rmtree(target)
                temp.rename(target)
                imported.append(model["name"])

        flash(f"Modelos importados com sucesso: {', '.join(imported)}.", "success")
    except zipfile.BadZipFile:
        flash("O arquivo enviado não é um ZIP válido.", "error")
    except Exception as exc:
        flash(f"Não foi possível importar os modelos: {exc}", "error")
    return redirect(url_for("admin_dashboard"))


@app.get("/api/fofoca-frames")
@login_required
def api_fofoca_frames():
    models = []
    for model in load_fofoca_models():
        # Modelo overlay sem PNG ainda é mantido fora do menu até ficar completo.
        if model.get("mode") == "overlay" and not model.get("overlay_available"):
            continue
        models.append(public_fofoca_model(model))
    return jsonify(models)


@app.get("/fofoca-frame/<frame_id>/overlay")
@login_required
def fofoca_frame_overlay(frame_id):
    frame_id = (frame_id or "").strip().lower()
    if not FOFOCA_FRAME_ID_RE.fullmatch(frame_id):
        return "Modelo inválido", 404
    folder = fofoca_frames_dir() / frame_id
    config_path = folder / "modelo.json"
    if not config_path.exists():
        return "Modelo não encontrado", 404
    try:
        model = validate_fofoca_model(json.loads(config_path.read_text(encoding="utf-8")))
    except Exception:
        return "Modelo inválido", 404
    overlay_name = Path(str(model.get("overlay") or "overlay.png")).name
    overlay_path = folder / overlay_name
    if not overlay_path.exists():
        return "Overlay não encontrado", 404
    response = send_from_directory(folder, overlay_name, mimetype="image/png")
    response.headers["Cache-Control"] = "private, max-age=300"
    return response


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
        "partner_typing": bool(partner and user_is_typing(conv.id, partner.id)),
    })


@app.post("/api/typing/<code>")
@login_required
def set_typing_state(code):
    user = current_user()
    conv = conversation_for_user(code, user)
    if not conv:
        return jsonify({"error": "Acesso negado."}), 403

    data = request.get_json(silent=True) or {}
    typing = bool(data.get("typing"))
    state = TypingState.query.filter_by(conversation_id=conv.id, user_id=user.id).first()

    if typing:
        if not state:
            state = TypingState(conversation_id=conv.id, user_id=user.id, last_typing_at=utcnow())
            db.session.add(state)
        else:
            state.last_typing_at = utcnow()
        user.last_seen = utcnow()
    elif state:
        db.session.delete(state)

    db.session.commit()
    return jsonify({"status": "ok", "typing": typing})


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
    if message_text.startswith((STICKER_PREFIX, ATTACHMENT_PREFIX)):
        message_text = " " + message_text
    message = Message(room_code=code, author=user.username, text=message_text)
    db.session.add(message)
    db.session.flush()
    if reply_target:
        db.session.add(MessageReply(message_id=message.id, replied_to_message_id=reply_target.id))
    clear_typing_state(conv.id, user.id)
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
    if new_text.startswith((STICKER_PREFIX, ATTACHMENT_PREFIX)):
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
    clear_typing_state(conv.id, user.id)
    user.last_seen = utcnow()
    db.session.commit()
    partner = partner_for(conv, user)
    if partner:
        send_user_push(partner.id, f"{user.full_name} • Nossa Sala", "enviou uma figurinha 🖼️", url_for("conversation", code=code), f"chat-{code}", sender_device_id)
    return jsonify(serialize_message(message)), 201


@app.post("/api/attachments/<code>")
@login_required
def send_attachment_message(code):
    user = current_user()
    conv = conversation_for_user(code, user)
    if not conv:
        return jsonify({"error": "Acesso negado."}), 403

    upload = request.files.get("file")
    if upload is None:
        return jsonify({"error": "Arquivo obrigatório."}), 400

    file_name = (upload.filename or "anexo").strip()[:255] or "anexo"
    kind, limit = classify_upload(upload.mimetype or "", file_name)
    if not kind:
        return jsonify({"error": "Tipo de arquivo não suportado. Envie documento, imagem ou vídeo."}), 400

    raw = upload.read(limit + 1)
    if not raw:
        return jsonify({"error": "O arquivo está vazio."}), 400
    if len(raw) > limit:
        max_mb = limit / (1024 * 1024)
        return jsonify({"error": f"O arquivo ultrapassou o limite de {max_mb:.0f} MB para este tipo."}), 413

    try:
        reply_target = validate_reply_target(code, request.form.get("reply_to_id"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    sender_device_id = (request.form.get("device_id") or "").strip()[:80]
    token = secrets.token_urlsafe(14).replace("-", "").replace("_", "")
    attachment = Attachment(
        token=token,
        room_code=code,
        owner=user.username,
        kind=kind,
        file_name=file_name,
        mime_type=(upload.mimetype or "application/octet-stream")[:120],
        file_size=len(raw),
        file_data=raw,
    )
    db.session.add(attachment)
    db.session.flush()

    message = Message(room_code=code, author=user.username, text=f"{ATTACHMENT_PREFIX}{attachment.token}")
    db.session.add(message)
    db.session.flush()
    if reply_target:
        db.session.add(MessageReply(message_id=message.id, replied_to_message_id=reply_target.id))
    clear_typing_state(conv.id, user.id)
    user.last_seen = utcnow()
    db.session.commit()

    partner = partner_for(conv, user)
    if partner:
        label = {"image": "enviou uma imagem 🖼️", "video": "enviou um vídeo 🎞️", "document": "enviou um documento 📎"}.get(kind, "enviou um anexo 📎")
        send_user_push(partner.id, f"{user.full_name} • Nossa Sala", label, url_for("conversation", code=code), f"chat-{code}", sender_device_id)
    return jsonify(serialize_message(message)), 201


@app.get("/attachment/<token>")
@login_required
def get_attachment_file(token):
    user = current_user()
    attachment = Attachment.query.filter_by(token=token).first()
    if not attachment or not conversation_for_user(attachment.room_code, user):
        return "Anexo não encontrado", 404
    response = Response(attachment.file_data, mimetype=attachment.mime_type or "application/octet-stream")
    disposition = "inline" if attachment.kind in {"image", "video"} else "attachment"
    safe_name = attachment.file_name.replace('\"', "")
    response.headers["Content-Disposition"] = f"{disposition}; filename=\"{safe_name}\""
    response.headers["Cache-Control"] = "private, max-age=86400"
    return response


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
        backend = db.engine.url.get_backend_name()
        volume_path = os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
        using_volume = bool(backend == "sqlite" and running_on_railway() and volume_path)
        storage = "postgresql" if backend == "postgresql" else ("railway-volume" if using_volume else "local-sqlite")
        return {
            "status": "ok",
            "database": backend,
            "storage": storage,
            "persistent": backend == "postgresql" or using_volume,
        }, 200
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

import os
from datetime import datetime, timedelta
import re
import secrets
from pathlib import Path

import psycopg2
from psycopg2.extras import DictCursor
from psycopg2 import OperationalError
from flask import (
    Flask,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
    flash,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

import moderation
import email_utils

try:
    from dotenv import load_dotenv
except ImportError:  # opcional em runtime
    load_dotenv = None


BASE_DIR = Path(__file__).resolve().parent
if load_dotenv is not None:
    # Carrega .env (preferencial) e, em ambiente acadêmico/local,
    # permite usar .env.example como fallback.
    load_dotenv(BASE_DIR / ".env", override=False)
    load_dotenv(BASE_DIR / ".env.example", override=False)

# Fallback: aceita arquivo .env/.env.example contendo apenas a URL em uma linha.
if not os.getenv("DATABASE_URL"):
    for env_path in (BASE_DIR / ".env", BASE_DIR / ".env.example"):
        if not env_path.exists():
            continue
        # Le com latin-1 para tolerar qualquer byte no Windows
        try:
            raw = env_path.read_text(encoding="utf-8").strip()
        except UnicodeDecodeError:
            raw = env_path.read_text(encoding="latin-1").strip()
        # Extrai a linha que contem a URL (suporta KEY=VALUE ou URL nua)
        _found = ""
        for _line in raw.splitlines():
            _line = _line.strip().strip('"').strip("'")
            if _line.startswith("DATABASE_URL="):
                _found = _line.split("=", 1)[1].strip().strip('"').strip("'")
                break
            if _line.startswith("postgresql://") or _line.startswith("postgres://"):
                _found = _line
                break
        if _found.startswith("postgresql://") or _found.startswith("postgres://"):
            # Garante que a URL e uma string ASCII pura
            os.environ["DATABASE_URL"] = _found.encode("ascii", errors="ignore").decode("ascii")
            break

DEFAULT_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:Morango@127.0.0.1:5432/concoop",
)

ALLOWED_ROLES = {
    "produtor",
    "servidor",
    "veterinario",
    "usuario",
    "vendedor",
    "loja",
}
PRODUCT_ROLES = {"produtor", "vendedor", "loja"}
SERVICE_ROLES = {"servidor"}
ROLE_LABELS = {
    "produtor": "Produtor Rural",
    "servidor": "Prestador de Serviços",
    "veterinario": "Veterinário",
    "usuario": "Usuário Comum",
    "vendedor": "Vendedor",
    "loja": "Loja",
}


def normalize_role(role: str | None) -> str | None:
    if role is None:
        return None
    normalized = str(role).strip().lower()
    return normalized if normalized in ALLOWED_ROLES else None


def can_create_product(role: str | None) -> bool:
    return normalize_role(role) in PRODUCT_ROLES


def can_create_service(role: str | None) -> bool:
    return normalize_role(role) in SERVICE_ROLES


class PostgresCompatConnection:
    """Conexão PostgreSQL com API simples para execute/commit/close."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, query, params=None):
        pg_query = query.replace("?", "%s")
        cur = self._conn.cursor(cursor_factory=DictCursor)
        cur.execute(pg_query, params or ())
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def _ascii_safe_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return text
    return text.encode("ascii", errors="ignore").decode("ascii")


def connect_db():
    # O psycopg2 no Windows le TODAS as variaveis de ambiente do sistema,
    # incluindo caminhos como C:\Users\Usuário que contem bytes nao-ASCII.
    # Solucao: parsear a URL manualmente, limpar as variaveis PG* do ambiente
    # antes da conexao e conectar por parametros nomeados, sem passar a DSN.
    from urllib.parse import urlparse, unquote
    parsed = urlparse(DEFAULT_DATABASE_URL)

    def _do_connect():
        pg_env_snapshot = {}
        for key in list(os.environ):
            if key.startswith("PG"):
                pg_env_snapshot[key] = os.environ[key]
                os.environ.pop(key, None)

        try:
            return psycopg2.connect(
                host=_ascii_safe_text(parsed.hostname or "127.0.0.1"),
                port=parsed.port or 5432,
                dbname=_ascii_safe_text((parsed.path or "/concoop").lstrip("/")),
                user=_ascii_safe_text(unquote(parsed.username or "postgres")),
                password=_ascii_safe_text(unquote(parsed.password or "postgres")),
            )
        finally:
            for key, value in pg_env_snapshot.items():
                os.environ[key] = value

    try:
        raw_conn = _do_connect()
        return PostgresCompatConnection(raw_conn)
    except OperationalError as exc:
        error_details = repr(exc)
        if exc.args:
            error_details += " | args=" + repr(exc.args)
        raise RuntimeError(
            "Nao foi possivel conectar ao PostgreSQL. "
            "Verifique se o servidor está rodando, se o DB existe e se a URL está correta. "
            "Exemplo: postgresql://agrolink:Morango@127.0.0.1:5432/concoop\n"
            f"Detalhes do erro: {error_details}"
        ) from exc

VALID_UFS = {
    "AC",
    "AL",
    "AP",
    "AM",
    "BA",
    "CE",
    "DF",
    "ES",
    "GO",
    "MA",
    "MT",
    "MS",
    "MG",
    "PA",
    "PB",
    "PR",
    "PE",
    "PI",
    "RJ",
    "RN",
    "RS",
    "RO",
    "RR",
    "SC",
    "SP",
    "SE",
    "TO",
}

UPLOAD_BASE = BASE_DIR / "static" / "uploads"
PROFILE_DIR = UPLOAD_BASE / "profiles"
PRODUCT_DIR = UPLOAD_BASE / "products"
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

# Tipos de alvo e motivos aceitos pelo sistema de denúncias.
REPORT_TARGET_TYPES = {"product", "service", "animal_report", "user"}
ALLOWED_REPORT_REASONS = {
    "Conteúdo inadequado",
    "Informação falsa ou enganosa",
    "Golpe ou fraude",
    "Spam ou propaganda",
    "Comportamento abusivo",
    "Outro",
}


def ensure_upload_dirs():
    """Garante que as pastas de upload existam."""
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    PRODUCT_DIR.mkdir(parents=True, exist_ok=True)


def allowed_image(filename: str) -> bool:
    """Verifica se a extensão do arquivo é de imagem permitida."""
    if "." not in filename:
        return False
    ext = filename.rsplit(".", 1)[1].lower()
    return ext in ALLOWED_IMAGE_EXTENSIONS


def save_image(file_storage, subfolder: str) -> str | None:
    """
    Salva uma imagem na pasta static/uploads/<subfolder> e
    retorna o caminho relativo para usar com url_for('static', filename=...).
    """
    if file_storage is None or file_storage.filename == "":
        return None

    filename = secure_filename(file_storage.filename)
    if not allowed_image(filename):
        return None

    target_dir = PROFILE_DIR if subfolder == "profiles" else PRODUCT_DIR
    ensure_upload_dirs()
    full_path = target_dir / filename

    # Evitar sobrescrever com mesmo nome
    base, ext = os.path.splitext(filename)
    counter = 1
    while full_path.exists():
        filename = f"{base}_{counter}{ext}"
        full_path = target_dir / filename
        counter += 1

    file_storage.save(full_path)
    # Caminho relativo dentro de static/
    rel_path = f"uploads/{subfolder}/{filename}"
    return rel_path


def _normalize_text(text: str) -> str:
    """Normaliza espaços e traços para facilitar a extração do CRMV."""
    if not isinstance(text, str):
        text = str(text)
    text = text.replace("–", "-").replace("—", "-")
    text = re.sub(r"\s+", " ", text.strip())
    return text


def extrair_crmv(texto: str):
    """
    Extrai UF e número de CRMV de um texto livre.
    Aceita formatos como:
      - CRMV-SC 12345
      - 12345/SC
      - Med. Vet. Fulano – CRMV-RS 9876/Z
    """
    texto = _normalize_text(texto)
    upper = texto.upper()

    padrao_prefixo = re.compile(
        r"CRMV\s*[-/]?\s*(?P<uf>[A-Z]{2})\s*(?P<num>\d{1,6})(?:\s*/\s*[A-Z0-9]+)?",
        re.IGNORECASE,
    )
    padrao_num_uf = re.compile(
        r"(?P<num>\d{1,6})\s*/\s*(?P<uf>[A-Z]{2})\b",
        re.IGNORECASE,
    )

    match = padrao_prefixo.search(upper)
    if not match:
        match = padrao_num_uf.search(upper)

    if not match:
        return {
            "valido": False,
            "mensagem": "Não foi possível identificar UF e número do CRMV.",
            "uf": None,
            "numero": None,
        }

    uf = match.group("uf").upper()
    numero = match.group("num")

    return {
        "valido": True,
        "mensagem": "Formato básico extraído com sucesso.",
        "uf": uf,
        "numero": numero,
    }


def validar_crmv_offline(texto: str):
    """
    Valida se um CRMV é plausível (apenas formato, sem consulta online).
    Regras:
      - UF válida do Brasil.
      - Número de 4 a 6 dígitos, aceitando zeros à esquerda, mas não todo zero.
    """
    extracao = extrair_crmv(texto)
    if not extracao["valido"]:
        return extracao

    uf = extracao["uf"]
    numero = extracao["numero"]

    if uf not in VALID_UFS:
        return {
            "valido": False,
            "mensagem": f"UF inválida para CRMV: {uf}.",
            "uf": uf,
            "numero": numero,
        }

    if not numero.isdigit():
        return {
            "valido": False,
            "mensagem": "Número de CRMV deve conter apenas dígitos.",
            "uf": uf,
            "numero": numero,
        }

    if not (4 <= len(numero) <= 6):
        return {
            "valido": False,
            "mensagem": "Número de CRMV deve ter entre 4 e 6 dígitos.",
            "uf": uf,
            "numero": numero,
        }

    if int(numero) == 0:
        return {
            "valido": False,
            "mensagem": "Número de CRMV não pode ser zero.",
            "uf": uf,
            "numero": numero,
        }

    return {
        "valido": True,
        "mensagem": "CRMV com formato plausível (validação local).",
        "uf": uf,
        "numero": numero,
    }


def get_report_target_owner(db, target_type, target_id):
    """
    Verifica se o alvo de uma denúncia existe e retorna o id do usuário
    responsável por ele: produtor (product), prestador (service), autor
    do relato (animal_report) ou o próprio usuário (user).

    Retorna uma tupla (existe: bool, owner_id: int | None).
    """
    if target_type == "product":
        row = db.execute(
            "SELECT producer_id FROM products WHERE id = ?", (target_id,)
        ).fetchone()
        return (row is not None, row["producer_id"] if row else None)

    if target_type == "service":
        row = db.execute(
            "SELECT provider_id FROM services WHERE id = ?", (target_id,)
        ).fetchone()
        return (row is not None, row["provider_id"] if row else None)

    if target_type == "animal_report":
        row = db.execute(
            "SELECT user_id FROM animal_reports WHERE id = ?", (target_id,)
        ).fetchone()
        return (row is not None, row["user_id"] if row else None)

    if target_type == "user":
        row = db.execute(
            "SELECT id FROM users WHERE id = ?", (target_id,)
        ).fetchone()
        return (row is not None, row["id"] if row else None)

    return (False, None)


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "change-this-secret-key")
    app.config["DATABASE_URL"] = DEFAULT_DATABASE_URL
    app.config["PREFERRED_URL_SCHEME"] = os.getenv("PREFERRED_URL_SCHEME", "https")
    server_name = os.getenv("SERVER_NAME", "").strip()
    if server_name:
        app.config["SERVER_NAME"] = server_name
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    ensure_upload_dirs()

    @app.before_request
    def load_logged_in_user():
        user_id = session.get("user_id")
        db = get_db()
        g.user = None
        g.unread_count = 0
        if user_id is not None:
            g.user = db.execute(
                """
                SELECT
                    id,
                    name,
                    email,
                    role,
                    city,
                    bio,
                    crmv,
                    is_vet_verified,
                    profile_image,
                    is_admin,
                    is_active
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
            if g.user is not None and not g.user["is_active"]:
                session.clear()
                g.user = None
                flash("Sua conta foi suspensa. Entre em contato com a administração.", "error")
            if g.user is not None:
                g.unread_count = (
                    db.execute(
                        """
                        SELECT COUNT(*) FROM messages
                        WHERE receiver_id = ? AND is_read = 0
                        """,
                        (user_id,),
                    ).fetchone()["count"]
                    or 0
                )

    @app.teardown_appcontext
    def close_db(exception):
        db = g.pop("db", None)
        if db is not None:
            db.close()

    @app.route("/")
    def index():
        db = get_db()
        featured_products = db.execute(
            """
            SELECT
                p.id,
                p.producer_id,
                p.title,
                p.description,
                p.price,
                p.stock,
                p.made_to_order,
                p.created_at,
                p.image_path,
                p.is_featured,
                u.name AS producer_name, u.city
            FROM products p
            JOIN users u ON p.producer_id = u.id
            WHERE p.is_featured = 1 AND (p.stock > 0 OR p.made_to_order = 1)
            ORDER BY p.created_at DESC
            LIMIT 12
            """
        ).fetchall()
        products = db.execute(
            """
            SELECT
                p.id,
                p.producer_id,
                p.title,
                p.description,
                p.price,
                p.stock,
                p.made_to_order,
                p.created_at,
                p.image_path,
                p.is_featured,
                u.name AS producer_name, u.city
            FROM products p
            JOIN users u ON p.producer_id = u.id
            WHERE p.stock > 0 OR p.made_to_order = 1
            ORDER BY p.created_at DESC
            LIMIT 20
            """
        ).fetchall()
        vets = db.execute(
            """
            SELECT id, name, city, bio
            FROM users
            WHERE role = 'veterinario' AND is_vet_verified = 1
            ORDER BY id DESC
            LIMIT 6
            """
        ).fetchall()
        return render_template(
            "index.html",
            products=products,
            featured_products=featured_products,
            vets=vets,
        )

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            name = request.form["name"].strip()
            email = request.form["email"].strip().lower()
            password = request.form["password"]
            role = normalize_role(request.form.get("role"))
            city = request.form.get("city", "").strip()
            bio = request.form.get("bio", "").strip()
            crmv_input = request.form.get("crmv", "").strip()
            terms_accepted = request.form.get("terms_accepted") == "1"
            profile_file = request.files.get("profile_image")

            error = None
            if not terms_accepted:
                error = "Você precisa aceitar os Termos de Uso e a Política de Privacidade para criar a conta."
            elif not name or not email or not password or not role:
                error = "Preencha todos os campos obrigatórios."
            elif role not in ALLOWED_ROLES:
                error = "Tipo de conta inválido."
            crmv_normalizado = None
            if error is None and role == "veterinario":
                if not crmv_input:
                    error = "Informe o número do CRMV para cadastro como veterinário."
                else:
                    crmv_res = validar_crmv_offline(crmv_input)
                    if not crmv_res["valido"]:
                        error = crmv_res["mensagem"]
                    else:
                        # Ex: "CRMV-SC 05432"
                        crmv_normalizado = f"CRMV-{crmv_res['uf']} {crmv_res['numero']}"

            profile_image_path = None
            if error is None and profile_file and profile_file.filename:
                profile_image_path = save_image(profile_file, "profiles")
                if profile_image_path is None:
                    error = "Imagem de perfil inválida. Envie um arquivo de imagem (jpg, png, gif, webp)."

            db = get_db()
            if error is None:
                existing = db.execute(
                    "SELECT id FROM users WHERE email = ?", (email,)
                ).fetchone()
                if existing:
                    error = "Este e-mail já está cadastrado."

            if error is None:
                verify_token = secrets.token_urlsafe(32)
                db.execute(
                    """
                    INSERT INTO users (
                        name,
                        email,
                        password_hash,
                        role,
                        city,
                        bio,
                        crmv,
                        is_vet_verified,
                        profile_image,
                        is_admin,
                        email_verified,
                        email_verify_token,
                        email_verify_sent_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        email,
                        generate_password_hash(password),
                        role,
                        city,
                        bio,
                        crmv_normalizado if role == "veterinario" else None,
                        0,
                        profile_image_path,
                        0,
                        0,
                        verify_token,
                        datetime.utcnow(),
                    ),
                )
                db.commit()

                verify_url = url_for("verify_email", token=verify_token, _external=True)
                sent = email_utils.send_verification_email(email, name, verify_url)

                if sent:
                    flash(
                        "Cadastro realizado! Enviamos um e-mail de confirmação "
                        f"para {email}. Clique no link recebido para ativar sua "
                        "conta antes de fazer login.",
                        "success",
                    )
                else:
                    # Envio de e-mail falhou (ou não está configurado no
                    # servidor). Para não impedir o uso da plataforma nesse
                    # caso, liberamos a conta automaticamente e avisamos o
                    # usuário.
                    db.execute(
                        "UPDATE users SET email_verified = 1 WHERE email = ?",
                        (email,),
                    )
                    db.commit()
                    flash(
                        "Cadastro realizado com sucesso! (Não foi possível "
                        "enviar o e-mail de confirmação no momento, então sua "
                        "conta já está liberada para uso.) Faça login.",
                        "success",
                    )
                return redirect(url_for("login"))
            else:
                flash(error, "error")

        return render_template("register.html")

    @app.route("/termos")
    def termos_uso():
        return render_template("termos_usos.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form["email"].strip().lower()
            password = request.form["password"]
            db = get_db()
            user = db.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()

            error = None
            if user is None or not check_password_hash(user["password_hash"], password):
                error = "E-mail ou senha inválidos."
            elif not user["is_active"]:
                error = "Esta conta foi suspensa. Entre em contato com a administração."
            elif not user["email_verified"]:
                error = (
                    "Você precisa confirmar seu e-mail antes de entrar. "
                    "Verifique sua caixa de entrada (e o spam) ou use o link "
                    "\"Reenviar confirmação\" abaixo."
                )

            if error is None:
                session.clear()
                session["user_id"] = user["id"]
                flash("Login realizado com sucesso!", "success")
                return redirect(url_for("dashboard"))
            else:
                flash(error, "error")

        return render_template("login.html")

    @app.route("/verify-email/<token>")
    def verify_email(token):
        """Confirma o e-mail do usuário a partir do link enviado por e-mail."""
        db = get_db()
        user = db.execute(
            "SELECT id, email_verified, email_verify_sent_at FROM users WHERE email_verify_token = ?",
            (token,),
        ).fetchone()

        if user is None:
            flash("Link de confirmação inválido. Solicite um novo link abaixo.", "error")
            return redirect(url_for("resend_verification"))

        if user["email_verified"]:
            flash("Este e-mail já havia sido confirmado. Você já pode fazer login.", "success")
            return redirect(url_for("login"))

        sent_at = user["email_verify_sent_at"]
        if sent_at is not None and datetime.utcnow() - sent_at > timedelta(hours=48):
            flash(
                "Este link de confirmação expirou. Solicite um novo link abaixo.",
                "error",
            )
            return redirect(url_for("resend_verification"))

        db.execute(
            """
            UPDATE users
            SET email_verified = 1, email_verify_token = NULL
            WHERE id = ?
            """,
            (user["id"],),
        )
        db.commit()
        flash("E-mail confirmado com sucesso! Você já pode fazer login.", "success")
        return redirect(url_for("login"))

    @app.route("/resend-verification", methods=["GET", "POST"])
    def resend_verification():
        """Permite reenviar o e-mail de confirmação caso o usuário não tenha
        recebido ou o link tenha expirado."""
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            db = get_db()
            user = db.execute(
                "SELECT id, name, email, email_verified FROM users WHERE email = ?",
                (email,),
            ).fetchone()

            # Resposta genérica sempre, para não revelar quais e-mails
            # estão cadastrados na plataforma.
            generic_message = (
                "Se este e-mail estiver cadastrado e pendente de confirmação, "
                "enviamos um novo link de verificação para ele."
            )

            if user is not None and not user["email_verified"]:
                new_token = secrets.token_urlsafe(32)
                db.execute(
                    """
                    UPDATE users
                    SET email_verify_token = ?, email_verify_sent_at = ?
                    WHERE id = ?
                    """,
                    (new_token, datetime.utcnow(), user["id"]),
                )
                db.commit()
                verify_url = url_for("verify_email", token=new_token, _external=True)
                sent = email_utils.send_verification_email(user["email"], user["name"], verify_url)
                if sent:
                    flash(generic_message, "success")
                else:
                    db.execute(
                        "UPDATE users SET email_verified = 1 WHERE id = ?",
                        (user["id"],),
                    )
                    db.commit()
                    flash(
                        "Não foi possível enviar o e-mail agora, então sua "
                        "conta já foi liberada automaticamente. Tente fazer login.",
                        "success",
                    )
            else:
                flash(generic_message, "success")

            return redirect(url_for("login"))

        return render_template("resend_verification.html")

    @app.route("/logout")
    def logout():
        session.clear()
        flash("Você saiu da sua conta.", "info")
        return redirect(url_for("index"))

    @app.route("/dashboard")
    def dashboard():
        if g.user is None:
            return redirect(url_for("login"))
        db = get_db()
        products = []
        received_messages = []
        sent_messages = []

        services = db.execute(
            """
            SELECT id, title, description, category, price, location, contact, created_at
            FROM services
            WHERE provider_id = ?
            ORDER BY created_at DESC
            """,
            (g.user["id"],),
        ).fetchall()

        if can_create_product(g.user["role"]):
            products = db.execute(
                """
                SELECT id, title, description, price, stock, made_to_order, created_at,
                       image_path, moderation_status, moderation_reason
                FROM products
                WHERE producer_id = ?
                ORDER BY created_at DESC
                """,
                (g.user["id"],),
            ).fetchall()

            received_messages = db.execute(
                """
                SELECT m.id, m.content, m.created_at,
                       m.sender_id, u.name AS vet_name
                FROM messages m
                JOIN users u ON m.sender_id = u.id
                WHERE m.receiver_id = ?
                ORDER BY m.created_at DESC
                """,
                (g.user["id"],),
            ).fetchall()

        elif g.user["role"] == "veterinario":
            received_messages = db.execute(
                """
                SELECT m.id, m.content, m.created_at,
                       m.sender_id, u.name AS producer_name
                FROM messages m
                JOIN users u ON m.sender_id = u.id
                WHERE m.receiver_id = ?
                ORDER BY m.created_at DESC
                """,
                (g.user["id"],),
            ).fetchall()

        sent_messages = db.execute(
            """
            SELECT m.id, m.content, m.created_at,
                   m.receiver_id, u.name AS receiver_name
            FROM messages m
            JOIN users u ON m.receiver_id = u.id
            WHERE m.sender_id = ?
            ORDER BY m.created_at DESC
            """,
            (g.user["id"],),
        ).fetchall()

        # Marcar mensagens como lidas ao visualizar o painel
        db.execute(
            "UPDATE messages SET is_read = 1 WHERE receiver_id = ? AND is_read = 0",
            (g.user["id"],),
        )
        db.commit()

        return render_template(
            "dashboard.html",
            products=products,
            services=services,
            received_messages=received_messages,
            sent_messages=sent_messages,
        )

    @app.route("/products/new", methods=["GET", "POST"])
    def new_product():
        if g.user is None or not can_create_product(g.user["role"]):
            flash("Apenas perfis de produtor, vendedor ou loja podem cadastrar produtos.", "error")
            return redirect(url_for("login"))

        if request.method == "POST":
            title = request.form["title"].strip()
            description = request.form["description"].strip()
            price = request.form.get("price", "").strip()
            stock_value = request.form.get("stock", "").strip()
            made_to_order = 1 if request.form.get("made_to_order") else 0
            product_file = request.files.get("product_image")

            if not title or not description:
                flash("Título e descrição são obrigatórios.", "error")
            elif not made_to_order and not stock_value.isdigit():
                flash("Informe uma quantidade de estoque valida (zero ou maior).", "error")
            else:
                stock = 0 if made_to_order else int(stock_value)
                db = get_db()
                image_path = None
                if product_file and product_file.filename:
                    image_path = save_image(product_file, "products")
                    if image_path is None:
                        flash(
                            "Imagem do produto inválida. Envie um arquivo de imagem (jpg, png, gif, webp).",
                            "error",
                        )
                        return redirect(url_for("new_product"))

                # Verificação automática de conteúdo com IA (Gemini) para
                # impedir a publicação de produtos ilícitos ou proibidos.
                # Em caso de dúvida (ou falha na verificação), o produto
                # fica pendente para revisão manual no painel de admin.
                check = moderation.check_product_content(
                    title=title, description=description, price=price
                )

                db.execute(
                    """
                    INSERT INTO products (
                        producer_id,
                        title,
                        description,
                        price,
                        stock,
                        made_to_order,
                        created_at,
                        image_path,
                        moderation_status,
                        moderation_reason,
                        moderation_checked_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        g.user["id"],
                        title,
                        description,
                        price or None,
                        stock,
                        made_to_order,
                        datetime.utcnow(),
                        image_path,
                        check.status,
                        check.reason,
                        datetime.utcnow(),
                    ),
                )
                db.commit()

                if check.is_approved:
                    flash("Produto cadastrado e publicado no Mercado!", "success")
                elif check.is_rejected:
                    flash(
                        "Seu produto foi cadastrado, mas NÃO foi publicado no "
                        "Mercado: nossa verificação automática identificou que "
                        f"ele pode violar as regras da plataforma ({check.reason}). "
                        "Se acredita que isso é um engano, entre em contato com o "
                        "suporte.",
                        "error",
                    )
                else:
                    flash(
                        "Produto cadastrado! Ele passará por uma revisão manual "
                        "antes de aparecer no Mercado, pois nossa verificação "
                        "automática não teve certeza sobre o conteúdo.",
                        "info",
                    )
                return redirect(url_for("dashboard"))

        return render_template("new_product.html")

    @app.route("/products/<int:product_id>/stock", methods=["POST"])
    def update_product_stock(product_id):
        """Permite que apenas o produtor altere o estoque do seu produto."""
        if g.user is None or g.user["role"] != "produtor":
            flash("Apenas produtores podem alterar o estoque.", "error")
            return redirect(url_for("login"))

        db = get_db()
        product = db.execute(
            "SELECT id, stock, made_to_order FROM products WHERE id = ? AND producer_id = ?",
            (product_id, g.user["id"]),
        ).fetchone()
        if product is None:
            flash("Produto nao encontrado.", "error")
            return redirect(url_for("dashboard"))
        if product["made_to_order"]:
            flash("Produtos feitos na hora nao usam controle de estoque.", "info")
            return redirect(url_for("dashboard"))

        operation = request.form.get("operation", "set")
        if operation == "increase":
            new_stock = product["stock"] + 1
        elif operation == "decrease":
            new_stock = max(product["stock"] - 1, 0)
        else:
            stock_value = request.form.get("stock", "").strip()
            if not stock_value.isdigit():
                flash("Informe uma quantidade de estoque valida (zero ou maior).", "error")
                return redirect(url_for("dashboard"))
            new_stock = int(stock_value)

        db.execute("UPDATE products SET stock = ? WHERE id = ?", (new_stock, product_id))
        db.commit()
        if new_stock == 0:
            flash("Estoque atualizado: produto marcado como Sem estoque e oculto do Mercado.", "info")
        else:
            flash(f"Estoque atualizado: {new_stock} unidade(s) disponivel(is).", "success")
        return redirect(url_for("dashboard"))

    @app.route("/marketplace")
    def marketplace():
        db = get_db()
        products = db.execute(
            """
            SELECT
                p.id,
                p.producer_id,
                p.title,
                p.description,
                p.price,
                p.stock,
                p.made_to_order,
                p.created_at,
                p.image_path,
                                (
                                    SELECT COUNT(*) FROM reports rep WHERE rep.target_type = 'product' AND rep.target_id = p.id AND rep.is_resolved = 0
                                ) AS report_count,
                p.is_featured,
                u.name AS producer_name, u.city
            FROM products p
            JOIN users u ON p.producer_id = u.id
            WHERE (p.stock > 0 OR p.made_to_order = 1)
              AND p.moderation_status = 'aprovado'
            ORDER BY p.created_at DESC
            """
        ).fetchall()
        cities = sorted(
            {row["city"] for row in products if row["city"]},
            key=str.casefold,
        )
        return render_template("marketplace.html", products=products, cities=cities)

    @app.route("/report", methods=["POST"])
    def report_content():
        redirect_url = request.referrer or url_for("marketplace")

        if g.user is None:
            flash("Faça login para denunciar conteúdo.", "error")
            return redirect(url_for("login"))

        target_type = request.form.get("target_type", "").strip()
        target_id = request.form.get("target_id", type=int)
        reason = request.form.get("reason", "").strip()
        details = request.form.get("details", "").strip()

        if target_type not in REPORT_TARGET_TYPES:
            flash("Tipo de denúncia inválido.", "error")
            return redirect(redirect_url)

        if not target_id or reason not in ALLOWED_REPORT_REASONS:
            flash("Selecione um motivo válido para a denúncia.", "error")
            return redirect(redirect_url)

        db = get_db()
        exists, owner_id = get_report_target_owner(db, target_type, target_id)

        if not exists:
            flash("Não foi possível localizar o conteúdo denunciado.", "error")
            return redirect(redirect_url)

        if owner_id == g.user["id"]:
            flash("Você não pode denunciar seu próprio conteúdo.", "error")
            return redirect(redirect_url)

        duplicate = db.execute(
            """
            SELECT id FROM reports
            WHERE reporter_id = ? AND target_type = ? AND target_id = ? AND is_resolved = 0
            """,
            (g.user["id"], target_type, target_id),
        ).fetchone()
        if duplicate:
            flash("Você já denunciou este conteúdo. Nossa equipe está analisando.", "info")
            return redirect(redirect_url)

        db.execute(
            """
            INSERT INTO reports (reporter_id, target_type, target_id, reason, details, created_at, is_resolved)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (g.user["id"], target_type, target_id, reason, details or None, datetime.utcnow()),
        )
        db.commit()

        flash("Denúncia enviada com sucesso. Obrigado por ajudar a manter a comunidade segura.", "success")
        return redirect(redirect_url)

    @app.route("/admin/reports")
    def admin_reports():
        if g.user is None or not g.user["is_admin"]:
            flash("Acesso restrito ao administrador.", "error")
            return redirect(url_for("index"))

        db = get_db()
        reports = db.execute(
            """
            SELECT
                r.id, r.reporter_id, r.target_type, r.target_id,
                r.reason, r.details, r.created_at, r.is_resolved,
                u.name AS reporter_name,
                p.title AS product_title, p.producer_id AS product_producer,
                s.title AS service_title, s.provider_id AS service_provider,
                ar.title AS animal_report_title, ar.user_id AS animal_report_author,
                ru.name AS reported_user_name, ru.is_active AS reported_user_active,
                ru.is_admin AS reported_user_is_admin
            FROM reports r
            JOIN users u ON u.id = r.reporter_id
            LEFT JOIN products p        ON p.id  = r.target_id AND r.target_type = 'product'
            LEFT JOIN services s        ON s.id  = r.target_id AND r.target_type = 'service'
            LEFT JOIN animal_reports ar ON ar.id  = r.target_id AND r.target_type = 'animal_report'
            LEFT JOIN users ru          ON ru.id  = r.target_id AND r.target_type = 'user'
            ORDER BY r.is_resolved ASC, r.created_at DESC
            """
        ).fetchall()
        return render_template("admin_reports.html", reports=reports)

    @app.route("/admin/reports/<int:report_id>/resolve", methods=["POST"])
    def resolve_report(report_id):
        if g.user is None or not g.user["is_admin"]:
            flash("Acesso restrito ao administrador.", "error")
            return redirect(url_for("index"))

        db = get_db()
        db.execute(
            "UPDATE reports SET is_resolved = 1 WHERE id = ?",
            (report_id,),
        )
        db.commit()
        flash("Denúncia marcada como resolvida.", "success")
        return redirect(url_for("admin_reports"))

    @app.route("/admin/product/<int:product_id>/delete", methods=["POST"])
    def admin_delete_product(product_id):
        if g.user is None or not g.user["is_admin"]:
            flash("Acesso restrito ao administrador.", "error")
            return redirect(url_for("index"))

        db = get_db()
        db.execute("DELETE FROM products WHERE id = ?", (product_id,))
        db.execute(
            "UPDATE reports SET is_resolved = 1 WHERE target_type = 'product' AND target_id = ?",
            (product_id,),
        )
        db.commit()
        flash("Produto excluído com sucesso.", "success")
        return redirect(url_for("admin_reports"))

    @app.route("/admin/service/<int:service_id>/delete", methods=["POST"])
    def admin_delete_service(service_id):
        if g.user is None or not g.user["is_admin"]:
            flash("Acesso restrito ao administrador.", "error")
            return redirect(url_for("index"))

        db = get_db()
        db.execute("DELETE FROM services WHERE id = ?", (service_id,))
        db.execute(
            "UPDATE reports SET is_resolved = 1 WHERE target_type = 'service' AND target_id = ?",
            (service_id,),
        )
        db.commit()
        flash("Serviço excluído com sucesso.", "success")
        return redirect(url_for("admin_reports"))

    @app.route("/admin/animal-report/<int:animal_report_id>/delete", methods=["POST"])
    def admin_delete_animal_report(animal_report_id):
        if g.user is None or not g.user["is_admin"]:
            flash("Acesso restrito ao administrador.", "error")
            return redirect(url_for("index"))

        db = get_db()
        db.execute("DELETE FROM animal_reports WHERE id = ?", (animal_report_id,))
        db.execute(
            "UPDATE reports SET is_resolved = 1 WHERE target_type = 'animal_report' AND target_id = ?",
            (animal_report_id,),
        )
        db.commit()
        flash("Relato excluído com sucesso.", "success")
        return redirect(url_for("admin_reports"))

    @app.route("/admin/user/<int:user_id>/suspend", methods=["POST"])
    def admin_toggle_user_active(user_id):
        if g.user is None or not g.user["is_admin"]:
            flash("Acesso restrito ao administrador.", "error")
            return redirect(url_for("index"))

        if user_id == g.user["id"]:
            flash("Você não pode suspender sua própria conta.", "error")
            return redirect(url_for("admin_reports"))

        db = get_db()
        target = db.execute(
            "SELECT id, is_active, is_admin FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if target is None:
            flash("Usuário não encontrado.", "error")
            return redirect(url_for("admin_reports"))
        if target["is_admin"]:
            flash("Não é possível suspender uma conta de administrador.", "error")
            return redirect(url_for("admin_reports"))

        new_status = 0 if target["is_active"] else 1
        db.execute("UPDATE users SET is_active = ? WHERE id = ?", (new_status, user_id))
        if new_status == 0:
            db.execute(
                "UPDATE reports SET is_resolved = 1 WHERE target_type = 'user' AND target_id = ?",
                (user_id,),
            )
        db.commit()

        flash(
            "Conta suspensa com sucesso." if new_status == 0 else "Conta reativada com sucesso.",
            "success",
        )
        return redirect(url_for("admin_reports"))

    @app.route("/vets")
    def vets():
        db = get_db()
        vets = db.execute(
            """
            SELECT id, name, city, bio
            FROM users
            WHERE role = 'veterinario' AND is_vet_verified = 1
            ORDER BY id DESC
            """
        ).fetchall()
        return render_template("vets.html", vets=vets)

    @app.route("/chat/<int:user_id>", methods=["GET", "POST"])
    def chat(user_id):
        if g.user is None:
            flash("Faça login para usar o chat.", "error")
            return redirect(url_for("login"))

        if g.user["id"] == user_id:
            flash("Você não pode abrir um chat consigo mesmo.", "error")
            return redirect(url_for("dashboard"))

        db = get_db()
        other = db.execute(
            "SELECT id, name, role, city, profile_image FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if other is None:
            flash("Usuário não encontrado.", "error")
            return redirect(url_for("dashboard"))

        product_id = request.args.get("product_id", type=int)
        product = None
        if product_id:
            product = db.execute(
                """
                SELECT p.id, p.title, p.description, p.price, p.image_path,
                       u.name AS producer_name
                FROM products p
                JOIN users u ON p.producer_id = u.id
                WHERE p.id = ?
                """,
                (product_id,),
            ).fetchone()

        if request.method == "POST":
            content = request.form.get("content", "").strip()
            if not content:
                flash("Mensagem não pode ser vazia.", "error")
            else:
                db.execute(
                    """
                    INSERT INTO messages (sender_id, receiver_id, content, created_at, is_read)
                    VALUES (?, ?, ?, ?, 0)
                    """,
                    (g.user["id"], other["id"], content, datetime.utcnow()),
                )
                db.commit()
                return redirect(
                    url_for("chat", user_id=other["id"], product_id=product_id)
                )

        # histórico de conversa entre os dois usuários
        messages = db.execute(
            """
            SELECT
                m.id,
                m.content,
                m.created_at,
                m.sender_id,
                m.receiver_id,
                su.name AS sender_name,
                ru.name AS receiver_name
            FROM messages m
            JOIN users su ON m.sender_id = su.id
            JOIN users ru ON m.receiver_id = ru.id
            WHERE (m.sender_id = ? AND m.receiver_id = ?)
               OR (m.sender_id = ? AND m.receiver_id = ?)
            ORDER BY m.created_at ASC
            """,
            (g.user["id"], other["id"], other["id"], g.user["id"]),
        ).fetchall()

        # marcar como lidas as mensagens que o outro enviou para mim
        db.execute(
            """
            UPDATE messages
            SET is_read = 1
            WHERE receiver_id = ? AND sender_id = ? AND is_read = 0
            """,
            (g.user["id"], other["id"]),
        )
        db.commit()

        return render_template(
            "chat.html",
            other=other,
            messages=messages,
            product=product,
        )

    @app.route("/api/messages/<int:user_id>")
    def api_messages(user_id):
        """Polling endpoint – returns JSON list of messages for real-time chat."""
        from flask import jsonify
        if g.user is None:
            return jsonify({"error": "not_logged_in"}), 401

        since_id = request.args.get("since", 0, type=int)
        db = get_db()

        rows = db.execute(
            """
            SELECT
                m.id,
                m.content,
                m.created_at,
                m.sender_id,
                su.name AS sender_name
            FROM messages m
            JOIN users su ON m.sender_id = su.id
            WHERE ((m.sender_id = ? AND m.receiver_id = ?)
               OR  (m.sender_id = ? AND m.receiver_id = ?))
              AND m.id > ?
            ORDER BY m.created_at ASC
            """,
            (g.user["id"], user_id, user_id, g.user["id"], since_id),
        ).fetchall()

        # mark incoming as read
        db.execute(
            "UPDATE messages SET is_read = 1 WHERE receiver_id = ? AND sender_id = ? AND is_read = 0",
            (g.user["id"], user_id),
        )
        db.commit()

        return jsonify([
            {
                "id": r["id"],
                "content": r["content"],
                "created_at": str(r["created_at"]),
                "sender_id": r["sender_id"],
                "sender_name": r["sender_name"],
                "is_mine": r["sender_id"] == g.user["id"],
            }
            for r in rows
        ])

    @app.route("/api/send/<int:user_id>", methods=["POST"])
    def api_send(user_id):
        """AJAX send endpoint for real-time chat."""
        from flask import jsonify
        if g.user is None:
            return jsonify({"error": "not_logged_in"}), 401

        data = request.get_json(silent=True) or {}
        content = (data.get("content") or "").strip()
        if not content:
            return jsonify({"error": "empty"}), 400

        db = get_db()
        db.execute(
            "INSERT INTO messages (sender_id, receiver_id, content, created_at, is_read) VALUES (?, ?, ?, ?, 0)",
            (g.user["id"], user_id, content, datetime.utcnow()),
        )
        db.commit()

        last = db.execute(
            "SELECT id, created_at FROM messages WHERE sender_id = ? AND receiver_id = ? ORDER BY id DESC LIMIT 1",
            (g.user["id"], user_id),
        ).fetchone()

        return jsonify({
            "id": last["id"],
            "content": content,
            "created_at": str(last["created_at"]),
            "sender_id": g.user["id"],
            "sender_name": g.user["name"],
            "is_mine": True,
        })

    @app.route("/admin")
    def admin_dashboard():
        if g.user is None or not g.user["is_admin"]:
            flash("Acesso restrito ao administrador.", "error")
            return redirect(url_for("index"))

        db = get_db()
        users = db.execute(
            """
            SELECT id, name, email, role, city, crmv, is_vet_verified, is_admin, is_active, email_verified
            FROM users
            ORDER BY id ASC
            """
        ).fetchall()
        products = db.execute(
            """
            SELECT p.id, p.title, p.price, p.is_featured,
                   p.moderation_status, p.moderation_reason,
                   u.name AS producer_name, u.city
            FROM products p
            JOIN users u ON p.producer_id = u.id
            ORDER BY p.id DESC
            """
        ).fetchall()
        pending_products = db.execute(
            """
            SELECT p.id, p.title, p.description, p.price, p.image_path,
                   p.moderation_reason, p.created_at,
                   u.name AS producer_name, u.id AS producer_id
            FROM products p
            JOIN users u ON p.producer_id = u.id
            WHERE p.moderation_status = 'pendente'
            ORDER BY p.created_at ASC
            """
        ).fetchall()
        messages = db.execute(
            """
            SELECT
                m.id,
                m.content,
                m.created_at,
                m.is_read,
                su.name AS sender_name,
                ru.name AS receiver_name
            FROM messages m
            JOIN users su ON m.sender_id = su.id
            JOIN users ru ON m.receiver_id = ru.id
            ORDER BY m.created_at DESC
            """
        ).fetchall()
        pending_reports = db.execute(
            "SELECT COUNT(*) FROM reports WHERE is_resolved = 0"
        ).fetchone()["count"]
        return render_template(
            "admin_dashboard.html",
            users=users,
            products=products,
            pending_products=pending_products,
            messages=messages,
            pending_reports=pending_reports,
        )

    @app.route("/admin/broadcast", methods=["POST"])
    def admin_broadcast():
        if g.user is None or not g.user["is_admin"]:
            flash("Acesso restrito ao administrador.", "error")
            return redirect(url_for("index"))

        target = request.form.get("target")  # all, produtores, vendedores, lojas, veterinarios, usuarios
        content = request.form.get("content", "").strip()

        if not content:
            flash("Mensagem não pode ser vazia.", "error")
            return redirect(url_for("admin_dashboard"))

        role_filter = None
        if target == "produtores":
            role_filter = "produtor"
        elif target == "vendedores":
            role_filter = "vendedor"
        elif target == "lojas":
            role_filter = "loja"
        elif target == "veterinarios":
            role_filter = "veterinario"
        elif target == "usuarios":
            role_filter = "usuario"

        db = get_db()
        if role_filter:
            receivers = db.execute(
                "SELECT id FROM users WHERE role = ? AND id != ?",
                (role_filter, g.user["id"]),
            ).fetchall()
        else:
            receivers = db.execute(
                "SELECT id FROM users WHERE id != ?", (g.user["id"],)
            ).fetchall()

        now = datetime.utcnow()
        for r in receivers:
            db.execute(
                """
                INSERT INTO messages (sender_id, receiver_id, content, created_at, is_read)
                VALUES (?, ?, ?, ?, 0)
                """,
                (g.user["id"], r["id"], content, now),
            )
        db.commit()

        flash("Mensagem enviada para os usuários selecionados.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/vet/<int:user_id>/toggle", methods=["POST"])
    def toggle_vet_verification(user_id):
        if g.user is None or not g.user["is_admin"]:
            flash("Acesso restrito ao administrador.", "error")
            return redirect(url_for("index"))

        db = get_db()
        vet = db.execute(
            "SELECT id, is_vet_verified, role FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if vet is None or vet["role"] != "veterinario":
            flash("Usuário não encontrado ou não é veterinário.", "error")
            return redirect(url_for("admin_dashboard"))

        new_status = 0 if vet["is_vet_verified"] else 1
        db.execute(
            "UPDATE users SET is_vet_verified = ? WHERE id = ?",
            (new_status, user_id),
        )
        db.commit()

        flash("Status de verificação atualizado.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/product/<int:product_id>/featured/toggle", methods=["POST"])
    def toggle_product_featured(product_id):
        if g.user is None or not g.user["is_admin"]:
            flash("Acesso restrito ao administrador.", "error")
            return redirect(url_for("index"))

        db = get_db()
        product = db.execute(
            "SELECT id, is_featured FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if product is None:
            flash("Produto nÃ£o encontrado.", "error")
            return redirect(url_for("admin_dashboard"))

        new_status = 0 if product["is_featured"] else 1
        db.execute(
            "UPDATE products SET is_featured = ? WHERE id = ?",
            (new_status, product_id),
        )
        db.commit()

        flash("Destaque do produto atualizado.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/product/<int:product_id>/approve", methods=["POST"])
    def admin_approve_product(product_id):
        """Aprova manualmente um produto que ficou pendente na verificação
        automática (segunda verificação humana)."""
        if g.user is None or not g.user["is_admin"]:
            flash("Acesso restrito ao administrador.", "error")
            return redirect(url_for("index"))

        db = get_db()
        product = db.execute(
            "SELECT id FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if product is None:
            flash("Produto não encontrado.", "error")
            return redirect(url_for("admin_dashboard"))

        db.execute(
            """
            UPDATE products
            SET moderation_status = 'aprovado',
                moderation_reason = 'Aprovado manualmente por administrador.',
                moderation_checked_at = ?
            WHERE id = ?
            """,
            (datetime.utcnow(), product_id),
        )
        db.commit()
        flash("Produto aprovado e publicado no Mercado.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/admin/product/<int:product_id>/reject", methods=["POST"])
    def admin_reject_product(product_id):
        """Rejeita manualmente um produto pendente (segunda verificação
        humana), impedindo que ele apareça no Mercado."""
        if g.user is None or not g.user["is_admin"]:
            flash("Acesso restrito ao administrador.", "error")
            return redirect(url_for("index"))

        db = get_db()
        product = db.execute(
            "SELECT id FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if product is None:
            flash("Produto não encontrado.", "error")
            return redirect(url_for("admin_dashboard"))

        admin_reason = request.form.get("reason", "").strip()
        reason = (
            f"Rejeitado manualmente por administrador. Motivo: {admin_reason}"
            if admin_reason
            else "Rejeitado manualmente por administrador."
        )

        db.execute(
            """
            UPDATE products
            SET moderation_status = 'rejeitado',
                moderation_reason = ?,
                moderation_checked_at = ?
            WHERE id = ?
            """,
            (reason, datetime.utcnow(), product_id),
        )
        db.commit()
        flash("Produto rejeitado e removido do Mercado.", "success")
        return redirect(url_for("admin_dashboard"))

    @app.route("/servicos")
    def servicos():
        db = get_db()
        lista = db.execute(
            """
            SELECT s.id, s.title, s.description, s.category,
                   s.price, s.location, s.contact, s.created_at,
                   u.name AS provider_name, u.id AS provider_id,
                   (
                       SELECT COUNT(*) FROM reports rep
                       WHERE rep.target_type = 'service' AND rep.target_id = s.id AND rep.is_resolved = 0
                   ) AS report_count
            FROM services s
            JOIN users u ON u.id = s.provider_id
            ORDER BY s.created_at DESC
            """
        ).fetchall()
        return render_template("servicos.html", servicos=lista)

    @app.route("/servicos/novo", methods=["GET", "POST"])
    def novo_servico():
        if g.user is None:
            flash("Faça login para publicar um serviço.", "error")
            return redirect(url_for("login"))

        if request.method == "POST":
            title       = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            category    = request.form.get("category", "").strip()
            price       = request.form.get("price", "").strip()
            location    = request.form.get("location", "").strip()
            contact     = request.form.get("contact", "").strip()

            if not title or not description:
                flash("Título e descrição são obrigatórios.", "error")
            else:
                db = get_db()
                db.execute(
                    """
                    INSERT INTO services
                        (provider_id, title, description, category, price, location, contact, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (g.user["id"], title, description,
                     category or None, price or None,
                     location or None, contact or None,
                     datetime.utcnow()),
                )
                db.commit()
                flash("Serviço publicado com sucesso!", "success")
                return redirect(url_for("dashboard"))

        return render_template("novo_servico.html")

    @app.route("/ajuda-animal")
    def ajuda_animal():
        db = get_db()
        relatos = db.execute(
            """
            SELECT r.id, r.title, r.description, r.species, r.urgency,
                   r.location, r.status, r.created_at,
                   u.name AS author_name, u.id AS author_id,
                   (
                       SELECT COUNT(*) FROM reports rep
                       WHERE rep.target_type = 'animal_report' AND rep.target_id = r.id AND rep.is_resolved = 0
                   ) AS report_count
            FROM animal_reports r
            JOIN users u ON u.id = r.user_id
            ORDER BY r.created_at DESC
            """
        ).fetchall()
        return render_template("ajuda_animal.html", relatos=relatos)

    @app.route("/ajuda-animal/novo", methods=["GET", "POST"])
    def novo_relato_animal():
        if g.user is None:
            flash("Faça login para publicar um relato.", "error")
            return redirect(url_for("login"))

        if request.method == "POST":
            title       = request.form.get("title", "").strip()
            description = request.form.get("description", "").strip()
            species     = request.form.get("species", "").strip()
            urgency     = request.form.get("urgency", "media").strip()
            location    = request.form.get("location", "").strip()

            if not title or not description or not species:
                flash("Título, descrição e espécie são obrigatórios.", "error")
            else:
                db = get_db()
                db.execute(
                    """
                    INSERT INTO animal_reports
                        (user_id, title, description, species, urgency, location, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (g.user["id"], title, description, species,
                     urgency, location or None, datetime.utcnow()),
                )
                db.commit()
                flash("Relato publicado! A comunidade irá te ajudar em breve.", "success")
                return redirect(url_for("ajuda_animal"))

        return render_template("novo_relato_animal.html")

    @app.route("/message/<int:vet_id>", methods=["GET", "POST"])
    def message_vet(vet_id):
        if g.user is None:
            flash("Faça login para enviar mensagens.", "error")
            return redirect(url_for("login"))

        db = get_db()
        vet = db.execute(
            """
            SELECT id, name, city, bio
            FROM users
            WHERE id = ? AND role = 'veterinario' AND is_vet_verified = 1
            """,
            (vet_id,),
        ).fetchone()
        if vet is None:
            flash("Veterinário não encontrado ou ainda não verificado.", "error")
            return redirect(url_for("vets"))

        if request.method == "POST":
            content = request.form["content"].strip()
            if not content:
                flash("Mensagem não pode ser vazia.", "error")
            else:
                db.execute(
                    """
                    INSERT INTO messages (sender_id, receiver_id, content, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (g.user["id"], vet["id"], content, datetime.utcnow()),
                )
                db.commit()
                flash("Mensagem enviada ao veterinário!", "success")
                return redirect(url_for("dashboard"))

        return render_template("message_vet.html", vet=vet)

    @app.route("/profile/<int:user_id>")
    def profile(user_id):
        db = get_db()
        user = db.execute(
            """
            SELECT id, name, email, role, city, bio, crmv, is_vet_verified, profile_image
            FROM users
            WHERE id = ?
            """,
            (user_id,),
        ).fetchone()
        if user is None:
            flash("Usuário não encontrado.", "error")
            return redirect(url_for("index"))

        products = []
        if can_create_product(user["role"]):
            is_owner = g.user is not None and g.user["id"] == user["id"]
            products = db.execute(
                """
                SELECT id, title, description, price, stock, made_to_order, created_at,
                       image_path, moderation_status, moderation_reason
                                ,(
                                    SELECT COUNT(*) FROM reports rep WHERE rep.target_type = 'product' AND rep.target_id = products.id AND rep.is_resolved = 0
                                ) AS report_count
                                FROM products
                                WHERE producer_id = ?
                                  AND (? OR moderation_status = 'aprovado')
                                ORDER BY created_at DESC
                """,
                (user["id"], is_owner),
            ).fetchall()

        return render_template("profile.html", user=user, products=products)

    return app


app = create_app()


def get_db():
    if "db" not in g:
        g.db = connect_db()
    return g.db


def init_db():
    db = connect_db()
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK (role IN ('produtor', 'servidor', 'veterinario', 'usuario', 'vendedor', 'loja')),
            city TEXT,
            bio TEXT,
            crmv TEXT,
            is_vet_verified INTEGER NOT NULL DEFAULT 0,
            profile_image TEXT,
            is_admin INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            producer_id INTEGER NOT NULL REFERENCES users (id),
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            price TEXT,
            stock INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
            made_to_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL,
            image_path TEXT,
            is_featured INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            sender_id INTEGER NOT NULL REFERENCES users (id),
            receiver_id INTEGER NOT NULL REFERENCES users (id),
            content TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            is_read INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS services (
            id SERIAL PRIMARY KEY,
            provider_id INTEGER NOT NULL REFERENCES users (id),
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            category TEXT,
            price TEXT,
            location TEXT,
            contact TEXT,
            created_at TIMESTAMP NOT NULL
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS animal_reports (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users (id),
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            species TEXT,
            urgency TEXT NOT NULL DEFAULT 'media',
            location TEXT,
            status TEXT NOT NULL DEFAULT 'aberto',
            created_at TIMESTAMP NOT NULL
        )
        """
    )

    db.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id SERIAL PRIMARY KEY,
            reporter_id INTEGER NOT NULL REFERENCES users (id),
            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            details TEXT,
            created_at TIMESTAMP NOT NULL,
            is_resolved INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    existing_constraints = db.execute(
        """
        SELECT conname
        FROM pg_constraint
        WHERE conrelid = 'users'::regclass
          AND contype = 'c'
          AND pg_get_constraintdef(oid) ILIKE '%role IN%'
        """
    ).fetchall()
    for row in existing_constraints:
        db.execute(
            f"ALTER TABLE users DROP CONSTRAINT IF EXISTS {row['conname']}"
        )
    db.execute(
        "ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('produtor', 'servidor', 'veterinario', 'usuario', 'vendedor', 'loja'))"
    )
    db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS crmv TEXT")
    db.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_vet_verified INTEGER NOT NULL DEFAULT 0"
    )
    db.execute(
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS is_read INTEGER NOT NULL DEFAULT 0"
    )
    db.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_image TEXT")
    db.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin INTEGER NOT NULL DEFAULT 0"
    )
    db.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS image_path TEXT")
    db.execute(
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS is_featured INTEGER NOT NULL DEFAULT 0"
    )
    # Produtos jÃ¡ cadastrados recebem uma unidade para continuarem visÃ­veis
    # atÃ© que o produtor informe o estoque real no painel.
    db.execute(
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS stock INTEGER NOT NULL DEFAULT 1"
    )
    db.execute(
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS made_to_order INTEGER NOT NULL DEFAULT 0"
    )
    db.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active INTEGER NOT NULL DEFAULT 1"
    )

    # Moderação automática de produtos (verificação via IA - Gemini).
    # moderation_status: 'aprovado' (visível no marketplace), 'pendente'
    # (aguardando revisão de um administrador) ou 'rejeitado' (bloqueado).
    db.execute(
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS moderation_status "
        "TEXT NOT NULL DEFAULT 'pendente'"
    )
    db.execute(
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS moderation_reason TEXT"
    )
    db.execute(
        "ALTER TABLE products ADD COLUMN IF NOT EXISTS moderation_checked_at TIMESTAMP"
    )
    # Produtos já cadastrados antes desta migração (sem verificação registrada)
    # são considerados aprovados, já que estavam publicados antes de a
    # verificação automática existir.
    db.execute(
        "UPDATE products SET moderation_status = 'aprovado' "
        "WHERE moderation_checked_at IS NULL AND moderation_status = 'pendente'"
    )

    # Verificação de e-mail no cadastro (confirmação por link enviado via Gmail).
    db.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified INTEGER NOT NULL DEFAULT 0"
    )
    db.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verify_token TEXT"
    )
    db.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verify_sent_at TIMESTAMP"
    )
    # Contas criadas antes desta migração (nunca passaram por um envio de
    # verificação) são consideradas confirmadas, para não bloquear o login
    # de quem já usava a plataforma.
    db.execute(
        "UPDATE users SET email_verified = 1 "
        "WHERE email_verified = 0 AND email_verify_sent_at IS NULL"
    )


    db.commit()
    db.close()


def seed_admin():
    """
    Cria uma conta de administrador padrão se ainda não existir.
    Email: admin@agrolink.local
    Senha: admin123  (recomendado trocar em produção)
    """
    conn = connect_db()
    existing = conn.execute("SELECT id FROM users WHERE is_admin = 1 LIMIT 1").fetchone()
    if existing is None:
        password_hash = generate_password_hash("admin123")
        conn.execute(
            """
            INSERT INTO users (
                name,
                email,
                password_hash,
                role,
                city,
                bio,
                crmv,
                is_vet_verified,
                profile_image,
                is_admin
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "Administrador",
                "admin@agrolink.local",
                password_hash,
                "usuario",
                None,
                "Conta administrativa padrão.",
                None,
                0,
                None,
                1,
            ),
        )
        conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    seed_admin()
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0").lower() in {"1", "true", "yes", "on"}
    app.run(host=host, port=port, debug=debug)
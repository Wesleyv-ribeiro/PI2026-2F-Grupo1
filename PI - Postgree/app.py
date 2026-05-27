import os
from datetime import datetime
import re
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
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

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
        raw = env_path.read_text(encoding="utf-8").strip()
        if raw.startswith("postgresql://") or raw.startswith("postgres://"):
            os.environ["DATABASE_URL"] = raw
            break

DEFAULT_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@127.0.0.1:5432/agrolink",
)


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




def connect_db():
    try:
        raw_conn = psycopg2.connect(DEFAULT_DATABASE_URL)
        return PostgresCompatConnection(raw_conn)
    except OperationalError as exc:
        raise RuntimeError(
            "Nao foi possivel conectar ao PostgreSQL. "
            "Configure a variavel DATABASE_URL com usuario/senha corretos e confirme "
            "que o banco existe. Exemplo: "
            "postgresql://SEU_USUARIO:SUA_SENHA@127.0.0.1:5432/agrolink"
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


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "change-this-secret-key"
    app.config["DATABASE_URL"] = DEFAULT_DATABASE_URL
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
                    is_admin
                FROM users
                WHERE id = ?
                """,
                (user_id,),
            ).fetchone()
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
        products = db.execute(
            """
            SELECT
                p.id,
                p.producer_id,
                p.title,
                p.description,
                p.price,
                p.created_at,
                p.image_path,
                   u.name AS producer_name, u.city
            FROM products p
            JOIN users u ON p.producer_id = u.id
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
        return render_template("index.html", products=products, vets=vets)

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            name = request.form["name"].strip()
            email = request.form["email"].strip().lower()
            password = request.form["password"]
            role = request.form.get("role")
            city = request.form.get("city", "").strip()
            bio = request.form.get("bio", "").strip()
            crmv_input = request.form.get("crmv", "").strip()
            profile_file = request.files.get("profile_image")

            error = None
            if not name or not email or not password or not role:
                error = "Preencha todos os campos obrigatórios."
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
                        is_admin
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
                db.commit()
                flash("Cadastro realizado com sucesso! Faça login.", "success")
                return redirect(url_for("login"))
            else:
                flash(error, "error")

        return render_template("register.html")

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

            if error is None:
                session.clear()
                session["user_id"] = user["id"]
                flash("Login realizado com sucesso!", "success")
                return redirect(url_for("dashboard"))
            else:
                flash(error, "error")

        return render_template("login.html")

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

        if g.user["role"] == "produtor":
            products = db.execute(
                """
                SELECT id, title, description, price, created_at, image_path
                FROM products
                WHERE producer_id = ?
                ORDER BY created_at DESC
                """,
                (g.user["id"],),
            ).fetchall()

            received_messages = db.execute(
                """
                SELECT m.id, m.content, m.created_at,
                       u.name AS vet_name
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
                       u.name AS producer_name
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
                   u.name AS receiver_name
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
            received_messages=received_messages,
            sent_messages=sent_messages,
        )

    @app.route("/products/new", methods=["GET", "POST"])
    def new_product():
        if g.user is None or g.user["role"] != "produtor":
            flash("Apenas produtores podem cadastrar produtos.", "error")
            return redirect(url_for("login"))

        if request.method == "POST":
            title = request.form["title"].strip()
            description = request.form["description"].strip()
            price = request.form.get("price", "").strip()
            product_file = request.files.get("product_image")

            if not title or not description:
                flash("Título e descrição são obrigatórios.", "error")
            else:
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
                db.execute(
                    """
                    INSERT INTO products (
                        producer_id,
                        title,
                        description,
                        price,
                        created_at,
                        image_path
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        g.user["id"],
                        title,
                        description,
                        price or None,
                        datetime.utcnow(),
                        image_path,
                    ),
                )
                db.commit()
                flash("Produto cadastrado com sucesso!", "success")
                return redirect(url_for("dashboard"))

        return render_template("new_product.html")

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
                p.created_at,
                p.image_path,
                   u.name AS producer_name, u.city
            FROM products p
            JOIN users u ON p.producer_id = u.id
            ORDER BY p.created_at DESC
            """
        ).fetchall()
        return render_template("marketplace.html", products=products)

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

    @app.route("/admin")
    def admin_dashboard():
        if g.user is None or not g.user["is_admin"]:
            flash("Acesso restrito ao administrador.", "error")
            return redirect(url_for("index"))

        db = get_db()
        users = db.execute(
            """
            SELECT id, name, email, role, city, crmv, is_vet_verified, is_admin
            FROM users
            ORDER BY id ASC
            """
        ).fetchall()
        products = db.execute(
            """
            SELECT p.id, p.title, p.price, u.name AS producer_name, u.city
            FROM products p
            JOIN users u ON p.producer_id = u.id
            ORDER BY p.id DESC
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

        return render_template(
            "admin_dashboard.html",
            users=users,
            products=products,
            messages=messages,
        )

    @app.route("/admin/broadcast", methods=["POST"])
    def admin_broadcast():
        if g.user is None or not g.user["is_admin"]:
            flash("Acesso restrito ao administrador.", "error")
            return redirect(url_for("index"))

        target = request.form.get("target")  # all, produtores, veterinarios, usuarios
        content = request.form.get("content", "").strip()

        if not content:
            flash("Mensagem não pode ser vazia.", "error")
            return redirect(url_for("admin_dashboard"))

        role_filter = None
        if target == "produtores":
            role_filter = "produtor"
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
        if user["role"] == "produtor":
            products = db.execute(
                """
                SELECT id, title, description, price, created_at, image_path
                FROM products
                WHERE producer_id = ?
                ORDER BY created_at DESC
                """,
                (user["id"],),
            ).fetchall()

        return render_template("profile.html", user=user, products=products)

    return app


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
            role TEXT NOT NULL CHECK (role IN ('produtor', 'veterinario', 'usuario')),
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
            created_at TIMESTAMP NOT NULL,
            image_path TEXT
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

    db.commit()
    db.close()


def seed_admin():
    """
    Cria uma conta de administrador padrão se ainda não existir.
    Email: admin@agrolink.local
    Senha: admin123  (recomendado trocar em produção)
    """
    conn = connect_db()
    # Sempre garanta que exista uma conta admin e atualize a senha para o valor desejado.
    existing = conn.execute("SELECT id FROM users WHERE is_admin = 1 LIMIT 1").fetchone()
    password_hash = generate_password_hash("Morango")
    if existing is None:
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
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    else:
        uid = existing["id"] if isinstance(existing, dict) or hasattr(existing, 'keys') else existing[0]
        conn.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (password_hash, uid),
        )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    seed_admin()
    app = create_app()
    app.run(debug=True)


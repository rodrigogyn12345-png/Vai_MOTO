import unicodedata
from pathlib import Path
from flask import send_from_directory, send_file
import os
import uuid
from werkzeug.utils import secure_filename
from flask import Flask, request, redirect, url_for, session, render_template_string, flash
import sqlite3
import json
import math
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "VAI_DE_MOTO_CHAVE_TROCAR_DEPOIS"
app.config['MAX_CONTENT_LENGTH'] = 80 * 1024 * 1024

DB = "/var/data/vai_de_moto.db"
LIMITE_MOTOQUEIROS = 20

# =========================================================
# ASAAS - PAGAMENTOS
# A chave fica somente em variável de ambiente.
# =========================================================
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY", "").strip()
ASAAS_BASE_URL = os.getenv(
    "ASAAS_BASE_URL",
    "https://api.asaas.com/v3"
).rstrip("/")

ASAAS_WEBHOOK_TOKEN = os.getenv(
    "ASAAS_WEBHOOK_TOKEN",
    ""
).strip()


def conectar():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn



def criar_checkout_asaas(corrida_id, valor, origem, destino):
    """
    Cria um Checkout Asaas para uma corrida.
    Retorna (checkout_id, checkout_url, erro).
    """
    if not ASAAS_API_KEY:
        return None, None, "ASAAS_API_KEY não configurada."

    try:
        payload = {
            "billingTypes": ["PIX", "CREDIT_CARD"],
            "chargeTypes": ["DETACHED"],
            "minutesToExpire": 60,
            "externalReference": f"corrida-{corrida_id}",
            "callback": {
                "successUrl": f"{request.url_root.rstrip('/')}/passageiro",
                "cancelUrl": f"{request.url_root.rstrip('/')}/passageiro",
                "expiredUrl": f"{request.url_root.rstrip('/')}/passageiro"
            },
            "items": [
                {
                    "name": f"Corrida VAI_DE_MOTO #{corrida_id}",
                    "description": f"{origem} → {destino}",
                    "quantity": 1,
                    "value": float(valor)
                }
            ]
        }

        req = Request(
            ASAAS_BASE_URL + "/checkouts",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "VAI_DE_MOTO/1.0",
                "access_token": ASAAS_API_KEY
            },
            method="POST"
        )

        with urlopen(req, timeout=20) as resp:
            dados = json.loads(resp.read().decode("utf-8"))

        checkout_id = dados.get("id")
        checkout_url = dados.get("link") or dados.get("url") or (f"https://sandbox.asaas.com/checkoutSession/show?id={checkout_id}" if checkout_id else "")

        if not checkout_id or not checkout_url:
            return None, None, "Asaas não retornou o link do Checkout."

        return checkout_id, checkout_url, None

    except Exception as e:
        detalhe = str(e)

        try:
            from urllib.error import HTTPError

            if isinstance(e, HTTPError):
                corpo = e.read().decode("utf-8", errors="replace").strip()
                if corpo:
                    detalhe = f"HTTP {e.code}: {corpo}"
        except Exception:
            pass

        return None, None, f"Erro ao criar Checkout Asaas: {detalhe}"


def iniciar_banco():
    conn = conectar()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS motoqueiros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            telefone TEXT NOT NULL,
            cpf TEXT NOT NULL,
            moto TEXT NOT NULL,
            placa TEXT NOT NULL,
            localizacao TEXT NOT NULL,
            observacao TEXT DEFAULT '',
            status TEXT DEFAULT 'pendente',
            conexao TEXT DEFAULT 'offline',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


    conn.execute("""
        CREATE TABLE IF NOT EXISTS passageiros (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            telefone TEXT NOT NULL,
            cpf TEXT NOT NULL,
            localizacao TEXT NOT NULL,
            observacao TEXT DEFAULT '',
            status TEXT DEFAULT 'ativo',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # ================================
    # TABELA DE CORRIDAS
    # ================================
    conn.execute("""
        CREATE TABLE IF NOT EXISTS corridas_vai (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            passageiro_id INTEGER,
            motorista_id INTEGER,
            origem TEXT NOT NULL,
            destino TEXT NOT NULL,
            valor REAL DEFAULT 0,
            status TEXT DEFAULT 'PENDENTE',
            observacao TEXT DEFAULT '',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            iniciado_em TIMESTAMP,
            concluido_em TIMESTAMP,
            cancelado_em TIMESTAMP,
            FOREIGN KEY (passageiro_id) REFERENCES passageiros(id),
            FOREIGN KEY (motorista_id) REFERENCES motoqueiros(id)
        )
    """)
    # Cria o administrador inicial somente se ainda não existir.
    admin = conn.execute(
        "SELECT id FROM admins WHERE usuario = ?",
        ("62993903299",)
    ).fetchone()

    if not admin:
        conn.execute(
            "INSERT INTO admins (usuario, senha) VALUES (?, ?)",
            ("62993903299", generate_password_hash("123456"))
        )
    else:
        conn.execute(
            "UPDATE admins SET senha = ? WHERE usuario = ?",
            (generate_password_hash("123456"), "62993903299")
        )

    try:
        conn.execute("ALTER TABLE corridas_vai ADD COLUMN pagamento TEXT DEFAULT 'DINHEIRO'")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE corridas_vai ADD COLUMN pagamento_status TEXT DEFAULT 'NAO_APLICAVEL'")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE corridas_vai ADD COLUMN pix_chave TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE passageiros ADD COLUMN senha TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE motoqueiros ADD COLUMN senha TEXT DEFAULT ''")
    except Exception:
        pass

    # Documentos do motorista
    for coluna in (
        "foto_motorista",
        "cnh_frente",
        "cnh_verso",
        "crlv"
    ):
        try:
            conn.execute(
                f"ALTER TABLE motoqueiros ADD COLUMN {coluna} TEXT DEFAULT ''"
            )
        except Exception:
            pass
    try:
        conn.execute("ALTER TABLE corridas_vai ADD COLUMN distancia_km REAL DEFAULT 0")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE corridas_vai ADD COLUMN taxa_app REAL DEFAULT 0")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE corridas_vai ADD COLUMN valor_motorista REAL DEFAULT 0")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE corridas_vai ADD COLUMN asaas_checkout_id TEXT DEFAULT ''")
    except Exception:
        pass

    try:
        conn.execute("ALTER TABLE corridas_vai ADD COLUMN asaas_checkout_url TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE corridas_vai ADD COLUMN etapa TEXT DEFAULT 'AGUARDANDO'")
    except Exception:
        pass

    # Corrige a senha do motorista de teste, se ele existir.
    motorista_teste = conn.execute(
        "SELECT id, senha FROM motoqueiros WHERE telefone = ?",
        ("62993903299",)
    ).fetchone()

    if motorista_teste:
        senha_ok = False
        try:
            if motorista_teste["senha"]:
                senha_ok = check_password_hash(
                    motorista_teste["senha"],
                    "123456"
                )
        except Exception:
            senha_ok = False

        if not senha_ok:
            conn.execute(
                "UPDATE motoqueiros SET senha = ? WHERE telefone = ?",
                (generate_password_hash("123456"), "62993903299")
            )

    conn.commit()
    conn.close()

iniciar_banco()

def login_obrigatorio(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if "admin_id" not in session:
            return redirect(url_for("login"))
        return func(*args, **kwargs)
    return wrapper


BASE = """
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<link rel="manifest" href="/manifest.json">
<link rel="icon" type="image/png" href="/icone/icon-192.png">
<meta name="theme-color" content="#111827">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VAI_DE_MOTO — ADMIN</title>
<style>
*{box-sizing:border-box}
body{
    margin:0;
    font-family:Arial,sans-serif;
    background:#f1f3f5;
    color:#222;
}
.topo{
    background:#111;
    color:#fff;
    padding:18px 16px;
    font-size:25px;
    font-weight:bold;
}
.menu{
    background:#fff;
    display:flex;
    gap:10px;
    padding:12px;
    overflow-x:auto;
    border-bottom:1px solid #ddd;
}
.menu a{
    background:#eee;
    color:#222;
    text-decoration:none;
    padding:14px 20px;
    border-radius:12px;
    white-space:nowrap;
    font-size:18px;
}
.menu a:hover{background:#ddd}
.container{
    max-width:1100px;
    margin:25px auto;
    padding:0 15px;
}
h1{font-size:42px;margin:15px 0 25px}
h2{font-size:27px}
.card{
    background:#fff;
    border-radius:18px;
    padding:22px;
    margin-bottom:20px;
    box-shadow:0 2px 10px rgba(0,0,0,.06);
}
.grid{
    display:grid;
    grid-template-columns:repeat(2,1fr);
    gap:18px;
}
.stat{
    background:#fff;
    border-radius:18px;
    padding:25px;
    box-shadow:0 2px 10px rgba(0,0,0,.06);
}
.stat .titulo{font-size:23px;font-weight:bold}
.stat .numero{font-size:38px;font-weight:bold;margin-top:15px}
form{margin:0}
label{
    display:block;
    font-weight:bold;
    margin:12px 0 6px;
}
input,textarea,select{
    width:100%;
    padding:14px;
    border:1px solid #ccc;
    border-radius:10px;
    font-size:16px;
}
textarea{min-height:90px;resize:vertical}
button,.btn{
    display:inline-block;
    border:0;
    background:#111;
    color:#fff;
    padding:12px 16px;
    border-radius:10px;
    text-decoration:none;
    cursor:pointer;
    font-size:15px;
    margin:4px 2px;
}
.btn-verde{background:#16833b}
.btn-vermelho{background:#c62828}
.btn-cinza{background:#666}
.btn-azul{background:#1769aa}
.alert{
    background:#fff3cd;
    color:#664d03;
    padding:14px;
    border-radius:10px;
    margin-bottom:15px;
}
.sucesso{
    background:#d1e7dd;
    color:#0f5132;
}
.erro{
    background:#f8d7da;
    color:#842029;
}
table{
    width:100%;
    border-collapse:collapse;
    min-width:900px;
}
th,td{
    padding:12px;
    border-bottom:1px solid #ddd;
    text-align:left;
    vertical-align:top;
}
th{background:#f5f5f5}
.tabela{overflow-x:auto}
.badge{
    display:inline-block;
    padding:6px 9px;
    border-radius:20px;
    background:#eee;
    font-weight:bold;
}
.pendente{background:#fff3cd}
.aprovado{background:#d1e7dd}
.reprovado{background:#f8d7da}
.online{background:#d1e7dd}
.offline{background:#eee}
.login{
    max-width:520px;
    margin:70px auto;
    padding:20px;
}
.login .card{padding:35px}
.login h1{font-size:35px}
@media(max-width:700px){
    .grid{grid-template-columns:1fr}
    h1{font-size:34px}
    .topo{font-size:21px}
}

/* ===== VAI_DE_MOTO - LETRAS GRANDES MOTORISTA E PASSAGEIRO ===== */

.motor-menu a {
    font-size: 50px !important;
    line-height: 1.2 !important;
    padding: 24px 16px !important;
    min-height: 85px !important;
}

.pub-info {
    font-size: 50px !important;
    line-height: 1.4 !important;
    padding: 28px !important;
}

.pub-card h3 {
    font-size: 54px !important;
    line-height: 1.25 !important;
}

.pub-card p,
.pub-card small {
    font-size: 42px !important;
    line-height: 1.45 !important;
}

.pub-card label {
    font-size: 42px !important;
    line-height: 1.4 !important;
}

.pub-input {
    font-size: 42px !important;
    padding: 24px !important;
    min-height: 75px !important;
}

.pub-btn {
    font-size: 44px !important;
    line-height: 1.25 !important;
    min-height: 80px !important;
    padding: 24px !important;
}

.pub-price {
    font-size: 62px !important;
    font-weight: 900 !important;
}

.ganho-num {
    font-size: 54px !important;
}

h2 {
    font-size: 54px !important;
    line-height: 1.25 !important;
}

h3 {
    font-size: 50px !important;
}

button,
input,
select,
textarea {
    font-size: 34px !important;
}

.alert {
    font-size: 34px !important;
    line-height: 1.4 !important;
    padding: 22px !important;
}

/* No celular */
@media(max-width:600px) {
    .motor-menu a {
        font-size: 44px !important;
        min-height: 82px !important;
    }

    .pub-info {
        font-size: 42px !important;
    }

    .pub-card h3 {
        font-size: 50px !important;
    }

    .pub-card p,
    .pub-card small {
        font-size: 32px !important;
    }

    .pub-card label {
        font-size: 32px !important;
    }

    .pub-input {
        font-size: 32px !important;
    }

    .pub-btn {
        font-size: 34px !important;
        min-height: 76px !important;
    }

    .pub-price {
        font-size: 50px !important;
    }

    .ganho-num {
        font-size: 44px !important;
    }

    h2 {
        font-size: 44px !important;
    }

    h3 {
        font-size: 44px !important;
    }

    button,
    input,
    select,
    textarea {
        font-size: 30px !important;
    }
}

</style>

<style>
#vaiSplash {
    position:fixed;
    inset:0;
    z-index:99999;
    background:#111827;
    display:flex;
    align-items:center;
    justify-content:center;
    flex-direction:column;
    transition:opacity .5s ease;
}
#vaiSplash img {
    width:min(75vw,320px);
    max-height:320px;
    object-fit:contain;
    border-radius:30px;
}
#vaiSplash .splash-titulo {
    margin-top:20px;
    color:white;
    font-size:28px;
    font-weight:800;
    letter-spacing:1px;
}
#vaiSplash .splash-carregando {
    margin-top:12px;
    color:#d1d5db;
    font-size:15px;
}
</style>

</head>
<body>

<div id="vaiSplash">
    <img src="/icone/splash-vai-de-moto.png"
         onerror="this.src='/icone/logo-vai-de-moto.png'">
    <div class="splash-titulo">VAI_DE_MOTO</div>
    <div class="splash-carregando">Carregando...</div>
</div>


<div class="top" style="display:flex;align-items:center;gap:12px;padding:10px 18px;"><img src="/static/logo-vai-de-moto.png" alt="VAI_DE_MOTO" style="width:52px;height:52px;object-fit:contain;border-radius:12px;"><div><div style="font-size:22px;font-weight:800;">VAI_DE_MOTO — ADMIN</div><div style="font-size:13px;opacity:.75;">PAINEL ADMINISTRATIVO</div></div></div>

{% if session.get("admin_id") %}
<div class="menu">
    <a href="{{ url_for('dashboard') }}">📊 Início</a>
    <a href="{{ url_for('motoqueiros') }}">🏍️ Motoqueiros</a>
    <a href="{{ url_for("passageiros") }}">👥 Passageiros</a>
    <a href="/corridas">🚕 Corridas</a>
    <a href="{{ url_for('logout') }}">🚪 Sair</a>
</div>
{% endif %}

<div class="container">
{% with mensagens = get_flashed_messages(with_categories=true) %}
    {% for categoria, mensagem in mensagens %}
        <div class="alert {{ categoria }}">{{ mensagem }}</div>
    {% endfor %}
{% endwith %}

{{ conteudo|safe }}
</div>

<script>
window.addEventListener("load", function() {
    setTimeout(function() {
        const splash = document.getElementById("vaiSplash");
        if (splash) {
            splash.style.opacity = "0";
            setTimeout(function() {
                splash.remove();
            }, 500);
        }
    }, 0);

    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.register("/service-worker.js")
            .catch(function(err) {
                console.log("PWA:", err);
            });
    }
});
</script>

</body>
</html>
"""


def pagina(conteudo):
    return render_template_string(BASE, conteudo=conteudo)


LOGIN_HTML = """
<div class="login">
<div class="card">
<h1><img src="/icone/logo-vai-de-moto.png" style="height:55px;vertical-align:middle;border-radius:12px;"> VAI_DE_MOTO</h1>
<h2>Painel Administrativo</h2>

<form method="post">
<label>Usuário</label>
<input name="usuario" placeholder="Usuário" required>

<label>Senha</label>
<input name="senha" type="password" placeholder="Senha" required>

<button type="submit" style="width:100%;margin-top:18px;font-size:18px">
ENTRAR
</button>
</form>

<div class="alert" style="margin-top:20px">
Usuário inicial: <b>admin</b><br>
Senha inicial: <b>123456</b>
</div>
</div>
</div>
"""


@app.route("/login", methods=["GET", "POST"])
@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        senha = request.form.get("senha", "")

        conn = conectar()
        admin = conn.execute(
            "SELECT * FROM admins WHERE usuario = ?",
            (usuario,)
        ).fetchone()
        conn.close()

        if admin and check_password_hash(admin["senha"], senha):
            session["admin_id"] = admin["id"]
            session["admin_usuario"] = admin["usuario"]
            return dashboard()

        flash("Usuário ou senha inválidos.", "erro")

    return pagina(LOGIN_HTML)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin/motorista/<int:id>/documento/<campo>")
@login_obrigatorio
def documento_motorista(id, campo):
    campos_permitidos = {
        "foto_motorista": "foto_motorista",
        "cnh_frente": "cnh_frente",
        "cnh_verso": "cnh_verso",
        "crlv": "crlv",
    }

    if campo not in campos_permitidos:
        return "Documento inválido.", 404

    conn = conectar()
    motorista = conn.execute(
        "SELECT nome, foto_motorista, cnh_frente, cnh_verso, crlv FROM motoqueiros WHERE id=?",
        (id,)
    ).fetchone()
    conn.close()

    if not motorista:
        return "Motorista não encontrado.", 404

    caminho = motorista[campos_permitidos[campo]]

    if not caminho:
        return "Documento não enviado.", 404

    arquivo = Path(caminho)

    if not arquivo.is_file():
        return "Arquivo do documento não encontrado.", 404

    pasta_base = Path("documentos_motoristas").resolve()
    arquivo_real = arquivo.resolve()

    try:
        arquivo_real.relative_to(pasta_base)
    except ValueError:
        return "Arquivo inválido.", 403

    return send_file(str(arquivo_real))


@app.route("/")
@login_obrigatorio
def dashboard():
    conn = conectar()

    total = conn.execute(
        "SELECT COUNT(*) AS n FROM motoqueiros"
    ).fetchone()["n"]

    aprovados = conn.execute(
        "SELECT COUNT(*) AS n FROM motoqueiros WHERE status='aprovado'"
    ).fetchone()["n"]

    pendentes = conn.execute(
        "SELECT COUNT(*) AS n FROM motoqueiros WHERE status='pendente'"
    ).fetchone()["n"]

    online = conn.execute(
        "SELECT COUNT(*) AS n FROM motoqueiros WHERE conexao='online'"
    ).fetchone()["n"]

    passageiros_total = conn.execute(
        "SELECT COUNT(*) AS n FROM passageiros"
    ).fetchone()["n"]

    total_corridas = conn.execute(
        "SELECT COUNT(*) AS n FROM corridas_vai"
    ).fetchone()["n"]

    conn.close()

    html = f"""
    
<div style="display:flex;align-items:center;gap:15px;margin-bottom:25px;">
    <img src="/icone/logo-vai-de-moto.png"
         style="width:85px;height:85px;object-fit:contain;border-radius:18px;"
         alt="VAI_DE_MOTO">
    <div>
        <h1 style="margin:0;">VAI_DE_MOTO</h1>
        <div style="font-size:18px;color:#666;margin-top:4px;">
            Painel Administrativo
        </div>
    </div>
</div>


    <div class="grid">
        <div class="stat">
            <div class="titulo">🏍️ Motoqueiros</div>
            <div class="numero">{total} / {LIMITE_MOTOQUEIROS}</div>
            <p>{aprovados} aprovados</p>
            <p>{pendentes} pendentes</p>
        </div>

        <div class="stat">
            <div class="titulo">🟢 Online</div>
            <div class="numero">{online}</div>
            <p>Motoqueiros conectados</p>
        </div>

        <div class="stat">
            <div class="titulo">👥 Passageiros</div>
            <div class="numero">{passageiros_total}</div>
            <p>Passageiros cadastrados</p>
        </div>

        <div class="stat">
            <div class="titulo">🚕 Corridas</div>
            <div class="numero">{total_corridas}</div>
        </div>
    </div>

    <div class="card">
        <h2>🏍️ Gerenciar motoqueiros</h2>
        <p>Cadastre e aprove os motoqueiros pelo painel.</p>
        <a class="btn btn-azul" href="{url_for('motoqueiros')}">
            ABRIR MOTOQUEIROS
        </a>
    </div>

    <div class="card">
        <h2>👥 Gerenciar passageiros</h2>
        <p>Cadastre e gerencie os passageiros.</p>
        <a class="btn btn-azul" href="{url_for('passageiros')}">
            ABRIR PASSAGEIROS
        </a>
    </div>


    <div class="card">
        <h2>💰 Financeiro</h2>
        <p>
            Faturamento, taxa de 9%, ganhos dos motoristas
            e controle dos repasses.
        </p>

        <a class="btn btn-azul"
           href="{url_for('financeiro')}">
            💰 ABRIR FINANCEIRO
        </a>
    </div>
    """

    html += """
    <div class="card">
        <h2>📡 Monitoramento</h2>
        <p>Acompanhe motoristas, GPS e corridas aceitas.</p>
        <a class="btn btn-azul"
           href="/admin/monitoramento">
            📍 ABRIR MONITORAMENTO
        </a>
    </div>

    <div class="card">
        <h2>💰 Solicitações de Saque</h2>
        <p>Veja e aprove os saques dos motoristas.</p>
        <a class="btn btn-azul"
           href="/saques">
            💰 ABRIR SAQUES
        </a>
    </div>
    """

    return pagina(html)


@app.route("/motoqueiros", methods=["GET", "POST"])
@login_obrigatorio
def motoqueiros():
    if request.method == "POST":
        conn = conectar()

        total = conn.execute(
            "SELECT COUNT(*) AS n FROM motoqueiros"
        ).fetchone()["n"]

        if total >= LIMITE_MOTOQUEIROS:
            conn.close()
            flash("Limite de 20 motoqueiros atingido.", "erro")
            return redirect(url_for("motoqueiros"))

        nome = request.form.get("nome", "").strip()
        telefone = request.form.get("telefone", "").strip()
        cpf = request.form.get("cpf", "").strip()
        moto = request.form.get("moto", "").strip()
        placa = request.form.get("placa", "").strip().upper()
        localizacao = request.form.get("localizacao", "").strip()
        observacao = request.form.get("observacao", "").strip()

        if not all([nome, telefone, cpf, moto, placa, localizacao]):
            conn.close()
            flash("Preencha todos os campos obrigatórios.", "erro")
            return redirect(url_for("motoqueiros"))

        conn.execute("""
            INSERT INTO motoqueiros
            (nome, telefone, cpf, moto, placa, localizacao, observacao)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            nome, telefone, cpf, moto, placa, localizacao, observacao
        ))

        conn.commit()
        conn.close()

        flash("Motoqueiro cadastrado com sucesso. Aguardando aprovação.", "sucesso")
        return redirect(url_for("motoqueiros"))

    conn = conectar()
    lista = conn.execute(
        "SELECT * FROM motoqueiros ORDER BY id DESC"
    ).fetchall()
    conn.close()

    linhas = ""

    for m in lista:
        status_class = m["status"]
        conexao_class = m["conexao"]

        if m["status"] == "aprovado":
            status_texto = "APROVADO"
        elif m["status"] == "reprovado":
            status_texto = "REPROVADO"
        else:
            status_texto = "PENDENTE"

        conexao_texto = "ONLINE" if m["conexao"] == "online" else "OFFLINE"

        linhas += f"""
        <tr>
            <td>{m["id"]}</td>
            <td><b>{m["nome"]}</b></td>
            <td>{m["telefone"]}</td>
            <td>{m["cpf"]}</td>
            <td>{m["moto"]}<br><b>{m["placa"]}</b></td>
            <td>{m["localizacao"]}</td>
            <td>
                <span class="badge {status_class}">
                    {status_texto}
                </span>
            </td>
            <td>
                <span class="badge {conexao_class}">
                    {conexao_texto}
                </span>
            </td>
            <td>
                {m["observacao"] or "-"}
            </td>
            <td>
                <a class="btn btn-azul"
                   href="{url_for('documento_motorista', id=m['id'], campo='foto_motorista')}"
                   target="_blank">
                   📷 Foto
                </a>

                <a class="btn btn-azul"
                   href="{url_for('documento_motorista', id=m['id'], campo='cnh_frente')}"
                   target="_blank">
                   🪪 CNH Frente
                </a>

                <a class="btn btn-azul"
                   href="{url_for('documento_motorista', id=m['id'], campo='cnh_verso')}"
                   target="_blank">
                   🪪 CNH Verso
                </a>

                <a class="btn btn-azul"
                   href="{url_for('documento_motorista', id=m['id'], campo='crlv')}"
                   target="_blank">
                   🛵 CRLV
                </a>
            </td>
            <td>
                <a class="btn btn-verde"
                   href="{url_for('alterar_status', id=m['id'], status='aprovado')}">
                   Aprovar
                </a>

                <a class="btn btn-vermelho"
                   href="{url_for('alterar_status', id=m['id'], status='reprovado')}">
                   Reprovar
                </a>

                <a class="btn btn-azul"
                   href="{url_for('alterar_conexao', id=m['id'], conexao='online')}">
                   Online
                </a>

                <a class="btn btn-cinza"
                   href="{url_for('alterar_conexao', id=m['id'], conexao='offline')}">
                   Offline
                </a>

                <a class="btn btn-vermelho"
                   href="{url_for('excluir_motoqueiro', id=m['id'])}"
                   onclick="return confirm('Excluir este motoqueiro?')">
                   Excluir
                </a>
            </td>
        </tr>
        """

    if not linhas:
        linhas = """
        <tr>
            <td colspan="11" style="text-align:center">
                Nenhum motoqueiro cadastrado.
            </td>
        </tr>
        """

    html = f"""
    <h1>🏍️ Motoqueiros</h1>

    <div class="card">
        <h2>➕ Cadastrar motoqueiro</h2>
        <p>Campos com * são obrigatórios.</p>

        <form method="post">
            <label>Nome *</label>
            <input name="nome" placeholder="Nome completo" required>

            <label>Telefone *</label>
            <input name="telefone" placeholder="(62) 99999-9999" required>

            <label>CPF *</label>
            <input name="cpf" placeholder="000.000.000-00" required>

            <label>Modelo da moto *</label>
            <input name="moto" placeholder="Ex.: Honda CG 160" required>

            <label>Placa *</label>
            <input name="placa" placeholder="ABC1D23" required>

            <label>Localização *</label>
            <input name="localizacao" placeholder="Cidade / bairro" required>

            <label>Observação</label>
            <textarea name="observacao"
                placeholder="Ex.: documento pendente, observação administrativa..."></textarea>

            <button type="submit">
                🏍️ CADASTRAR MOTOQUEIRO
            </button>
        </form>
    </div>

    <div class="card">
        <h2>📋 Motoqueiros cadastrados</h2>
        <p><b>Total:</b> {len(lista)} / {LIMITE_MOTOQUEIROS}</p>

        <div class="tabela">
        <table>
            <tr>
                <th>ID</th>
                <th>Nome</th>
                <th>Telefone</th>
                <th>CPF</th>
                <th>Moto / Placa</th>
                <th>Localização</th>
                <th>Status</th>
                <th>Conexão</th>
                <th>Observação</th>
                <th>Documentos</th>
                <th>Ações</th>
            </tr>
            {linhas}
        </table>
        </div>
    </div>
    """

    return pagina(html)


@app.route("/passageiros", methods=["GET", "POST"])
@login_obrigatorio
def passageiros():
    if request.method == "POST":
        conn = conectar()

        nome = request.form.get("nome", "").strip()
        telefone = request.form.get("telefone", "").strip()
        cpf = request.form.get("cpf", "").strip()
        localizacao = request.form.get("localizacao", "").strip()
        observacao = request.form.get("observacao", "").strip()

        if not all([nome, telefone, cpf, localizacao]):
            conn.close()
            flash("Preencha todos os campos obrigatórios.", "erro")
            return redirect(url_for("passageiros"))

        conn.execute("""
            INSERT INTO passageiros
            (nome, telefone, cpf, localizacao, observacao)
            VALUES (?, ?, ?, ?, ?)
        """, (
            nome, telefone, cpf, localizacao, observacao
        ))

        conn.commit()
        conn.close()

        flash("Passageiro cadastrado com sucesso.", "sucesso")
        return redirect(url_for("passageiros"))

    conn = conectar()

    lista = conn.execute("""
        SELECT * FROM passageiros
        ORDER BY id DESC
    """).fetchall()

    conn.close()

    linhas = ""

    for p in lista:
        status = p["status"] if "status" in p.keys() else "ativo"
        status_texto = "ATIVO" if status == "ativo" else "BLOQUEADO"
        status_class = "aprovado" if status == "ativo" else "reprovado"

        linhas += f"""
        <tr>
            <td>{p["id"]}</td>
            <td><b>{p["nome"]}</b></td>
            <td>{p["telefone"]}</td>
            <td>{p["cpf"]}</td>
            <td>{p["localizacao"]}</td>
            <td>{p["observacao"] or "-"}</td>

            <td>
                <span class="badge {status_class}">
                    {status_texto}
                </span>
            </td>

            <td>
                <a class="btn btn-verde"
                   href="{url_for('alterar_status_passageiro', id=p['id'], status='ativo')}">
                   Ativar
                </a>

                <a class="btn btn-vermelho"
                   href="{url_for('alterar_status_passageiro', id=p['id'], status='bloqueado')}">
                   Bloquear
                </a>

                <a class="btn btn-vermelho"
                   href="{url_for('excluir_passageiro', id=p['id'])}"
                   onclick="return confirm('Excluir este passageiro?')">
                   Excluir
                </a>
            </td>
        </tr>
        """

    if not linhas:
        linhas = """
        <tr>
            <td colspan="8" style="text-align:center">
                Nenhum passageiro cadastrado.
            </td>
        </tr>
        """

    html = f"""
    <h1>👥 Passageiros</h1>

    <div class="card">
        <h2>➕ Cadastrar passageiro</h2>
        <p>Campos com * são obrigatórios.</p>

        <form method="post">

            <label>Nome *</label>
            <input
                name="nome"
                placeholder="Nome completo"
                required
            >

            <label>Telefone *</label>
            <input
                name="telefone"
                placeholder="(62) 99999-9999"
                required
            >

            <label>CPF *</label>
            <input
                name="cpf"
                placeholder="000.000.000-00"
                required
            >

            <label>Localização *</label>
            <input
                name="localizacao"
                placeholder="Cidade / bairro"
                required
            >

            <label>Observação</label>
            <textarea
                name="observacao"
                placeholder="Observação do passageiro..."
            ></textarea>

            <button type="submit">
                👥 CADASTRAR PASSAGEIRO
            </button>

        </form>
    </div>

    <div class="card">
        <h2>📋 Passageiros cadastrados</h2>

        <p>
            <b>Total:</b> {len(lista)}
        </p>

        <div class="tabela">
            <table>

                <tr>
                    <th>ID</th>
                    <th>Nome</th>
                    <th>Telefone</th>
                    <th>CPF</th>
                    <th>Localização</th>
                    <th>Observação</th>
                    <th>Status</th>
                    <th>Ações</th>
                </tr>

                {linhas}

            </table>
        </div>
    </div>
    """

    return pagina(html)


@app.route("/passageiros/status/<int:id>/<status>")
@login_obrigatorio
def alterar_status_passageiro(id, status):

    if status not in ("ativo", "bloqueado"):
        flash("Status de passageiro inválido.", "erro")
        return redirect(url_for("passageiros"))

    conn = conectar()

    conn.execute(
        "UPDATE passageiros SET status=? WHERE id=?",
        (status, id)
    )

    conn.commit()
    conn.close()

    flash("Status do passageiro atualizado.", "sucesso")

    return redirect(url_for("passageiros"))


@app.route("/passageiros/excluir/<int:id>")
@login_obrigatorio
def excluir_passageiro(id):

    conn = conectar()

    conn.execute(
        "DELETE FROM passageiros WHERE id=?",
        (id,)
    )

    conn.commit()
    conn.close()

    flash("Passageiro excluído.", "sucesso")

    return redirect(url_for("passageiros"))



@app.route("/motoqueiros/status/<int:id>/<status>")
@login_obrigatorio
def alterar_status(id, status):
    if status not in ("aprovado", "reprovado", "pendente"):
        flash("Status inválido.", "erro")
        return redirect(url_for("motoqueiros"))

    conn = conectar()
    conn.execute(
        "UPDATE motoqueiros SET status=? WHERE id=?",
        (status, id)
    )
    conn.commit()
    conn.close()

    flash("Status atualizado.", "sucesso")
    return redirect(url_for("motoqueiros"))


@app.route("/motoqueiros/conexao/<int:id>/<conexao>")
@login_obrigatorio
def alterar_conexao(id, conexao):
    if conexao not in ("online", "offline"):
        flash("Conexão inválida.", "erro")
        return redirect(url_for("motoqueiros"))

    conn = conectar()
    conn.execute(
        "UPDATE motoqueiros SET conexao=? WHERE id=?",
        (conexao, id)
    )
    conn.commit()
    conn.close()

    flash("Conexão atualizada.", "sucesso")
    return redirect(url_for("motoqueiros"))


@app.route("/motoqueiros/excluir/<int:id>")
@login_obrigatorio
def excluir_motoqueiro(id):
    conn = conectar()
    conn.execute(
        "DELETE FROM motoqueiros WHERE id=?",
        (id,)
    )
    conn.commit()
    conn.close()

    flash("Motoqueiro excluído.", "sucesso")
    return redirect(url_for("motoqueiros"))

# ============================================================
# ADMIN - CORRIDAS
# ============================================================


@app.route("/financeiro", methods=["GET", "POST"])
@login_obrigatorio
def financeiro():

    conn = conectar()

    # Migração automática para controle de repasse
    try:
        conn.execute("""
            ALTER TABLE corridas_vai
            ADD COLUMN repasse_status TEXT DEFAULT 'PENDENTE'
        """)
    except Exception:
        pass

    try:
        conn.execute("""
            ALTER TABLE corridas_vai
            ADD COLUMN repasse_em TIMESTAMP
        """)
    except Exception:
        pass

    conn.commit()

    if request.method == "POST":

        motorista_id = request.form.get("motorista_id", type=int)
        inicio = request.form.get("inicio", "").strip()
        fim = request.form.get("fim", "").strip()

        if motorista_id:

            sql = """
                UPDATE corridas_vai
                SET
                    repasse_status = 'REPASSADO',
                    repasse_em = CURRENT_TIMESTAMP
                WHERE motorista_id = ?
                  AND status = 'CONCLUIDA'
                  AND COALESCE(repasse_status, 'PENDENTE') = 'PENDENTE'
            """

            params = [motorista_id]

            if inicio:
                sql += " AND DATE(criado_em) >= DATE(?)"
                params.append(inicio)

            if fim:
                sql += " AND DATE(criado_em) <= DATE(?)"
                params.append(fim)

            conn.execute(sql, params)
            conn.commit()

        conn.close()

        return redirect(
            url_for(
                "financeiro",
                inicio=inicio,
                fim=fim
            )
        )

    inicio = request.args.get("inicio", "").strip()
    fim = request.args.get("fim", "").strip()

    filtro_sql = """
        WHERE c.status = 'CONCLUIDA'
    """

    params = []

    if inicio:
        filtro_sql += " AND DATE(c.criado_em) >= DATE(?)"
        params.append(inicio)

    if fim:
        filtro_sql += " AND DATE(c.criado_em) <= DATE(?)"
        params.append(fim)

    motoristas = conn.execute("""
        SELECT
            m.id,
            m.nome,
            m.telefone
        FROM motoqueiros m
        ORDER BY m.nome
    """).fetchall()

    resumo = conn.execute(f"""
        SELECT
            c.motorista_id,

            COUNT(c.id) AS quantidade,

            COALESCE(SUM(c.valor), 0) AS bruto,

            COALESCE(
                SUM(
                    CASE
                        WHEN c.taxa_app IS NOT NULL
                             AND c.taxa_app > 0
                        THEN c.taxa_app
                        ELSE COALESCE(c.valor, 0) * 0.09
                    END
                ),
                0
            ) AS taxa,

            COALESCE(
                SUM(
                    CASE
                        WHEN c.valor_motorista IS NOT NULL
                             AND c.valor_motorista > 0
                        THEN c.valor_motorista
                        ELSE COALESCE(c.valor, 0) * 0.91
                    END
                ),
                0
            ) AS motorista,

            SUM(
                CASE
                    WHEN COALESCE(c.repasse_status, 'PENDENTE') = 'REPASSADO'
                    THEN 1
                    ELSE 0
                END
            ) AS repassadas,

            SUM(
                CASE
                    WHEN COALESCE(c.repasse_status, 'PENDENTE') = 'PENDENTE'
                    THEN 1
                    ELSE 0
                END
            ) AS pendentes

        FROM corridas_vai c
        {filtro_sql}
        GROUP BY c.motorista_id
    """, params).fetchall()

    conn.close()

    resumo_por_motorista = {
        r["motorista_id"]: r
        for r in resumo
    }

    total_bruto = sum(float(r["bruto"] or 0) for r in resumo)
    total_taxa = sum(float(r["taxa"] or 0) for r in resumo)
    total_motoristas = sum(float(r["motorista"] or 0) for r in resumo)
    total_corridas = sum(int(r["quantidade"] or 0) for r in resumo)

    cards = ""

    for m in motoristas:

        r = resumo_por_motorista.get(m["id"])

        if r:
            quantidade = int(r["quantidade"] or 0)
            bruto = float(r["bruto"] or 0)
            taxa = float(r["taxa"] or 0)
            valor_motorista = float(r["motorista"] or 0)
            repassadas = int(r["repassadas"] or 0)
            pendentes = int(r["pendentes"] or 0)
        else:
            quantidade = 0
            bruto = 0
            taxa = 0
            valor_motorista = 0
            repassadas = 0
            pendentes = 0

        cards += f"""
        <div class="card" style="
            margin-bottom:18px;
            border-left:6px solid #111;
        ">

            <div style="
                display:flex;
                justify-content:space-between;
                align-items:center;
                gap:10px;
                flex-wrap:wrap;
            ">

                <div>
                    <h2 style="margin:0;">
                        🏍️ {m["nome"]}
                    </h2>

                    <div style="color:#666;margin-top:5px;">
                        {m["telefone"] or ""}
                    </div>
                </div>

                <div style="
                    font-size:24px;
                    font-weight:bold;
                ">
                    R$ {valor_motorista:.2f}
                </div>

            </div>

            <hr>

            <div class="grid">

                <div class="stat">
                    <div class="titulo">🏍️ Corridas</div>
                    <div class="numero">{quantidade}</div>
                </div>

                <div class="stat">
                    <div class="titulo">💰 Bruto</div>
                    <div class="numero">
                        R$ {bruto:.2f}
                    </div>
                </div>

                <div class="stat">
                    <div class="titulo">🏢 Taxa 9%</div>
                    <div class="numero">
                        R$ {taxa:.2f}
                    </div>
                </div>

                <div class="stat">
                    <div class="titulo">🏍️ Motorista 91%</div>
                    <div class="numero">
                        R$ {valor_motorista:.2f}
                    </div>
                </div>

            </div>

            <div style="
                display:flex;
                gap:10px;
                flex-wrap:wrap;
                margin-top:15px;
            ">

                <span style="
                    background:#fff3cd;
                    padding:9px 13px;
                    border-radius:10px;
                    font-weight:bold;
                ">
                    🟡 {pendentes} pendente(s)
                </span>

                <span style="
                    background:#d1e7dd;
                    padding:9px 13px;
                    border-radius:10px;
                    font-weight:bold;
                ">
                    🟢 {repassadas} repassada(s)
                </span>

            </div>

            <form method="POST"
                  action="{url_for('financeiro')}"
                  style="margin-top:18px;">

                <input type="hidden"
                       name="motorista_id"
                       value="{m["id"]}">

                <input type="hidden"
                       name="inicio"
                       value="{inicio}">

                <input type="hidden"
                       name="fim"
                       value="{fim}">

                <button type="submit"
                        class="btn btn-azul"
                        onclick="return confirm('Confirmar repasse deste motorista no período selecionado?');">
                    💸 MARCAR COMO REPASSADO
                </button>

            </form>

        </div>
        """

    html = f"""

    <div style="
        display:flex;
        align-items:center;
        gap:15px;
        margin-bottom:25px;
    ">

        <img src="/icone/logo-vai-de-moto.png"
             style="
                width:75px;
                height:75px;
                object-fit:contain;
                border-radius:18px;
             "
             alt="VAI_DE_MOTO">

        <div>
            <h1 style="margin:0;">
                💰 FINANCEIRO
            </h1>

            <div style="
                font-size:17px;
                color:#666;
                margin-top:4px;
            ">
                Controle de ganhos e repasses
            </div>
        </div>

    </div>

    <div class="card">

        <h2>📅 Filtrar período</h2>

        <form method="GET"
              action="{url_for('financeiro')}"
              style="
                display:flex;
                gap:10px;
                flex-wrap:wrap;
                align-items:end;
              ">

            <div>
                <label>Data inicial</label><br>
                <input type="date"
                       name="inicio"
                       value="{inicio}">
            </div>

            <div>
                <label>Data final</label><br>
                <input type="date"
                       name="fim"
                       value="{fim}">
            </div>

            <button class="btn btn-azul"
                    type="submit">
                🔎 FILTRAR
            </button>

            <a class="btn"
               href="{url_for('financeiro')}">
                LIMPAR
            </a>

        </form>

    </div>

    <div class="grid">

        <div class="stat">
            <div class="titulo">🚕 Corridas concluídas</div>
            <div class="numero">{total_corridas}</div>
        </div>

        <div class="stat">
            <div class="titulo">💰 Faturamento bruto</div>
            <div class="numero">
                R$ {total_bruto:.2f}
            </div>
        </div>

        <div class="stat">
            <div class="titulo">🏢 VAI_DE_MOTO 9%</div>
            <div class="numero">
                R$ {total_taxa:.2f}
            </div>
        </div>

        <div class="stat">
            <div class="titulo">🏍️ Motoristas 91%</div>
            <div class="numero">
                R$ {total_motoristas:.2f}
            </div>
        </div>

    </div>

    <div class="card">

        <h2>🏍️ Repasses por motorista</h2>

        <p>
            O motorista recebe <strong>91%</strong>
            e o aplicativo fica com <strong>9%</strong>.
        </p>

    </div>

    {cards}

    <div style="margin-top:20px;">
        <a class="btn btn-azul"
           href="{url_for('dashboard')}">
            ⬅️ VOLTAR AO PAINEL
        </a>
    </div>

    """

    return pagina(html)



@app.route("/corridas")
@login_obrigatorio
def corridas():
    filtro = request.args.get("status", "TODAS").upper()

    status_validos = {
        "TODAS": None,
        "PENDENTE": "PENDENTE",
        "EM_ANDAMENTO": "EM_ANDAMENTO",
        "CONCLUIDA": "CONCLUIDA",
        "CANCELADA": "CANCELADA"
    }

    if filtro not in status_validos:
        filtro = "TODAS"

    conn = conectar()

    if status_validos[filtro]:
        corridas_lista = conn.execute("""
            SELECT
                c.*,
                p.nome AS passageiro_nome,
                p.telefone AS passageiro_telefone,
                m.nome AS motorista_nome,
                m.telefone AS motorista_telefone
            FROM corridas_vai c
            LEFT JOIN passageiros p
                ON p.id = c.passageiro_id
            LEFT JOIN motoqueiros m
                ON m.id = c.motorista_id
            WHERE c.status = ?
            ORDER BY c.id DESC
        """, (status_validos[filtro],)).fetchall()
    else:
        corridas_lista = conn.execute("""
            SELECT
                c.*,
                p.nome AS passageiro_nome,
                p.telefone AS passageiro_telefone,
                m.nome AS motorista_nome,
                m.telefone AS motorista_telefone
            FROM corridas_vai c
            LEFT JOIN passageiros p
                ON p.id = c.passageiro_id
            LEFT JOIN motoqueiros m
                ON m.id = c.motorista_id
            ORDER BY c.id DESC
        """).fetchall()

    conn.close()

    def classe_status(status):
        status = (status or "").upper()

        if status == "PENDENTE":
            return "status-pendente"

        if status in ("ACEITA", "EM_ANDAMENTO"):
            return "status-andamento"

        if status == "CONCLUIDA":
            return "status-concluida"

        if status == "CANCELADA":
            return "status-cancelada"

        return "status-outro"

    def texto_status(status):
        status = (status or "").upper()

        nomes = {
            "PENDENTE": "PENDENTE",
            "ACEITA": "ACEITA",
            "EM_ANDAMENTO": "EM ANDAMENTO",
            "CONCLUIDA": "CONCLUÍDA",
            "CANCELADA": "CANCELADA"
        }

        return nomes.get(status, status)

    cards = ""

    for c in corridas_lista:

        status = c["status"] or "PENDENTE"

        passageiro = c["passageiro_nome"] or "Não informado"
        motorista = c["motorista_nome"] or "Ainda não definido"

        partida = c["origem"] or "-"
        destino = c["destino"] or "-"

        try:
            valor = float(c["valor"] or 0)
        except:
            valor = 0

        botoes = f"""
        <a class="btn btn-azul"
           href="{url_for('corrida_detalhes', id=c['id'])}">
           👁️ VER DETALHES
        </a>
        """

        if status == "PENDENTE":
            botoes += f"""
            <a class="btn btn-verde"
               href="{url_for('admin_corrida_status',
                              id=c['id'],
                              status='ACEITA')}">
               🟢 ACEITAR
            </a>
            """

        elif status == "ACEITA":
            botoes += f"""
            <a class="btn btn-verde"
               href="{url_for('admin_corrida_status',
                              id=c['id'],
                              status='EM_ANDAMENTO')}">
               🚦 INICIAR
            </a>
            """

        elif status == "EM_ANDAMENTO":
            botoes += f"""
            <a class="btn btn-verde"
               href="{url_for('admin_corrida_status',
                              id=c['id'],
                              status='CONCLUIDA')}">
               ✅ CONCLUIR
            </a>
            """

        if status in ("PENDENTE", "ACEITA", "EM_ANDAMENTO"):
            botoes += f"""
            <a class="btn btn-vermelho"
               href="{url_for('admin_corrida_status',
                              id=c['id'],
                              status='CANCELADA')}"
               onclick="return confirm('Cancelar esta corrida?')">
               🔴 CANCELAR
            </a>
            """

        botoes += f"""
        <a class="btn btn-vermelho"
           href="{url_for('excluir_corrida',
                          id=c['id'])}"
           onclick="return confirm('Excluir esta corrida definitivamente?')">
           🗑️ EXCLUIR
        </a>
        """

        cards += f"""
        <div class="corrida-card">

            <div class="corrida-topo">
                <strong>🚕 CORRIDA #{c['id']}</strong>

                <span class="badge-corrida {classe_status(status)}">
                    {texto_status(status)}
                </span>
            </div>

            <div class="corrida-info">
                <p>👤 <b>Passageiro:</b> {passageiro}</p>

                <p>🏍️ <b>Motoqueiro:</b> {motorista}</p>

                <p>📍 <b>Origem:</b> {partida}</p>

                <p>🏁 <b>Destino:</b> {destino}</p>

                <p>💰 <b>Valor pago:</b>
                R$ {valor:.2f}
            </p>

            <p>🏢 <b>Taxa VAI_DE_MOTO (9%):</b>
                R$ {round(valor * 0.09, 2):.2f}
            </p>

            <p>🏍️ <b>Motorista recebe (91%):</b>
                R$ {round(valor * 0.91, 2):.2f}
            </p>
            </div>

            <div class="corrida-acoes">
                {botoes}
            </div>

        </div>
        """

    if not cards:
        cards = """
        <div class="card">
            <h2>🚕 Nenhuma corrida encontrada</h2>
            <p>Não existem corridas neste filtro.</p>
        </div>
        """

    html = f"""
    <style>

    .filtros-corridas {{
        display:flex;
        gap:8px;
        flex-wrap:wrap;
        margin-bottom:20px;
    }}

    .filtro-corrida {{
        display:inline-block;
        padding:12px 16px;
        border-radius:12px;
        background:#eee;
        color:#222;
        text-decoration:none;
        font-weight:bold;
    }}

    .filtro-corrida.ativo {{
        background:#111;
        color:#fff;
    }}

    .corridas-desktop {{
        display:block;
    }}

    .corridas-mobile {{
        display:none;
    }}

    .corrida-card {{
        background:#fff;
        border-radius:18px;
        padding:20px;
        margin-bottom:16px;
        box-shadow:0 2px 10px rgba(0,0,0,.08);
    }}

    .corrida-topo {{
        display:flex;
        justify-content:space-between;
        align-items:center;
        gap:10px;
        border-bottom:1px solid #eee;
        padding-bottom:14px;
        margin-bottom:14px;
    }}

    .badge-corrida {{
        display:inline-block;
        padding:7px 10px;
        border-radius:20px;
        font-weight:bold;
        font-size:13px;
    }}

    .status-pendente {{
        background:#fff3cd;
        color:#664d03;
    }}

    .status-andamento {{
        background:#cfe2ff;
        color:#084298;
    }}

    .status-concluida {{
        background:#d1e7dd;
        color:#0f5132;
    }}

    .status-cancelada {{
        background:#f8d7da;
        color:#842029;
    }}

    .status-outro {{
        background:#eee;
        color:#333;
    }}

    .corrida-info p {{
        margin:9px 0;
        font-size:16px;
    }}

    .corrida-acoes {{
        margin-top:16px;
        display:flex;
        flex-wrap:wrap;
        gap:5px;
    }}

    .corrida-acoes .btn {{
        margin:0;
    }}

    .tabela-corridas {{
        overflow-x:auto;
    }}

    .tabela-corridas table {{
        min-width:1050px;
    }}

    @media(max-width:700px) {{

        .corridas-desktop {{
            display:none;
        }}

        .corridas-mobile {{
            display:block;
        }}

        .filtros-corridas {{
            display:grid;
            grid-template-columns:1fr 1fr;
        }}

        .filtro-corrida {{
            text-align:center;
            padding:13px 8px;
        }}

        .filtro-corrida:first-child {{
            grid-column:1 / -1;
        }}

        .corrida-topo {{
            align-items:flex-start;
            flex-direction:column;
        }}

        .corrida-acoes {{
            display:grid;
            grid-template-columns:1fr;
        }}

        .corrida-acoes .btn {{
            width:100%;
            text-align:center;
            padding:14px 10px;
        }}
    }}

    </style>

    <h1>🚕 Corridas</h1>

    <div class="card">

        <h2>🔎 Filtrar corridas</h2>

        <div class="filtros-corridas">

            <a class="filtro-corrida {'ativo' if filtro == 'TODAS' else ''}"
               href="{url_for('corridas', status='TODAS')}">
               TODAS
            </a>

            <a class="filtro-corrida {'ativo' if filtro == 'PENDENTE' else ''}"
               href="{url_for('corridas', status='PENDENTE')}">
               🟡 PENDENTES
            </a>

            <a class="filtro-corrida {'ativo' if filtro == 'EM_ANDAMENTO' else ''}"
               href="{url_for('corridas', status='EM_ANDAMENTO')}">
               🚦 EM ANDAMENTO
            </a>

            <a class="filtro-corrida {'ativo' if filtro == 'CONCLUIDA' else ''}"
               href="{url_for('corridas', status='CONCLUIDA')}">
               🟢 CONCLUÍDAS
            </a>

            <a class="filtro-corrida {'ativo' if filtro == 'CANCELADA' else ''}"
               href="{url_for('corridas', status='CANCELADA')}">
               🔴 CANCELADAS
            </a>

        </div>

        <p>
            <b>Total exibido:</b> {len(corridas_lista)}
        </p>

    </div>

    <!-- CELULAR -->
    <div class="corridas-mobile">
        {cards}
    </div>

    <!-- COMPUTADOR -->
    <div class="corridas-desktop">

        <div class="card">

            <h2>📋 Lista de corridas</h2>

            <div class="tabela-corridas">

                <table>

                    <tr>
                        <th>ID</th>
                        <th>Passageiro</th>
                        <th>Motoqueiro</th>
                        <th>Origem</th>
                        <th>Destino</th>
                        <th>Valor</th>
                        <th>Status</th>
                        <th>Ações</th>
                    </tr>

                    {''.join(f'''
                    <tr>
                        <td><b>#{c["id"]}</b></td>

                        <td>
                            {c["passageiro_nome"] or "Não informado"}
                        </td>

                        <td>
                            {c["motorista_nome"] or "Não definido"}
                        </td>

                        <td>
                            {c["origem"] or "-"}
                        </td>

                        <td>
                            {c["destino"] or "-"}
                        </td>

                        <td>
                            R$ {float(c["valor"] or 0):.2f}
                        </td>

                        <td>
                            <span class="badge-corrida {classe_status(c["status"])}">
                                {texto_status(c["status"])}
                            </span>
                        </td>

                        <td>
                            <a class="btn btn-azul"
                               href="{url_for("corrida_detalhes", id=c["id"])}">
                               Ver
                            </a>

                            <a class="btn btn-vermelho"
                               href="{url_for("excluir_corrida", id=c["id"])}"
                               onclick="return confirm("Excluir esta corrida?")">
                               Excluir
                            </a>
                        </td>
                    </tr>
                    ''' for c in corridas_lista)}

                </table>

            </div>

        </div>

    </div>
    """

    return pagina(html)


@app.route("/corridas/<int:id>")
@login_obrigatorio
def corrida_detalhes(id):

    conn = conectar()

    corrida = conn.execute("""
        SELECT
            c.*,
            p.nome AS passageiro_nome,
            p.telefone AS passageiro_telefone,
            m.nome AS motorista_nome,
            m.telefone AS motorista_telefone
        FROM corridas_vai c
        LEFT JOIN passageiros p
            ON p.id = c.passageiro_id
        LEFT JOIN motoqueiros m
            ON m.id = c.motorista_id
        WHERE c.id = ?
    """, (id,)).fetchone()

    conn.close()

    if not corrida:
        flash("Corrida não encontrada.", "erro")
        return redirect(url_for("corridas"))

    valor = float(corrida["valor"] or 0)

    html = f"""
    <h1>🚕 Corrida #{corrida["id"]}</h1>

    <div class="card">

        <h2>👤 Passageiro</h2>

        <p>
            <b>Nome:</b>
            {corrida["passageiro_nome"] or "Não informado"}
        </p>

        <p>
            <b>Telefone:</b>
            {corrida["passageiro_telefone"] or "-"}
        </p>

        <h2>🏍️ Motoqueiro</h2>

        <p>
            <b>Nome:</b>
            {corrida["motorista_nome"] or "Ainda não definido"}
        </p>

        <p>
            <b>Telefone:</b>
            {corrida["motorista_telefone"] or "-"}
        </p>

        <h2>📍 Trajeto</h2>

        <p>
            <b>Origem:</b>
            {corrida["origem"] or "-"}
        </p>

        <p>
            <b>Destino:</b>
            {corrida["destino"] or "-"}
        </p>

        <h2>💰 Pagamento</h2>

        <p>
            <b>Valor:</b>
            R$ {valor:.2f}
        </p>

        <p>
            <b>Status:</b>
            {corrida["status"] or "-"}
        </p>

        <br>

        <a class="btn btn-cinza"
           href="{url_for("corridas")}">
           ⬅️ VOLTAR
        </a>

    </div>
    """

    return pagina(html)


@app.route("/corridas/status/<int:id>/<status>")
@login_obrigatorio
def admin_corrida_status(id, status):

    status_permitidos = (
        "PENDENTE",
        "ACEITA",
        "EM_ANDAMENTO",
        "CONCLUIDA",
        "CANCELADA"
    )

    if status not in status_permitidos:
        flash("Status inválido.", "erro")
        return redirect(url_for("corridas"))

    conn = conectar()

    corrida = conn.execute(
        "SELECT id FROM corridas_vai WHERE id=?",
        (id,)
    ).fetchone()

    if not corrida:
        conn.close()
        flash("Corrida não encontrada.", "erro")
        return redirect(url_for("corridas"))

    conn.execute(
        "UPDATE corridas_vai SET status=? WHERE id=?",
        (status, id)
    )

    conn.commit()
    conn.close()

    flash("Status da corrida atualizado.", "sucesso")

    return redirect(url_for("corridas"))


@app.route("/corridas/excluir/<int:id>")
@login_obrigatorio
def excluir_corrida(id):

    conn = conectar()

    corrida = conn.execute(
        "SELECT id FROM corridas_vai WHERE id=?",
        (id,)
    ).fetchone()

    if not corrida:
        conn.close()
        flash("Corrida não encontrada.", "erro")
        return redirect(url_for("corridas"))

    conn.execute(
        "DELETE FROM corridas_vai WHERE id=?",
        (id,)
    )

    conn.commit()
    conn.close()

    flash("Corrida excluída.", "sucesso")

    return redirect(url_for("corridas"))
# ==========================================
# LISTAGEM DE CORRIDAS + FILTROS
# ==========================================




# ============================================================
# ÁREA PÚBLICA — PASSAGEIRO + MOTORISTA
# ============================================================

PRECO_KM = 2.00
TAXA_APP = 0.09
PIX_ADMIN = "53.427.807/0001-71"


def _distancia_km(lat1, lon1, lat2, lon2):
    """Distância aproximada em linha reta (Haversine)."""
    try:
        lat1, lon1, lat2, lon2 = map(float, (lat1, lon1, lat2, lon2))
        r = 6371.0
        p1 = math.radians(lat1)
        p2 = math.radians(lat2)
        dp = math.radians(lat2 - lat1)
        dl = math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return r * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0, 1 - a)))
    except Exception:
        return 0.0


def _pagina_publica(titulo, corpo, manifesto=None):
    if manifesto == "motorista":
        manifest_href = "/static/pwa/manifest-motorista.json"
    elif manifesto == "passageiro":
        manifest_href = "/static/pwa/manifest-passageiro.json"
    else:
        manifest_href = "/manifest.json"

    html = f"""
    <link rel="manifest" href="{manifest_href}">
    <meta name="theme-color" content="#111111">
    <meta name="mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-title" content="VAI_DE_MOTO">

    <style>
    .pub-wrap{{max-width:760px;margin:25px auto;padding:0 14px}}
    .pub-card{{background:#fff;border-radius:22px;padding:24px;box-shadow:0 3px 14px rgba(0,0,0,.08);margin-bottom:18px}}
    .pub-title{{font-size:32px;font-weight:800;margin:5px 0 8px}}
    .pub-sub{{color:#666;margin-bottom:20px}}
    .pub-btn{{display:block;width:100%;padding:15px;border:0;border-radius:13px;background:#111;color:#fff;text-align:center;text-decoration:none;font-size:17px;font-weight:700;margin-top:10px}}
    .pub-green{{background:#16833b}}
    .pub-blue{{background:#1769aa}}
    .pub-yellow{{background:#d99a00;color:#111}}
    .pub-input{{width:100%;padding:14px;border:1px solid #ccc;border-radius:12px;font-size:16px;margin:6px 0 12px}}
    .pub-label{{font-weight:700;display:block;margin-top:10px}}
    .pub-grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
    .pub-info{{background:#f4f6f8;padding:15px;border-radius:14px;margin:10px 0}}
    .pub-price{{font-size:34px;font-weight:800}}
    .pub-nav{{display:flex;gap:8px;overflow:auto;margin-bottom:15px}}
    .pub-nav a{{white-space:nowrap;background:#eee;color:#222;text-decoration:none;padding:11px 14px;border-radius:12px}}
    @media(max-width:650px){{.pub-grid{{grid-template-columns:1fr}}}}
    </style>

    <script>
    if ("serviceWorker" in navigator) {{
      window.addEventListener("load", function() {{
        navigator.serviceWorker.register("/service-worker.js").catch(function(e) {{
          console.log("PWA:", e);
        }});
      }});
    }}
    </script>

    <div class="pub-wrap">
      <div class="pub-card">
        <div class="pub-title">🏍️ VAI_DE_MOTO</div>
        <div class="pub-sub">Transporte de moto rápido e local</div>
        {corpo}
      </div>
    </div>
    """
    return render_template_string(html)


CADASTRO_HTML = """
<div class="pub-card">
  <h2>Comece pelo seu perfil</h2>
  <p>Escolha como você vai usar o VAI_DE_MOTO.</p>
  <a class="pub-btn pub-green" href="/cadastro/passageiro">👤 CADASTRAR PASSAGEIRO</a>
  <a class="pub-btn pub-blue" href="/cadastro/motorista">🏍️ CADASTRAR MOTORISTA</a>
  <a class="pub-btn" href="/login-passageiro">🔐 ENTRAR COMO PASSAGEIRO</a>
  <a class="pub-btn" href="/login-motorista">🏍️ ENTRAR COMO MOTORISTA</a>
</div>
"""


@app.route("/cadastro")
def cadastro():
    return _pagina_publica("Cadastro", CADASTRO_HTML)


@app.route("/cadastro/passageiro", methods=["GET", "POST"])
def cadastro_passageiro():
    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        telefone = request.form.get("telefone", "").strip()
        cpf = request.form.get("cpf", "").strip()
        senha = request.form.get("senha", "")
        localizacao = request.form.get("localizacao", "").strip()

        if not all([nome, telefone, cpf, senha, localizacao]):
            return _pagina_publica("Cadastro", '<div class="alert erro">Preencha todos os campos.</div>' + PASSAGEIRO_CADASTRO_FORM)

        if len(senha) < 6:
            return _pagina_publica("Cadastro", '<div class="alert erro">A senha deve ter pelo menos 6 caracteres.</div>' + PASSAGEIRO_CADASTRO_FORM)

        conn = conectar()
        try:
            existente = conn.execute("SELECT id FROM passageiros WHERE telefone=?", (telefone,)).fetchone()
            if existente:
                conn.close()
                return _pagina_publica("Cadastro", '<div class="alert erro">Telefone já cadastrado.</div>' + PASSAGEIRO_CADASTRO_FORM)

            conn.execute("""
                INSERT INTO passageiros (nome, telefone, cpf, localizacao, observacao, status, senha)
                VALUES (?, ?, ?, ?, ?, 'ativo', ?)
            """, (nome, telefone, cpf, localizacao, "", generate_password_hash(senha)))
            conn.commit()
        finally:
            conn.close()

        return _pagina_publica("Cadastro concluído", """
          <div class="alert sucesso">Cadastro realizado com sucesso!</div>
          <a class="pub-btn pub-green" href="/login-passageiro">ENTRAR COMO PASSAGEIRO</a>
        """)

    return _pagina_publica("Cadastro", PASSAGEIRO_CADASTRO_FORM)


PASSAGEIRO_CADASTRO_FORM = """
<h2>👤 Cadastro de passageiro</h2>
<form method="post">
<label class="pub-label">Nome *</label>
<input class="pub-input" name="nome" required placeholder="Nome completo">
<label class="pub-label">Telefone *</label>
<input class="pub-input" name="telefone" required placeholder="(62) 99999-9999">
<label class="pub-label">CPF *</label>
<input class="pub-input" name="cpf" required placeholder="000.000.000-00">
<label class="pub-label">Localização *</label>
<input class="pub-input" name="localizacao" required placeholder="Cidade / bairro">
<label class="pub-label">Senha *</label>
<input class="pub-input" name="senha" type="password" minlength="6" required placeholder="Mínimo 6 caracteres">
<button class="pub-btn pub-green" type="submit">CRIAR CONTA</button>
</form>
"""


MOTORISTA_CADASTRO_FORM = """
<h2>🏍️ Cadastro de motorista</h2>

<p>
Após o cadastro, o administrador irá conferir seus dados e documentos
antes de liberar o acesso para trabalhar.
</p>

<form method="post" enctype="multipart/form-data">

<label class="pub-label">Nome completo *</label>
<input class="pub-input"
       name="nome"
       required
       placeholder="Nome completo">

<label class="pub-label">Telefone *</label>
<input class="pub-input"
       name="telefone"
       required
       placeholder="(62) 99999-9999">

<label class="pub-label">CPF *</label>
<input class="pub-input"
       name="cpf"
       required
       placeholder="000.000.000-00">

<label class="pub-label">Modelo da moto *</label>
<input class="pub-input"
       name="moto"
       required
       placeholder="Honda CG 160">

<label class="pub-label">Placa *</label>
<input class="pub-input"
       name="placa"
       required
       placeholder="ABC1D23">

<label class="pub-label">Localização *</label>
<input class="pub-input"
       name="localizacao"
       required
       placeholder="Cidade / bairro">

<hr>

<h3>🔐 Documentos para aprovação</h3>

<p>
Envie fotos nítidas dos documentos. Os documentos serão analisados
pelo administrador antes da aprovação.
</p>

<label class="pub-label">📷 Foto do motorista *</label>
<input class="pub-input"
       type="file"
       name="foto_motorista"
       accept="image/jpeg,image/png,image/webp"
       required>

<label class="pub-label">🪪 CNH - frente *</label>
<input class="pub-input"
       type="file"
       name="cnh_frente"
       accept="image/jpeg,image/png,image/webp,application/pdf"
       required>

<label class="pub-label">🪪 CNH - verso *</label>
<input class="pub-input"
       type="file"
       name="cnh_verso"
       accept="image/jpeg,image/png,image/webp,application/pdf"
       required>

<label class="pub-label">🛵 CRLV *</label>
<input class="pub-input"
       type="file"
       name="crlv"
       accept="image/jpeg,image/png,image/webp,application/pdf"
       required>

<hr>

<label class="pub-label">Senha *</label>
<input class="pub-input"
       name="senha"
       type="password"
       minlength="6"
       required
       placeholder="Mínimo 6 caracteres">

<button class="pub-btn pub-blue" type="submit">
ENVIAR CADASTRO
</button>

</form>
"""


@app.route("/cadastro/motorista", methods=["GET", "POST"])
def cadastro_motorista():

    if request.method == "POST":

        nome = request.form.get("nome", "").strip()
        telefone = request.form.get("telefone", "").strip()
        cpf = request.form.get("cpf", "").strip()
        moto = request.form.get("moto", "").strip()
        placa = request.form.get("placa", "").strip().upper()
        localizacao = request.form.get("localizacao", "").strip()
        senha = request.form.get("senha", "")

        foto_motorista = request.files.get("foto_motorista")
        cnh_frente = request.files.get("cnh_frente")
        cnh_verso = request.files.get("cnh_verso")
        crlv = request.files.get("crlv")

        if not all([
            nome,
            telefone,
            cpf,
            moto,
            placa,
            localizacao,
            senha
        ]):
            return _pagina_publica(
                "Cadastro",
                '<div class="alert erro">Preencha todos os campos.</div>'
                + MOTORISTA_CADASTRO_FORM
            )

        if not all([
            foto_motorista,
            cnh_frente,
            cnh_verso,
            crlv
        ]):
            return _pagina_publica(
                "Cadastro",
                '<div class="alert erro">Envie todos os documentos obrigatórios.</div>'
                + MOTORISTA_CADASTRO_FORM
            )

        if len(senha) < 6:
            return _pagina_publica(
                "Cadastro",
                '<div class="alert erro">A senha deve ter pelo menos 6 caracteres.</div>'
                + MOTORISTA_CADASTRO_FORM
            )

        extensoes_permitidas = {
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".pdf"
        }

        arquivos = {
            "foto_motorista": foto_motorista,
            "cnh_frente": cnh_frente,
            "cnh_verso": cnh_verso,
            "crlv": crlv
        }

        for nome_campo, arquivo in arquivos.items():

            if not arquivo or not arquivo.filename:
                return _pagina_publica(
                    "Cadastro",
                    '<div class="alert erro">Arquivo inválido.</div>'
                    + MOTORISTA_CADASTRO_FORM
                )

            extensao = os.path.splitext(
                arquivo.filename.lower()
            )[1]

            if extensao not in extensoes_permitidas:
                return _pagina_publica(
                    "Cadastro",
                    '<div class="alert erro">'
                    'Formato de arquivo não permitido. '
                    'Use JPG, PNG, WEBP ou PDF.'
                    '</div>'
                    + MOTORISTA_CADASTRO_FORM
                )

        conn = conectar()

        try:

            if conn.execute(
                "SELECT id FROM motoqueiros WHERE telefone=?",
                (telefone,)
            ).fetchone():

                return _pagina_publica(
                    "Cadastro",
                    '<div class="alert erro">Telefone já cadastrado.</div>'
                    + MOTORISTA_CADASTRO_FORM
                )

            total = conn.execute(
                "SELECT COUNT(*) AS n FROM motoqueiros"
            ).fetchone()["n"]

            if total >= LIMITE_MOTOQUEIROS:

                return _pagina_publica(
                    "Cadastro",
                    '<div class="alert erro">'
                    'Limite de 20 motoqueiros atingido.'
                    '</div>'
                    + MOTORISTA_CADASTRO_FORM
                )

            # Primeiro cria o motorista para obter o ID.
            cur = conn.execute("""
                INSERT INTO motoqueiros
                (
                    nome,
                    telefone,
                    cpf,
                    moto,
                    placa,
                    localizacao,
                    observacao,
                    status,
                    conexao,
                    senha
                )
                VALUES (?, ?, ?, ?, ?, ?, '', 'pendente', 'offline', ?)
            """, (
                nome,
                telefone,
                cpf,
                moto,
                placa,
                localizacao,
                generate_password_hash(senha)
            ))

            motorista_id = cur.lastrowid

            # Pasta protegida, fora de static.
            pasta = Path("documentos_motoristas") / str(motorista_id)
            pasta.mkdir(parents=True, exist_ok=True)

            caminhos = {}

            for nome_campo, arquivo in arquivos.items():

                extensao = os.path.splitext(
                    arquivo.filename.lower()
                )[1]

                nome_arquivo = (
                    nome_campo
                    + "_"
                    + uuid.uuid4().hex
                    + extensao
                )

                caminho = pasta / nome_arquivo

                arquivo.save(str(caminho))

                caminhos[nome_campo] = str(caminho)

            conn.execute("""
                UPDATE motoqueiros
                SET
                    foto_motorista=?,
                    cnh_frente=?,
                    cnh_verso=?,
                    crlv=?
                WHERE id=?
            """, (
                caminhos["foto_motorista"],
                caminhos["cnh_frente"],
                caminhos["cnh_verso"],
                caminhos["crlv"],
                motorista_id
            ))

            conn.commit()

        except Exception:

            conn.rollback()
            raise

        finally:
            conn.close()

        return _pagina_publica(
            "Cadastro enviado",
            """
            <div class="alert sucesso">
            ✅ Cadastro enviado com sucesso.<br><br>
            📄 Seus documentos foram enviados para análise.<br>
            👨‍💼 Aguarde a aprovação do administrador.
            </div>

            <a class="pub-btn pub-blue"
               href="/login-motorista">
               ENTRAR COMO MOTORISTA
            </a>
            """
        )

    return _pagina_publica(
        "Cadastro",
        MOTORISTA_CADASTRO_FORM
    )


@app.route("/login-passageiro", methods=["GET", "POST"])
def login_passageiro():
    if request.method == "POST":
        telefone = request.form.get("telefone", "").strip()
        senha = request.form.get("senha", "")
        conn = conectar()
        p = conn.execute("SELECT * FROM passageiros WHERE telefone=?", (telefone,)).fetchone()
        conn.close()
        if p and p["senha"] and check_password_hash(p["senha"], senha) and p["status"] == "ativo":
            session.clear()
            session["passageiro_id"] = p["id"]
            session["passageiro_nome"] = p["nome"]
            return redirect(url_for("passageiro"))
        return _pagina_publica("Login", '<div class="alert erro">Telefone, senha inválidos ou conta bloqueada.</div>' + PASSAGEIRO_LOGIN_FORM, manifesto="passageiro")
    return _pagina_publica("Login", PASSAGEIRO_LOGIN_FORM, manifesto="passageiro")


PASSAGEIRO_LOGIN_FORM = """
<h2>👤 Entrar como passageiro</h2>
<form method="post">
<label class="pub-label">Telefone</label>
<input class="pub-input" name="telefone" required>
<label class="pub-label">Senha</label>
<input class="pub-input" name="senha" type="password" required>
<button class="pub-btn pub-green" type="submit">ENTRAR</button>
</form>
<a class="pub-btn" href="/cadastro/passageiro">CRIAR CONTA</a>
"""


@app.route("/login-motorista", methods=["GET", "POST"])
def login_motorista():
    if request.method == "POST":
        telefone = request.form.get("telefone", "").strip()
        senha = request.form.get("senha", "")
        conn = conectar()
        m = conn.execute("SELECT * FROM motoqueiros WHERE telefone=?", (telefone,)).fetchone()
        conn.close()
        if m and m["senha"] and check_password_hash(m["senha"], senha):
            if m["status"] != "aprovado":
                return _pagina_publica("Login", '<div class="alert">Seu cadastro ainda não foi aprovado pelo administrador.</div>' + MOTORISTA_LOGIN_FORM, manifesto="motorista")
            session.clear()
            session["motorista_id"] = m["id"]
            session["motorista_nome"] = m["nome"]
            return redirect(url_for("motorista"))
        return _pagina_publica("Login", '<div class="alert erro">Telefone ou senha inválidos.</div>' + MOTORISTA_LOGIN_FORM, manifesto="motorista")
    return _pagina_publica("Login", MOTORISTA_LOGIN_FORM, manifesto="motorista")


MOTORISTA_LOGIN_FORM = """
<h2>🏍️ Entrar como motorista</h2>
<form method="post">
<label class="pub-label">Telefone</label>
<input class="pub-input" name="telefone" required>
<label class="pub-label">Senha</label>
<input class="pub-input" name="senha" type="password" required>
<button class="pub-btn pub-blue" type="submit">ENTRAR</button>
</form>
<a class="pub-btn" href="/cadastro/motorista">CRIAR CONTA DE MOTORISTA</a>
"""


@app.route("/logout-usuario")
def logout_usuario():
    session.pop("passageiro_id", None)
    session.pop("passageiro_nome", None)
    session.pop("motorista_id", None)
    session.pop("motorista_nome", None)
    return redirect(url_for("cadastro"))


@app.route("/passageiro")
def passageiro():
    if "passageiro_id" not in session:
        return redirect(url_for("login_passageiro"))

    nome = session.get("passageiro_nome", "Passageiro")
    return _pagina_publica("Passageiro", """

<style>
/* ===== PAINEL DO PASSAGEIRO - LETRAS MAIORES ===== */
.pub-nav a {
    font-size:20px !important;
    padding:15px 12px !important;
}

.pub-info {
    font-size:20px !important;
    line-height:1.45;
}

.pub-card h3 {
    font-size:25px !important;
    line-height:1.3;
}

.pub-card p,
.pub-card small {
    font-size:18px !important;
    line-height:1.45;
}

.pub-input {
    font-size:19px !important;
    padding:16px !important;
}

.pub-btn {
    font-size:19px !important;
    min-height:54px;
    padding:16px !important;
}

.pub-card label {
    font-size:19px !important;
}

</style>

      <div class="pub-nav">
        <a href="/passageiro">🏠 Início</a>
        <a href="/logout-usuario">🚪 Sair</a>
      </div>
      <h2>Olá, {{ session.get("passageiro_nome", "Passageiro") }}! 👋</h2>
      <div class="pub-info">📍 Ative o GPS para preencher sua localização.</div>

      <div class="pub-card">
        <h3>📍 Origem</h3>
        <input id="origem" class="pub-input" placeholder="Sua localização">
        <input id="origem_lat" type="hidden">
        <input id="origem_lon" type="hidden">
        <button class="pub-btn pub-blue" type="button" onclick="usarMinhaLocalizacao()">🎯 USAR MINHA LOCALIZAÇÃO</button>
         <small>O GPS tentará mostrar rua, número e bairro.</small>

<script>
async function usarMinhaLocalizacao(){
    const origem = document.getElementById("origem");
    const latInput = document.getElementById("origem_lat");
    const lonInput = document.getElementById("origem_lon");

    origem.value = "📍 LOCALIZANDO...";
    
    if(!navigator.geolocation){
        origem.value = "";
        alert("Este aparelho não suporta localização por GPS.");
        return;
    }

    navigator.geolocation.getCurrentPosition(
        async function(pos){
            const lat = pos.coords.latitude;
            const lon = pos.coords.longitude;

            latInput.value = lat;
            lonInput.value = lon;

            origem.value = "🔄 BUSCANDO ENDEREÇO...";

            try{
                const resposta = await fetch("/api/endereco-gps", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json"
                    },
                    body: JSON.stringify({
                        lat: lat,
                        lon: lon
                    }),
                    cache: "no-store"
                });

                const dados = await resposta.json();

                if(dados.ok && dados.endereco){
                    origem.value = dados.endereco;
                    alert("Localização encontrada!");
                }else{
                    origem.value = lat.toFixed(6) + ", " + lon.toFixed(6);
                    alert("GPS encontrado, mas não foi possível obter o endereço. As coordenadas foram mantidas.");
                }

            }catch(erro){
                origem.value = lat.toFixed(6) + ", " + lon.toFixed(6);
                alert("GPS encontrado, mas houve erro ao buscar o endereço.");
            }
        },
        function(erro){
            origem.value = "";
            alert("GPS não conseguiu localizar. Ative a localização precisa e tente novamente.");
        },
        {
            enableHighAccuracy: true,
            timeout: 20000,
            maximumAge: 0
        }
    );
}
</script>

        <h3>🏁 Destino</h3>
        <input id="destino" class="pub-input" placeholder="Rua, número, bairro ou endereço completo">
        <input id="dest_lat" type="hidden">
        <input id="dest_lon" type="hidden">

        <button class="pub-btn" type="button" onclick="buscarDestino()">🔎 BUSCAR DESTINO</button>

        <div id="resultado-endereco"></div>
        <div id="estimativa" class="pub-info" style="display:none"></div>

        <label class="pub-label">Pagamento</label>
        <select id="pagamento" class="pub-input">
          <option value="DINHEIRO">💵 Dinheiro</option>
          <option value="PIX">🔑 PIX</option>
        </select>

        <button class="pub-btn pub-green" type="button" onclick="calcular()">💰 CALCULAR CORRIDA</button>
        <button id="solicitar" class="pub-btn pub-yellow" type="button" onclick="solicitar()" style="display:none">🏍️ SOLICITAR CORRIDA</button>
        <div id="mensagem"></div>
      </div>

      <div id="corridas" class="pub-card">
        <h3>🚕 Minhas corridas</h3>
        <div id="lista-corridas">Carregando...</div>
      </div>

<script>
function msg(t, cls="alert"){document.getElementById("mensagem").innerHTML='<div class="alert '+cls+'">'+t+'</div>';}
async function usarGPS(){
  if(!navigator.geolocation){msg("Seu navegador não suporta GPS.","erro");return;}
  navigator.geolocation.getCurrentPosition(async p=>{
    const lat=p.coords.latitude;
    const lon=p.coords.longitude;
    document.getElementById("origem_lat").value=lat;
    document.getElementById("origem_lon").value=lon;
    document.getElementById("origem").value=lat.toFixed(6)+", "+lon.toFixed(6);
    msg("GPS localizado. Procurando rua e número...","sucesso");
    try{
      const r=await fetch("/api/endereco-gps",{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({lat,lon})
      });
      const d=await r.json();
      if(d.ok && d.endereco){
        document.getElementById("origem").value=d.endereco;
        msg("Origem encontrada: "+d.endereco,"sucesso");
      }else{
        msg("GPS ativado, mas não foi possível encontrar o nome da rua.\\nUse o endereço manualmente.","alert");
      }
    }catch(e){
      msg("GPS ativado. Digite a rua se o endereço não aparecer.","alert");
    }
  }, e=>msg("Não foi possível obter o GPS. Permita a localização no navegador.","erro"), {enableHighAccuracy:true,timeout:15000,maximumAge:10000});
}
async function buscarDestino(){
  const q=document.getElementById("destino").value.trim();
  if(!q){msg("Digite o destino.","erro");return;}

  const lat=document.getElementById("origem_lat").value;
  const lon=document.getElementById("origem_lon").value;

  const r=await fetch("/api/buscar-enderecos",{
    method:"POST",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({q,lat,lon})
  });
  const d=await r.json();
  const box=document.getElementById("resultado-endereco");
  box.innerHTML="";
  if(!d.ok || !d.resultados.length){box.innerHTML='<div class="alert erro">Endereço não encontrado.</div>';return;}
  d.resultados.forEach(x=>{
    const b=document.createElement("button");
    b.className="pub-btn"; b.type="button"; b.textContent=x.display_name;
    b.onclick=()=>{document.getElementById("destino").value=x.display_name;document.getElementById("dest_lat").value=x.lat;document.getElementById("dest_lon").value=x.lon;box.innerHTML='<div class="alert sucesso">Destino selecionado.</div>';};
    box.appendChild(b);
  });
}
async function calcular(){
  const aLat=document.getElementById("origem_lat").value, aLon=document.getElementById("origem_lon").value;
  const dLat=document.getElementById("dest_lat").value, dLon=document.getElementById("dest_lon").value;
  if(!aLat||!aLon){msg("Use o GPS para definir a origem.","erro");return;}
  if(!dLat||!dLon){msg("Busque e selecione o destino.","erro");return;}
  try {
    const r=await fetch("/api/calcular-corrida",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({origem_lat:aLat,origem_lon:aLon,dest_lat:dLat,dest_lon:dLon})});
    const d=await r.json();
    if(!d.ok){msg(d.erro||"Não foi possível calcular.","erro");return;}
    document.getElementById("estimativa").style.display="block";
    document.getElementById("estimativa").innerHTML='<b>Distância:</b> '+d.distancia_km.toFixed(2)+' km<br><div class="pub-price">R$ '+d.valor.toFixed(2)+'</div><small>Taxa do aplicativo: R$ '+d.taxa_app.toFixed(2)+' · Motorista: R$ '+d.valor_motorista.toFixed(2)+'</small>';
    document.getElementById("solicitar").style.display="block";
    window._corrida=d;
  } catch(e) {
    msg("Erro ao calcular a corrida. Tente novamente.","erro");
  }
}
async function solicitar(){
  if(!window._corrida){msg("Calcule a corrida primeiro.","erro");return;}
  const body={
    origem:document.getElementById("origem").value,
    destino:document.getElementById("destino").value,
    origem_lat:document.getElementById("origem_lat").value,
    origem_lon:document.getElementById("origem_lon").value,
    dest_lat:document.getElementById("dest_lat").value,
    dest_lon:document.getElementById("dest_lon").value,
    pagamento:document.getElementById("pagamento").value,
    distancia_km:window._corrida.distancia_km,
    valor:window._corrida.valor,
    taxa_app:window._corrida.taxa_app,
    valor_motorista:window._corrida.valor_motorista
  };
  const r=await fetch("/api/solicitar-corrida",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const d=await r.json();
  if(!d.ok){msg(d.erro||"Erro ao solicitar corrida.","erro");return;}
  document.getElementById("solicitar").style.display="none";

  if(d.checkout_url){
    msg(
      'Corrida criada!<br><br>' +
      '<a href="' + d.checkout_url + '" target="_blank" ' +
      'class="pub-btn pub-green" style="display:block;text-align:center;text-decoration:none">' +
      '💳 PAGAR COM PIX / CARTÃO' +
      '</a>',
      "sucesso"
    );
  }else{
    msg("Corrida solicitada! Aguarde um motorista.","sucesso");
  }

  carregarCorridas();
}
async function carregarCorridas(){
  const r=await fetch("/api/minhas-corridas"); const d=await r.json();
  const box=document.getElementById("lista-corridas");
  if(!d.ok){box.innerHTML="Faça login novamente.";return;}
  if(!d.corridas.length){box.innerHTML="Nenhuma corrida ainda.";return;}
  box.innerHTML=d.corridas.map(c=>`
    <div class="pub-info">
      <b>🚕 Corrida #${c.id}</b><br>
      📍 ${c.origem}<br>
      🏁 ${c.destino}<br>
      💰 R$ ${Number(c.valor).toFixed(2)}<br>
      📌 ${c.status}<br>
      🏍️ ${c.motorista_nome||"Aguardando motorista"}

      ${
        (c.status === "PENDENTE" || c.status === "ACEITA")
        ? `
          <br><br>
          <button
            type="button"
            class="pub-btn"
            style="background:#b00000;color:#fff;font-weight:900;"
            onclick="cancelarCorrida(${c.id})">
            🔴 CANCELAR CORRIDA
          </button>
        `
        : ""
      }
    </div>
  `).join("");

  window.cancelarCorrida = async function(id){
    if(!confirm("Tem certeza que deseja cancelar esta corrida?")){
      return;
    }

    try{
      const r = await fetch(
        "/api/corrida/" + id + "/cancelar",
        {
          method:"POST",
          credentials:"same-origin"
        }
      );

      const d = await r.json();

      if(!d.ok){
        msg(d.erro || "Não foi possível cancelar a corrida.","erro");
        return;
      }

      msg("Corrida cancelada com sucesso.","sucesso");

      carregarCorridas();

    }catch(e){
      msg("Erro ao cancelar a corrida.","erro");
    }
  }
}
carregarCorridas();

/* ===== NOTIFICAÇÃO: MOTORISTA CHEGOU ===== */
(function(){

  let ultimaEtapaChegou = localStorage.getItem("vai_moto_etapa_chegou") || "";

  function somMotoristaChegou(){
    try{
      const ctx = new (window.AudioContext || window.webkitAudioContext)();

      if(ctx.state === "suspended"){
        ctx.resume();
      }

      function beep(freq, inicio, duracao){
        const o = ctx.createOscillator();
        const g = ctx.createGain();

        o.type = "sine";
        o.frequency.setValueAtTime(freq, ctx.currentTime + inicio);

        g.gain.setValueAtTime(0.0001, ctx.currentTime + inicio);
        g.gain.exponentialRampToValueAtTime(
          0.8,
          ctx.currentTime + inicio + 0.03
        );
        g.gain.exponentialRampToValueAtTime(
          0.0001,
          ctx.currentTime + inicio + duracao
        );

        o.connect(g);
        g.connect(ctx.destination);

        o.start(ctx.currentTime + inicio);
        o.stop(ctx.currentTime + inicio + duracao + 0.05);
      }

      beep(880,  0.00, 0.18);
      beep(1175, 0.22, 0.18);
      beep(1568, 0.44, 0.30);

    }catch(e){}
  }

  function mostrarMotoristaChegou(){

    const antigo = document.getElementById("aviso-motorista-chegou");
    if(antigo) antigo.remove();

    const aviso = document.createElement("div");

    aviso.id = "aviso-motorista-chegou";

    aviso.style.cssText =
      "position:fixed;" +
      "top:15px;" +
      "left:50%;" +
      "transform:translateX(-50%);" +
      "z-index:999999;" +
      "width:calc(100% - 24px);" +
      "max-width:520px;" +
      "background:#087f23;" +
      "color:white;" +
      "border:4px solid white;" +
      "border-radius:20px;" +
      "padding:20px;" +
      "text-align:center;" +
      "font-weight:900;" +
      "box-shadow:0 8px 30px rgba(0,0,0,.65);";

    aviso.innerHTML =
      '<div style="font-size:30px;">🚨 MOTORISTA CHEGOU!</div>' +
      '<div style="font-size:20px;margin-top:8px;">📍 Seu motorista está aguardando você.</div>';

    document.body.appendChild(aviso);

    somMotoristaChegou();

    setTimeout(function(){
      if(aviso.parentNode){
        aviso.remove();
      }
    }, 8000);
  }

  async function verificarMotoristaChegou(){

    try{

      const r = await fetch(
        "/api/minhas-corridas",
        {
          cache:"no-store",
          credentials:"same-origin"
        }
      );

      if(!r.ok) return;

      const d = await r.json();

      if(!d.ok || !Array.isArray(d.corridas)) return;

      const corrida = d.corridas.find(function(c){
        return c.etapa === "CHEGOU" &&
               (c.status === "ACEITA" || c.status === "EM_ANDAMENTO");
      });

      if(!corrida){
        return;
      }

      const chave = String(corrida.id);

      if(ultimaEtapaChegou !== chave){

        ultimaEtapaChegou = chave;

        localStorage.setItem(
          "vai_moto_etapa_chegou",
          chave
        );

        mostrarMotoristaChegou();
      }

    }catch(e){}
  }

  verificarMotoristaChegou();

  setInterval(
    verificarMotoristaChegou,
    2000
  );

})();
</script>
    """, manifesto="passageiro")


@app.route("/motorista/alternar-status", methods=["GET", "POST"])
def motorista_alternar_status():
    mid = _motorista_logado()
    if not mid:
        return redirect(url_for("login_motorista"))

    acao = (request.args.get("acao") or request.form.get("acao") or "").strip().lower()
    if acao not in ("online", "offline"):
        acao = None

    conn = conectar()
    m = conn.execute(
        "SELECT status, conexao FROM motoqueiros WHERE id=?",
        (mid,)
    ).fetchone()

    if not m:
        conn.close()
        return redirect(url_for("login_motorista"))

    if m["status"] != "aprovado":
        conn.close()
        return _pagina_publica(
            "Motorista",
            '<div class="alert erro">Motorista ainda não foi aprovado.</div>'
        )

    # Ação explícita: não dependemos de JavaScript para definir o estado.
    # Sem ?acao, mantém o comportamento de alternar.
    if acao is None:
        acao = "offline" if m["conexao"] == "online" else "online"

    conn.execute(
        "UPDATE motoqueiros SET conexao=? WHERE id=?",
        (acao, mid)
    )
    conn.commit()
    conn.close()

    return redirect(url_for("motorista"))


@app.route("/motorista")
def motorista():
    if "motorista_id" not in session:
        return redirect(url_for("login_motorista"))

    nome = session.get("motorista_nome", "Motorista")
    inicial = (nome[:1] or "M").upper()

    return render_template_string(r'''
<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no,viewport-fit=cover">
<meta name="theme-color" content="#050505">
<title>VAI_DE_MOTO • Motorista</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;width:100%;height:100%;overflow:hidden;background:#050505;font-family:Arial,Helvetica,sans-serif;color:#fff}
body{overscroll-behavior:none}
.driver-app{position:fixed;inset:0;background:#050505;overflow:hidden}
#map{position:absolute;inset:0;z-index:1;background:#dfe6eb}
.leaflet-control-zoom{display:none!important}
.leaflet-control-attribution{font-size:9px!important;background:rgba(255,255,255,.7)!important}
.topbar{position:absolute;z-index:20;top:0;left:0;right:0;padding:calc(10px + env(safe-area-inset-top)) 14px 12px;display:flex;align-items:center;justify-content:space-between;background:linear-gradient(180deg,rgba(0,0,0,.9),rgba(0,0,0,.45),transparent)}
.profile{display:flex;align-items:center;gap:10px;min-width:0}
.avatar{width:43px;height:43px;border-radius:50%;display:flex;align-items:center;justify-content:center;background:#171717;border:1px solid #f57c00;color:#ff8a00;font-size:20px;font-weight:800;box-shadow:0 0 0 3px rgba(0,0,0,.2)}
.driver-name{font-weight:800;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:190px}
.driver-role{font-size:12px;color:#9d9d9d;margin-top:2px}
.profile-btn{width:42px;height:42px;border-radius:50%;border:1px solid #6e2800;background:rgba(10,10,10,.85);color:#ff7b00;font-size:21px;text-decoration:none;display:flex;align-items:center;justify-content:center}
.earnings-pill{position:absolute;z-index:21;top:calc(72px + env(safe-area-inset-top));left:50%;transform:translateX(-50%);min-width:185px;padding:13px 18px;border-radius:30px;background:rgba(36,36,36,.94);box-shadow:0 8px 22px rgba(0,0,0,.3);text-align:center}
.earnings-pill .money{font-size:20px;font-weight:900}.earnings-pill .today{font-size:11px;color:#aaa;margin-left:4px}
.status-pill{position:absolute;z-index:22;top:calc(72px + env(safe-area-inset-top));right:12px;border:0;border-radius:24px;padding:11px 15px;background:#343434;color:#ddd;font-weight:800;box-shadow:0 7px 18px rgba(0,0,0,.25)}
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:#bbb}.status-pill.online{background:#17371f;color:#c9ffd5}.status-pill.online .status-dot{background:#32d46b;box-shadow:0 0 8px #32d46b}
.status-pill.offline .status-dot{background:#aaa}
.online-action{position:absolute;z-index:25;right:15px;top:calc(122px + env(safe-area-inset-top));border:0;border-radius:25px;padding:10px 15px;background:#ef7900;color:#111;font-weight:900;box-shadow:0 7px 18px rgba(0,0,0,.28);display:none}
.online-action.off{display:block;background:#2b2b2b;color:#fff}
.center-btn{position:absolute;z-index:20;right:14px;bottom:275px;width:52px;height:52px;border:0;border-radius:50%;background:#252525;color:#fff;font-size:22px;box-shadow:0 8px 20px rgba(0,0,0,.3)}
.sound-btn{position:absolute;z-index:20;right:14px;bottom:207px;width:58px;height:58px;border-radius:50%;border:1px solid #111;background:#454545;color:#ff7a00;font-size:25px;box-shadow:0 8px 20px rgba(0,0,0,.35)}
.sheet{position:absolute;z-index:15;left:0;right:0;bottom:62px;min-height:245px;max-height:58vh;background:rgba(5,5,5,.97);border-radius:28px 28px 0 0;box-shadow:0 -12px 30px rgba(0,0,0,.22);padding:13px 15px 20px;overflow:auto}
.handle{width:45px;height:5px;border-radius:5px;background:#3b3b3b;margin:0 auto 14px}
.sheet-title{font-size:20px;font-weight:900;margin-bottom:8px}.sheet-sub{font-size:13px;color:#858585;margin-bottom:12px}
.ride-card{background:#151515;border:1px solid #292929;border-radius:17px;padding:13px;margin:10px 0}.ride-row{display:flex;justify-content:space-between;gap:12px}.ride-main{font-size:14px;line-height:1.45}.ride-price{font-size:20px;font-weight:900;color:#fff;white-space:nowrap}.ride-muted{color:#999;font-size:12px}.ride-actions{display:flex;gap:9px;margin-top:12px}.ride-actions button{flex:1;border:0;border-radius:12px;padding:12px;font-weight:900}.btn-accept{background:#22b45b;color:#07130c}.btn-reject{background:#2b2b2b;color:#eee}
.btn-go-client{background:#168cff;color:#fff}
.btn-go-dest{background:#ff7900;color:#111}
.btn-finish{background:#e51f2a;color:#fff}
.flow-actions{display:flex;flex-direction:column}
.flow-actions button{width:100%;font-size:15px;padding:14px!important}
.active-ride{border:1px solid #ff7900;box-shadow:0 0 0 1px rgba(255,122,0,.12)}
.stat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:10px 0}.stat{background:#151515;border-radius:15px;padding:13px 9px;text-align:center}.stat b{display:block;font-size:17px}.stat span{font-size:10px;color:#888}
.empty{padding:26px 10px;text-align:center;color:#777}.empty strong{display:block;color:#ddd;margin-bottom:5px}
.bottom-nav{position:absolute;z-index:30;bottom:0;left:0;right:0;height:calc(62px + env(safe-area-inset-bottom));padding-bottom:env(safe-area-inset-bottom);display:grid;grid-template-columns:repeat(5,1fr);background:#050505;border-top:1px solid #1c1c1c}
.nav-item{border:0;background:transparent;color:#666;font-size:10px;font-weight:800;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:4px}.nav-item .ico{font-size:22px;line-height:1}.nav-item.active{color:#ff7a00}.nav-item:active{transform:scale(.96)}
.toast{position:absolute;z-index:50;left:50%;top:18%;transform:translateX(-50%);background:#171717;border:1px solid #333;border-radius:14px;padding:12px 16px;display:none;max-width:90%;font-weight:700;box-shadow:0 10px 30px #000}
@media(min-width:700px){.sheet{left:50%;right:auto;transform:translateX(-50%);width:520px;bottom:72px;border-radius:28px}.bottom-nav{left:50%;right:auto;transform:translateX(-50%);width:520px;border-radius:18px 18px 0 0}.status-pill{right:calc(50% - 250px)}.online-action{right:calc(50% - 250px)}}
</style>
</head>
<body>
<div class="driver-app">
  <div id="map"></div>
  <div class="topbar">
    <div class="profile"><div class="avatar">{{ inicial }}</div><div><div class="driver-name">{{ nome }}</div><div class="driver-role">Motorista VAI_DE_MOTO</div></div></div>
    <a class="profile-btn" href="/logout-usuario" aria-label="Sair">♙</a>
  </div>

  <div class="earnings-pill"><span class="money" id="pillMoney">R$ 0,00</span><span class="today">hoje</span></div>
  <button id="statusPill" class="status-pill offline" onclick="alternarStatus()"><span class="status-dot"></span><span id="statusText">Offline</span></button>
  <button id="onlineAction" class="online-action off" onclick="alternarStatus()">FICAR ONLINE</button>

  <button class="center-btn" onclick="centralizar()" title="Centralizar">⌖</button>
  <button class="sound-btn" onclick="testarSom()" title="Notificação">◉</button>

  <div class="sheet" id="sheet">
    <div class="handle"></div>
    <div class="sheet-title" id="sheetTitle">Você está offline</div>
    <div class="sheet-sub" id="sheetSub">Fique online para receber novos pedidos em Aragoiânia.</div>
    <div class="stat-grid">
      <div class="stat"><b id="hojeCorridas">0</b><span>CORRIDAS HOJE</span></div>
      <div class="stat"><b id="hojeGanhos">R$ 0,00</b><span>GANHOS HOJE</span></div>
      <div class="stat"><b id="totalGanhos">R$ 0,00</b><span>TOTAL</span></div>
    </div>
    <div id="lista"></div>
  </div>

  <nav class="bottom-nav">
    <button class="nav-item active" onclick="mostrar('pedidos',this)"><span class="ico">🏍️</span>CORRIDAS</button>
    <button class="nav-item" onclick="mostrar('inicio',this)"><span class="ico">⌂</span>INÍCIO</button>
    <button class="nav-item" onclick="mostrar('pedidos',this)"><span class="ico">🛍️</span>PEDIDOS</button>
    <button class="nav-item" onclick="mostrar('historico',this)"><span class="ico">▤</span>HISTÓRICO</button>
    <button class="nav-item" onclick="mostrar('ganhos',this)"><span class="ico">▣</span>GANHOS</button>
  </nav>
  <div id="toast" class="toast"></div>
</div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const motoristaId={{ session.get('motorista_id')|tojson }};
let online=false, map=null, marker=null, lastRideIds=new Set(), currentView='pedidos';
function br(v){return Number(v||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});}
function toast(t){const x=document.getElementById('toast');x.textContent=t;x.style.display='block';clearTimeout(window._toast);window._toast=setTimeout(()=>x.style.display='none',2600)}
function initMap(){
  map=L.map('map',{zoomControl:false,attributionControl:true}).setView([-16.9167,-49.4483],14);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:'© OpenStreetMap'}).addTo(map);
  if(navigator.geolocation){navigator.geolocation.getCurrentPosition(p=>setPosition(p.coords.latitude,p.coords.longitude),()=>{}, {enableHighAccuracy:true,timeout:10000});}
}
function setPosition(lat,lon){
  if(!map)return;
  if(!marker) marker=L.circleMarker([lat,lon],{radius:8,color:'#111',weight:3,fillColor:'#1e88ff',fillOpacity:1}).addTo(map);
  else marker.setLatLng([lat,lon]);
  map.setView([lat,lon],16);
}
function centralizar(){
  if(navigator.geolocation){navigator.geolocation.getCurrentPosition(p=>setPosition(p.coords.latitude,p.coords.longitude),()=>toast('Permita o GPS para centralizar.'));}
}
async function status(){
 try{
  const r=await fetch('/api/motorista/me',{cache:'no-store'}),d=await r.json();
  if(!d.ok)return;
  online=d.conexao==='online';
  const sp=document.getElementById('statusPill'),st=document.getElementById('statusText'),oa=document.getElementById('onlineAction');
  sp.className='status-pill '+(online?'online':'offline');st.textContent=online?'Online':'Offline';
  oa.textContent=online?'FICAR OFFLINE':'FICAR ONLINE';oa.className='online-action '+(online?'':'off');
  document.getElementById('sheetTitle').textContent=online?'Você está online':'Você está offline';
  document.getElementById('sheetSub').textContent=online?'Aguardando novos pedidos em Aragoiânia.':'Fique online para receber novos pedidos em Aragoiânia.';
 }catch(e){}
}
async function alternarStatus(){
 try{
  const r=await fetch('/api/motorista/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({online:!online}),cache:'no-store'}),d=await r.json();
  if(!d.ok){toast(d.erro||'Não foi possível alterar o status.');return;}
  online=d.conexao==='online';toast(online?'Você está ONLINE 🟢':'Você está OFFLINE');status();carregar();
 }catch(e){toast('Erro de conexão.');}
}
async function carregar(){
 try{
  Promise.all([
   fetch('/api/corridas-disponiveis',{cache:'no-store'}).then(r=>r.json()),
   fetch('/api/motorista/minhas-corridas',{cache:'no-store'}).then(r=>r.json())
  ]).then(([d,m])=>{
   const pendentes=d.ok?(d.corridas||[]):[];
   const todas=m.ok?(m.corridas||[]):[];
   const ativas=todas.filter(x=>['ACEITA','EM_ANDAMENTO'].includes(x.status));

   if(currentView==='pedidos' || currentView==='inicio'){
    renderPedidos(pendentes,ativas);
   }

   if(pendentes.some(x=>!lastRideIds.has(x.id)) && online && lastRideIds.size){
    testarSom();
    toast('🔔 Nova corrida disponível!');
   }

   lastRideIds=new Set(pendentes.map(x=>x.id));
   window._minhas=todas;
  });
 }catch(e){}
}

function renderPedidos(rides,ativas){
 const box=document.getElementById('lista');
 let html='';

 if(ativas && ativas.length){
  html += ativas.map(c=>{
   const origem=encodeURIComponent(c.origem||'');
   const destino=encodeURIComponent(c.destino||'');

   const mapsOrigem='https://www.google.com/maps/dir/?api=1&destination='+origem;
   const mapsDestino='https://www.google.com/maps/dir/?api=1&destination='+destino;

   if(c.status==='ACEITA'){
    return `
    <div class="ride-card active-ride">
      <div class="ride-row">
        <div class="ride-main">
          <b>🏍️ CORRIDA #${c.id} ACEITA</b><br>
          👤 ${esc(c.passageiro_nome||'Passageiro')}<br>
          📍 ${esc(c.origem)}<br>
          🏁 ${esc(c.destino)}<br>
          <span class="ride-muted">💵 ${c.pagamento||'Dinheiro'}</span>
        </div>
        <div class="ride-price">${br(c.valor)}</div>
      </div>

      <div class="ride-actions flow-actions">
        <button class="btn-go-client"
          onclick="irCliente(${c.id},'${mapsOrigem}')">
          🗺️ GOOGLE MAPS<br><small>IR ATÉ O PASSAGEIRO</small>
        </button>

        <button class="btn-waze"
          onclick="irClienteWaze(${c.id},'https://www.waze.com/ul?q='+encodeURIComponent('${esc(c.origem||'')}')+'&navigate=yes')">
          🚗 WAZE<br><small>IR ATÉ O PASSAGEIRO</small>
        </button>

        <button class="btn-finish"
          onclick="iniciarCorrida(${c.id})">
          📍 CHEGUEI AO PASSAGEIRO<br><small>INICIAR CORRIDA</small>
        </button>
      </div>
    </div>`;
   }

   return `
   <div class="ride-card active-ride">
     <div class="ride-row">
       <div class="ride-main">
         <b>🏁 CORRIDA #${c.id} EM ANDAMENTO</b><br>
         👤 ${esc(c.passageiro_nome||'Passageiro')}<br>
         📍 ${esc(c.origem)}<br>
         🏁 ${esc(c.destino)}
       </div>
       <div class="ride-price">${br(c.valor)}</div>
     </div>

     <div class="ride-actions flow-actions">
       <button class="btn-go-dest"
         onclick="irDestino(${c.id},'${mapsDestino}')">
         🗺️ GOOGLE MAPS<br><small>IR ATÉ O DESTINO</small>
       </button>

       <button class="btn-waze"
         onclick="irDestinoWaze(${c.id},'https://www.waze.com/ul?q='+encodeURIComponent('${esc(c.destino||'')}')+'&navigate=yes')">
         🚗 WAZE<br><small>IR ATÉ O DESTINO</small>
       </button>

       <button class="btn-finish"
         onclick="concluir(${c.id})">
         ✅ CONCLUIR CORRIDA
       </button>
     </div>
   </div>`;
  }).join('');
 }

 if(online && rides.length){
  html += rides.map(c=>`
   <div class="ride-card">
     <div class="ride-row">
       <div class="ride-main">
         <b>🆕 NOVA CORRIDA #${c.id}</b><br>
         📍 ${esc(c.origem)}<br>
         🏁 ${esc(c.destino)}<br>
         <span class="ride-muted">💵 ${c.pagamento||'Dinheiro'}</span>
       </div>
       <div class="ride-price">${br(c.valor)}</div>
     </div>

     <div class="ride-actions">
       <button class="btn-reject"
         onclick="ignorarPedido(${c.id},this)">
         IGNORAR
       </button>

       <button class="btn-accept"
         onclick="aceitar(${c.id})">
         🟢 ACEITAR CORRIDA
       </button>
     </div>
   </div>`).join('');
 }

 if(!html){
  html=`
   <div class="empty">
    <strong>${!online?'Você está offline':'Nenhuma corrida no momento'}</strong>
    ${!online?'Ative o online para receber corridas.':'Quando surgir um pedido, ele aparecerá aqui.'}
   </div>`;
 }

 box.innerHTML=html;
}

function abrirMaps(url){
 window.open(url,'_blank');
}

function abrirWaze(url){
 window.open(url,'_blank');
}

async function irCliente(id,url){
 abrirMaps(url);
 toast('🔵 Rota até o passageiro aberta no Google Maps.');
}

async function irClienteWaze(id,url){
 abrirWaze(url);
 toast('🚗 Rota até o passageiro aberta no Waze.');
}

async function irDestino(id,url){
 abrirMaps(url);
 toast('🗺️ Rota até o destino aberta no Google Maps.');
}

async function irDestinoWaze(id,url){
 abrirWaze(url);
 toast('🚗 Rota até o destino aberta no Waze.');
}

async function iniciarCorrida(id){
 try{
  const chegada=await fetch('/motorista/cheguei/'+id,{
   method:'POST'
  });

  if(!chegada.ok){
   toast('Não foi possível registrar a chegada.');
   return;
  }

  const r=await fetch('/api/corrida/'+id+'/iniciar',{
   method:'POST'
  });

  const d=await r.json();

  if(!d.ok){
   toast(d.erro||'Não foi possível iniciar a corrida.');
   return;
  }

  toast('🏍️ Corrida iniciada!');
  carregar();
  minhas();
  ganhos();

 }catch(e){
  toast('Erro de conexão.');
 }
}

async function concluir(id){
 try{
  const r=await fetch('/api/corrida/'+id+'/concluir',{
   method:'POST'
  });

  const d=await r.json();

  if(!d.ok){
   toast(d.erro||'Não foi possível concluir a corrida.');
   return;
  }

  toast('🔴 Corrida concluída! 💰');
  carregar();
  minhas();
  ganhos();

 }catch(e){
  toast('Erro de conexão.');
 }
}

function ignorarPedido(id,btn){const card=btn.closest('.ride-card');if(card)card.style.display='none';}
async function minhas(){
 try{const r=await fetch('/api/motorista/minhas-corridas',{cache:'no-store'}),d=await r.json(); if(!d.ok)return; window._minhas=d.corridas||[]; if(currentView==='historico')renderHistorico(window._minhas);}catch(e){}
}
function renderHistorico(cs){
 const box=document.getElementById('lista');
 if(!cs.length){box.innerHTML='<div class="empty"><strong>Nenhuma corrida ainda</strong>Suas corridas aparecerão aqui.</div>';return}
 box.innerHTML=cs.map(c=>`<div class="ride-card"><div class="ride-row"><div class="ride-main"><b>Corrida #${c.id}</b><br>👤 ${esc(c.passageiro_nome||'Passageiro')}<br>📍 ${esc(c.origem)}<br>🏁 ${esc(c.destino)}<br><span class="ride-muted">📌 ${esc(c.status)}</span></div><div class="ride-price">${br(c.valor)}</div></div>${c.status==='ACEITA'?'<div class="ride-actions"><button class="btn-accept" onclick="acao('+c.id+',\'iniciar\')">INICIAR CORRIDA</button></div>':''}${c.status==='EM_ANDAMENTO'?'<div class="ride-actions"><button class="btn-accept" onclick="acao('+c.id+',\'concluir\')">CONCLUIR CORRIDA</button></div>':''}</div>`).join('');
}
async function acao(id,a){const r=await fetch('/api/corrida/'+id+'/'+a,{method:'POST'}),d=await r.json();toast(d.ok?'Atualizado!':(d.erro||'Erro'));minhas();ganhos();carregar();}
async function ganhos(){
 try{const r=await fetch('/api/motorista/ganhos',{cache:'no-store'}),d=await r.json();if(!d.ok)return;document.getElementById('pillMoney').textContent=br(d.total_hoje);document.getElementById('hojeCorridas').textContent=d.corridas_hoje;document.getElementById('hojeGanhos').textContent=br(d.total_hoje);document.getElementById('totalGanhos').textContent=br(d.total_geral);if(currentView==='ganhos')renderGanhos(d);}catch(e){}
}
function renderGanhos(d){document.getElementById('lista').innerHTML='<div class="ride-card"><div class="ride-row"><div><div class="ride-muted">GANHOS DE HOJE</div><div style="font-size:32px;font-weight:900;margin-top:4px">'+br(d.total_hoje)+'</div></div><div style="text-align:right"><div class="ride-muted">CORRIDAS</div><div style="font-size:25px;font-weight:900">'+d.corridas_hoje+'</div></div></div><div style="margin-top:15px;color:#aaa;font-size:13px">Total concluído: <b style="color:#fff">'+br(d.total_geral)+'</b></div></div>';}
function mostrar(view,el){currentView=view;document.querySelectorAll('.nav-item').forEach(x=>x.classList.remove('active'));if(el)el.classList.add('active');if(view==='historico'){minhas();renderHistorico(window._minhas||[]);document.getElementById('sheetTitle').textContent='Histórico de corridas';document.getElementById('sheetSub').textContent='Confira suas corridas e os respectivos valores.'}else if(view==='ganhos'){ganhos();document.getElementById('sheetTitle').textContent='Meus ganhos';document.getElementById('sheetSub').textContent='Acompanhe quanto você ganhou.'}else{document.getElementById('sheetTitle').textContent=online?'Você está online':'Você está offline';document.getElementById('sheetSub').textContent=online?'Aguardando novos pedidos em Aragoiânia.':'Fique online para receber novos pedidos em Aragoiânia.';carregar();}}
function testarSom(){try{const C=window.AudioContext||window.webkitAudioContext,c=new C(),o=c.createOscillator(),g=c.createGain();o.frequency.value=720;g.gain.value=.07;o.connect(g);g.connect(c.destination);o.start();setTimeout(()=>o.frequency.value=1050,130);setTimeout(()=>{o.stop();c.close()},380)}catch(e){}}
initMap();status();carregar();minhas();ganhos();setInterval(()=>{status();carregar();minhas();ganhos()},5000);
</script>
</body>
</html>
''', nome=nome, inicial=inicial)


@app.route("/motorista/aceitar/<int:id>", methods=["POST"])
def motorista_aceitar(id):
    mid = _motorista_logado()

    if not mid:
        return redirect(url_for("login_motorista"))

    conn = conectar()

    m = conn.execute("""
        SELECT status,conexao
        FROM motoqueiros
        WHERE id=?
    """, (mid,)).fetchone()

    if not m or m["status"] != "aprovado":
        conn.close()
        return redirect(url_for("login_motorista"))

    if m["conexao"] != "online":
        conn.close()
        return _pagina_publica(
            "Motorista",
            '<div class="alert erro">Fique ONLINE para aceitar uma corrida.</div>'
            '<a class="pub-btn pub-green" href="/motorista">Voltar</a>'
        )

    cur = conn.execute("""
        UPDATE corridas_vai
        SET motorista_id=?, status='ACEITA'
        WHERE id=?
          AND status='PENDENTE'
          AND motorista_id IS NULL
    """, (mid, id))

    conn.commit()
    conn.close()

    if cur.rowcount == 0:
        return _pagina_publica(
            "Corrida",
            '<div class="alert erro">Essa corrida já foi aceita por outro motorista.</div>'
            '<a class="pub-btn pub-green" href="/motorista">Voltar</a>'
        )

    return redirect(url_for("motorista"))


@app.route("/motorista/cancelar/<int:id>", methods=["POST"])
def motorista_cancelar_corrida(id):
    mid = _motorista_logado()

    if not mid:
        return redirect(url_for("login_motorista"))

    conn = conectar()

    cur = conn.execute("""
        UPDATE corridas_vai
        SET status='CANCELADA',
            cancelado_em=CURRENT_TIMESTAMP
        WHERE id=?
          AND motorista_id=?
          AND status IN ('ACEITA','EM_ANDAMENTO')
    """, (id, mid))

    conn.commit()
    conn.close()

    return redirect(url_for("motorista"))


@app.route("/motorista/iniciar/<int:id>", methods=["POST"])
def motorista_iniciar(id):
    mid = _motorista_logado()

    if not mid:
        return redirect(url_for("login_motorista"))

    conn = conectar()

    cur = conn.execute("""
        UPDATE corridas_vai
        SET status='EM_ANDAMENTO'
        WHERE id=?
          AND motorista_id=?
          AND status='ACEITA'
          AND etapa='CHEGOU'
    """, (id, mid))

    conn.commit()
    conn.close()

    return redirect(url_for("motorista"))


@app.route("/motorista/concluir/<int:id>", methods=["POST"])
def motorista_concluir(id):
    mid = _motorista_logado()

    if not mid:
        return redirect(url_for("login_motorista"))

    conn = conectar()

    cur = conn.execute("""
        UPDATE corridas_vai
        SET status='CONCLUIDA',
            concluido_em=datetime('now','localtime')
        WHERE id=?
          AND motorista_id=?
          AND status='EM_ANDAMENTO'
    """, (id, mid))

    conn.commit()
    conn.close()

    return redirect(url_for("motorista"))


def _json():
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict()


def _passageiro_logado():
    return session.get("passageiro_id")


def _motorista_logado():
    return session.get("motorista_id")


@app.route("/api/motorista/som-dinheiro")
def api_motorista_som_dinheiro():
    mid = _motorista_logado()

    if not mid:
        return {"ok": False}, 401

    tocar = bool(session.pop("vai_moto_som_dinheiro", False))

    return {"ok": True, "tocar": tocar}


@app.route("/api/buscar-enderecos", methods=["POST"])
def api_buscar_enderecos():
    data = _json()

    q = " ".join((data.get("q") or "").split()).strip()

    if not q:
        return {"ok": False, "resultados": []}

    # GPS do passageiro.
    # Serve para PRIORIZAR resultados próximos.
    # Nunca limita a busca a uma cidade específica.
    gps_lat = data.get("lat")
    gps_lon = data.get("lon")

    try:
        gps_lat = float(gps_lat) if gps_lat not in (None, "") else None
        gps_lon = float(gps_lon) if gps_lon not in (None, "") else None
    except Exception:
        gps_lat = None
        gps_lon = None

    resultados = []

    def normalizar(texto):
        return " ".join((texto or "").lower().split()).strip()

    def sem_acentos(texto):
        texto = str(texto or "")
        return "".join(
            c for c in unicodedata.normalize("NFD", texto)
            if unicodedata.category(c) != "Mn"
        )

    def adicionar(lat, lon, nome, fonte=""):
        if lat in (None, "") or lon in (None, ""):
            return

        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except Exception:
            return

        nome = str(nome or "").strip()

        if not nome:
            nome = "Endereço encontrado"

        # Evita duplicados.
        chave = (round(lat_f, 6), round(lon_f, 6))

        for r in resultados:
            try:
                chave2 = (
                    round(float(r["lat"]), 6),
                    round(float(r["lon"]), 6)
                )

                if chave == chave2:
                    return
            except Exception:
                pass

        resultados.append({
            "display_name": nome,
            "lat": str(lat_f),
            "lon": str(lon_f),
            "_fonte": fonte
        })

    # =========================================================
    # CORREÇÕES DE DIGITAÇÃO
    # =========================================================

    correcoes = {
        "girasol": "girassol",
        "aragoiania": "aragoiânia",
        "aragoiania go": "aragoiânia GO",
        "aragoiania goias": "aragoiânia Goiás"
    }

    q_corrigido = q

    for errado, correto in correcoes.items():
        q_corrigido = q_corrigido.replace(errado, correto)

    # =========================================================
    # MONTA VÁRIAS FORMAS DA PESQUISA
    # =========================================================

    consultas = []

    def adicionar_consulta(valor):
        valor = " ".join((valor or "").split()).strip()

        if not valor:
            return

        chave = normalizar(valor)

        if chave not in {normalizar(x) for x in consultas}:
            consultas.append(valor)

    adicionar_consulta(q)
    adicionar_consulta(q_corrigido)

    # Também tenta a versão sem acentos.
    # Isso ajuda em nomes como Aragoiânia/Aragoiania,
    # João/Joao, São/Sao, etc.
    adicionar_consulta(sem_acentos(q))
    adicionar_consulta(sem_acentos(q_corrigido))

    # Algumas ruas e locais possuem grafias diferentes
    # cadastradas nos mapas.
    q_izabel = q_corrigido.replace(
        "Isabel", "Izabel"
    ).replace(
        "isabel", "izabel"
    )

    q_isabel = q_corrigido.replace(
        "Izabel", "Isabel"
    ).replace(
        "izabel", "isabel"
    )

    adicionar_consulta(q_izabel)
    adicionar_consulta(q_isabel)

    # Brasil ajuda o geocodificador a interpretar a pesquisa.
    adicionar_consulta(q + ", Brasil")
    adicionar_consulta(q_corrigido + ", Brasil")

    # Se o usuário estiver procurando um estabelecimento,
    # também tentamos retirar palavras genéricas.
    # Ex.: "Escola Municipal Padre João Bosco"
    # -> "Padre João Bosco"
    termos_genericos = {
        "escola",
        "municipal",
        "estadual",
        "colégio",
        "colegio",
        "creche",
        "unidade",
        "posto",
        "igreja",
        "hospital",
        "mercado",
        "supermercado",
        "farmácia",
        "farmacia"
    }

    palavras = q_corrigido.split()

    palavras_reduzidas = [
        p for p in palavras
        if normalizar(p).strip(",") not in termos_genericos
    ]

    if len(palavras_reduzidas) >= 2:
        adicionar_consulta(" ".join(palavras_reduzidas))
        adicionar_consulta(
            " ".join(palavras_reduzidas) + ", Brasil"
        )

    # =========================================================
    # 1) NOMINATIM / OPENSTREETMAP
    # =========================================================

    for consulta in consultas:
        try:
            params_dict = {
                "q": consulta,
                "format": "jsonv2",
                "limit": 10,
                "countrycodes": "br",
                "addressdetails": 1,
                "dedupe": 1,
                "accept-language": "pt-BR"
            }

            # Viewbox é apenas preferência.
            # NÃO usamos bounded=1.
            if gps_lat is not None and gps_lon is not None:
                margem = 0.35

                params_dict["viewbox"] = ",".join([
                    str(gps_lon - margem),
                    str(gps_lat + margem),
                    str(gps_lon + margem),
                    str(gps_lat - margem)
                ])

            params = urlencode(params_dict)

            req = Request(
                "https://nominatim.openstreetmap.org/search?" + params,
                headers={
                    "User-Agent":
                        "VAI_DE_MOTO/1.0 (aplicativo de transporte)"
                }
            )

            with urlopen(req, timeout=10) as resp:
                arr = json.loads(
                    resp.read().decode("utf-8")
                )

            for x in arr:
                adicionar(
                    x.get("lat"),
                    x.get("lon"),
                    x.get("display_name", ""),
                    "nominatim"
                )

        except Exception:
            continue

    # =========================================================
    # 2) PHOTON / KOMOOT
    # =========================================================

    for consulta in consultas:
        try:
            params_dict = {
                "q": consulta,
                "limit": 10,
                "lang": "pt"
            }

            # GPS ajuda o Photon a trazer primeiro
            # os resultados próximos.
            if gps_lat is not None and gps_lon is not None:
                params_dict["lat"] = gps_lat
                params_dict["lon"] = gps_lon

            params = urlencode(params_dict)

            req = Request(
                "https://photon.komoot.io/api/?" + params,
                headers={
                    "User-Agent":
                        "VAI_DE_MOTO/1.0 (aplicativo de transporte)"
                }
            )

            with urlopen(req, timeout=10) as resp:
                dados = json.loads(
                    resp.read().decode("utf-8")
                )

            for feature in dados.get("features", []):
                geom = feature.get("geometry", {})
                coords = geom.get("coordinates", [])

                if len(coords) < 2:
                    continue

                lon = coords[0]
                lat = coords[1]

                props = feature.get("properties", {})

                partes = []

                for chave in (
                    "name",
                    "street",
                    "housenumber",
                    "district",
                    "city",
                    "county",
                    "state",
                    "country",
                    "postcode"
                ):
                    valor = props.get(chave)

                    if valor and str(valor) not in partes:
                        partes.append(str(valor))

                nome = ", ".join(partes)

                adicionar(
                    lat,
                    lon,
                    nome,
                    "photon"
                )

        except Exception:
            continue

    # =========================================================
    # 3) ARCGIS WORLD GEOCODING
    # =========================================================
    # Complementa Nominatim + Photon para POIs, escolas,
    # comércios, ruas e endereços que não estejam bem
    # indexados nas outras fontes.
    # A busca continua nacional e o GPS apenas ajuda
    # a priorizar a região do passageiro.
    # =========================================================

    try:
        consultas_arcgis = []

        for consulta in consultas:
            if consulta not in consultas_arcgis:
                consultas_arcgis.append(consulta)

        # A consulta original/corrigida é a mais importante.
        for consulta in consultas_arcgis:
            try:
                params_dict = {
                    "SingleLine": consulta,
                    "countryCode": "BRA",
                    "maxLocations": 10,
                    "f": "json"
                }

                # O GPS é usado como ponto de preferência.
                if gps_lat is not None and gps_lon is not None:
                    params_dict["location"] = (
                        f"{gps_lon},{gps_lat}"
                    )

                params = urlencode(params_dict)

                req = Request(
                    "https://geocode.arcgis.com/arcgis/rest/services/"
                    "World/GeocodeServer/findAddressCandidates?"
                    + params,
                    headers={
                        "User-Agent":
                            "VAI_DE_MOTO/1.0 (aplicativo de transporte)"
                    }
                )

                with urlopen(req, timeout=10) as resp:
                    dados = json.loads(
                        resp.read().decode("utf-8")
                    )

                for candidato in dados.get("candidates", []):
                    endereco = candidato.get(
                        "address",
                        ""
                    )

                    localizacao = candidato.get(
                        "location",
                        {}
                    )

                    adicionar(
                        localizacao.get("y"),
                        localizacao.get("x"),
                        endereco,
                        "arcgis"
                    )

            except Exception:
                continue

    except Exception:
        pass

    # =========================================================
    # 4) ORDENAÇÃO INTELIGENTE
    # =========================================================

    termos_busca = [
        x.strip(" ,.-").lower()
        for x in q_corrigido.split()
        if len(x.strip(" ,.-")) >= 3
    ]

    def pontuacao(item):
        nome = normalizar(item.get("display_name", ""))

        # Quanto mais palavras da busca aparecem no resultado,
        # melhor a pontuação.
        correspondencias = 0

        for termo in termos_busca:
            if termo in nome:
                correspondencias += 1

        # Distância aproximada do GPS.
        distancia = 999999.0

        if gps_lat is not None and gps_lon is not None:
            try:
                lat = float(item["lat"])
                lon = float(item["lon"])

                # Aproximação suficiente para ordenar resultados.
                dlat = (lat - gps_lat) * 111.0
                dlon = (
                    (lon - gps_lon)
                    * 111.0
                    * __import__("math").cos(
                        __import__("math").radians(gps_lat)
                    )
                )

                distancia = (
                    (dlat * dlat + dlon * dlon)
                    ** 0.5
                )
            except Exception:
                pass

        # Correspondência de texto pesa bastante.
        # Proximidade desempata resultados parecidos.
        score = (
            correspondencias * 1000
            - min(distancia, 500) * 2
        )

        return score

    resultados.sort(
        key=pontuacao,
        reverse=True
    )

    # Remove campo interno antes de enviar ao navegador.
    saida = []

    for item in resultados[:15]:
        saida.append({
            "display_name": item["display_name"],
            "lat": item["lat"],
            "lon": item["lon"]
        })

    return {
        "ok": True,
        "resultados": saida
    }

    # GPS atual do passageiro: usado apenas para priorizar
    # resultados próximos, sem limitar a busca à cidade atual.
    gps_lat = data.get("lat")
    gps_lon = data.get("lon")

    try:
        gps_lat = float(gps_lat) if gps_lat not in (None, "") else None
        gps_lon = float(gps_lon) if gps_lon not in (None, "") else None
    except Exception:
        gps_lat = None
        gps_lon = None

    resultados = []

    def adicionar(lat, lon, nome):
        if lat in (None, "") or lon in (None, ""):
            return

        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except Exception:
            return

        item = {
            "display_name": nome or "",
            "lat": str(lat_f),
            "lon": str(lon_f)
        }

        chave = (round(lat_f, 6), round(lon_f, 6))

        for r in resultados:
            try:
                chave2 = (
                    round(float(r["lat"]), 6),
                    round(float(r["lon"]), 6)
                )
                if chave == chave2:
                    return
            except Exception:
                pass

        resultados.append(item)

    # =========================================================
    # 1) NOMINATIM / OPENSTREETMAP
    # =========================================================
    consultas = []

    def adicionar_consulta(texto):
        texto = " ".join((texto or "").split()).strip()
        if texto and texto.lower() not in {
            x.lower() for x in consultas
        }:
            consultas.append(texto)

    adicionar_consulta(q)

    # =========================================================
    # MELHORIAS PARA ENDEREÇOS DIGITADOS COM ERROS
    # =========================================================
    q_lower = q.lower()

    # Corrige erros comuns de digitação
    correcoes = {
        "girasol": "girassol",
        "aragoiania": "aragoiânia",
        "aragoiania, goias": "aragoiânia, Goiás",
        "aragoiania, go": "aragoiânia, Goiás"
    }

    q_corrigido = q
    for errado, correto in correcoes.items():
        q_corrigido = q_corrigido.replace(errado, correto)

    adicionar_consulta(q_corrigido)

    # A busca é nacional.
    # Não forçamos nenhuma cidade como padrão.
    cidades_conhecidas = ()

    # Tentativas gerais para endereços do Brasil.
    adicionar_consulta(q_corrigido)
    adicionar_consulta(q + ", Brasil")
    adicionar_consulta(q_corrigido + ", Brasil")

    for consulta in consultas:
        try:
            params_dict = {
                "q": consulta,
                "format": "jsonv2",
                "limit": 10,
                "countrycodes": "br",
                "addressdetails": 1,
                "dedupe": 1,
                "accept-language": "pt-BR"
            }

            # Quando temos GPS, usamos uma área ao redor do passageiro
            # apenas como prioridade de busca. A busca continua nacional.
            if gps_lat is not None and gps_lon is not None:
                margem = 0.35
                params_dict["viewbox"] = ",".join([
                    str(gps_lon - margem),
                    str(gps_lat + margem),
                    str(gps_lon + margem),
                    str(gps_lat - margem)
                ])

            params = urlencode(params_dict)

            req = Request(
                "https://nominatim.openstreetmap.org/search?" + params,
                headers={
                    "User-Agent":
                        "VAI_DE_MOTO/1.0 (aplicativo de transporte)"
                }
            )

            with urlopen(req, timeout=10) as resp:
                arr = json.loads(
                    resp.read().decode("utf-8")
                )

            for x in arr:
                adicionar(
                    x.get("lat"),
                    x.get("lon"),
                    x.get("display_name", "")
                )

            # Deixa espaço para o Photon complementar a busca.
            if len(resultados) >= 5:
                break

        except Exception:
            continue

    # =========================================================
    # 2) PHOTON / KOMOOT
    # Sempre complementa a busca do Nominatim.
    # =========================================================
    if len(resultados) < 10:
        consultas_photon = [q]

        # Usa a versão corrigida também no Photon.
        if q_corrigido.lower() not in {
            x.lower() for x in consultas_photon
        }:
            consultas_photon.append(q_corrigido)

        # Para buscas sem "Brasil", fazemos tentativas extras
        # deixando explícito que o endereço está no Brasil.
        if "brasil" not in q.lower():
            consultas_photon.append(q + ", Brasil")
            consultas_photon.append(q_corrigido + ", Brasil")

        # Não forçamos Aragoiânia.
        # O GPS já serve para priorizar resultados próximos.

        for consulta in consultas_photon:
            try:
                params_dict = {
                    "q": consulta,
                    "limit": 10,
                    "lang": "pt"
                }

                # O Photon também recebe o GPS para priorizar
                # resultados próximos, sem bloquear outras cidades.
                if gps_lat is not None and gps_lon is not None:
                    params_dict["lat"] = gps_lat
                    params_dict["lon"] = gps_lon

                params = urlencode(params_dict)

                req = Request(
                    "https://photon.komoot.io/api/?" + params,
                    headers={
                        "User-Agent":
                            "VAI_DE_MOTO/1.0 (aplicativo de transporte)"
                    }
                )

                with urlopen(req, timeout=10) as resp:
                    dados = json.loads(
                        resp.read().decode("utf-8")
                    )

                for feature in dados.get("features", []):
                    geom = feature.get("geometry", {})
                    coords = geom.get("coordinates", [])

                    if len(coords) < 2:
                        continue

                    lon = coords[0]
                    lat = coords[1]

                    props = feature.get("properties", {})
                    partes = []

                    for chave in (
                        "name",
                        "street",
                        "housenumber",
                        "district",
                        "city",
                        "county",
                        "state",
                        "country"
                    ):
                        valor = props.get(chave)

                        if valor and str(valor) not in partes:
                            partes.append(str(valor))

                    nome = ", ".join(partes)

                    adicionar(lat, lon, nome)

                    if len(resultados) >= 10:
                        break

                if len(resultados) >= 10:
                    break

            except Exception:
                continue

    return {
        "ok": True,
        "resultados": resultados[:10]
    }

@app.route("/api/endereco-gps", methods=["POST"])
def api_endereco_gps():
    data = _json()
    lat = data.get("lat")
    lon = data.get("lon")
    if lat in (None, "") or lon in (None, ""):
        return {"ok": False, "erro": "Coordenadas do GPS não informadas."}

    try:
        params = urlencode({
            "lat": lat,
            "lon": lon,
            "format": "jsonv2",
            "addressdetails": 1,
            "zoom": 18
        })
        req = Request(
            "https://nominatim.openstreetmap.org/reverse?" + params,
            headers={
                "User-Agent": "VAI_DE_MOTO/1.0 (aplicativo de transporte local)"
            }
        )
        with urlopen(req, timeout=10) as resp:
            x = json.loads(resp.read().decode("utf-8"))
        return {
            "ok": True,
            "endereco": x.get("display_name", ""),
            "lat": x.get("lat", lat),
            "lon": x.get("lon", lon)
        }
    except Exception:
        return {
            "ok": False,
            "erro": "Não foi possível transformar o GPS em endereço."
        }


@app.route("/api/calcular-corrida", methods=["POST"])
def api_calcular_corrida():
    data = _json()
    distancia = _distancia_km(
        data.get("origem_lat"), data.get("origem_lon"),
        data.get("dest_lat"), data.get("dest_lon")
    )
    if distancia <= 0:
        return {"ok": False, "erro": "Coordenadas inválidas."}
    # Tarifa VAI_DE_MOTO:
    # Até 4 km = R$ 7,00 fixos
    # Acima de 4 km = R$ 2,00 por km da distância total
    if distancia <= 4:
        valor = 7.00
    else:
        valor = round(distancia * PRECO_KM, 2)

    taxa = round(valor * TAXA_APP, 2)
    motorista = round(valor - taxa, 2)
    return {
        "ok": True,
        "distancia_km": round(distancia, 2),
        "valor": valor,
        "taxa_app": taxa,
        "valor_motorista": motorista,
        "preco_km": PRECO_KM
    }


@app.route("/api/solicitar-corrida", methods=["POST"])
def api_solicitar_corrida():
    pid = _passageiro_logado()
    if not pid:
        return {"ok": False, "erro": "Faça login como passageiro."}, 401

    data = _json()
    origem = (data.get("origem") or "").strip()
    destino = (data.get("destino") or "").strip()
    pagamento = (data.get("pagamento") or "DINHEIRO").upper()
    try:
        distancia = float(data.get("distancia_km") or 0)
        valor = round(float(data.get("valor") or 0), 2)
    except Exception:
        distancia, valor = 0, 0

    if not origem or not destino or distancia <= 0 or valor <= 0:
        return {"ok": False, "erro": "Origem, destino e valor são obrigatórios."}
    if pagamento not in ("DINHEIRO", "PIX"):
        pagamento = "DINHEIRO"

    taxa = round(valor * TAXA_APP, 2)
    valor_motorista = round(valor - taxa, 2)
    pagamento_status = "PENDENTE" if pagamento == "PIX" else "NAO_APLICAVEL"

    conn = conectar()
    cur = conn.execute("""
        INSERT INTO corridas_vai
        (passageiro_id, motorista_id, origem, destino, valor, status, observacao,
         pagamento, pagamento_status, pix_chave, distancia_km, taxa_app, valor_motorista)
        VALUES (?, NULL, ?, ?, ?, 'PENDENTE', '', ?, ?, ?, ?, ?, ?)
    """, (pid, origem, destino, valor, pagamento, pagamento_status, PIX_ADMIN,
          distancia, taxa, valor_motorista))
    conn.commit()
    corrida_id = cur.lastrowid
    conn.close()

    # =========================================================
    # ASAAS - cria Checkout para pagamento online
    # =========================================================
    checkout_url = ""

    if pagamento == "PIX":
        checkout_id, checkout_url, erro_asaas = criar_checkout_asaas(
            corrida_id,
            valor,
            origem,
            destino
        )

        if erro_asaas:
            # Não deixa uma corrida PIX criada sem Checkout.
            conn = conectar()
            conn.execute(
                "DELETE FROM corridas_vai WHERE id=? AND passageiro_id=?",
                (corrida_id, pid)
            )
            conn.commit()
            conn.close()

            return {
                "ok": False,
                "erro": erro_asaas
            }

        conn = conectar()
        conn.execute("""
            UPDATE corridas_vai
            SET asaas_checkout_id=?,
                asaas_checkout_url=?
            WHERE id=? AND passageiro_id=?
        """, (
            checkout_id,
            checkout_url,
            corrida_id,
            pid
        ))
        conn.commit()
        conn.close()

    return {
        "ok": True,
        "id": corrida_id,
        "valor": valor,
        "taxa_app": taxa,
        "valor_motorista": valor_motorista,
        "pagamento": pagamento,
        "pagamento_status": pagamento_status,
        "checkout_url": checkout_url
    }


@app.route("/api/minhas-corridas")
def api_minhas_corridas():
    pid = _passageiro_logado()
    if not pid:
        return {"ok": False, "erro": "Não autenticado."}, 401
    conn = conectar()
    rows = conn.execute("""
        SELECT c.*, m.nome AS motorista_nome, m.telefone AS motorista_telefone
        FROM corridas_vai c
        LEFT JOIN motoqueiros m ON m.id=c.motorista_id
        WHERE c.passageiro_id=?
        ORDER BY c.id DESC
        LIMIT 30
    """, (pid,)).fetchall()
    conn.close()
    return {"ok": True, "corridas": [dict(r) for r in rows]}


@app.route("/api/corrida/<int:id>/cancelar", methods=["POST"])
def api_cancelar_corrida(id):
    pid = _passageiro_logado()
    if not pid:
        return {"ok": False, "erro": "Não autenticado."}, 401
    conn = conectar()
    cur = conn.execute("""
        UPDATE corridas_vai SET status='CANCELADA', cancelado_em=CURRENT_TIMESTAMP
        WHERE id=? AND passageiro_id=? AND status IN ('PENDENTE','ACEITA')
    """, (id, pid))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        return {"ok": False, "erro": "Corrida não pode mais ser cancelada."}
    return {"ok": True}


@app.route("/api/motorista/me")
def api_motorista_me():
    mid = _motorista_logado()
    if not mid:
        return {"ok": False, "erro": "Não autenticado."}, 401
    conn = conectar()
    m = conn.execute("SELECT id,nome,telefone,status,conexao FROM motoqueiros WHERE id=?", (mid,)).fetchone()
    conn.close()
    if not m:
        return {"ok": False, "erro": "Motorista não encontrado."}, 404
    return {"ok": True, **dict(m)}


@app.route("/api/motorista/status", methods=["POST"])
def api_motorista_status():
    mid = _motorista_logado()
    if not mid:
        return {"ok": False, "erro": "Não autenticado."}, 401
    data = _json()
    online = bool(data.get("online"))
    conn = conectar()
    m = conn.execute("SELECT status FROM motoqueiros WHERE id=?", (mid,)).fetchone()
    if not m or m["status"] != "aprovado":
        conn.close()
        return {"ok": False, "erro": "Motorista ainda não aprovado."}
    conn.execute("UPDATE motoqueiros SET conexao=? WHERE id=?", ("online" if online else "offline", mid))
    conn.commit()
    conn.close()
    return {"ok": True, "conexao": "online" if online else "offline"}


@app.route("/api/motorista/heartbeat", methods=["POST"])
def api_motorista_heartbeat():
    mid = _motorista_logado()
    if not mid:
        return {"ok": False}, 401

    data = _json()

    try:
        latitude = float(data.get("latitude"))
        longitude = float(data.get("longitude"))
    except Exception:
        latitude = None
        longitude = None

    conn = conectar()

    if latitude is not None and longitude is not None:
        conn.execute("""
            UPDATE motoqueiros
            SET conexao='online',
                latitude=?,
                longitude=?
            WHERE id=? AND status='aprovado'
        """, (latitude, longitude, mid))
    else:
        conn.execute("""
            UPDATE motoqueiros
            SET conexao='online'
            WHERE id=? AND status='aprovado'
        """, (mid,))

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "latitude": latitude,
        "longitude": longitude
    }


@app.route("/api/corridas-disponiveis")
def api_corridas_disponiveis():
    mid = _motorista_logado()
    if not mid:
        return {"ok": False, "erro": "Faça login como motorista."}, 401
    conn = conectar()
    m = conn.execute("SELECT status,conexao FROM motoqueiros WHERE id=?", (mid,)).fetchone()
    if not m or m["status"] != "aprovado":
        conn.close()
        return {"ok": False, "erro": "Motorista não aprovado."}
    if m["conexao"] != "online":
        conn.close()
        return {"ok": True, "corridas": []}
    rows = conn.execute("""
        SELECT c.id,c.origem,c.destino,c.valor,c.status,c.pagamento,c.distancia_km,c.criado_em
        FROM corridas_vai c
        WHERE c.status='PENDENTE' AND c.motorista_id IS NULL
        ORDER BY c.id DESC LIMIT 20
    """).fetchall()
    conn.close()
    return {"ok": True, "corridas": [dict(r) for r in rows]}


@app.route("/api/corrida/<int:id>/aceitar", methods=["POST"])
def api_aceitar_corrida(id):
    mid = _motorista_logado()
    if not mid:
        return {"ok": False, "erro": "Não autenticado."}, 401
    conn = conectar()
    m = conn.execute("SELECT status,conexao FROM motoqueiros WHERE id=?", (mid,)).fetchone()
    if not m or m["status"] != "aprovado" or m["conexao"] != "online":
        conn.close()
        return {"ok": False, "erro": "Fique online e esteja aprovado para aceitar."}
    cur = conn.execute("""
        UPDATE corridas_vai
        SET motorista_id=?, status='ACEITA'
        WHERE id=? AND status='PENDENTE' AND motorista_id IS NULL
    """, (mid, id))
    conn.commit()
    conn.close()
    if cur.rowcount == 0:
        return {"ok": False, "erro": "Essa corrida já foi aceita por outro motorista."}
    return {"ok": True}


@app.route("/motorista/cheguei/<int:id>", methods=["POST"])
def motorista_cheguei(id):
    mid = _motorista_logado()
    if not mid:
        return redirect(url_for("login_motorista"))

    conn = conectar()
    cur = conn.execute("""
        UPDATE corridas_vai
        SET etapa='CHEGOU'
        WHERE id=? AND motorista_id=? AND status='ACEITA'
    """, (id, mid))
    conn.commit()
    conn.close()

    return redirect(url_for("motorista"))


@app.route("/api/corrida/<int:id>/iniciar", methods=["POST"])
def api_iniciar_corrida(id):
    mid = _motorista_logado()
    if not mid:
        return {"ok": False, "erro": "Não autenticado."}, 401
    conn = conectar()
    cur = conn.execute("""
        UPDATE corridas_vai SET status='EM_ANDAMENTO', iniciado_em=CURRENT_TIMESTAMP
        WHERE id=? AND motorista_id=? AND status='ACEITA'
    """, (id, mid))
    conn.commit()
    conn.close()
    return {"ok": cur.rowcount > 0, "erro": None if cur.rowcount else "Corrida não está disponível para iniciar."}


@app.route("/api/corrida/<int:id>/concluir", methods=["POST"])
def api_concluir_corrida(id):
    mid = _motorista_logado()
    if not mid:
        return {"ok": False, "erro": "Não autenticado."}, 401
    conn = conectar()
    cur = conn.execute("""
        UPDATE corridas_vai SET status='CONCLUIDA', concluido_em=CURRENT_TIMESTAMP
        WHERE id=? AND motorista_id=? AND status='EM_ANDAMENTO'
    """, (id, mid))
    conn.commit()
    conn.close()
    if cur.rowcount > 0:
        session["vai_moto_som_dinheiro"] = True

    return {"ok": cur.rowcount > 0, "erro": None if cur.rowcount else "Corrida não está em andamento."}


@app.route("/api/motorista/minhas-corridas")
def api_motorista_minhas_corridas():
    mid = _motorista_logado()
    if not mid:
        return {"ok": False, "erro": "Não autenticado."}, 401
    conn = conectar()
    rows = conn.execute("""
        SELECT c.*, p.nome AS passageiro_nome, p.telefone AS passageiro_telefone
        FROM corridas_vai c
        LEFT JOIN passageiros p ON p.id=c.passageiro_id
        WHERE c.motorista_id=?
        ORDER BY c.id DESC LIMIT 30
    """, (mid,)).fetchall()
    conn.close()
    return {"ok": True, "corridas": [dict(r) for r in rows]}


@app.route("/api/motorista/ganhos")
def api_motorista_ganhos():
    mid = _motorista_logado()
    if not mid:
        return {"ok": False, "erro": "Não autenticado."}, 401
    conn = conectar()
    hoje = conn.execute("""
        SELECT COUNT(*) AS n, COALESCE(SUM(valor_motorista),0) AS total
        FROM corridas_vai
        WHERE motorista_id=? AND status='CONCLUIDA'
          AND date(concluido_em)=date('now','localtime')
    """, (mid,)).fetchone()
    geral = conn.execute("""
        SELECT COALESCE(SUM(valor_motorista),0) AS total
        FROM corridas_vai WHERE motorista_id=? AND status='CONCLUIDA'
    """, (mid,)).fetchone()
    conn.close()
    return {
        "ok": True,
        "corridas_hoje": hoje["n"],
        "total_hoje": float(hoje["total"] or 0),
        "total_geral": float(geral["total"] or 0)
    }


# Aliases de compatibilidade com a estrutura pública que já vinha sendo usada.
api_motoqueiro_me = api_motorista_me
api_motoqueiro_status = api_motorista_status


# ==============================

# =========================================================
# VAI_DE_MOTO - SISTEMA DE SAQUES
# =========================================================

def garantir_tabela_saques():
    conn = conectar()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS saques (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            motorista_id INTEGER NOT NULL,
            valor REAL NOT NULL DEFAULT 0,
            pix_chave TEXT NOT NULL DEFAULT '',
            pix_tipo TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'PENDENTE',
            observacao TEXT NOT NULL DEFAULT '',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            pago_em TIMESTAMP,
            FOREIGN KEY (motorista_id) REFERENCES motoqueiros(id)
        )
    """)
    conn.commit()
    conn.close()


@app.route("/motorista/saque", methods=["GET", "POST"])
def motorista_saque():
    mid = _motorista_logado()

    if not mid:
        return redirect(url_for("login_motorista"))

    garantir_tabela_saques()

    conn = conectar()

    motorista = conn.execute("""
        SELECT id, nome, telefone, status
        FROM motoqueiros
        WHERE id=?
    """, (mid,)).fetchone()

    if not motorista:
        conn.close()
        return "Motorista não encontrado.", 404

    ganhos = conn.execute("""
        SELECT COALESCE(SUM(valor_motorista),0) AS total
        FROM corridas_vai
        WHERE motorista_id=?
          AND status='CONCLUIDA'
    """, (mid,)).fetchone()

    saques = conn.execute("""
        SELECT COALESCE(SUM(valor),0) AS total
        FROM saques
        WHERE motorista_id=?
          AND status IN ('PENDENTE','PAGO')
    """, (mid,)).fetchone()

    saldo = round(
        float(ganhos["total"] or 0) -
        float(saques["total"] or 0),
        2
    )

    if request.method == "POST":

        try:
            valor = round(float(
                (request.form.get("valor") or "0")
                .replace(",", ".")
            ), 2)
        except Exception:
            valor = 0

        pix_chave = (
            request.form.get("pix_chave") or ""
        ).strip()

        pix_tipo = (
            request.form.get("pix_tipo") or ""
        ).strip().upper()

        if valor <= 0:
            conn.close()
            return """
            <script>
            alert("Informe um valor válido.");
            history.back();
            </script>
            """

        if valor > saldo:
            conn.close()
            return """
            <script>
            alert("O valor solicitado é maior que seu saldo disponível.");
            history.back();
            </script>
            """

        if not pix_chave:
            conn.close()
            return """
            <script>
            alert("Informe sua chave PIX.");
            history.back();
            </script>
            """

        tipos_validos = ("CPF", "CNPJ", "EMAIL", "PHONE", "EVP")

        if pix_tipo not in tipos_validos:
            conn.close()
            return """
            <script>
            alert("Selecione o tipo da chave PIX.");
            history.back();
            </script>
            """

        conn.execute("""
            INSERT INTO saques
            (motorista_id, valor, pix_chave, pix_tipo, status)
            VALUES (?, ?, ?, ?, 'PENDENTE')
        """, (
            mid,
            valor,
            pix_chave,
            pix_tipo
        ))

        conn.commit()
        conn.close()

        return redirect(url_for("motorista_saque"))

    historico = conn.execute("""
        SELECT id, valor, pix_chave, pix_tipo,
               status, criado_em, pago_em, observacao
        FROM saques
        WHERE motorista_id=?
        ORDER BY id DESC
        LIMIT 30
    """, (mid,)).fetchall()

    conn.close()

    import html

    linhas = ""

    for saque in historico:
        status = html.escape(str(saque["status"] or ""))

        linhas += f"""
        <div style="
            background:#181818;
            border:1px solid #333;
            border-radius:16px;
            padding:16px;
            margin:12px 0;
        ">
            <b>💰 SAQUE #{saque["id"]}</b><br>
            Valor:
            <strong>R$ {float(saque["valor"] or 0):.2f}</strong><br>
            PIX:
            {html.escape(str(saque["pix_chave"] or ""))}<br>
            Tipo:
            {html.escape(str(saque["pix_tipo"] or ""))}<br>
            Status:
            <strong>{status}</strong><br>
            <small>{html.escape(str(saque["criado_em"] or ""))}</small>
        </div>
        """

    if not linhas:
        linhas = "<p>Nenhum saque solicitado.</p>"

    corpo = f"""
    <style>
        body {{
            background:#080808;
            color:#fff;
        }}

        .saque-box {{
            background:#111;
            border:1px solid #333;
            border-radius:20px;
            padding:20px;
            margin:15px 0;
        }}

        .saldo {{
            font-size:38px;
            font-weight:900;
            text-align:center;
            color:#43e56b;
            margin:15px 0;
        }}

        .campo {{
            width:100%;
            box-sizing:border-box;
            padding:15px;
            margin:7px 0 15px;
            border-radius:12px;
            border:1px solid #555;
            background:#222;
            color:#fff;
            font-size:18px;
        }}

        .botao {{
            width:100%;
            padding:17px;
            border:0;
            border-radius:14px;
            font-size:19px;
            font-weight:900;
            cursor:pointer;
        }}

        .verde {{
            background:#16833b;
            color:#fff;
        }}

        .azul {{
            background:#1769aa;
            color:#fff;
            text-decoration:none;
            display:block;
            text-align:center;
            box-sizing:border-box;
        }}
    </style>

    <div class="saque-box">
        <h2>💰 Solicitar Saque</h2>

        <p>Motorista: <b>{html.escape(str(motorista["nome"]))}</b></p>

        <p>Saldo disponível:</p>

        <div class="saldo">
            R$ {saldo:.2f}
        </div>

        <form method="POST">

            <label>Valor do saque</label>

            <input
                class="campo"
                type="number"
                name="valor"
                min="0.01"
                max="{saldo:.2f}"
                step="0.01"
                placeholder="Ex.: 50.00"
                required
            >

            <label>Tipo da chave PIX</label>

            <select class="campo" name="pix_tipo" required>
                <option value="">Selecione</option>
                <option value="CPF">CPF</option>
                <option value="CNPJ">CNPJ</option>
                <option value="EMAIL">E-mail</option>
                <option value="PHONE">Telefone</option>
                <option value="EVP">Chave aleatória</option>
            </select>

            <label>Chave PIX</label>

            <input
                class="campo"
                type="text"
                name="pix_chave"
                placeholder="Digite sua chave PIX"
                required
            >

            <button class="botao verde" type="submit">
                💰 SOLICITAR SAQUE
            </button>

        </form>
    </div>

    <div class="saque-box">
        <h2>📋 Meus Saques</h2>
        {linhas}
    </div>

    <a class="botao azul" href="/motorista">
        🏍️ VOLTAR AO PAINEL
    </a>
    """

    return _pagina_publica(
        "Solicitar Saque",
        corpo,
        manifesto="motorista"
    )


@app.route("/saques")
@login_obrigatorio
def admin_saques():

    garantir_tabela_saques()

    conn = conectar()

    rows = conn.execute("""
        SELECT
            s.*,
            m.nome AS motorista_nome,
            m.telefone AS motorista_telefone
        FROM saques s
        LEFT JOIN motoqueiros m
            ON m.id=s.motorista_id
        ORDER BY
            CASE WHEN s.status='PENDENTE' THEN 0 ELSE 1 END,
            s.id DESC
        LIMIT 100
    """).fetchall()

    conn.close()

    import html

    cards = ""

    for s in rows:

        status = str(s["status"] or "")

        if status == "PAGO":
            cor = "#16833b"
        elif status == "CANCELADO":
            cor = "#b00000"
        else:
            cor = "#d99a00"

        botoes = ""

        if status == "PENDENTE":
            botoes = f"""
            <form method="POST"
                  action="/saques/status/{s["id"]}/PAGO"
                  style="margin-top:10px;">
                <button
                    style="
                        width:100%;
                        padding:15px;
                        border:0;
                        border-radius:12px;
                        background:#16833b;
                        color:#fff;
                        font-size:18px;
                        font-weight:900;
                    "
                    onclick="return confirm('Confirmar saque como PAGO?')">
                    ✅ MARCAR COMO PAGO
                </button>
            </form>

            <form method="POST"
                  action="/saques/status/{s["id"]}/CANCELADO"
                  style="margin-top:10px;">
                <button
                    style="
                        width:100%;
                        padding:15px;
                        border:0;
                        border-radius:12px;
                        background:#b00000;
                        color:#fff;
                        font-size:18px;
                        font-weight:900;
                    "
                    onclick="return confirm('Cancelar este saque?')">
                    ❌ CANCELAR SAQUE
                </button>
            </form>
            """

        cards += f"""
        <div style="
            background:#fff;
            color:#111;
            border-radius:16px;
            padding:18px;
            margin:15px 0;
            border-left:8px solid {cor};
        ">
            <h3>💰 SAQUE #{s["id"]}</h3>

            <b>🏍️ Motorista:</b>
            {html.escape(str(s["motorista_nome"] or ""))}<br>

            <b>📞 Telefone:</b>
            {html.escape(str(s["motorista_telefone"] or ""))}<br>

            <b>💵 Valor:</b>
            R$ {float(s["valor"] or 0):.2f}<br>

            <b>🔑 PIX:</b>
            {html.escape(str(s["pix_chave"] or ""))}<br>

            <b>Tipo:</b>
            {html.escape(str(s["pix_tipo"] or ""))}<br>

            <b>📌 Status:</b>
            <strong>{html.escape(status)}</strong><br>

            <small>{html.escape(str(s["criado_em"] or ""))}</small>

            {botoes}
        </div>
        """

    if not cards:
        cards = "<p>Nenhuma solicitação de saque.</p>"

    html_page = f"""
    <!doctype html>
    <html lang="pt-BR">
    <head>
        <meta name="viewport"
              content="width=device-width,initial-scale=1">
        <title>Saques - VAI_DE_MOTO</title>
        <meta http-equiv="refresh" content="10">
    </head>

    <body style="
        margin:0;
        padding:20px;
        background:#111;
        color:#fff;
        font-family:Arial,sans-serif;
    ">

        <h1>💰 Solicitações de Saque</h1>

        {cards}

        <a href="/"
           style="
             display:block;
             background:#1769aa;
             color:#fff;
             padding:16px;
             border-radius:12px;
             text-align:center;
             text-decoration:none;
             font-weight:900;
           ">
            🏠 VOLTAR
        </a>

    </body>
    </html>
    """

    return html_page


@app.route("/saques/status/<int:id>/<status>", methods=["POST"])
@login_obrigatorio
def admin_saque_status(id, status):

    status = status.upper()

    if status not in ("PAGO", "CANCELADO"):
        return "Status inválido.", 400

    garantir_tabela_saques()

    conn = conectar()

    if status == "PAGO":
        conn.execute("""
            UPDATE saques
            SET status='PAGO',
                pago_em=CURRENT_TIMESTAMP
            WHERE id=? AND status='PENDENTE'
        """, (id,))
    else:
        conn.execute("""
            UPDATE saques
            SET status='CANCELADO'
            WHERE id=? AND status='PENDENTE'
        """, (id,))

    conn.commit()
    conn.close()

    return redirect(url_for("admin_saques"))


# =========================================================
# MONITORAMENTO ADMINISTRATIVO
# =========================================================

@app.route("/admin/monitoramento")
@login_obrigatorio
def admin_monitoramento():

    conn = conectar()

    motoristas = conn.execute("""
        SELECT
            id,
            nome,
            telefone,
            status,
            conexao,
            latitude,
            longitude
        FROM motoqueiros
        WHERE status='aprovado'
        ORDER BY
            CASE WHEN conexao='online' THEN 0 ELSE 1 END,
            nome
    """).fetchall()

    corridas = conn.execute("""
        SELECT
            c.id,
            c.origem,
            c.destino,
            c.valor,
            c.status,
            c.etapa,
            c.criado_em,
            c.motorista_id,
            m.nome AS motorista_nome,
            m.telefone AS motorista_telefone,
            p.nome AS passageiro_nome,
            p.telefone AS passageiro_telefone
        FROM corridas_vai c
        LEFT JOIN motoqueiros m
            ON m.id=c.motorista_id
        LEFT JOIN passageiros p
            ON p.id=c.passageiro_id
        WHERE c.status IN ('ACEITA','EM_ANDAMENTO')
        ORDER BY c.id DESC
        LIMIT 50
    """).fetchall()

    conn.close()

    import html

    lista_motoristas = ""

    for m in motoristas:

        lat = m["latitude"]
        lon = m["longitude"]

        if lat is not None and lon is not None:

            maps = (
                f"https://www.google.com/maps?q="
                f"{lat},{lon}"
            )

            waze = (
                f"https://waze.com/ul?"
                f"ll={lat}%2C{lon}&navigate=yes"
            )

            local = f"""
            <b>📍 GPS:</b>
            {float(lat):.6f}, {float(lon):.6f}

            <a href="{maps}"
               target="_blank"
               style="
                 display:block;
                 margin-top:10px;
                 padding:12px;
                 background:#1769aa;
                 color:#fff;
                 border-radius:10px;
                 text-decoration:none;
                 text-align:center;
                 font-weight:900;
               ">
                🗺️ ABRIR GOOGLE MAPS
            </a>

            <a href="{waze}"
               target="_blank"
               style="
                 display:block;
                 margin-top:8px;
                 padding:12px;
                 background:#222;
                 color:#fff;
                 border-radius:10px;
                 text-decoration:none;
                 text-align:center;
                 font-weight:900;
               ">
                🚗 ABRIR WAZE
            </a>
            """

        else:
            local = "<b>📍 GPS:</b> aguardando localização..."

        cor = "#16833b" if m["conexao"] == "online" else "#777"

        lista_motoristas += f"""
        <div style="
            background:#fff;
            color:#111;
            border-radius:16px;
            padding:18px;
            margin:12px 0;
            border-left:8px solid {cor};
        ">

            <h3>
                🏍️ {html.escape(str(m["nome"] or ""))}
            </h3>

            <b>📞 Telefone:</b>
            {html.escape(str(m["telefone"] or ""))}<br>

            <b>📡 Conexão:</b>
            {html.escape(str(m["conexao"] or "offline"))}<br>

            {local}

        </div>
        """

    if not lista_motoristas:
        lista_motoristas = "<p>Nenhum motorista cadastrado.</p>"

    lista_corridas = ""

    for c in corridas:

        lista_corridas += f"""
        <div style="
            background:#fff;
            color:#111;
            border-radius:16px;
            padding:18px;
            margin:12px 0;
            border-left:8px solid #1769aa;
        ">

            <h3>🚕 CORRIDA #{c["id"]}</h3>

            <b>📌 Status:</b>
            {html.escape(str(c["status"] or ""))}<br>

            <b>🚦 Etapa:</b>
            {html.escape(str(c["etapa"] or ""))}<br>

            <b>🏍️ Motorista:</b>
            {html.escape(str(c["motorista_nome"] or "Não informado"))}<br>

            <b>👤 Passageiro:</b>
            {html.escape(str(c["passageiro_nome"] or ""))}<br>

            <b>📍 Origem:</b><br>
            {html.escape(str(c["origem"] or ""))}<br><br>

            <b>🏁 Destino:</b><br>
            {html.escape(str(c["destino"] or ""))}<br><br>

            <b>💰 Valor:</b>
            R$ {float(c["valor"] or 0):.2f}

        </div>
        """

    if not lista_corridas:
        lista_corridas = "<p>Nenhuma corrida aceita ou em andamento.</p>"

    pagina_monitoramento = f"""
    <!doctype html>
    <html lang="pt-BR">

    <head>
        <meta name="viewport"
              content="width=device-width,initial-scale=1">

        <title>Monitoramento - VAI_DE_MOTO</title>

        <meta http-equiv="refresh" content="5">
    </head>

    <body style="
        margin:0;
        padding:18px;
        background:#111;
        color:#fff;
        font-family:Arial,sans-serif;
    ">

        <h1>📡 MONITORAMENTO</h1>

        <div style="
            background:#1769aa;
            padding:16px;
            border-radius:15px;
            margin-bottom:20px;
        ">
            Atualização automática a cada 5 segundos.
        </div>

        <h2>🏍️ MOTORISTAS</h2>

        {lista_motoristas}

        <h2>🚕 CORRIDAS ACEITAS</h2>

        {lista_corridas}

        <a href="/"
           style="
             display:block;
             background:#16833b;
             color:#fff;
             padding:16px;
             border-radius:12px;
             text-align:center;
             text-decoration:none;
             font-weight:900;
             margin-top:20px;
           ">
            🏠 VOLTAR AO ADMIN
        </a>

    </body>
    </html>
    """

    return pagina_monitoramento


# PWA VAI_DE_MOTO
# ==============================


@app.route("/asaas/webhook", methods=["POST"])
def asaas_webhook():
    # O Asaas envia o token configurado no Webhook
    # pelo header "asaas-access-token".
    if not ASAAS_WEBHOOK_TOKEN:
        return {"ok": False, "erro": "Webhook Asaas não configurado."}, 503

    token_recebido = request.headers.get(
        "asaas-access-token",
        ""
    ).strip()

    if token_recebido != ASAAS_WEBHOOK_TOKEN:
        return {"ok": False, "erro": "Não autorizado."}, 401

    data = request.get_json(silent=True) or {}

    evento_id = str(data.get("id") or "").strip()
    evento = str(data.get("event") or "").strip().upper()
    checkout = data.get("checkout") or {}
    checkout_id = str(checkout.get("id") or "").strip()

    if not evento_id or not evento:
        return {"ok": False, "erro": "Evento inválido."}, 400

    # Idempotência: o mesmo evento pode ser enviado mais de uma vez.
    conn = conectar()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS asaas_webhook_eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evento_id TEXT UNIQUE NOT NULL,
            evento TEXT DEFAULT '',
            checkout_id TEXT DEFAULT '',
            criado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    try:
        conn.execute("""
            INSERT INTO asaas_webhook_eventos
            (evento_id, evento, checkout_id)
            VALUES (?, ?, ?)
        """, (evento_id, evento, checkout_id))
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return {"ok": True, "duplicado": True}

    # Localiza a corrida pelo Checkout ID.
    corrida = None

    if checkout_id:
        corrida = conn.execute("""
            SELECT id
            FROM corridas_vai
            WHERE asaas_checkout_id=?
            LIMIT 1
        """, (checkout_id,)).fetchone()

    # Fallback usando externalReference: corrida-123
    if not corrida:
        referencia = str(
            checkout.get("externalReference") or ""
        ).strip()

        if referencia.startswith("corrida-"):
            try:
                corrida_id = int(
                    referencia.replace("corrida-", "", 1)
                )
                corrida = conn.execute("""
                    SELECT id
                    FROM corridas_vai
                    WHERE id=?
                    LIMIT 1
                """, (corrida_id,)).fetchone()
            except Exception:
                corrida = None

    if corrida:
        if evento == "CHECKOUT_PAID":
            conn.execute("""
                UPDATE corridas_vai
                SET pagamento_status='PAGO'
                WHERE id=?
            """, (corrida["id"],))

        elif evento == "CHECKOUT_CANCELED":
            conn.execute("""
                UPDATE corridas_vai
                SET pagamento_status='CANCELADO'
                WHERE id=?
            """, (corrida["id"],))

        elif evento == "CHECKOUT_EXPIRED":
            conn.execute("""
                UPDATE corridas_vai
                SET pagamento_status='EXPIRADO'
                WHERE id=?
            """, (corrida["id"],))

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "evento": evento,
        "checkout_id": checkout_id
    }, 200


@app.route("/service-worker.js")
def service_worker():
    return send_from_directory(
        "static",
        "service-worker.js",
        mimetype="application/javascript"
    )

@app.route("/manifest.json")
def manifest():
    return send_from_directory(
        "static",
        "manifest.json",
        mimetype="application/manifest+json"
    )

@app.route("/manifest-motorista.json")
def manifest_motorista():
    return send_from_directory(
        os.path.join(app.root_path, "static", "pwa"),
        "manifest-motorista.json",
        mimetype="application/manifest+json",
        max_age=0
    )

@app.route("/manifest-passageiro.json")
def manifest_passageiro():
    return send_from_directory(
        os.path.join(app.root_path, "static", "pwa"),
        "manifest-passageiro.json",
        mimetype="application/manifest+json",
        max_age=0
    )

@app.route("/icone/<path:nome>")
def icone(nome):
    return send_from_directory("static", nome)


# ===== ALERTA SONORO VAI_DE_MOTO =====
@app.after_request
def alerta_sonoro_motorista(response):
    try:
        if request.path == "/motorista":
            tipo = response.headers.get("Content-Type", "")
            if "text/html" in tipo:
                texto = response.get_data(as_text=True)

                alerta = r"""
<script>
(function(){
  if(window.__vaiDeMotoSom) return;
  window.__vaiDeMotoSom = true;

  let ctx = null;
  let tocando = false;
  let intervalo = null;
  let ultimaQuantidade = Number(
    localStorage.getItem("vai_moto_qtd_corridas") || "0"
  );

  function liberarAudio(){
    try{
      if(!ctx){
        ctx = new (window.AudioContext || window.webkitAudioContext)();
      }

      if(ctx.state === "suspended"){
        ctx.resume();
      }

      // Pequeno som silencioso para liberar o contexto no Android.
      const o = ctx.createOscillator();
      const g = ctx.createGain();

      g.gain.value = 0.0001;
      o.connect(g);
      g.connect(ctx.destination);

      o.start();
      o.stop(ctx.currentTime + 0.03);
    }catch(e){}
  }

  function beep(freq, inicio, duracao, tipo="square", volume=1.0){
    try{
      if(!ctx){
        ctx = new (window.AudioContext || window.webkitAudioContext)();
      }

      if(ctx.state === "suspended"){
        ctx.resume();
      }

      const agora = ctx.currentTime;
      const o = ctx.createOscillator();
      const g = ctx.createGain();

      o.type = tipo;

      o.frequency.setValueAtTime(
        freq,
        agora + inicio
      );

      g.gain.setValueAtTime(
        0.0001,
        agora + inicio
      );

      g.gain.exponentialRampToValueAtTime(
        volume,
        agora + inicio + 0.02
      );

      g.gain.exponentialRampToValueAtTime(
        0.0001,
        agora + inicio + duracao
      );

      o.connect(g);
      g.connect(ctx.destination);

      o.start(agora + inicio);
      o.stop(
        agora + inicio + duracao + 0.1
      );
    }catch(e){}
  }

  function falarNovaCorrida(){
    try{
      if(!("speechSynthesis" in window)) return;

      window.speechSynthesis.cancel();

      const fala = new SpeechSynthesisUtterance(
        "VAI-DE-MOTO!"
      );

      fala.lang = "pt-BR";
      fala.rate = 0.75;
      fala.pitch = 1.0;
      fala.volume = 1.0;

      window.speechSynthesis.speak(fala);
    }catch(e){}
  }

  function tocarChamada(){
    if(tocando) return;

    tocando = true;
    liberarAudio();

    function chamada(){
      if(!tocando) return;

      // TOQUE FORTE E CHAMATIVO
      beep(880,  0.00, 0.22, "square",   1.0);
      beep(1175, 0.25, 0.22, "square",   1.0);
      beep(1480, 0.50, 0.22, "triangle", 1.0);

      beep(880,  0.82, 0.22, "square",   1.0);
      beep(1175, 1.07, 0.22, "square",   1.0);
      beep(1480, 1.32, 0.30, "triangle", 1.0);

      // FALA
      setTimeout(function(){
        if(tocando){
          falarNovaCorrida();
        }
      }, 1750);
    }

    chamada();

    // Repete a chamada enquanto houver corrida
    intervalo = setInterval(chamada, 4200);
  }

  function pararChamada(){
    tocando = false;

    if(intervalo){
      clearInterval(intervalo);
      intervalo = null;
    }

    try{
      if(window.speechSynthesis){
        window.speechSynthesis.cancel();
      }
    }catch(e){}
  }

  function iniciarContagemCorrida(id){
    const antigo = document.getElementById("contador-nova-corrida");
    if(antigo) antigo.remove();

    let n = 9;

    const painel = document.createElement("div");
    painel.id = "contador-nova-corrida";

    painel.style.cssText =
      "position:fixed;top:10px;left:50%;transform:translateX(-50%);" +
      "z-index:999999;width:calc(100% - 20px);max-width:520px;" +
      "background:#b00000;color:white;border:4px solid white;" +
      "border-radius:18px;padding:16px;text-align:center;" +
      "font-weight:bold;box-shadow:0 8px 30px rgba(0,0,0,.6);";

    painel.innerHTML =
      '<div style="font-size:26px">🚨 NOVA CORRIDA!</div>' +
      '<div style="font-size:20px;margin-top:5px">ACEITE A CORRIDA</div>' +
      '<div id="numero-contador-corrida" style="font-size:64px;line-height:1;margin:8px">9</div>' +
      '<div style="font-size:18px">SEGUNDOS</div>' +
      '<button id="btn-aceitar-popup" ' +
      'style="margin-top:14px;width:100%;padding:16px;border:0;border-radius:12px;' +
      'background:#16a34a;color:white;font-size:20px;font-weight:900;" ' +
      '>' +
      '🟢 ACEITAR CORRIDA</button>';

    document.body.appendChild(painel);

    const botaoPopup = document.getElementById("btn-aceitar-popup");
    if(botaoPopup){ botaoPopup.addEventListener("click", function(e){ e.preventDefault(); e.stopPropagation(); aceitarCorridaPopup(id); }); }

    const relogio = setInterval(function(){
      n--;

      const numero = document.getElementById("numero-contador-corrida");

      if(numero){
        numero.textContent = n;
      }

      if(n <= 0){
        clearInterval(relogio);

        const texto = painel.querySelector("div:nth-child(2)");

        if(texto){
          texto.textContent = "⏰ TEMPO ENCERRADO";
        }

        const botao = document.getElementById("btn-aceitar-popup");
        if(botao){
          botao.remove();
        }

        setTimeout(function(){
          if(painel.parentNode){
            painel.remove();
          }
        }, 2500);
      }
    }, 1000);
  }

  async function aceitarCorridaPopup(id){
    const botao = document.getElementById("btn-aceitar-popup");

    if(botao){
      botao.disabled = true;
      botao.textContent = "⏳ ACEITANDO...";
      botao.style.opacity = "0.7";
    }

    try{
      const r = await fetch(
        "/api/corrida/"+id+"/aceitar",
        {
          method:"POST",
          credentials:"same-origin"
        }
      );

      const d = await r.json();

      if(!d.ok){
        toast(d.erro || "Não foi possível aceitar a corrida.");

        if(botao){
          botao.disabled = false;
          botao.textContent = "🟢 ACEITAR CORRIDA";
          botao.style.opacity = "1";
        }

        return;
      }

      const painel = document.getElementById("contador-nova-corrida");
      if(painel) painel.remove();

      clearInterval(window._contadorNovaCorrida);

      toast("🟢 CORRIDA ACEITA!");

      await minhas();
      await ganhos();
      await carregar();
      setTimeout(() => carregar(), 800);

    }catch(e){
      toast("Erro de conexão ao aceitar a corrida.");

      if(botao){
        botao.disabled = false;
        botao.textContent = "🟢 ACEITAR CORRIDA";
        botao.style.opacity = "1";
      }
    }
  }

  async function verificarCorridas(){
    try{
      const r = await fetch(
        "/api/corridas-disponiveis",
        {
          cache:"no-store",
          credentials:"same-origin"
        }
      );

      if(!r.ok) return;

      const d = await r.json();

      if(!d.ok) return;

      const quantidade =
        Array.isArray(d.corridas)
          ? d.corridas.length
          : 0;

      if(quantidade > ultimaQuantidade){
        tocarChamada();

        const novaCorrida =
          Array.isArray(d.corridas) && d.corridas.length
            ? d.corridas[0]
            : null;

        if(novaCorrida && novaCorrida.id){
          iniciarContagemCorrida(novaCorrida.id);
        }
      }

      if(quantidade === 0){
        pararChamada();

        const painel =
          document.getElementById("contador-nova-corrida");

        if(painel){
          painel.remove();
        }
      }

      ultimaQuantidade = quantidade;

      localStorage.setItem(
        "vai_moto_qtd_corridas",
        String(quantidade)
      );

    }catch(e){}
  }

  function tocarSomDinheiro(){
    try{
      liberarAudio();

      // Som de dinheiro / recebimento
      beep(880,  0.00, 0.12, "sine", 0.9);
      beep(1175, 0.14, 0.12, "sine", 0.9);
      beep(1568, 0.28, 0.18, "sine", 1.0);
      beep(2093, 0.48, 0.25, "sine", 1.0);

      if("speechSynthesis" in window){
        setTimeout(function(){
          try{
            const fala = new SpeechSynthesisUtterance(
              "Dinheiro recebido!"
            );
            fala.lang = "pt-BR";
            fala.rate = 0.85;
            fala.volume = 1.0;
            window.speechSynthesis.speak(fala);
          }catch(e){}
        }, 550);
      }
    }catch(e){}
  }

  async function verificarSomDinheiro(){
    try{
      const r = await fetch(
        "/api/motorista/som-dinheiro",
        {
          cache:"no-store",
          credentials:"same-origin"
        }
      );

      if(!r.ok) return;

      const d = await r.json();

      if(d.ok && d.tocar){
        tocarSomDinheiro();
      }
    }catch(e){}
  }

  verificarCorridas();
  setInterval(verificarCorridas, 2000);

  verificarSomDinheiro();
  setInterval(verificarSomDinheiro, 1000);

  window.testarSomVaiDeMoto = function(){
    liberarAudio();
    tocarChamada();

    setTimeout(function(){
      pararChamada();
    }, 8000);
  };

})();
</script>
"""

                botao = r"""
<div style="margin:15px 0;text-align:center;">
  <button type="button"
          onclick="testarSomVaiDeMoto()"
          style="
            width:100%;
            border:0;
            border-radius:10px;
            padding:14px;
            background:#222;
            color:white;
            font-weight:bold;
            font-size:15px;
            cursor:pointer;">
    🔊 TESTAR SOM DE CHAMADA
  </button>
</div>
"""

                if "testarSomVaiDeMoto" not in texto:
                    if "</body>" in texto:
                        texto = texto.replace(
                            "</body>",
                            botao + alerta + "</body>",
                            1
                        )
                    else:
                        texto += botao + alerta

                    response.set_data(texto)

    except Exception:
        pass

    return response

if __name__ == "__main__":
    iniciar_banco()

    print("")
    print("==============================")
    print("     VAI_DE_MOTO")
    print("  PAINEL ADMINISTRATIVO")
    print("==============================")
    print("")
    print("Abra no navegador:")
    print("http://127.0.0.1:5000")
    print("")
    print("Usuário: admin")
    print("Senha: 123456")
    print("")

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )

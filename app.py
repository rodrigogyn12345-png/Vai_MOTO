from flask import send_from_directory
from flask import Flask, request, redirect, url_for, session, render_template_string, flash
import sqlite3
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "VAI_DE_MOTO_CHAVE_TROCAR_DEPOIS"

DB = "vai_de_moto.db"
LIMITE_MOTOQUEIROS = 20


def conectar():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


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
        ("admin",)
    ).fetchone()

    if not admin:
        conn.execute(
            "INSERT INTO admins (usuario, senha) VALUES (?, ?)",
            ("admin", generate_password_hash("123456"))
        )

    conn.commit()
    conn.close()


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
    }, 1200);

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
            return redirect(url_for("dashboard"))

        flash("Usuário ou senha inválidos.", "erro")

    return pagina(LOGIN_HTML)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


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
            <div class="numero">0</div>
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
            <td colspan="10" style="text-align:center">
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
            LEFT JOIN passageiros m
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

        partida = c["partida"] or "-"
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

                <p>💰 <b>Valor:</b>
                    R$ {valor:.2f}
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
                            {c["partida"] or "-"}
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
            {corrida["partida"] or "-"}
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



# ==============================
# PWA VAI_DE_MOTO
# ==============================

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

@app.route("/icone/<path:nome>")
def icone(nome):
    return send_from_directory("static", nome)


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

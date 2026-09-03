from flask import Flask, request, redirect, url_for, session, render_template_string, jsonify
import sqlite3
import time
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "VAIDE_MOTO_CHAVE_LOCAL_2026_TROQUE_DEPOIS"

DB = "vaimoto.db"
TARIFA_KM = 2.00
TAXA_APP = 0.09
PERCENTUAL_MOTORISTA = 0.91
ONLINE_TIMEOUT = 35

ADMIN_WHATSAPP = "62993903299"
ADMIN_SENHA = "1234"


# ============================================================
# BANCO
# ============================================================

def conectar():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def adicionar_coluna_se_nao_existir(con, tabela, coluna, definicao):
    cols = [r["name"] for r in con.execute(f"PRAGMA table_info({tabela})").fetchall()]
    if coluna not in cols:
        con.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}")


def inicializar_banco():
    con = conectar()

    con.execute("""
        CREATE TABLE IF NOT EXISTS usuarios_vai (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            whatsapp TEXT NOT NULL UNIQUE,
            senha TEXT NOT NULL,
            tipo TEXT NOT NULL CHECK(tipo IN ('passageiro','motorista')),
            aprovado INTEGER NOT NULL DEFAULT 1,
            online INTEGER NOT NULL DEFAULT 0,
            last_seen REAL NOT NULL DEFAULT 0,
            criado_em REAL NOT NULL
        )
    """)

    # Migração segura para bancos antigos
    adicionar_coluna_se_nao_existir(con, "usuarios_vai", "aprovado", "INTEGER NOT NULL DEFAULT 1")
    adicionar_coluna_se_nao_existir(con, "usuarios_vai", "online", "INTEGER NOT NULL DEFAULT 0")
    adicionar_coluna_se_nao_existir(con, "usuarios_vai", "last_seen", "REAL NOT NULL DEFAULT 0")

    con.execute("""
        CREATE TABLE IF NOT EXISTS corridas_vai (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            passageiro_id INTEGER NOT NULL,
            partida TEXT NOT NULL,
            destino TEXT NOT NULL,
            distancia REAL NOT NULL DEFAULT 0,
            valor REAL NOT NULL,
            taxa_app REAL NOT NULL DEFAULT 0,
            ganho_motorista REAL NOT NULL DEFAULT 0,
            pagamento TEXT NOT NULL DEFAULT 'Dinheiro',
            status TEXT NOT NULL DEFAULT 'PENDENTE',
            motorista_id INTEGER,
            criada_em REAL NOT NULL,
            aceita_em REAL,
            iniciada_em REAL,
            concluida_em REAL
        )
    """)

    adicionar_coluna_se_nao_existir(con, "corridas_vai", "distancia", "REAL NOT NULL DEFAULT 0")
    adicionar_coluna_se_nao_existir(con, "corridas_vai", "taxa_app", "REAL NOT NULL DEFAULT 0")
    adicionar_coluna_se_nao_existir(con, "corridas_vai", "ganho_motorista", "REAL NOT NULL DEFAULT 0")
    adicionar_coluna_se_nao_existir(con, "corridas_vai", "pagamento", "TEXT NOT NULL DEFAULT 'Dinheiro'")
    adicionar_coluna_se_nao_existir(con, "corridas_vai", "aceita_em", "REAL")
    adicionar_coluna_se_nao_existir(con, "corridas_vai", "iniciada_em", "REAL")
    adicionar_coluna_se_nao_existir(con, "corridas_vai", "concluida_em", "REAL")

    # Garante o administrador.
    admin = con.execute(
        "SELECT id FROM usuarios_vai WHERE whatsapp=?",
        (ADMIN_WHATSAPP,)
    ).fetchone()

    if not admin:
        con.execute("""
            INSERT INTO usuarios_vai
            (nome, whatsapp, senha, tipo, aprovado, online, last_seen, criado_em)
            VALUES (?, ?, ?, 'passageiro', 1, 0, 0, ?)
        """, (
            "Administrador VAI_DE_MOTO",
            ADMIN_WHATSAPP,
            generate_password_hash(ADMIN_SENHA),
            time.time()
        ))
    else:
        con.execute(
            "UPDATE usuarios_vai SET aprovado=1 WHERE whatsapp=?",
            (ADMIN_WHATSAPP,)
        )

    con.commit()
    con.close()


def marcar_motoristas_expirados():
    limite = time.time() - ONLINE_TIMEOUT
    con = conectar()
    con.execute("""
        UPDATE usuarios_vai
        SET online=0
        WHERE tipo='motorista' AND online=1 AND last_seen < ?
    """, (limite,))
    con.commit()
    con.close()


def contar_motoristas_online():
    marcar_motoristas_expirados()
    con = conectar()
    row = con.execute("""
        SELECT COUNT(*) AS total
        FROM usuarios_vai
        WHERE tipo='motorista' AND aprovado=1 AND online=1
    """).fetchone()
    con.close()
    return row["total"]


def usuario_logado():
    uid = session.get("usuario_id")
    if not uid:
        return None
    con = conectar()
    u = con.execute(
        "SELECT * FROM usuarios_vai WHERE id=?",
        (uid,)
    ).fetchone()
    con.close()
    return u


def motorista_aprovado_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        u = usuario_logado()
        if not u or u["tipo"] != "motorista":
            return redirect(url_for("login"))
        if not u["aprovado"]:
            session.clear()
            return render_template_string(
                AVISO,
                titulo="Cadastro aguardando aprovação",
                mensagem="Seu cadastro de motorista ainda precisa ser aprovado pelo administrador.",
                voltar=url_for("login")
            )
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapper


# ============================================================
# CSS
# ============================================================

CSS = """
<style>
*{box-sizing:border-box}
body{
    margin:0;
    background:#101827;
    font-family:Arial,sans-serif;
    color:#111;
}
.card{
    width:min(94%,700px);
    margin:28px auto;
    background:#fff;
    border-radius:26px;
    padding:28px;
    box-shadow:0 10px 30px rgba(0,0,0,.22);
}
.logo{
    text-align:center;
    font-size:31px;
    font-weight:900;
    color:#111;
}
.logo span{color:#159447}
.subtitle{
    text-align:center;
    color:#666;
    font-size:19px;
    margin-top:6px;
}
h1{font-size:31px;margin:18px 0}
h2{font-size:24px}
label{
    display:block;
    font-size:18px;
    font-weight:800;
    margin-top:15px;
}
input,select{
    width:100%;
    padding:17px;
    margin-top:7px;
    border:1px solid #ccc;
    border-radius:16px;
    font-size:18px;
    background:#fff;
}
button,.btn{
    display:block;
    width:100%;
    padding:17px;
    margin-top:14px;
    border:0;
    border-radius:16px;
    font-size:18px;
    font-weight:800;
    text-align:center;
    text-decoration:none;
    cursor:pointer;
}
.green{background:#159447;color:#fff}
.red{background:#dc3545;color:#fff}
.blue{background:#1769aa;color:#fff}
.black{background:#111;color:#fff}
.gray{background:#666;color:#fff}
.orange{background:#ef8b00;color:#fff}
.box{
    padding:17px;
    margin:14px 0;
    border-radius:17px;
    background:#f1f3f6;
}
.info{
    background:#eaf6ff;
    border-radius:18px;
    padding:17px;
    margin:15px 0;
    font-size:18px;
}
.success{
    background:#dff7e6;
    color:#08752e;
    padding:17px;
    border-radius:16px;
    font-weight:800;
    margin:14px 0;
}
.warning{
    background:#fff0c7;
    color:#7a5200;
    padding:17px;
    border-radius:16px;
    font-weight:800;
    margin:14px 0;
}
.error{
    background:#ffe1e1;
    color:#a80000;
    padding:17px;
    border-radius:16px;
    font-weight:800;
    margin:14px 0;
}
.status{
    padding:18px;
    border-radius:18px;
    text-align:center;
    font-size:21px;
    font-weight:900;
    margin:14px 0;
}
.status.online{background:#dff7e6;color:#08752e}
.status.offline{background:#eee;color:#555}
.grid{
    display:grid;
    grid-template-columns:1fr 1fr;
    gap:12px;
}
.stat{
    background:#f1f3f6;
    border-radius:18px;
    padding:16px;
    text-align:center;
}
.stat b{display:block;font-size:26px}
.small{font-size:14px;color:#666}
hr{border:0;border-top:1px solid #ddd;margin:22px 0}
@media(max-width:560px){
    .card{padding:20px;margin:16px auto}
    .grid{grid-template-columns:1fr}
    h1{font-size:28px}
}
</style>
"""


# ============================================================
# TEMPLATES
# ============================================================

HOME = CSS + """
<div class="card">
    <div class="logo">🏍️ VAI_<span>DE_MOTO</span></div>
    <div class="subtitle">Transporte de moto local</div>

    <h1>Bem-vindo</h1>
    <a class="btn green" href="{{ url_for('login') }}">🔐 ENTRAR</a>
    <a class="btn blue" href="{{ url_for('cadastro') }}">📝 CRIAR CADASTRO</a>
    <a class="btn black" href="{{ url_for('admin_login') }}">⚙️ ÁREA DO ADMINISTRADOR</a>

    <div class="info">
        💰 <b>R$ 2,00 por km</b><br><br>
        🏍️ Motorista recebe <b>91%</b><br>
        ⚙️ Taxa do aplicativo <b>9%</b><br>
        💵 Pagamento: <b>Dinheiro</b>
    </div>
</div>
"""

CADASTRO = CSS + """
<div class="card">
    <div class="logo">🏍️ VAI_<span>DE_MOTO</span></div>
    <div class="subtitle">Criar cadastro</div>

    {% if erro %}<div class="error">{{ erro }}</div>{% endif %}

    <form method="post">
        <label>👤 Nome completo</label>
        <input name="nome" placeholder="Digite seu nome" required>

        <label>📱 WhatsApp</label>
        <input name="whatsapp" placeholder="Digite seu WhatsApp" required>

        <label>🔑 Senha</label>
        <input name="senha" type="password" placeholder="Crie uma senha" required minlength="4">

        <label>Tipo de cadastro</label>
        <select name="tipo">
            <option value="passageiro">👤 Passageiro</option>
            <option value="motorista">🏍️ Motorista</option>
        </select>

        <button class="green" type="submit">✅ CADASTRAR</button>
    </form>

    <a class="btn gray" href="{{ url_for('login') }}">🔐 Já tenho cadastro - Entrar</a>

    <div class="info">
        💰 R$ 2,00 por km<br>
        🏍️ Motorista recebe 91%<br>
        ⚙️ Taxa do aplicativo: 9%
    </div>
</div>
"""

LOGIN = CSS + """
<div class="card">
    <div class="logo">🏍️ VAI_<span>DE_MOTO</span></div>
    <div class="subtitle">Entrar</div>

    {% if erro %}<div class="error">{{ erro }}</div>{% endif %}

    <form method="post">
        <label>📱 WhatsApp</label>
        <input name="whatsapp" placeholder="Digite seu WhatsApp" required>

        <label>🔑 Senha</label>
        <input name="senha" type="password" placeholder="Digite sua senha" required>

        <button class="green" type="submit">🔐 ENTRAR</button>
    </form>

    <a class="btn gray" href="{{ url_for('cadastro') }}">📝 Ainda não tenho cadastro</a>

    <div class="info">
        💰 R$ 2,00 por km<br>
        🏍️ Motorista recebe 91%<br>
        ⚙️ Taxa do aplicativo: 9%
    </div>
</div>
"""

PASSAGEIRO = CSS + """
<div class="card">
    <div class="logo">🏍️ VAI_<span>DE_MOTO</span></div>
    <div class="subtitle">Área do passageiro</div>

    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px">
        <h1>🚕 Solicitar corrida</h1>
        <a class="btn red" style="width:auto;padding:12px 18px" href="{{ url_for('logout') }}">Sair</a>
    </div>

    <div class="status online">
        🟢 Motoristas online: <span id="onlineCount">{{ online }}</span>
    </div>

    {% if mensagem %}<div class="success">{{ mensagem }}</div>{% endif %}
    {% if erro %}<div class="error">{{ erro }}</div>{% endif %}

    <form method="post" action="{{ url_for('solicitar_corrida') }}">
        <label>📍 Local de partida</label>
        <input name="partida" placeholder="Digite o local de partida" required>

        <label>🏁 Destino</label>
        <input name="destino" placeholder="Digite o destino" required>

        <label>📏 Distância aproximada em km</label>
        <input id="distancia" name="distancia" type="number" step="0.1" min="0.1"
               placeholder="Ex.: 5" required oninput="calcular()">

        <div class="info">
            💰 <b>R$ 2,00 por km</b><br>
            💵 Valor da corrida: <b id="valor">R$ 0,00</b><br>
            ⚙️ Taxa do aplicativo: <b>9%</b><br>
            🏍️ Motorista recebe: <b>91%</b><br>
            💵 Pagamento: <b>Dinheiro</b>
        </div>

        <button class="green" type="submit">🏍️ SOLICITAR CORRIDA</button>
    </form>

    <hr>
    <h2>📋 Minhas corridas</h2>

    {% if corridas %}
        {% for c in corridas %}
        <div class="box">
            <b>#{{ c['id'] }} — {{ c['partida'] }} → {{ c['destino'] }}</b><br>
            📏 {{ "%.1f"|format(c['distancia']) }} km<br>
            💰 R$ {{ "%.2f"|format(c['valor']) }}<br>
            💵 {{ c['pagamento'] }}<br>
            <b>📌 {{ c['status'] }}</b>
            {% if c['status'] == 'PENDENTE' %}
                <br><span class="small">Procurando motorista...</span>
            {% elif c['status'] == 'ACEITA' %}
                <br><span class="small">Motorista aceitou a corrida.</span>
            {% elif c['status'] == 'EM_ANDAMENTO' %}
                <br><span class="small">Corrida em andamento.</span>
            {% elif c['status'] == 'CONCLUIDA' %}
                <br><span class="small">Corrida concluída.</span>
            {% endif %}
        </div>
        {% endfor %}
    {% else %}
        <div class="box">Você ainda não possui corridas.</div>
    {% endif %}
</div>

<script>
function calcular(){
    const km=parseFloat(document.getElementById("distancia").value)||0;
    document.getElementById("valor").textContent =
        "R$ " + (km*2).toFixed(2).replace(".",",");
}
async function atualizarOnline(){
    try{
        const r=await fetch("/api/motoristas-online");
        const d=await r.json();
        document.getElementById("onlineCount").textContent=d.online;
    }catch(e){}
}
setInterval(atualizarOnline,5000);
</script>
"""

MOTORISTA = CSS + """
<div class="card">
    <div class="logo">🏍️ VAI_<span>DE_MOTO</span></div>
    <div class="subtitle">Área do motorista</div>

    <h1>Olá, {{ usuario['nome'] }}</h1>

    <div id="statusBox" class="status {{ 'online' if usuario['online'] else 'offline' }}">
        {% if usuario['online'] %}🟢 MOTORISTA ONLINE{% else %}⚪ MOTORISTA OFFLINE{% endif %}
    </div>

    <button id="statusButton"
            class="{{ 'red' if usuario['online'] else 'green' }}"
            onclick="alternarStatus()">
        {% if usuario['online'] %}Ficar offline{% else %}Ficar online{% endif %}
    </button>

    <div class="grid">
        <div class="stat">
            <b id="onlineCount">{{ online }}</b>
            Motoristas online
        </div>
        <div class="stat">
            <b>91%</b>
            Seu recebimento
        </div>
    </div>

    <div class="info">
        💰 Tarifa: <b>R$ 2,00/km</b><br>
        ⚙️ Aplicativo: <b>9%</b><br>
        🏍️ Motorista: <b>91%</b>
    </div>

    <h2>🚕 Corridas disponíveis</h2>
    <div id="corridasArea">
        {% if corridas %}
            {% for c in corridas %}
            <div class="box">
                <b>Corrida #{{ c['id'] }}</b><br>
                👤 {{ c['passageiro_nome'] }}<br>
                📍 {{ c['partida'] }}<br>
                🏁 {{ c['destino'] }}<br>
                📏 {{ "%.1f"|format(c['distancia']) }} km<br>
                💰 R$ {{ "%.2f"|format(c['valor']) }}<br>
                💵 {{ c['pagamento'] }}<br>

                {% if c['status']=='PENDENTE' %}
                <form method="post" action="{{ url_for('aceitar_corrida', corrida_id=c['id']) }}">
                    <button class="green" type="submit">🏍️ ACEITAR CORRIDA</button>
                </form>
                {% elif c['status']=='ACEITA' %}
                    <div class="warning">Você aceitou esta corrida.</div>
                    <form method="post" action="{{ url_for('iniciar_corrida', corrida_id=c['id']) }}">
                        <button class="blue" type="submit">▶️ INICIAR CORRIDA</button>
                    </form>
                {% elif c['status']=='EM_ANDAMENTO' %}
                    <div class="success">Corrida em andamento.</div>
                    <form method="post" action="{{ url_for('concluir_corrida', corrida_id=c['id']) }}">
                        <button class="green" type="submit">✅ CONCLUIR CORRIDA</button>
                    </form>
                {% endif %}
            </div>
            {% endfor %}
        {% else %}
            <div class="box">Nenhuma corrida pendente no momento.</div>
        {% endif %}
    </div>

    <h2>💰 Meus ganhos</h2>
    <div class="grid">
        <div class="stat">
            <b>R$ {{ "%.2f"|format(total_ganho) }}</b>
            Total
        </div>
        <div class="stat">
            <b>{{ total_corridas }}</b>
            Corridas concluídas
        </div>
    </div>

    <a class="btn gray" href="{{ url_for('logout') }}">Sair</a>
</div>

<script>
let online={{ 1 if usuario['online'] else 0 }};

function atualizarTela(){
    const box=document.getElementById("statusBox");
    const btn=document.getElementById("statusButton");
    if(online){
        box.className="status online";
        box.textContent="🟢 MOTORISTA ONLINE";
        btn.className="red";
        btn.textContent="Ficar offline";
    }else{
        box.className="status offline";
        box.textContent="⚪ MOTORISTA OFFLINE";
        btn.className="green";
        btn.textContent="Ficar online";
    }
}

async function alternarStatus(){
    const novo=online?0:1;
    try{
        const r=await fetch("/api/motorista/status",{
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({online:novo})
        });
        const d=await r.json();
        if(d.ok){
            online=d.online;
            atualizarTela();
        }else{
            alert(d.erro||"Não foi possível alterar o status.");
        }
    }catch(e){
        alert("Erro de conexão.");
    }
}

async function heartbeat(){
    if(!online)return;
    try{
        await fetch("/api/motorista/heartbeat",{method:"POST"});
    }catch(e){}
}

async function atualizarCorridas(){
    if(!online)return;
    try{
        const r=await fetch("/api/motorista/corridas");
        const d=await r.json();
        if(d.ok && d.novas){
            location.reload();
        }
    }catch(e){}
}

setInterval(heartbeat,10000);
setInterval(atualizarCorridas,5000);
heartbeat();
</script>
"""

AVISO = CSS + """
<div class="card">
    <div class="logo">🏍️ VAI_<span>DE_MOTO</span></div>
    <h1>{{ titulo }}</h1>
    <div class="warning">{{ mensagem }}</div>
    <a class="btn green" href="{{ voltar }}">Voltar</a>
</div>
"""

ADMIN_LOGIN = CSS + """
<div class="card">
    <div class="logo">🏍️ VAI_<span>DE_MOTO</span></div>
    <div class="subtitle">Área administrativa</div>

    {% if erro %}<div class="error">{{ erro }}</div>{% endif %}

    <form method="post">
        <label>📱 WhatsApp do administrador</label>
        <input name="whatsapp" placeholder="WhatsApp" required>

        <label>🔑 Senha</label>
        <input name="senha" type="password" placeholder="Senha" required>

        <button class="black" type="submit">⚙️ ENTRAR NO ADMIN</button>
    </form>

    <a class="btn gray" href="{{ url_for('index') }}">Voltar</a>
</div>
"""

ADMIN = CSS + """
<div class="card">
    <div class="logo">🏍️ VAI_<span>DE_MOTO</span></div>
    <div class="subtitle">Painel administrativo</div>

    <div style="display:flex;justify-content:space-between;align-items:center;gap:10px">
        <h1>⚙️ Administração</h1>
        <a class="btn red" style="width:auto;padding:12px 18px" href="{{ url_for('admin_logout') }}">Sair</a>
    </div>

    <div class="grid">
        <div class="stat"><b>{{ pendentes }}</b>Motoristas pendentes</div>
        <div class="stat"><b>{{ online }}</b>Motoristas online</div>
        <div class="stat"><b>{{ motoristas }}</b>Motoristas aprovados</div>
        <div class="stat"><b>{{ corridas }}</b>Corridas</div>
    </div>

    {% if mensagem %}<div class="success">{{ mensagem }}</div>{% endif %}

    <h2>🏍️ Motoristas aguardando aprovação</h2>

    {% if motoristas_pendentes %}
        {% for m in motoristas_pendentes %}
        <div class="box">
            <b>{{ m['nome'] }}</b><br>
            📱 {{ m['whatsapp'] }}<br>
            📅 Cadastro: {{ m['criado_em']|int }}

            <form method="post" action="{{ url_for('aprovar_motorista', motorista_id=m['id']) }}">
                <button class="green" type="submit">✅ APROVAR</button>
            </form>

            <form method="post" action="{{ url_for('excluir_motorista', motorista_id=m['id']) }}"
                  onsubmit="return confirm('Excluir este motorista?')">
                <button class="red" type="submit">🗑️ EXCLUIR</button>
            </form>
        </div>
        {% endfor %}
    {% else %}
        <div class="box">Não há motoristas aguardando aprovação.</div>
    {% endif %}

    <h2>🏍️ Motoristas aprovados</h2>

    {% if motoristas_aprovados %}
        {% for m in motoristas_aprovados %}
        <div class="box">
            <b>{{ m['nome'] }}</b><br>
            📱 {{ m['whatsapp'] }}<br>
            {% if m['online'] %}
                🟢 ONLINE
            {% else %}
                ⚪ OFFLINE
            {% endif %}

            <form method="post" action="{{ url_for('bloquear_motorista', motorista_id=m['id']) }}">
                <button class="orange" type="submit">🚫 BLOQUEAR</button>
            </form>

            <form method="post" action="{{ url_for('excluir_motorista', motorista_id=m['id']) }}"
                  onsubmit="return confirm('Excluir este motorista?')">
                <button class="red" type="submit">🗑️ EXCLUIR</button>
            </form>
        </div>
        {% endfor %}
    {% else %}
        <div class="box">Nenhum motorista aprovado.</div>
    {% endif %}

    <h2>🚕 Últimas corridas</h2>

    {% for c in ultimas_corridas %}
    <div class="box">
        <b>#{{ c['id'] }}</b> — {{ c['status'] }}<br>
        👤 Passageiro: {{ c['passageiro_nome'] }}<br>
        🏍️ Motorista: {{ c['motorista_nome'] or 'Não definido' }}<br>
        📍 {{ c['partida'] }} → {{ c['destino'] }}<br>
        📏 {{ "%.1f"|format(c['distancia']) }} km<br>
        💰 R$ {{ "%.2f"|format(c['valor']) }} |
        ⚙️ App R$ {{ "%.2f"|format(c['taxa_app']) }} |
        🏍️ Motorista R$ {{ "%.2f"|format(c['ganho_motorista']) }}
    </div>
    {% else %}
    <div class="box">Nenhuma corrida registrada.</div>
    {% endfor %}
</div>
"""


# ============================================================
# ROTAS
# ============================================================

@app.route("/")
def index():
    return render_template_string(HOME)


@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    erro = ""

    if request.method == "POST":
        nome = request.form.get("nome", "").strip()
        whatsapp = request.form.get("whatsapp", "").strip()
        senha = request.form.get("senha", "")
        tipo = request.form.get("tipo", "")

        if not nome or not whatsapp or not senha:
            erro = "Preencha todos os campos."
        elif tipo not in ("passageiro", "motorista"):
            erro = "Tipo de cadastro inválido."
        elif whatsapp == ADMIN_WHATSAPP:
            erro = "Esse WhatsApp é reservado para o administrador."
        else:
            con = conectar()
            try:
                aprovado = 1 if tipo == "passageiro" else 0
                cur = con.execute("""
                    INSERT INTO usuarios_vai
                    (nome, whatsapp, senha, tipo, aprovado, online, last_seen, criado_em)
                    VALUES (?, ?, ?, ?, ?, 0, 0, ?)
                """, (
                    nome,
                    whatsapp,
                    generate_password_hash(senha),
                    tipo,
                    aprovado,
                    time.time()
                ))
                con.commit()
                uid = cur.lastrowid
                con.close()

                session["usuario_id"] = uid

                if tipo == "motorista":
                    return render_template_string(
                        AVISO,
                        titulo="Cadastro realizado!",
                        mensagem="Aguarde o administrador aprovar seu cadastro de motorista.",
                        voltar=url_for("login")
                    )

                return redirect(url_for("passageiro"))

            except sqlite3.IntegrityError:
                con.close()
                erro = "Esse WhatsApp já está cadastrado."
            except Exception as e:
                con.close()
                erro = "Erro no cadastro: " + str(e)

    return render_template_string(CADASTRO, erro=erro)


@app.route("/login", methods=["GET", "POST"])
def login():
    erro = ""

    if request.method == "POST":
        whatsapp = request.form.get("whatsapp", "").strip()
        senha = request.form.get("senha", "")

        if whatsapp == ADMIN_WHATSAPP and senha == ADMIN_SENHA:
            session.clear()
            session["admin"] = True
            return redirect(url_for("admin"))

        con = conectar()
        usuario = con.execute(
            "SELECT * FROM usuarios_vai WHERE whatsapp=?",
            (whatsapp,)
        ).fetchone()
        con.close()

        if usuario and check_password_hash(usuario["senha"], senha):
            if usuario["tipo"] == "motorista" and not usuario["aprovado"]:
                return render_template_string(
                    AVISO,
                    titulo="Motorista aguardando aprovação",
                    mensagem="Seu cadastro ainda não foi aprovado pelo administrador.",
                    voltar=url_for("login")
                )

            session.clear()
            session["usuario_id"] = usuario["id"]

            if usuario["tipo"] == "motorista":
                return redirect(url_for("motorista"))
            return redirect(url_for("passageiro"))

        erro = "WhatsApp ou senha incorretos."

    return render_template_string(LOGIN, erro=erro)


@app.route("/logout")
def logout():
    uid = session.get("usuario_id")

    if uid:
        con = conectar()
        con.execute("""
            UPDATE usuarios_vai
            SET online=0, last_seen=0
            WHERE id=? AND tipo='motorista'
        """, (uid,))
        con.commit()
        con.close()

    session.clear()
    return redirect(url_for("index"))


# ============================================================
# PASSAGEIRO
# ============================================================

@app.route("/passageiro")
def passageiro():
    uid = session.get("usuario_id")
    if not uid:
        return redirect(url_for("login"))

    con = conectar()
    usuario = con.execute(
        "SELECT * FROM usuarios_vai WHERE id=?",
        (uid,)
    ).fetchone()

    if not usuario or usuario["tipo"] != "passageiro":
        con.close()
        return "Acesso permitido somente para passageiros.", 403

    corridas = con.execute("""
        SELECT * FROM corridas_vai
        WHERE passageiro_id=?
        ORDER BY id DESC
    """, (uid,)).fetchall()
    con.close()

    return render_template_string(
        PASSAGEIRO,
        usuario=usuario,
        corridas=corridas,
        online=contar_motoristas_online(),
        mensagem=session.pop("mensagem", ""),
        erro=session.pop("erro", "")
    )


@app.route("/solicitar-corrida", methods=["POST"])
def solicitar_corrida():
    uid = session.get("usuario_id")
    if not uid:
        return redirect(url_for("login"))

    partida = request.form.get("partida", "").strip()
    destino = request.form.get("destino", "").strip()

    try:
        distancia = float(request.form.get("distancia", "0").replace(",", "."))
    except ValueError:
        distancia = 0

    if not partida or not destino or distancia <= 0:
        session["erro"] = "Informe partida, destino e uma distância válida."
        return redirect(url_for("passageiro"))

    valor = round(distancia * TARIFA_KM, 2)
    taxa = round(valor * TAXA_APP, 2)
    ganho = round(valor * PERCENTUAL_MOTORISTA, 2)

    con = conectar()

    usuario = con.execute(
        "SELECT * FROM usuarios_vai WHERE id=? AND tipo='passageiro'",
        (uid,)
    ).fetchone()

    if not usuario:
        con.close()
        return "Acesso negado.", 403

    if contar_motoristas_online() <= 0:
        con.close()
        session["erro"] = "Nenhum motorista está online no momento."
        return redirect(url_for("passageiro"))

    con.execute("""
        INSERT INTO corridas_vai
        (passageiro_id, partida, destino, distancia, valor, taxa_app,
         ganho_motorista, pagamento, status, criada_em)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Dinheiro', 'PENDENTE', ?)
    """, (
        uid, partida, destino, distancia, valor, taxa, ganho, time.time()
    ))

    con.commit()
    con.close()

    session["mensagem"] = f"Corrida solicitada! Valor: R$ {valor:.2f}. Procurando motorista."
    return redirect(url_for("passageiro"))


# ============================================================
# MOTORISTA
# ============================================================

@app.route("/motorista")
@motorista_aprovado_required
def motorista():
    uid = session.get("usuario_id")
    marcar_motoristas_expirados()

    con = conectar()

    usuario = con.execute(
        "SELECT * FROM usuarios_vai WHERE id=?",
        (uid,)
    ).fetchone()

    corridas = con.execute("""
        SELECT c.*, u.nome AS passageiro_nome
        FROM corridas_vai c
        JOIN usuarios_vai u ON u.id=c.passageiro_id
        WHERE c.status='PENDENTE'
           OR (c.motorista_id=? AND c.status IN ('ACEITA','EM_ANDAMENTO'))
        ORDER BY c.id DESC
    """, (uid,)).fetchall()

    total = con.execute("""
        SELECT COALESCE(SUM(ganho_motorista),0) AS total
        FROM corridas_vai
        WHERE motorista_id=? AND status='CONCLUIDA'
    """, (uid,)).fetchone()["total"]

    qtd = con.execute("""
        SELECT COUNT(*) AS total
        FROM corridas_vai
        WHERE motorista_id=? AND status='CONCLUIDA'
    """, (uid,)).fetchone()["total"]

    con.close()

    return render_template_string(
        MOTORISTA,
        usuario=usuario,
        corridas=corridas,
        total_ganho=total,
        total_corridas=qtd,
        online=contar_motoristas_online()
    )


@app.route("/api/motorista/status", methods=["POST"])
@motorista_aprovado_required
def api_status():
    uid = session.get("usuario_id")
    dados = request.get_json(silent=True) or {}
    novo_status = 1 if dados.get("online") else 0

    con = conectar()

    if novo_status:
        # Limite de 20 motoristas
        aprovados = con.execute("""
            SELECT COUNT(*) AS total
            FROM usuarios_vai
            WHERE tipo='motorista' AND aprovado=1
        """).fetchone()["total"]

        if aprovados > 20:
            con.close()
            return jsonify(ok=False, erro="Limite de 20 motoristas atingido."), 400

        con.execute("""
            UPDATE usuarios_vai
            SET online=1, last_seen=?
            WHERE id=? AND tipo='motorista' AND aprovado=1
        """, (time.time(), uid))
    else:
        con.execute("""
            UPDATE usuarios_vai
            SET online=0, last_seen=0
            WHERE id=? AND tipo='motorista'
        """, (uid,))

    con.commit()
    con.close()

    return jsonify(
        ok=True,
        online=novo_status,
        online_total=contar_motoristas_online()
    )


@app.route("/api/motorista/heartbeat", methods=["POST"])
@motorista_aprovado_required
def api_heartbeat():
    uid = session.get("usuario_id")

    con = conectar()
    usuario = con.execute("""
        SELECT tipo, aprovado, online
        FROM usuarios_vai
        WHERE id=?
    """, (uid,)).fetchone()

    if not usuario or usuario["tipo"] != "motorista" or not usuario["aprovado"]:
        con.close()
        return jsonify(ok=False), 403

    if usuario["online"]:
        con.execute(
            "UPDATE usuarios_vai SET last_seen=? WHERE id=?",
            (time.time(), uid)
        )
        con.commit()

    con.close()
    return jsonify(ok=True)


@app.route("/api/motorista/corridas")
@motorista_aprovado_required
def api_motorista_corridas():
    uid = session.get("usuario_id")
    con = conectar()
    row = con.execute("""
        SELECT COUNT(*) AS total
        FROM corridas_vai
        WHERE status='PENDENTE'
    """).fetchone()
    con.close()
    return jsonify(ok=True, novas=row["total"] > 0)


@app.route("/api/motoristas-online")
def api_motoristas_online():
    return jsonify(online=contar_motoristas_online())


@app.route("/corrida/<int:corrida_id>/aceitar", methods=["POST"])
@motorista_aprovado_required
def aceitar_corrida(corrida_id):
    uid = session.get("usuario_id")
    con = conectar()

    motorista = con.execute("""
        SELECT * FROM usuarios_vai
        WHERE id=? AND tipo='motorista' AND aprovado=1 AND online=1
    """, (uid,)).fetchone()

    if not motorista:
        con.close()
        return "Motorista precisa estar aprovado e online.", 400

    cur = con.execute("""
        UPDATE corridas_vai
        SET motorista_id=?, status='ACEITA', aceita_em=?
        WHERE id=? AND status='PENDENTE' AND motorista_id IS NULL
    """, (uid, time.time(), corrida_id))

    con.commit()
    con.close()

    if cur.rowcount == 0:
        return "Essa corrida já foi aceita por outro motorista.", 409

    return redirect(url_for("motorista"))


@app.route("/corrida/<int:corrida_id>/iniciar", methods=["POST"])
@motorista_aprovado_required
def iniciar_corrida(corrida_id):
    uid = session.get("usuario_id")
    con = conectar()

    cur = con.execute("""
        UPDATE corridas_vai
        SET status='EM_ANDAMENTO', iniciada_em=?
        WHERE id=? AND motorista_id=? AND status='ACEITA'
    """, (time.time(), corrida_id, uid))

    con.commit()
    con.close()

    if cur.rowcount == 0:
        return "Não foi possível iniciar essa corrida.", 400

    return redirect(url_for("motorista"))


@app.route("/corrida/<int:corrida_id>/concluir", methods=["POST"])
@motorista_aprovado_required
def concluir_corrida(corrida_id):
    uid = session.get("usuario_id")
    con = conectar()

    cur = con.execute("""
        UPDATE corridas_vai
        SET status='CONCLUIDA', concluida_em=?
        WHERE id=? AND motorista_id=? AND status='EM_ANDAMENTO'
    """, (time.time(), corrida_id, uid))

    con.commit()
    con.close()

    if cur.rowcount == 0:
        return "Não foi possível concluir essa corrida.", 400

    return redirect(url_for("motorista"))


# ============================================================
# ADMIN
# ============================================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    erro = ""

    if request.method == "POST":
        whatsapp = request.form.get("whatsapp", "").strip()
        senha = request.form.get("senha", "")

        if whatsapp == ADMIN_WHATSAPP and senha == ADMIN_SENHA:
            session.clear()
            session["admin"] = True
            return redirect(url_for("admin"))

        erro = "Dados do administrador incorretos."

    return render_template_string(ADMIN_LOGIN, erro=erro)


@app.route("/admin")
@admin_required
def admin():
    con = conectar()

    pendentes = con.execute("""
        SELECT COUNT(*) AS total FROM usuarios_vai
        WHERE tipo='motorista' AND aprovado=0
    """).fetchone()["total"]

    motoristas = con.execute("""
        SELECT COUNT(*) AS total FROM usuarios_vai
        WHERE tipo='motorista' AND aprovado=1
    """).fetchone()["total"]

    online = contar_motoristas_online()

    corridas = con.execute("""
        SELECT COUNT(*) AS total FROM corridas_vai
    """).fetchone()["total"]

    motoristas_pendentes = con.execute("""
        SELECT * FROM usuarios_vai
        WHERE tipo='motorista' AND aprovado=0
        ORDER BY id DESC
    """).fetchall()

    motoristas_aprovados = con.execute("""
        SELECT * FROM usuarios_vai
        WHERE tipo='motorista' AND aprovado=1
        ORDER BY id DESC
    """).fetchall()

    ultimas_corridas = con.execute("""
        SELECT c.*,
               p.nome AS passageiro_nome,
               m.nome AS motorista_nome
        FROM corridas_vai c
        JOIN usuarios_vai p ON p.id=c.passageiro_id
        LEFT JOIN usuarios_vai m ON m.id=c.motorista_id
        ORDER BY c.id DESC
        LIMIT 20
    """).fetchall()

    con.close()

    return render_template_string(
        ADMIN,
        pendentes=pendentes,
        motoristas=motoristas,
        online=online,
        corridas=corridas,
        motoristas_pendentes=motoristas_pendentes,
        motoristas_aprovados=motoristas_aprovados,
        ultimas_corridas=ultimas_corridas,
        mensagem=session.pop("admin_mensagem", "")
    )


@app.route("/admin/motorista/<int:motorista_id>/aprovar", methods=["POST"])
@admin_required
def aprovar_motorista(motorista_id):
    con = conectar()

    total = con.execute("""
        SELECT COUNT(*) AS total FROM usuarios_vai
        WHERE tipo='motorista'
    """).fetchone()["total"]

    if total >= 20:
        con.close()
        session["admin_mensagem"] = "Limite de 20 motoristas atingido."
        return redirect(url_for("admin"))

    cur = con.execute("""
        UPDATE usuarios_vai
        SET aprovado=1
        WHERE id=? AND tipo='motorista'
    """, (motorista_id,))

    con.commit()
    con.close()

    session["admin_mensagem"] = (
        "Motorista aprovado com sucesso."
        if cur.rowcount else
        "Motorista não encontrado."
    )
    return redirect(url_for("admin"))


@app.route("/admin/motorista/<int:motorista_id>/bloquear", methods=["POST"])
@admin_required
def bloquear_motorista(motorista_id):
    con = conectar()
    con.execute("""
        UPDATE usuarios_vai
        SET aprovado=0, online=0, last_seen=0
        WHERE id=? AND tipo='motorista'
    """, (motorista_id,))
    con.commit()
    con.close()

    session["admin_mensagem"] = "Motorista bloqueado."
    return redirect(url_for("admin"))


@app.route("/admin/motorista/<int:motorista_id>/excluir", methods=["POST"])
@admin_required
def excluir_motorista(motorista_id):
    con = conectar()

    con.execute("""
        UPDATE corridas_vai
        SET motorista_id=NULL
        WHERE motorista_id=?
    """, (motorista_id,))

    con.execute("""
        DELETE FROM usuarios_vai
        WHERE id=? AND tipo='motorista'
    """, (motorista_id,))

    con.commit()
    con.close()

    session["admin_mensagem"] = "Motorista excluído."
    return redirect(url_for("admin"))


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("index"))


# ============================================================
# ERROS
# ============================================================

@app.errorhandler(404)
def erro_404(e):
    return render_template_string(
        AVISO,
        titulo="Página não encontrada",
        mensagem="A página solicitada não existe.",
        voltar=url_for("index")
    ), 404


@app.errorhandler(500)
def erro_500(e):
    return render_template_string(
        AVISO,
        titulo="Erro interno",
        mensagem="Ocorreu um erro no aplicativo. Veja o terminal para o detalhe.",
        voltar=url_for("index")
    ), 500


# ============================================================
# INICIALIZAÇÃO
# ============================================================

inicializar_banco()

if __name__ == "__main__":
    print("=" * 55)
    print("🏍️  VAI_DE_MOTO INICIADO")
    print(f"💰 Tarifa: R$ {TARIFA_KM:.2f}/km")
    print(f"⚙️  App: {TAXA_APP*100:.0f}%")
    print(f"🏍️  Motorista: {PERCENTUAL_MOTORISTA*100:.0f}%")
    print(f"🔐 Admin: {ADMIN_WHATSAPP} / {ADMIN_SENHA}")
    print("🌐 http://127.0.0.1:5000")
    print("🌐 http://0.0.0.0:5000")
    print("=" * 55)

    app.run(host="0.0.0.0", port=5000, debug=False)

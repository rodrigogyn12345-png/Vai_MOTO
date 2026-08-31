from flask import Flask, request, redirect, url_for, session, render_template_string, jsonify
import sqlite3
import time
import json
import math
import urllib.parse
import urllib.request
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

# ============================================================
# VAI_DE_MOTO - APP COMPLETO
# Corrigido: GPS + cálculo automático de rota/preço
# Tarifa: R$ 2,00/km | App: 9% | Motorista: 91%
# Pagamento atual: Dinheiro
# ============================================================

app = Flask(__name__)
app.secret_key = "VAIDE_MOTO_CHAVE_2026_TROQUE_EM_PRODUCAO"

DB = "vaimoto.db"

TARIFA_KM = 2.00
TAXA_APP = 0.09
PERCENTUAL_MOTORISTA = 0.91

ONLINE_TIMEOUT = 45
MAX_MOTORISTAS = 20

ADMIN_WHATSAPP = "62993903299"
ADMIN_SENHA = "1234"

# Centro usado para ajudar a geocodificar endereços curtos.
CIDADE_PADRAO = "Aragoiânia, Goiás, Brasil"


# ============================================================
# BANCO DE DADOS
# ============================================================

def conectar():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con


def coluna_existe(con, tabela, coluna):
    return any(r["name"] == coluna for r in con.execute(f"PRAGMA table_info({tabela})").fetchall())


def adicionar_coluna_se_nao_existir(con, tabela, coluna, definicao):
    if not coluna_existe(con, tabela, coluna):
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
            valor REAL NOT NULL DEFAULT 0,
            taxa_app REAL NOT NULL DEFAULT 0,
            ganho_motorista REAL NOT NULL DEFAULT 0,
            pagamento TEXT NOT NULL DEFAULT 'Dinheiro',
            status TEXT NOT NULL DEFAULT 'PENDENTE',
            motorista_id INTEGER,
            criada_em REAL NOT NULL,
            aceita_em REAL,
            iniciada_em REAL,
            concluida_em REAL,
            origem_lat REAL,
            origem_lon REAL,
            destino_lat REAL,
            destino_lon REAL
        )
    """)

    # Migração para bancos antigos.
    campos = {
        "distancia": "REAL NOT NULL DEFAULT 0",
        "taxa_app": "REAL NOT NULL DEFAULT 0",
        "ganho_motorista": "REAL NOT NULL DEFAULT 0",
        "pagamento": "TEXT NOT NULL DEFAULT 'Dinheiro'",
        "aceita_em": "REAL",
        "iniciada_em": "REAL",
        "concluida_em": "REAL",
        "origem_lat": "REAL",
        "origem_lon": "REAL",
        "destino_lat": "REAL",
        "destino_lon": "REAL",
    }
    for nome, definicao in campos.items():
        adicionar_coluna_se_nao_existir(con, "corridas_vai", nome, definicao)

    # Garante administrador. O admin não é tratado como passageiro na interface.
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
        SET online=0, last_seen=0
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
    return int(row["total"])


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


# ============================================================
# AUTORIZAÇÃO
# ============================================================

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
# GEOLOCALIZAÇÃO E ROTAS
# ============================================================

def numero(v):
    try:
        return float(str(v).replace(",", "."))
    except Exception:
        return None


def distancia_haversine(lat1, lon1, lat2, lon2):
    """Distância em linha reta, usada somente como fallback."""
    r = 6371.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * (2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def requisicao_json(url, timeout=12):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "VAI_DE_MOTO/1.0 contato-vai-de-moto",
            "Accept": "application/json",
        }
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def geocodificar_endereco(endereco):
    """
    Busca coordenadas do endereço usando Nominatim.
    Primeiro tenta o endereço exatamente como informado.
    Depois tenta acrescentar Aragoiânia/Goiás/Brasil.
    """
    endereco = (endereco or "").strip()
    if not endereco:
        raise ValueError("Digite o endereço de destino.")

    consultas = [
        endereco,
        f"{endereco}, {CIDADE_PADRAO}"
    ]

    ultimo_erro = None

    for consulta in consultas:
        try:
            params = urllib.parse.urlencode({
                "q": consulta,
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "br",
                "addressdetails": 1,
            })
            url = "https://nominatim.openstreetmap.org/search?" + params
            dados = requisicao_json(url, timeout=15)

            if dados:
                lat = numero(dados[0].get("lat"))
                lon = numero(dados[0].get("lon"))
                if lat is not None and lon is not None:
                    return lat, lon, dados[0].get("display_name", consulta)
        except Exception as e:
            ultimo_erro = e

    if ultimo_erro:
        raise RuntimeError(
            "Não consegui localizar esse endereço agora. "
            "Confira o nome da rua, número, bairro e cidade."
        )

    raise RuntimeError(
        "Endereço não encontrado. Digite rua, número, bairro e cidade."
    )


def calcular_rota_osrm(origem_lat, origem_lon, destino_lat, destino_lon):
    """
    Retorna distância de rota em km.
    Usa OSRM. Se o serviço de rota falhar, usa Haversine como fallback
    com um fator pequeno para não deixar a corrida travada.
    """
    try:
        coords = f"{origem_lon},{origem_lat};{destino_lon},{destino_lat}"
        params = urllib.parse.urlencode({
            "overview": "false",
            "alternatives": "false",
            "steps": "false",
        })
        url = f"https://router.project-osrm.org/route/v1/driving/{coords}?{params}"
        dados = requisicao_json(url, timeout=15)

        if dados.get("code") == "Ok" and dados.get("routes"):
            metros = float(dados["routes"][0]["distance"])
            km = metros / 1000.0
            if km > 0:
                return km, "rota"

    except Exception:
        pass

    # Fallback para não retornar "erro ao calcular" quando a rota externa
    # estiver temporariamente indisponível.
    km_reta = distancia_haversine(
        origem_lat, origem_lon, destino_lat, destino_lon
    )
    if km_reta > 0:
        return max(km_reta * 1.25, 0.1), "estimada"

    raise RuntimeError("Não foi possível calcular a distância.")


def calcular_valores(km):
    valor = round(km * TARIFA_KM, 2)
    taxa = round(valor * TAXA_APP, 2)
    ganho = round(valor * PERCENTUAL_MOTORISTA, 2)
    return valor, taxa, ganho


# ============================================================
# CSS
# ============================================================

CSS = """
<style>
*{box-sizing:border-box}
body{
    margin:0;
    background:#080808;
    font-family:Arial,Helvetica,sans-serif;
    color:#fff;
}
.page{
    width:min(100%,900px);
    margin:0 auto;
    padding:14px 10px 40px;
}
.card{
    width:100%;
    background:#141414;
    border:1px solid #292929;
    border-radius:24px;
    padding:22px;
    margin:14px auto;
    box-shadow:0 8px 28px rgba(0,0,0,.35);
}
.white{
    background:#fff;
    color:#111;
}
.logo{
    text-align:center;
    font-size:30px;
    font-weight:900;
}
.logo span{color:#16a34a}
.subtitle{
    text-align:center;
    color:#aaa;
    font-size:15px;
    margin-top:5px;
}
h1{font-size:28px;margin:18px 0}
h2{font-size:22px;margin-top:24px}
label{
    display:block;
    font-size:16px;
    font-weight:800;
    margin-top:14px;
}
input,select{
    width:100%;
    padding:16px;
    margin-top:7px;
    border:1px solid #ccc;
    border-radius:14px;
    font-size:17px;
    background:#fff;
    color:#111;
}
button,.btn{
    display:block;
    width:100%;
    padding:16px;
    margin-top:12px;
    border:0;
    border-radius:14px;
    font-size:17px;
    font-weight:900;
    text-align:center;
    text-decoration:none;
    cursor:pointer;
}
.green{background:#16a34a;color:#fff}
.red{background:#dc2626;color:#fff}
.blue{background:#1677d2;color:#fff}
.black{background:#050505;color:#fff}
.gray{background:#555;color:#fff}
.orange{background:#e88b00;color:#fff}
.yellow{background:#ffd400;color:#111}
.box{
    padding:16px;
    margin:12px 0;
    border-radius:16px;
    background:#202020;
    border:1px solid #333;
}
.white .box{
    background:#f3f4f6;
    color:#111;
    border-color:#ddd;
}
.info{
    background:#172233;
    border-radius:16px;
    padding:16px;
    margin:14px 0;
    font-size:17px;
    line-height:1.65;
}
.status{
    padding:16px;
    border-radius:16px;
    text-align:center;
    font-size:18px;
    font-weight:900;
    margin:14px 0;
}
.status.online{background:#10351f;color:#55e58a}
.status.offline{background:#292929;color:#ccc}
.success{
    background:#10351f;
    color:#66e59a;
    padding:15px;
    border-radius:14px;
    font-weight:800;
    margin:12px 0;
}
.warning{
    background:#3a2d08;
    color:#ffd65a;
    padding:15px;
    border-radius:14px;
    font-weight:800;
    margin:12px 0;
}
.error{
    background:#431313;
    color:#ff9d9d;
    padding:15px;
    border-radius:14px;
    font-weight:800;
    margin:12px 0;
}
.grid{
    display:grid;
    grid-template-columns:repeat(2,1fr);
    gap:10px;
}
.stat{
    background:#202020;
    border-radius:16px;
    padding:15px;
    text-align:center;
    border:1px solid #333;
}
.stat b{display:block;font-size:25px;margin-bottom:5px}
.muted{color:#aaa}
.big-price{
    font-size:31px;
    font-weight:900;
    text-align:center;
    padding:12px;
}
.location-box{
    border:1px solid #333;
    background:#0e0e0e;
    padding:13px;
    border-radius:14px;
    margin-top:10px;
    word-break:break-word;
}
.white .location-box{
    background:#f8f8f8;
    color:#111;
}
.row{
    display:flex;
    gap:8px;
    align-items:center;
}
.row>*{flex:1}
small{color:#aaa}
hr{border:0;border-top:1px solid #333;margin:22px 0}
@media(max-width:560px){
    .card{padding:18px}
    .grid{grid-template-columns:1fr}
    h1{font-size:25px}
    .logo{font-size:27px}
}
<style>
#resultado.info {
    color: #ffffff !important;
    background: #172235 !important;
    font-size: 16px !important;
    line-height: 1.8 !important;
    font-weight: 600 !important;
}
#resultado.info b {
    color: #ffffff !important;
}
</style>
</style>
"""


# ============================================================
# TEMPLATES
# ============================================================

HOME = CSS + """
<div class="page">
<div class="card">
    <div class="logo">🏍️ VAI_<span>DE_MOTO</span></div>
    <div class="subtitle">Transporte de moto local</div>

    <div class="info">
        💰 <b>R$ 2,00 por km</b><br>
        ⚙️ Taxa do aplicativo: <b>9%</b><br>
        🏍️ Motorista recebe: <b>91%</b><br>
        💵 Pagamento: <b>Dinheiro</b>
    </div>

    <a class="btn yellow" href="{{ url_for('login') }}">🔐 ENTRAR</a>
    <a class="btn green" href="{{ url_for('cadastro') }}">📝 CADASTRAR</a>
    <a class="btn black" href="{{ url_for('admin_login') }}">⚙️ ÁREA DO ADMINISTRADOR</a>
</div>
</div>
"""

CADASTRO = CSS + """
<div class="page"><div class="card">
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

<a class="btn gray" href="{{ url_for('login') }}">🔐 Já tenho cadastro</a>
</div></div>
"""

LOGIN = CSS + """
<div class="page"><div class="card">
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

<a class="btn gray" href="{{ url_for('cadastro') }}">📝 Criar cadastro</a>
</div></div>
"""

PASSAGEIRO = CSS + """
<div class="page">
<div class="card">
<div class="logo">🏍️ VAI_<span>DE_MOTO</span></div>
<div class="subtitle">Área do passageiro</div>

<h1>Olá, {{ usuario['nome'] }} 👋</h1>

<div id="gpsStatus" class="status offline">📍 GPS não capturado</div>
<button class="blue" type="button" onclick="usarGPS()">📍 USAR MINHA LOCALIZAÇÃO</button>

<div id="coordenadas" class="location-box">
Nenhuma localização capturada.
</div>

{% if mensagem %}<div class="success">{{ mensagem }}</div>{% endif %}
{% if erro %}<div class="error">{{ erro }}</div>{% endif %}

<div class="card white" style="margin:14px 0;padding:18px">
<h1>🏍️ Solicitar corrida</h1>

<form id="formCorrida" method="post" action="{{ url_for('solicitar_corrida') }}">
<label>📍 Origem / endereço de embarque</label>
<input id="partida" name="partida" placeholder="Use o GPS ou digite o endereço" required>

<label>🏁 Destino</label>
<input id="destino" name="destino" placeholder="Digite rua, número, bairro e cidade" required>

<input type="hidden" id="origem_lat" name="origem_lat">
<input type="hidden" id="origem_lon" name="origem_lon">
<input type="hidden" id="destino_lat" name="destino_lat">
<input type="hidden" id="destino_lon" name="destino_lon">
<input type="hidden" id="distancia" name="distancia">
<input type="hidden" id="valor_hidden" name="valor">

<button id="btnCalcular" class="blue" type="button" onclick="calcularRota()">
🧮 CALCULAR VALOR
</button>

<div id="resultado" class="info" style="color:#ffffff !important;background:#162334 !important;">
Digite o destino e toque em <b>CALCULAR VALOR</b>.
</div>

<label>💵 Forma de pagamento</label>
<select name="pagamento">
<option value="Dinheiro">💵 Dinheiro</option>
</select>

<button id="btnSolicitar" class="green" type="submit" disabled>
🏍️ SOLICITAR CORRIDA
</button>
</form>
</div>

<h2>📋 Minhas corridas</h2>
<div id="minhasCorridas">
{% if corridas %}
{% for c in corridas %}
<div class="box">
<b>Corrida #{{ c['id'] }}</b><br>
📍 {{ c['partida'] }}<br>
🏁 {{ c['destino'] }}<br>
📏 {{ "%.2f"|format(c['distancia']) }} km<br>
💰 R$ {{ "%.2f"|format(c['valor']) }}<br>
💵 {{ c['pagamento'] }}<br>
📌 <b>{{ c['status'] }}</b>
</div>
{% endfor %}
{% else %}
<div class="box">Nenhuma corrida.</div>
{% endif %}
</div>

<a class="btn red" href="{{ url_for('logout') }}">Sair</a>
</div>
</div>

<script>
let gpsLat = null;
let gpsLon = null;

function setGpsStatus(text, online){
    const box = document.getElementById("gpsStatus");
    box.textContent = text;
    box.className = online ? "status online" : "status offline";
}

function usarGPS(){
    if(!navigator.geolocation){
        setGpsStatus("❌ Este navegador não suporta GPS.", false);
        return;
    }

    setGpsStatus("📍 Capturando GPS...", false);

    navigator.geolocation.getCurrentPosition(
        function(pos){
            gpsLat = pos.coords.latitude;
            gpsLon = pos.coords.longitude;

            document.getElementById("origem_lat").value = gpsLat;
            document.getElementById("origem_lon").value = gpsLon;

            document.getElementById("partida").value =
                "Minha localização (" + gpsLat.toFixed(6) + ", " + gpsLon.toFixed(6) + ")";

            document.getElementById("coordenadas").textContent =
                "Latitude: " + gpsLat.toFixed(6) +
                " | Longitude: " + gpsLon.toFixed(6);

            setGpsStatus("✅ GPS capturado com sucesso", true);
        },
        function(err){
            let msg = "❌ Não foi possível obter o GPS.";
            if(err.code === 1) msg = "❌ Permita o GPS no navegador.";
            if(err.code === 2) msg = "❌ Localização indisponível.";
            if(err.code === 3) msg = "❌ GPS demorou demais. Tente novamente.";
            setGpsStatus(msg, false);
        },
        {enableHighAccuracy:true, timeout:15000, maximumAge:10000}
    );
}

async function calcularRota(){
    const btn = document.getElementById("btnCalcular");
    const resultado = document.getElementById("resultado");
    const destino = document.getElementById("destino").value.trim();

    if(!destino){
        resultado.innerHTML = "❌ Digite o destino.";
        return;
    }

    if(!gpsLat || !gpsLon){
        resultado.innerHTML = "❌ Primeiro toque em <b>USAR MINHA LOCALIZAÇÃO</b> e permita o GPS.";
        return;
    }

    btn.disabled = true;
    btn.textContent = "🧮 CALCULANDO...";

    try{
        const body = new URLSearchParams();
        body.append("origem_lat", gpsLat);
        body.append("origem_lon", gpsLon);
        body.append("destino", destino);

        const resp = await fetch("/api/calcular-rota", {
            method:"POST",
            headers:{"Content-Type":"application/x-www-form-urlencoded;charset=UTF-8"},
            body:body.toString()
        });

        const data = await resp.json();

        if(!resp.ok || !data.ok){
            resultado.innerHTML = "❌ " + (data.erro || "Não foi possível calcular a rota.");
            document.getElementById("btnSolicitar").disabled = true;
            return;
        }

        document.getElementById("destino_lat").value = data.destino_lat;
        document.getElementById("destino_lon").value = data.destino_lon;
        document.getElementById("distancia").value = data.distancia;
        document.getElementById("valor_hidden").value = data.valor;

        resultado.innerHTML =
            "📏 Distância: <b>" + data.distancia.toFixed(2) + " km</b><br>" +
            "💰 Valor da corrida: <b>R$ " + data.valor.toFixed(2).replace(".", ",") + "</b><br>" +
            "⚙️ Taxa do app (9%): <b>R$ " + data.taxa.toFixed(2).replace(".", ",") + "</b><br>" +
            "🏍️ Motorista (91%): <b>R$ " + data.ganho.toFixed(2).replace(".", ",") + "</b><br>" +
            "📍 Destino localizado: " + data.endereco + "</div>";

        document.getElementById("btnSolicitar").disabled = false;

    }catch(e){
        resultado.innerHTML =
            "❌ Não foi possível calcular agora. Verifique sua conexão e tente novamente. " +
            "Se continuar, confira o endereço.";
        document.getElementById("btnSolicitar").disabled = true;
    }finally{
        btn.disabled = false;
        btn.textContent = "🧮 CALCULAR VALOR";
    }
}

document.getElementById("formCorrida").addEventListener("submit", function(e){
    const km = parseFloat(document.getElementById("distancia").value || "0");
    if(!km || km <= 0){
        e.preventDefault();
        alert("Calcule o valor antes de solicitar a corrida.");
    }
});
</script>
"""

MOTORISTA = CSS + """
<div class="page"><div class="card">
<div class="logo">🏍️ VAI_<span>DE_MOTO</span></div>
<div class="subtitle">Área do motorista</div>

<h1>Olá, {{ usuario['nome'] }} 👋</h1>

<div id="statusBox" class="status {{ 'online' if usuario['online'] else 'offline' }}">
{% if usuario['online'] %}🟢 MOTORISTA ONLINE{% else %}⚪ MOTORISTA OFFLINE{% endif %}
</div>

<button id="statusButton"
class="{{ 'red' if usuario['online'] else 'green' }}"
onclick="alternarStatus()">
{% if usuario['online'] %}FICAR OFFLINE{% else %}FICAR ONLINE{% endif %}
</button>

<div class="grid">
<div class="stat"><b id="onlineCount">{{ online }}</b>Motoristas online</div>
<div class="stat"><b>91%</b>Seu recebimento</div>
</div>

<div class="info">
💰 Tarifa: <b>R$ 2,00/km</b><br>
⚙️ App: <b>9%</b><br>
🏍️ Você recebe: <b>91%</b><br>
💵 Pagamento: <b>Dinheiro</b>
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
📏 {{ "%.2f"|format(c['distancia']) }} km<br>
💰 R$ {{ "%.2f"|format(c['valor']) }}<br>
💵 {{ c['pagamento'] }}<br>

{% if c['status']=='PENDENTE' %}
<form method="post" action="{{ url_for('aceitar_corrida', corrida_id=c['id']) }}">
<button class="green" type="submit">🏍️ ACEITAR CORRIDA</button>
</form>
{% elif c['status']=='ACEITA' and c['motorista_id']==usuario['id'] %}
<div class="warning">Você aceitou esta corrida.</div>
<form method="post" action="{{ url_for('iniciar_corrida', corrida_id=c['id']) }}">
<button class="blue" type="submit">▶️ INICIAR CORRIDA</button>
</form>
{% elif c['status']=='EM_ANDAMENTO' and c['motorista_id']==usuario['id'] %}
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
<div class="stat"><b>R$ {{ "%.2f"|format(total_ganho) }}</b>Total</div>
<div class="stat"><b>{{ total_corridas }}</b>Corridas concluídas</div>
</div>

<a class="btn gray" href="{{ url_for('logout') }}">Sair</a>
</div></div>

<script>
let online = {{ 1 if usuario['online'] else 0 }};

function atualizarTela(){
    const box = document.getElementById("statusBox");
    const btn = document.getElementById("statusButton");

    if(online){
        box.className = "status online";
        box.textContent = "🟢 MOTORISTA ONLINE";
        btn.className = "red";
        btn.textContent = "FICAR OFFLINE";
    }else{
        box.className = "status offline";
        box.textContent = "⚪ MOTORISTA OFFLINE";
        btn.className = "green";
        btn.textContent = "FICAR ONLINE";
    }
}

async function alternarStatus(){
    const novo = online ? 0 : 1;

    try{
        const r = await fetch("/api/motorista/status", {
            method:"POST",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({online:novo})
        });

        const d = await r.json();

        if(d.ok){
            online = d.online;
            document.getElementById("onlineCount").textContent = d.online_total;
            atualizarTela();
            if(online) location.reload();
        }else{
            alert(d.erro || "Não foi possível alterar o status.");
        }
    }catch(e){
        alert("Erro de conexão.");
    }
}

async function heartbeat(){
    if(!online) return;
    try{
        await fetch("/api/motorista/heartbeat", {method:"POST"});
    }catch(e){}
}

async function verificarCorridas(){
    if(!online) return;
    try{
        const r = await fetch("/api/motorista/corridas");
        const d = await r.json();
        if(d.ok && d.novas) location.reload();
    }catch(e){}
}

setInterval(heartbeat, 10000);
setInterval(verificarCorridas, 5000);
heartbeat();
</script>
"""

AVISO = CSS + """
<div class="page"><div class="card">
<div class="logo">🏍️ VAI_<span>DE_MOTO</span></div>
<h1>{{ titulo }}</h1>
<div class="warning">{{ mensagem }}</div>
<a class="btn green" href="{{ voltar }}">Voltar</a>
</div></div>
"""

ADMIN_LOGIN = CSS + """
<div class="page"><div class="card">
<div class="logo">🏍️ VAI_<span>DE_MOTO</span></div>
<div class="subtitle">Área administrativa</div>

{% if erro %}<div class="error">{{ erro }}</div>{% endif %}

<form method="post">
<label>📱 WhatsApp do administrador</label>
<input name="whatsapp" required>

<label>🔑 Senha</label>
<input name="senha" type="password" required>

<button class="black" type="submit">⚙️ ENTRAR NO ADMIN</button>
</form>
<a class="btn gray" href="{{ url_for('index') }}">Voltar</a>
</div></div>
"""

ADMIN = CSS + """
<div class="page"><div class="card">
<div class="logo">🏍️ VAI_<span>DE_MOTO</span></div>
<div class="subtitle">Painel administrativo</div>

<h1>⚙️ Administração</h1>

{% if mensagem %}<div class="success">{{ mensagem }}</div>{% endif %}

<div class="grid">
<div class="stat"><b>{{ pendentes }}</b>Motoristas pendentes</div>
<div class="stat"><b>{{ online }}</b>Motoristas online</div>
<div class="stat"><b>{{ motoristas }}</b>Motoristas aprovados</div>
<div class="stat"><b>{{ corridas }}</b>Corridas</div>
</div>

<h2>🏍️ Motoristas aguardando aprovação</h2>
{% if motoristas_pendentes %}
{% for m in motoristas_pendentes %}
<div class="box">
<b>{{ m['nome'] }}</b><br>
📱 {{ m['whatsapp'] }}<br>
<form method="post" action="{{ url_for('aprovar_motorista', motorista_id=m['id']) }}">
<button class="green">✅ APROVAR</button>
</form>
<form method="post" action="{{ url_for('excluir_motorista', motorista_id=m['id']) }}" onsubmit="return confirm('Excluir este motorista?')">
<button class="red">🗑️ EXCLUIR</button>
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
{% if m['online'] %}🟢 ONLINE{% else %}⚪ OFFLINE{% endif %}
<form method="post" action="{{ url_for('bloquear_motorista', motorista_id=m['id']) }}">
<button class="orange">🚫 BLOQUEAR</button>
</form>
<form method="post" action="{{ url_for('excluir_motorista', motorista_id=m['id']) }}" onsubmit="return confirm('Excluir este motorista?')">
<button class="red">🗑️ EXCLUIR</button>
</form>
</div>
{% endfor %}
{% else %}
<div class="box">Nenhum motorista aprovado.</div>
{% endif %}

<h2>🚕 Últimas corridas</h2>
{% if ultimas_corridas %}
{% for c in ultimas_corridas %}
<div class="box">
<b>#{{ c['id'] }} — {{ c['status'] }}</b><br>
👤 Passageiro: {{ c['passageiro_nome'] }}<br>
🏍️ Motorista: {{ c['motorista_nome'] or 'Não definido' }}<br>
📍 {{ c['partida'] }} → {{ c['destino'] }}<br>
📏 {{ "%.2f"|format(c['distancia']) }} km<br>
💰 R$ {{ "%.2f"|format(c['valor']) }}<br>
⚙️ App: R$ {{ "%.2f"|format(c['taxa_app']) }}<br>
🏍️ Motorista: R$ {{ "%.2f"|format(c['ganho_motorista']) }}
</div>
{% endfor %}
{% else %}
<div class="box">Nenhuma corrida registrada.</div>
{% endif %}

<a class="btn red" href="{{ url_for('admin_logout') }}">Sair</a>
</div></div>
"""


# ============================================================
# ROTAS PRINCIPAIS
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

                total_motoristas = con.execute("""
                    SELECT COUNT(*) AS total
                    FROM usuarios_vai
                    WHERE tipo='motorista'
                """).fetchone()["total"]

                if tipo == "motorista" and total_motoristas >= MAX_MOTORISTAS:
                    con.close()
                    erro = f"Limite de {MAX_MOTORISTAS} motoristas atingido."
                else:
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

                    session.clear()
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
        SELECT *
        FROM corridas_vai
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


@app.route("/api/calcular-rota", methods=["POST"])
def api_calcular_rota():
    uid = session.get("usuario_id")
    if not uid:
        return jsonify(ok=False, erro="Sessão expirada. Entre novamente."), 401

    origem_lat = numero(request.form.get("origem_lat"))
    origem_lon = numero(request.form.get("origem_lon"))
    destino = request.form.get("destino", "").strip()

    if origem_lat is None or origem_lon is None:
        return jsonify(
            ok=False,
            erro="Origem GPS não encontrada. Toque em USAR MINHA LOCALIZAÇÃO."
        ), 400

    if not destino:
        return jsonify(ok=False, erro="Digite o destino."), 400

    if not (-90 <= origem_lat <= 90 and -180 <= origem_lon <= 180):
        return jsonify(ok=False, erro="Coordenadas GPS inválidas."), 400

    try:
        destino_lat, destino_lon, endereco = geocodificar_endereco(destino)

        km, origem_calculo = calcular_rota_osrm(
            origem_lat, origem_lon, destino_lat, destino_lon
        )

        valor, taxa, ganho = calcular_valores(km)

        return jsonify(
            ok=True,
            distancia=round(km, 2),
            valor=valor,
            taxa=taxa,
            ganho=ganho,
            destino_lat=destino_lat,
            destino_lon=destino_lon,
            endereco=endereco,
            calculo=origem_calculo
        )

    except ValueError as e:
        return jsonify(ok=False, erro=str(e)), 400
    except RuntimeError as e:
        return jsonify(ok=False, erro=str(e)), 502
    except Exception:
        return jsonify(
            ok=False,
            erro="Não foi possível calcular a rota agora. Tente novamente em alguns segundos."
        ), 502


@app.route("/solicitar-corrida", methods=["POST"])
def solicitar_corrida():
    uid = session.get("usuario_id")

    if not uid:
        return redirect(url_for("login"))

    partida = request.form.get("partida", "").strip()
    destino = request.form.get("destino", "").strip()
    pagamento = request.form.get("pagamento", "Dinheiro")

    origem_lat = numero(request.form.get("origem_lat"))
    origem_lon = numero(request.form.get("origem_lon"))
    destino_lat = numero(request.form.get("destino_lat"))
    destino_lon = numero(request.form.get("destino_lon"))

    distancia = numero(request.form.get("distancia"))

    if pagamento != "Dinheiro":
        pagamento = "Dinheiro"

    if not partida or not destino:
        session["erro"] = "Informe origem e destino."
        return redirect(url_for("passageiro"))

    if origem_lat is None or origem_lon is None:
        session["erro"] = "Use o GPS antes de solicitar a corrida."
        return redirect(url_for("passageiro"))

    # Recalcula no servidor para evitar alteração do valor pelo navegador.
    try:
        if destino_lat is None or destino_lon is None:
            destino_lat, destino_lon, _ = geocodificar_endereco(destino)

        distancia, _ = calcular_rota_osrm(
            origem_lat, origem_lon, destino_lat, destino_lon
        )
    except Exception:
        # Se já veio uma distância calculada pelo servidor, aceita como fallback.
        if distancia is None or distancia <= 0:
            session["erro"] = "Não foi possível calcular a rota. Tente novamente."
            return redirect(url_for("passageiro"))

    if distancia <= 0:
        session["erro"] = "Distância inválida."
        return redirect(url_for("passageiro"))

    valor, taxa, ganho = calcular_valores(distancia)

    con = conectar()

    usuario = con.execute(
        "SELECT * FROM usuarios_vai WHERE id=? AND tipo='passageiro'",
        (uid,)
    ).fetchone()

    if not usuario:
        con.close()
        return "Acesso negado.", 403

    # A corrida só é criada se houver motorista online.
    if contar_motoristas_online() <= 0:
        con.close()
        session["erro"] = "Nenhum motorista está online no momento."
        return redirect(url_for("passageiro"))

    con.execute("""
        INSERT INTO corridas_vai
        (passageiro_id, partida, destino, distancia, valor, taxa_app,
         ganho_motorista, pagamento, status, criada_em,
         origem_lat, origem_lon, destino_lat, destino_lon)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDENTE', ?, ?, ?, ?, ?, ?)
    """, (
        uid,
        partida,
        destino,
        distancia,
        valor,
        taxa,
        ganho,
        pagamento,
        time.time(),
        origem_lat,
        origem_lon,
        destino_lat,
        destino_lon
    ))

    con.commit()
    con.close()

    session["mensagem"] = (
        f"Corrida solicitada! Valor: R$ {valor:.2f}. "
        "Procurando motorista."
    )

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
        aprovados = con.execute("""
            SELECT COUNT(*) AS total
            FROM usuarios_vai
            WHERE tipo='motorista' AND aprovado=1
        """).fetchone()["total"]

        if aprovados > MAX_MOTORISTAS:
            con.close()
            return jsonify(
                ok=False,
                erro=f"Limite de {MAX_MOTORISTAS} motoristas atingido."
            ), 400

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
        SELECT COUNT(*) AS total
        FROM usuarios_vai
        WHERE tipo='motorista' AND aprovado=0
    """).fetchone()["total"]

    motoristas = con.execute("""
        SELECT COUNT(*) AS total
        FROM usuarios_vai
        WHERE tipo='motorista' AND aprovado=1
    """).fetchone()["total"]

    online = contar_motoristas_online()

    corridas = con.execute("""
        SELECT COUNT(*) AS total FROM corridas_vai
    """).fetchone()["total"]

    motoristas_pendentes = con.execute("""
        SELECT *
        FROM usuarios_vai
        WHERE tipo='motorista' AND aprovado=0
        ORDER BY id DESC
    """).fetchall()

    motoristas_aprovados = con.execute("""
        SELECT *
        FROM usuarios_vai
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
        LIMIT 30
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
        SELECT COUNT(*) AS total
        FROM usuarios_vai
        WHERE tipo='motorista'
    """).fetchone()["total"]

    if total > MAX_MOTORISTAS:
        con.close()
        session["admin_mensagem"] = f"Limite de {MAX_MOTORISTAS} motoristas atingido."
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
        if cur.rowcount
        else "Motorista não encontrado."
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

    # Corridas concluídas permanecem no histórico, apenas perdem o vínculo.
    con.execute("""
        UPDATE corridas_vai
        SET motorista_id=NULL
        WHERE motorista_id=? AND status IN ('PENDENTE','ACEITA','EM_ANDAMENTO')
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
        mensagem="Ocorreu um erro interno. Tente novamente.",
        voltar=url_for("index")
    ), 500


# ============================================================
# INICIALIZAÇÃO
# ============================================================

inicializar_banco()

if __name__ == "__main__":
    print("=" * 60)
    print("🏍️ VAI_DE_MOTO INICIADO")
    print(f"💰 Tarifa: R$ {TARIFA_KM:.2f}/km")
    print(f"⚙️ App: {TAXA_APP * 100:.0f}%")
    print(f"🏍️ Motorista: {PERCENTUAL_MOTORISTA * 100:.0f}%")
    print(f"👥 Limite: {MAX_MOTORISTAS} motoristas")
    print(f"🔐 Admin: {ADMIN_WHATSAPP} / {ADMIN_SENHA}")
    print("🌐 Porta: 5000")
    print("=" * 60)

    app.run(host="0.0.0.0", port=5000, debug=False)

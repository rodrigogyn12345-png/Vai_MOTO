from flask import Flask, request, redirect, url_for, session, render_template_string, jsonify
from pathlib import Path
import sqlite3
import time
import re
import math
import json
from urllib.parse import quote
from urllib.request import Request, urlopen
from werkzeug.security import generate_password_hash, check_password_hash

# ============================================================
# VAIMOTO V10 - PASSAGEIRO + MOTORISTA + GPS + TARIFA
# ============================================================
# Tarifa: R$ 1,20/km
# Taxa administrativa: 8%
# Motorista: 92%
# IMPORTANTE: este arquivo usa HTTP local. Abra no celular com
# http://IP_DO_ANDROID:5000 (nao https://).
# ============================================================

app = Flask(__name__)
app.secret_key = "vaimoto-v10-troque-esta-chave"

BASE_DIR = Path(__file__).resolve().parent
DB = str(BASE_DIR / "vaimoto.db")

PRECO_KM = 1.20
TAXA_ADMIN = 0.08
PERCENTUAL_MOTORISTA = 1.0 - TAXA_ADMIN
ONLINE_TIMEOUT = 35
LOCATION_TIMEOUT = 60
PENDENTE_TIMEOUT = 10 * 60  # 10 minutos sem motorista: corrida pendente expira
POLL_PASSAGEIRO = 2000
POLL_MOTORISTA = 1500
ADMIN_KEY = "vaimoto-admin-1234"  # troque antes de colocar na internet

STATUS_ATIVOS = ("PENDENTE", "ACEITA", "EM_ANDAMENTO")


def dinheiro(v):
    return round(float(v or 0), 2)


def conectar():
    con = sqlite3.connect(DB, timeout=15)
    con.row_factory = sqlite3.Row
    return con


def coluna_existe(con, tabela, coluna):
    return any(r["name"] == coluna for r in con.execute(f"PRAGMA table_info({tabela})").fetchall())


def inicializar_banco():
    con = conectar()
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""
        CREATE TABLE IF NOT EXISTS usuarios_vai (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            whatsapp TEXT NOT NULL UNIQUE,
            senha TEXT NOT NULL,
            tipo TEXT NOT NULL CHECK(tipo IN ('passageiro','motorista')),
            online INTEGER NOT NULL DEFAULT 0,
            last_seen REAL NOT NULL DEFAULT 0,
            latitude REAL,
            longitude REAL,
            location_seen REAL NOT NULL DEFAULT 0,
            criado_em REAL NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS corridas_vai (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            passageiro_id INTEGER NOT NULL,
            partida TEXT NOT NULL,
            destino TEXT NOT NULL,
            valor REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'PENDENTE',
            motorista_id INTEGER,
            latitude_partida REAL,
            longitude_partida REAL,
            latitude_destino REAL,
            longitude_destino REAL,
            distancia_km REAL,
            preco_km REAL DEFAULT 1.20,
            taxa_admin_percent REAL DEFAULT 8.0,
            taxa_admin REAL DEFAULT 0,
            valor_motorista REAL DEFAULT 0,
            criada_em REAL NOT NULL,
            aceita_em REAL,
            iniciada_em REAL,
            concluida_em REAL,
            cancelada_em REAL
        )
    """)

    usuarios = [
        ("online", "INTEGER NOT NULL DEFAULT 0"),
        ("last_seen", "REAL NOT NULL DEFAULT 0"),
        ("latitude", "REAL"),
        ("longitude", "REAL"),
        ("location_seen", "REAL NOT NULL DEFAULT 0"),
        ("criado_em", "REAL NOT NULL DEFAULT 0"),
        ("bloqueado", "INTEGER NOT NULL DEFAULT 0"),
    ]
    for col, typ in usuarios:
        if not coluna_existe(con, "usuarios_vai", col):
            con.execute(f"ALTER TABLE usuarios_vai ADD COLUMN {col} {typ}")

    corridas = [
        ("motorista_id", "INTEGER"),
        ("latitude_partida", "REAL"),
        ("longitude_partida", "REAL"),
        ("latitude_destino", "REAL"),
        ("longitude_destino", "REAL"),
        ("distancia_km", "REAL"),
        ("preco_km", "REAL DEFAULT 1.20"),
        ("taxa_admin_percent", "REAL DEFAULT 8.0"),
        ("taxa_admin", "REAL DEFAULT 0"),
        ("valor_motorista", "REAL DEFAULT 0"),
        ("aceita_em", "REAL"),
        ("iniciada_em", "REAL"),
        ("concluida_em", "REAL"),
        ("cancelada_em", "REAL"),
    ]
    for col, typ in corridas:
        if not coluna_existe(con, "corridas_vai", col):
            con.execute(f"ALTER TABLE corridas_vai ADD COLUMN {col} {typ}")

    # Corridas antigas: conserva o valor e calcula a divisão administrativa.
    con.execute("""
        UPDATE corridas_vai
        SET preco_km=COALESCE(preco_km, 2.00),
            taxa_admin_percent=COALESCE(taxa_admin_percent, 8.0),
            taxa_admin=COALESCE(taxa_admin, ROUND(valor * 0.08, 2)),
            valor_motorista=COALESCE(valor_motorista, ROUND(valor * 0.92, 2))
    """)
    con.commit()
    con.close()
inicializar_banco()


def marcar_motoristas_expirados():
    limite = time.time() - ONLINE_TIMEOUT
    con = conectar()
    con.execute("""
        UPDATE usuarios_vai
        SET online=0, latitude=NULL, longitude=NULL, location_seen=0
        WHERE tipo='motorista' AND online=1 AND last_seen < ?
    """, (limite,))
    con.commit()
    con.close()


def expirar_corridas_antigas():
    """Remove chamadas antigas da fila de motoristas.

    Uma corrida PENDENTE que ficou mais de PENDENTE_TIMEOUT sem ser aceita
    vira CANCELADA para não continuar aparecendo como chamada disponível.
    """
    limite = time.time() - PENDENTE_TIMEOUT
    con = conectar()
    con.execute("""
        UPDATE corridas_vai
        SET status='CANCELADA', cancelada_em=?
        WHERE status='PENDENTE' AND criada_em < ?
    """, (time.time(), limite))
    con.commit()
    con.close()


def contar_motoristas_online():
    marcar_motoristas_expirados()
    expirar_corridas_antigas()
    con = conectar()
    n = con.execute("SELECT COUNT(*) total FROM usuarios_vai WHERE tipo='motorista' AND online=1").fetchone()["total"]
    con.close()
    return n


def usuario_logado():
    uid = session.get("usuario_id")
    if not uid:
        return None
    con = conectar()
    u = con.execute("SELECT * FROM usuarios_vai WHERE id=?", (uid,)).fetchone()
    con.close()
    return u


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def geocodificar(endereco):
    """Busca coordenadas do destino usando Nominatim/OpenStreetMap."""
    url = "https://nominatim.openstreetmap.org/search?format=jsonv2&limit=1&q=" + quote(endereco)
    req = Request(url, headers={"User-Agent": "VaiMoto/10.0 local-app"})
    with urlopen(req, timeout=8) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data:
        return None
    return float(data[0]["lat"]), float(data[0]["lon"]), data[0].get("display_name", endereco)


def calcular_rota_osrm(lat1, lon1, lat2, lon2):
    """Distancia por ruas usando OSRM. Retorna km ou None."""
    url = (
        "https://router.project-osrm.org/route/v1/driving/"
        f"{lon1},{lat1};{lon2},{lat2}?overview=false"
    )
    req = Request(url, headers={"User-Agent": "VaiMoto/10.0 local-app"})
    with urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("code") != "Ok" or not data.get("routes"):
        return None
    return float(data["routes"][0]["distance"]) / 1000.0


def rota_destino(lat1, lon1, destino):
    geo = geocodificar(destino)
    if not geo:
        return None
    lat2, lon2, nome = geo
    try:
        distancia = calcular_rota_osrm(lat1, lon1, lat2, lon2)
        fonte = "rota por ruas"
    except Exception:
        distancia = None
        fonte = "distância aproximada"
    if distancia is None:
        distancia = haversine_km(lat1, lon1, lat2, lon2)
    return {
        "latitude_destino": lat2,
        "longitude_destino": lon2,
        "destino_encontrado": nome,
        "distancia_km": max(0.01, distancia),
        "fonte_distancia": fonte,
    }


def calcular_valores(distancia_km):
    total = dinheiro(distancia_km * PRECO_KM)
    taxa = dinheiro(total * TAXA_ADMIN)
    motorista = dinheiro(total - taxa)
    return total, taxa, motorista


def sessao_tipo(tipo):
    u = usuario_logado()
    return u and u["tipo"] == tipo


# ============================================================
# CSS
# ============================================================
CSS = """
<style>
*{box-sizing:border-box}
body{margin:0;background:#050505;font-family:Arial,sans-serif;color:#fff}
.card{width:94%;max-width:760px;margin:18px auto;background:#111;border-radius:24px;padding:20px;box-shadow:0 8px 25px rgba(0,0,0,.5)}
h1{margin:0 0 8px;font-size:30px}
h2{font-size:23px;margin:12px 0}
p{font-size:17px}
input,select{width:100%;padding:15px;margin:6px 0 12px;border:2px solid #333;border-radius:14px;font-size:17px;background:#fff;color:#111}
button,.btn{display:block;width:100%;padding:15px;margin:9px 0;border:0;border-radius:14px;font-size:18px;text-align:center;text-decoration:none;cursor:pointer}
button,.btn.black{background:#000;color:#FFD000}
.green{background:#159447;color:#fff}
.black{background:#000;color:#FFD000}
.gray{background:#666;color:#fff}
.red{background:#d62828;color:#fff}
.blue{background:#FFD000;color:#000}
.orange{background:#e67e22;color:#fff}
.status{padding:15px;border-radius:14px;margin:12px 0;font-size:19px;font-weight:bold;text-align:center}
.online{background:#1b5e20;color:#fff}
.offline{background:#333;color:#fff}
.box,.ride{border:2px solid #333;border-radius:16px;padding:15px;margin:12px 0;background:#fff;color:#111}
.ride{border-width:2px}
.small{font-size:14px;color:#555}
.badge{display:inline-block;padding:5px 9px;border-radius:20px;background:#FFD000;color:#000;font-size:12px;font-weight:bold}
.row{display:flex;gap:8px}
.row>*{flex:1}
.price{font-size:28px;font-weight:bold;margin:8px 0}
.maplink{color:#FFD000;font-weight:bold;text-decoration:none}
.top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
.top .badge{margin-top:5px}
.money{background:#FFD000;color:#000;border:2px solid #000;border-radius:14px;padding:12px}
.warn{background:#FFD000;color:#000;border:2px solid #000;border-radius:14px;padding:12px}
a{color:#FFD000}
@media(max-width:520px){
.card{width:96%;padding:15px}
h1{font-size:26px}
.row{display:block}
}
</style>
"""


# ============================================================
# HOME / CADASTRO / LOGIN
# ============================================================
HOME = CSS + """
<div class="card">
  <h1>🏍️ VaiMoto V10</h1>
  <p>Solicite sua corrida ou trabalhe como motorista.</p>
  <div class="money"><b>💰 Tarifa:</b> R$ 1,20 por km<br><b>🏢 Taxa do app:</b> 8%</div>
  <a class="btn black" href="{{ url_for('login') }}">Entrar</a>
  <a class="btn green" href="{{ url_for('cadastro') }}">Criar cadastro</a>
</div>
"""

CADASTRO = CSS + """
<div class="card">
  <h1>📝 Cadastro</h1>
  {% if erro %}<div class="box" style="color:#b00020">❌ {{ erro }}</div>{% endif %}
  <form method="post">
    <input name="nome" placeholder="Nome completo" value="{{ dados.get('nome','') }}" autocomplete="name" required>
    <input name="whatsapp" placeholder="WhatsApp com DDD" value="{{ dados.get('whatsapp','') }}" inputmode="tel" autocomplete="tel" required>
    <input name="senha" type="password" placeholder="Senha (mínimo 4 caracteres)" minlength="4" required>
    <select name="tipo" required>
      <option value="passageiro" {% if dados.get('tipo')=='passageiro' %}selected{% endif %}>Passageiro</option>
      <option value="motorista" {% if dados.get('tipo')=='motorista' %}selected{% endif %}>Motoboy/Mototaxista</option>
    </select>
    <button class="green" type="submit">Cadastrar</button>
  </form>
  <a class="btn gray" href="{{ url_for('index') }}">Voltar</a>
</div>
"""

LOGIN = CSS + """
<div class="card">
  <h1>🔐 Entrar</h1>
  {% if erro %}<div class="box" style="color:#b00020">❌ {{ erro }}</div>{% endif %}
  <form method="post">
    <input name="whatsapp" placeholder="WhatsApp" inputmode="tel" required>
    <input name="senha" type="password" placeholder="Senha" required>
    <button class="black" type="submit">Entrar</button>
  </form>
  <a class="btn gray" href="{{ url_for('index') }}">Voltar</a>
</div>
"""


# ============================================================
# PASSAGEIRO
# ============================================================
PASSAGEIRO = CSS + """
<div class="card">
  <div class="top">
    <div><div class="small">🏍️ VAIMOTO</div><h1>Olá, {{ usuario['nome'] }} 👋</h1></div>
    <div class="badge">🟢 <span id="onlineCount">{{ online }}</span> online</div>
  </div>

  <div id="gpsStatus" class="status offline">📍 GPS aguardando</div>

  <div class="box gps">
    <b>📍 Origem / embarque</b>
    <p class="small">Use o GPS para pegar sua posição atual.</p>
    <button class="blue" type="button" onclick="capturarGPS()">📍 USAR MINHA LOCALIZAÇÃO</button>
    <div id="gpsText" class="small">Nenhuma localização capturada.</div>
  </div>

  <div class="box">
    <h2>🚕 Solicitar corrida</h2>
    <label><b>Origem</b></label>
    <input id="partida" placeholder="Origem / endereço de embarque" required>
    <label><b>Destino</b></label>
    <input id="destino" placeholder="Digite o endereço de destino" autocomplete="street-address" required>
    <input type="hidden" id="latitude_partida">
    <input type="hidden" id="longitude_partida">

    <button class="blue" id="btnCalcular" type="button" onclick="calcularCorrida()">🧮 CALCULAR VALOR</button>
    <div id="calculo" style="display:none" class="money">
      <div>📏 Distância: <b id="distancia">0,00 km</b></div>
      <div class="price" id="valor">R$ 0,00</div>
      <div>🏢 Taxa do app (8%): <b id="taxa">R$ 0,00</b></div>
      <div>🏍️ Motorista recebe (92%): <b id="motoristaValor">R$ 0,00</b></div>
      <div class="small" id="fonteRota"></div>
    </div>
    <label style="display:block;margin-top:15px"><b>💳 Forma de pagamento</b></label>
    <select id="formaPagamento" style="width:100%;padding:12px;border-radius:10px;font-size:16px">
      <option value="DINHEIRO">💵 Dinheiro — pagar ao motorista no final</option>
      <option value="PIX">📱 PIX — pagamento antecipado</option>
    </select>
    <div id="pagamentoAviso" class="small" style="margin-top:8px">
      💵 Você paga ao motorista no final da corrida.
    </div>
    <button class="green" id="btnSolicitar" type="button" onclick="solicitarCorrida()" disabled>🏍️ SOLICITAR CORRIDA</button>
    <div id="msgCorrida" class="small"></div>
  </div>

  <div class="box" id="chamadaBox">
    <h2>📲 Chamada do motorista</h2>
    <div id="chamadaStatus" class="status offline">Nenhuma corrida ativa.</div>
    <div id="motoristaAtivo" class="small">Depois que um motorista aceitar, o telefone e a localização aparecem aqui.</div>
  </div>

  <div class="box" id="mapBox" style="display:none">
    <b>🗺️ Acompanhar motorista</b>
    <div id="mapInfo" class="small"></div>
    <a id="mapMotorista" class="btn blue" target="_blank" rel="noopener">📍 VER MOTORISTA NO MAPA</a>
  </div>

  <h2>📋 Minhas corridas</h2>
  <div id="corridas"><div class="box">Carregando...</div></div>
  <a class="btn gray" href="{{ url_for('logout') }}">Sair</a>
</div>

<script>
let gpsLat=null,gpsLon=null,gpsWatch=null;
let calculoAtual=null;
let ultimaNotificacao={};
let audioLiberado=false;

function br(v){return Number(v||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function som(){
  if(!audioLiberado)return;
  try{const C=AudioContext||webkitAudioContext,ctx=new C(),o=ctx.createOscillator(),g=ctx.createGain();
  o.frequency.value=880;g.gain.value=.08;o.connect(g);g.connect(ctx.destination);o.start();
  setTimeout(()=>o.frequency.value=1175,130);setTimeout(()=>{o.stop();ctx.close()},320);}catch(e){}
}
function liberarAudio(){audioLiberado=true}
document.addEventListener('click',liberarAudio,{once:true});

function mostrarGPS(lat,lon,acc){
  gpsLat=lat;gpsLon=lon;
  document.getElementById('latitude_partida').value=lat;
  document.getElementById('longitude_partida').value=lon;
  document.getElementById('partida').value='Minha localização atual';
  document.getElementById('gpsStatus').className='status online';
  document.getElementById('gpsStatus').textContent='🟢 GPS ATIVO';
  document.getElementById('gpsText').textContent='📍 '+lat.toFixed(6)+', '+lon.toFixed(6)+' • precisão ~'+Math.round(acc||0)+' m';
  fetch('/api/passageiro/localizacao',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({latitude:lat,longitude:lon})}).catch(()=>{});
}
function iniciarGPS(){
  if(!navigator.geolocation){alert('Este navegador não oferece GPS.');return;}
  if(gpsWatch!==null)return;
  gpsWatch=navigator.geolocation.watchPosition(p=>mostrarGPS(p.coords.latitude,p.coords.longitude,p.coords.accuracy),e=>{
    document.getElementById('gpsStatus').className='status offline';
    document.getElementById('gpsStatus').textContent='⚠️ Permita a localização do navegador';
  },{enableHighAccuracy:true,maximumAge:5000,timeout:15000});
}
function capturarGPS(){
  if(!navigator.geolocation){alert('GPS não disponível.');return;}
  document.getElementById('gpsStatus').textContent='📍 Obtendo GPS...';
  navigator.geolocation.getCurrentPosition(p=>{mostrarGPS(p.coords.latitude,p.coords.longitude,p.coords.accuracy);iniciarGPS()},e=>alert('Não consegui acessar o GPS. Ative a localização e permita o acesso do navegador.'),{enableHighAccuracy:true,timeout:15000,maximumAge:0});
}

async function calcularCorrida(){
  const destino=document.getElementById('destino').value.trim();
  if(gpsLat===null||gpsLon===null){alert('Primeiro toque em USAR MINHA LOCALIZAÇÃO.');return;}
  if(!destino){alert('Digite o destino.');return;}
  const b=document.getElementById('btnCalcular');b.disabled=true;b.textContent='🧮 CALCULANDO ROTA...';
  try{
    const r=await fetch('/api/calcular-corrida',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({latitude_partida:gpsLat,longitude_partida:gpsLon,destino:destino})});
    const d=await r.json();
    if(!d.ok){alert(d.erro||'Não foi possível calcular a rota.');return;}
    calculoAtual=d;
    document.getElementById('distancia').textContent=Number(d.distancia_km).toFixed(2).replace('.',',')+' km';
    document.getElementById('valor').textContent=br(d.valor);
    document.getElementById('taxa').textContent=br(d.taxa_admin);
    document.getElementById('motoristaValor').textContent=br(d.valor_motorista);
    document.getElementById('fonteRota').textContent=d.fonte_distancia==='rota por ruas'?'🗺️ Valor calculado pela rota de carro/moto.':'📏 Valor aproximado pela distância GPS.';
    document.getElementById('calculo').style.display='block';
    document.getElementById('btnSolicitar').disabled=false;
    document.getElementById('msgCorrida').textContent='Destino localizado: '+d.destino_encontrado;
  }catch(e){alert('Falha ao calcular. Verifique sua internet e tente novamente.');}
  finally{b.disabled=false;b.textContent='🧮 CALCULAR VALOR';}
}

async function solicitarCorrida(){
  if(!calculoAtual)return;
  const partida=document.getElementById('partida').value.trim();
  const destino=document.getElementById('destino').value.trim();
  if(!partida||!destino){alert('Preencha origem e destino.');return;}
  const b=document.getElementById('btnSolicitar');b.disabled=true;b.textContent='📲 CHAMANDO MOTORISTAS...';
  try{
    const r=await fetch('/api/solicitar-corrida',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      partida,destino,latitude_partida:gpsLat,longitude_partida:gpsLon,
      latitude_destino:calculoAtual.latitude_destino,longitude_destino:calculoAtual.longitude_destino,
      distancia_km:calculoAtual.distancia_km,forma_pagamento:document.getElementById('formaPagamento').value,valor:calculoAtual.valor,
      taxa_admin:calculoAtual.taxa_admin,valor_motorista:calculoAtual.valor_motorista
    })});
    const d=await r.json();
    if(!d.ok){alert(d.erro||'Não foi possível solicitar.');return;}
    som();
    document.getElementById('msgCorrida').textContent='📲 Corrida #'+d.corrida.id+' enviada aos motoristas online.';
    calculoAtual=null;document.getElementById('btnSolicitar').disabled=true;
    carregarCorridas();
  }catch(e){alert('Falha de conexão com o servidor.');}
  finally{b.disabled=false;b.textContent='🏍️ SOLICITAR CORRIDA';}
}

function renderCorridas(lista){
  const area=document.getElementById('corridas');
  if(!lista.length){area.innerHTML='<div class="box">Você ainda não solicitou nenhuma corrida.</div>';return;}
  area.innerHTML='';
  lista.forEach(c=>{
    let s='';
    if(c.status==='PENDENTE')s='<div class="status online">🔎 CHAMANDO MOTORISTA...</div>';
    if(c.status==='ACEITA')s='<div class="status online">✅ MOTORISTA ACEITOU</div>';
    if(c.status==='EM_ANDAMENTO')s='<div class="status online">🏍️ CORRIDA EM ANDAMENTO</div>';
    if(c.status==='CONCLUIDA')s='<div class="status online">🏁 CORRIDA CONCLUÍDA</div>';
    if(c.status==='CANCELADA')s='<div class="status offline">❌ CORRIDA CANCELADA</div>';
    let motorista='';
    if(c.motorista_nome){motorista='<div class="box"><b>🏍️ '+esc(c.motorista_nome)+'</b><br>📞 '+esc(c.motorista_whatsapp||'')+'<a class="btn green" href="tel:'+esc(c.motorista_whatsapp||'')+'">📞 LIGAR PARA MOTORISTA</a>';
      if(c.motorista_lat!=null&&c.motorista_lon!=null){motorista+='<a class="btn blue" target="_blank" href="https://www.google.com/maps/search/?api=1&query='+c.motorista_lat+','+c.motorista_lon+'">📍 VER MOTORISTA NO MAPA</a>';}
      motorista+='</div>';
    }
    let cancel='';if(c.status==='PENDENTE'||c.status==='ACEITA')cancel='<button class="red" onclick="cancelarCorrida('+c.id+')">❌ CANCELAR CORRIDA</button>';
    const el=document.createElement('div');el.className='ride';
    el.innerHTML='<b>🚕 Corrida #'+c.id+'</b> <span class="badge">'+esc(c.status)+'</span><br>📍 '+esc(c.partida)+'<br>🏁 '+esc(c.destino)+'<br>📏 '+Number(c.distancia_km||0).toFixed(2).replace('.',',')+' km<br><div class="price">'+br(c.valor)+'</div><div class="small">🏢 App 8%: '+br(c.taxa_admin)+' • 🏍️ Motorista 92%: '+br(c.valor_motorista)+'</div>'+s+motorista+cancel;
    area.appendChild(el);
    const old=ultimaNotificacao[c.id];
    if(old && old!==c.status && c.status==='ACEITA'){som();if(navigator.vibrate)navigator.vibrate([200,100,300]);}
    ultimaNotificacao[c.id]=c.status;
    if(c.status==='ACEITA'||c.status==='EM_ANDAMENTO'){
      document.getElementById('chamadaStatus').className='status online';
      document.getElementById('chamadaStatus').textContent=c.status==='ACEITA'?'✅ MOTORISTA A CAMINHO':'🏍️ CORRIDA EM ANDAMENTO';
      document.getElementById('motoristaAtivo').textContent=c.motorista_nome?'Motorista: '+c.motorista_nome:'Motorista encontrado.';
      if(c.motorista_lat!=null&&c.motorista_lon!=null){document.getElementById('mapBox').style.display='block';document.getElementById('mapInfo').textContent='GPS do motorista: '+Number(c.motorista_lat).toFixed(6)+', '+Number(c.motorista_lon).toFixed(6);document.getElementById('mapMotorista').href='https://www.google.com/maps/search/?api=1&query='+c.motorista_lat+','+c.motorista_lon;}
    }
  });
}
async function carregarCorridas(){
  try{const r=await fetch('/api/minhas-corridas');if(!r.ok)return;const d=await r.json();renderCorridas(d.corridas||[]);}catch(e){}
}
async function cancelarCorrida(id){
  if(!confirm('Cancelar esta corrida?'))return;
  const r=await fetch('/api/corrida/'+id+'/cancelar',{method:'POST'});const d=await r.json();
  if(!d.ok){alert(d.erro||'Não foi possível cancelar.');return;}som();carregarCorridas();
}
async function atualizarOnline(){try{const r=await fetch('/api/motoristas-online');const d=await r.json();document.getElementById('onlineCount').textContent=d.online}catch(e){}}

iniciarGPS();carregarCorridas();atualizarOnline();
setInterval(carregarCorridas,{{ poll }});setInterval(atualizarOnline,5000);
</script>
"""


# ============================================================
# MOTORISTA
# ============================================================
MOTORISTA = CSS + """
<div class="card">
  <div class="top"><div><div class="small">🏍️ VAIMOTO</div><h1>{{ usuario['nome'] }}</h1></div><div class="badge">👤 MOTORISTA</div></div>
  <div id="onlineBox" class="status {% if usuario['online'] %}online{% else %}offline{% endif %}">{% if usuario['online'] %}🟢 MOTORISTA ONLINE{% else %}⚪ MOTORISTA OFFLINE{% endif %}</div>
  <button id="toggleBtn" class="{% if usuario['online'] %}red{% else %}green{% endif %}" onclick="alternarOnline()">{% if usuario['online'] %}FICAR OFFLINE{% else %}FICAR ONLINE{% endif %}</button>

  <div class="box gps"><b>📍 GPS do motorista</b><div id="gpsText" class="small">Ative o modo online para enviar sua localização.</div></div>

  <div class="money"><b>💰 Regra de ganhos</b><br>R$ 1,20/km • motorista recebe 92% • app fica com 8%</div>

  <h2>🔔 Corridas disponíveis</h2>
  <div id="corridas"><div class="box">Carregando...</div></div>

  <h2>🏍️ Minha corrida</h2>
  <div id="minhaCorrida"><div class="box">Nenhuma corrida aceita.</div></div>

  <p class="small">Deixe esta página aberta para continuar online. O GPS depende da permissão de localização do navegador.</p>
  <a class="btn gray" href="{{ url_for('logout') }}">Sair</a>
</div>
<script>
let online={{ 'true' if usuario['online'] else 'false' }};let gpsWatch=null;let ultimoId=null;let ultimoStatus=null;let audioLiberado=false;
function br(v){return Number(v||0).toLocaleString('pt-BR',{style:'currency',currency:'BRL'});}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function som(){if(!audioLiberado)return;try{const C=AudioContext||webkitAudioContext,ctx=new C(),o=ctx.createOscillator(),g=ctx.createGain();o.frequency.value=700;g.gain.value=.1;o.connect(g);g.connect(ctx.destination);o.start();setTimeout(()=>o.frequency.value=1100,150);setTimeout(()=>{o.stop();ctx.close()},450)}catch(e){}}
document.addEventListener('click',()=>{audioLiberado=true},{once:true});
function gps(){if(!online||!navigator.geolocation)return;if(gpsWatch!==null)return;gpsWatch=navigator.geolocation.watchPosition(p=>{document.getElementById('gpsText').textContent='🟢 GPS ativo: '+p.coords.latitude.toFixed(6)+', '+p.coords.longitude.toFixed(6)+' • precisão ~'+Math.round(p.coords.accuracy||0)+' m';fetch('/api/motorista/localizacao',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({latitude:p.coords.latitude,longitude:p.coords.longitude})}).catch(()=>{})},()=>{document.getElementById('gpsText').textContent='⚠️ Permita a localização do navegador.'},{enableHighAccuracy:true,maximumAge:5000,timeout:15000});}
function pararGPS(){if(gpsWatch!==null){navigator.geolocation.clearWatch(gpsWatch);gpsWatch=null;}document.getElementById('gpsText').textContent='GPS desligado.';}
async function alternarOnline(){const novo=!online;const b=document.getElementById('toggleBtn');b.disabled=true;try{const r=await fetch('/api/motorista/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({online:novo})});const d=await r.json();if(!d.ok){alert(d.erro||'Erro');return;}online=!!d.online;const box=document.getElementById('onlineBox');box.className='status '+(online?'online':'offline');box.textContent=online?'🟢 MOTORISTA ONLINE':'⚪ MOTORISTA OFFLINE';b.className=online?'red':'green';b.textContent=online?'FICAR OFFLINE':'FICAR ONLINE';if(online)gps();else pararGPS();carregarCorridas();}catch(e){alert('Falha de conexão.')}finally{b.disabled=false;}}
async function carregarCorridas(){
  if(!online){document.getElementById('corridas').innerHTML='<div class="box">Fique online para receber chamadas.</div>';return;}
  try{const r=await fetch('/api/corridas-disponiveis');if(!r.ok)return;const d=await r.json();const area=document.getElementById('corridas');
    if(!d.corridas.length){area.innerHTML='<div class="box">Nenhuma corrida pendente no momento.</div>';return;}
    area.innerHTML='';d.corridas.forEach(c=>{if(ultimoId!==c.id){som();if(navigator.vibrate)navigator.vibrate([300,150,300]);ultimoId=c.id;}
      const el=document.createElement('div');el.className='ride';el.innerHTML='<b>🚕 Corrida #'+c.id+'</b><br>📍 '+esc(c.partida)+'<br>🏁 '+esc(c.destino)+'<br>📏 '+Number(c.distancia_km||0).toFixed(2).replace('.',',')+' km<div class="price">'+br(c.valor)+'</div><div class="small">🏢 App 8%: '+br(c.taxa_admin)+' • 🏍️ Você recebe: '+br(c.valor_motorista)+'</div><a class="btn blue" target="_blank" href="https://www.google.com/maps/search/?api=1&query='+ (c.latitude_partida!=null&&c.longitude_partida!=null?c.latitude_partida+','+c.longitude_partida:encodeURIComponent(c.partida)) +'">📍 IR PARA EMBARQUE</a><button class="green" onclick="aceitar('+c.id+')">🏍️ ACEITAR CORRIDA</button>';area.appendChild(el);});
  }catch(e){}}
async function aceitar(id){const r=await fetch('/api/corrida/'+id+'/aceitar',{method:'POST'});const d=await r.json();if(!d.ok){alert(d.erro||'Essa corrida já foi aceita.');carregarCorridas();return;}som();carregarCorridas();carregarMinhaCorrida();}
async function carregarMinhaCorrida(){try{const r=await fetch('/api/minha-corrida-motorista');if(!r.ok)return;const d=await r.json();const c=d.corrida;const area=document.getElementById('minhaCorrida');if(!c){area.innerHTML='<div class="box">Nenhuma corrida aceita.</div>';return;}if(ultimoStatus&&ultimoStatus!==c.status)som();ultimoStatus=c.status;
 let botoes='';if(c.status==='ACEITA')botoes+='<button class="green" onclick="iniciar('+c.id+')">🏍️ INICIAR CORRIDA</button>';if(c.status==='EM_ANDAMENTO')botoes+='<button class="black" onclick="concluir('+c.id+')">✅ CONCLUIR CORRIDA</button>';
 area.innerHTML='<div class="ride"><b>🚕 Corrida #'+c.id+'</b><span class="badge">'+esc(c.status)+'</span><br>👤 '+esc(c.passageiro_nome)+'<br>📞 '+esc(c.passageiro_whatsapp)+'<br>📍 '+esc(c.partida)+'<br>🏁 '+esc(c.destino)+'<br>📏 '+Number(c.distancia_km||0).toFixed(2).replace('.',',')+' km<div class="price">'+br(c.valor)+'</div><div class="money">🏍️ Seu ganho: <b>'+br(c.valor_motorista)+'</b><br>🏢 Taxa do app: '+br(c.taxa_admin)+'</div><a class="btn blue" target="_blank" href="https://www.google.com/maps/search/?api=1&query='+(c.latitude_partida!=null&&c.longitude_partida!=null?c.latitude_partida+','+c.longitude_partida:encodeURIComponent(c.partida))+'">📍 ABRIR EMBARQUE</a><a class="btn orange" href="tel:'+esc(c.passageiro_whatsapp)+'">📞 LIGAR PARA PASSAGEIRO</a>'+botoes+'</div>';
 }catch(e){}}
async function iniciar(id){const r=await fetch('/api/corrida/'+id+'/iniciar',{method:'POST'});const d=await r.json();if(!d.ok){alert(d.erro||'Não foi possível iniciar.');return;}som();carregarMinhaCorrida();carregarCorridas();}
async function concluir(id){if(!confirm('Concluir esta corrida?'))return;const r=await fetch('/api/corrida/'+id+'/concluir',{method:'POST'});const d=await r.json();if(!d.ok){alert(d.erro||'Não foi possível concluir.');return;}som();carregarMinhaCorrida();carregarCorridas();}
if(online)gps();carregarCorridas();carregarMinhaCorrida();setInterval(()=>{if(online){gps();carregarCorridas();carregarMinhaCorrida()}},{{ poll }});
</script>
"""


# ============================================================
# ROTAS BÁSICAS
# ============================================================
@app.route("/")
def index():
    return render_template_string(HOME)


@app.route("/cadastro", methods=["GET", "POST"])
def cadastro():
    dados={"nome":"","whatsapp":"","tipo":"passageiro"}
    erro=""
    if request.method=="POST":
        dados["nome"]=request.form.get("nome","").strip()
        dados["whatsapp"]=re.sub(r"\D","",request.form.get("whatsapp","").strip())
        dados["tipo"]=request.form.get("tipo","passageiro").strip()
        senha=request.form.get("senha","")
        if len(dados["nome"])<2: erro="Digite seu nome completo."
        elif len(dados["whatsapp"])<10: erro="Digite um WhatsApp válido com DDD."
        elif len(senha)<4: erro="A senha precisa ter pelo menos 4 caracteres."
        elif dados["tipo"] not in ("passageiro","motorista"): erro="Tipo de conta inválido."
        else:
            con=conectar()
            try:
                if con.execute("SELECT id FROM usuarios_vai WHERE whatsapp=?",(dados["whatsapp"],)).fetchone():
                    erro="Esse WhatsApp já está cadastrado."
                else:
                    cur=con.execute("""
                        INSERT INTO usuarios_vai(nome,whatsapp,senha,tipo,online,last_seen,latitude,longitude,location_seen,criado_em)
                        VALUES(?,?,?,?,0,0,NULL,NULL,0,?)
                    """,(dados["nome"],dados["whatsapp"],generate_password_hash(senha),dados["tipo"],time.time()))
                    con.commit();session["usuario_id"]=cur.lastrowid
                    return redirect(url_for("motorista" if dados["tipo"]=="motorista" else "passageiro"))
            except sqlite3.IntegrityError: con.rollback();erro="Esse WhatsApp já está cadastrado."
            finally: con.close()
    return render_template_string(CADASTRO,dados=dados,erro=erro)


@app.route("/login", methods=["GET","POST"])
def login():
    erro=""
    if request.method=="POST":
        whatsapp=re.sub(r"\D","",request.form.get("whatsapp","").strip())
        senha=request.form.get("senha","")
        con=conectar();u=con.execute("SELECT * FROM usuarios_vai WHERE whatsapp=?",(whatsapp,)).fetchone();con.close()
        if u and check_password_hash(u["senha"],senha):
            session["usuario_id"]=u["id"];return redirect(url_for("motorista" if u["tipo"]=="motorista" else "passageiro"))
        erro="WhatsApp ou senha incorretos."
    return render_template_string(LOGIN,erro=erro)


@app.route("/logout")
def logout():
    uid=session.get("usuario_id")
    if uid:
        con=conectar();con.execute("UPDATE usuarios_vai SET online=0,last_seen=0,latitude=NULL,longitude=NULL,location_seen=0 WHERE id=? AND tipo='motorista'",(uid,));con.commit();con.close()
    session.clear();return redirect(url_for("index"))


# ============================================================
# PASSAGEIRO
# ============================================================
@app.route("/passageiro")
def passageiro():
    u=usuario_logado()
    if not u:return redirect(url_for("login"))
    if u["tipo"]!="passageiro":return "Acesso permitido somente para passageiros.",403
    return render_template_string(PASSAGEIRO,usuario=u,online=contar_motoristas_online(),poll=POLL_PASSAGEIRO)


@app.route("/api/calcular-corrida", methods=["POST"])
def api_calcular_corrida():
    u=usuario_logado()
    if not u or u["tipo"]!="passageiro":return jsonify(ok=False,erro="Faça login como passageiro."),401
    d=request.get_json(silent=True) or {}
    try:
        lat=float(d.get("latitude_partida"));lon=float(d.get("longitude_partida"))
        if not (-90<=lat<=90 and -180<=lon<=180):raise ValueError
    except (TypeError,ValueError):return jsonify(ok=False,erro="GPS da origem inválido. Ative sua localização."),400
    destino=str(d.get("destino","")).strip()
    if len(destino)<3:return jsonify(ok=False,erro="Digite um destino válido."),400
    try:
        rota=rota_destino(lat,lon,destino)
    except Exception:
        return jsonify(ok=False,erro="Não consegui localizar o destino. Confira o endereço e sua internet."),400
    if not rota:return jsonify(ok=False,erro="Destino não encontrado. Digite endereço completo, com bairro/cidade."),400
    total,taxa,mot=calcular_valores(rota["distancia_km"])
    rota.update(ok=True,valor=total,taxa_admin=taxa,valor_motorista=mot,preco_km=PRECO_KM,taxa_admin_percent=TAXA_ADMIN*100)
    return jsonify(rota)


@app.route("/api/solicitar-corrida", methods=["POST"])
def api_solicitar_corrida():
    u=usuario_logado()
    if not u or u["tipo"]!="passageiro":return jsonify(ok=False,erro="Faça login como passageiro."),401
    d=request.get_json(silent=True) or {}
    try:
        lat=float(d.get("latitude_partida"));lon=float(d.get("longitude_partida"));lat2=float(d.get("latitude_destino"));lon2=float(d.get("longitude_destino"));dist=float(d.get("distancia_km"));valor=float(d.get("valor"))
    except (TypeError,ValueError):return jsonify(ok=False,erro="Dados da corrida inválidos."),400
    if not(-90<=lat<=90 and -180<=lon<=180 and -90<=lat2<=90 and -180<=lon2<=180):return jsonify(ok=False,erro="Coordenadas inválidas."),400
    if dist<=0 or valor<=0:return jsonify(ok=False,erro="Distância ou valor inválido."),400
    partida=str(d.get("partida","")).strip();destino=str(d.get("destino","")).strip()
    if not partida or not destino:return jsonify(ok=False,erro="Origem e destino são obrigatórios."),400
    # Recalcula no servidor para o passageiro não poder alterar a tarifa pelo navegador.
    total,taxa,mot=calcular_valores(dist)
    con=conectar()
    try:
        # Evita duas corridas simultâneas do mesmo passageiro.
        ativa=con.execute("SELECT id FROM corridas_vai WHERE passageiro_id=? AND status IN ('PENDENTE','ACEITA','EM_ANDAMENTO') LIMIT 1",(u["id"],)).fetchone()
        if ativa:return jsonify(ok=False,erro="Você já possui uma corrida ativa."),409
        cur=con.execute("""
INSERT INTO corridas_vai(
passageiro_id,partida,destino,valor,status,motorista_id,
latitude_partida,longitude_partida,latitude_destino,longitude_destino,
distancia_km,preco_km,taxa_admin_percent,taxa_admin,valor_motorista,criada_em
)
VALUES(?,?,?,?,'PENDENTE',NULL,?,?,?,?,?,?,?,?,?,?)
""",(
u["id"],partida,destino,total,
lat,lon,lat2,lon2,dist,PRECO_KM,
TAXA_ADMIN*100,taxa,mot,time.time()
))
        
        con.commit()
        rid=cur.lastrowid
        row=con.execute("SELECT * FROM corridas_vai WHERE id=?",(rid,)).fetchone()
        return jsonify(ok=True,corrida=dict(row))
        
    finally:
        con.close()
 


@app.route("/api/passageiro/localizacao",methods=["POST"])
def api_passageiro_localizacao():
    u=usuario_logado()
    if not u or u["tipo"]!="passageiro":return jsonify(ok=False,erro="Acesso negado."),401
    d=request.get_json(silent=True) or {}
    try:lat=float(d.get("latitude"));lon=float(d.get("longitude"));assert -90<=lat<=90 and -180<=lon<=180
    except (TypeError,ValueError,AssertionError):return jsonify(ok=False,erro="Coordenadas inválidas."),400
    con=conectar();row=con.execute("SELECT id FROM corridas_vai WHERE passageiro_id=? AND status IN ('PENDENTE','ACEITA','EM_ANDAMENTO') ORDER BY id DESC LIMIT 1",(u["id"],)).fetchone()
    if row:con.execute("UPDATE corridas_vai SET latitude_partida=?,longitude_partida=? WHERE id=?",(lat,lon,row["id"]));con.commit()
    con.close();return jsonify(ok=True,latitude=lat,longitude=lon)


@app.route("/api/minhas-corridas")
def api_minhas_corridas():
    u=usuario_logado()
    if not u:return jsonify(corridas=[]),401
    con=conectar();rows=con.execute("""
      SELECT c.*,m.nome motorista_nome,m.whatsapp motorista_whatsapp,m.latitude motorista_lat,m.longitude motorista_lon,m.location_seen motorista_location_seen
      FROM corridas_vai c LEFT JOIN usuarios_vai m ON m.id=c.motorista_id
      WHERE c.passageiro_id=? ORDER BY c.id DESC
    """,(u["id"],)).fetchall();con.close()
    out=[]
    for r in rows:
        d=dict(r)
        if d.get("motorista_location_seen") and time.time()-d["motorista_location_seen"]>LOCATION_TIMEOUT:d["motorista_lat"]=None;d["motorista_lon"]=None
        out.append(d)
    return jsonify(corridas=out)


@app.route("/api/corrida/<int:corrida_id>/cancelar",methods=["POST"])
def cancelar_corrida(corrida_id):
    u=usuario_logado()
    if not u:return jsonify(ok=False,erro="Faça login novamente."),401
    con=conectar();cur=con.execute("UPDATE corridas_vai SET status='CANCELADA',cancelada_em=? WHERE id=? AND passageiro_id=? AND status IN ('PENDENTE','ACEITA')",(time.time(),corrida_id,u["id"]));con.commit();con.close()
    return jsonify(ok=cur.rowcount==1,erro=None if cur.rowcount==1 else "Esta corrida não pode mais ser cancelada.")


# ============================================================
# MOTORISTA
# ============================================================
@app.route("/motorista")
def motorista():
    u=usuario_logado()
    if not u:return redirect(url_for("login"))
    if u["tipo"]!="motorista":return "Acesso permitido somente para motoristas.",403
    marcar_motoristas_expirados();u=usuario_logado()
    return render_template_string(MOTORISTA,usuario=u,poll=POLL_MOTORISTA)


@app.route("/api/motorista/status",methods=["POST"])
def api_motorista_status():
    u=usuario_logado()
    if not u or u["tipo"]!="motorista":return jsonify(ok=False,erro="Acesso negado."),401
    d=request.get_json(silent=True) or {};online=1 if d.get("online") else 0
    con=conectar();now=time.time()
    if online:con.execute("UPDATE usuarios_vai SET online=1,last_seen=? WHERE id=?",(now,u["id"]))
    else:con.execute("UPDATE usuarios_vai SET online=0,last_seen=0,latitude=NULL,longitude=NULL,location_seen=0 WHERE id=?",(u["id"],))
    con.commit();con.close();return jsonify(ok=True,online=bool(online),online_total=contar_motoristas_online())


@app.route("/api/motorista/heartbeat",methods=["POST"])
def api_motorista_heartbeat():
    u=usuario_logado()
    if not u or u["tipo"]!="motorista":return jsonify(ok=False),401
    con=conectar();con.execute("UPDATE usuarios_vai SET last_seen=? WHERE id=? AND online=1",(time.time(),u["id"]));con.commit();con.close();return jsonify(ok=True)


@app.route("/api/motorista/localizacao",methods=["POST"])
def api_motorista_localizacao():
    u=usuario_logado()
    if not u or u["tipo"]!="motorista":return jsonify(ok=False,erro="Acesso negado."),401
    d=request.get_json(silent=True) or {}
    try:lat=float(d.get("latitude"));lon=float(d.get("longitude"));assert -90<=lat<=90 and -180<=lon<=180
    except (TypeError,ValueError,AssertionError):return jsonify(ok=False,erro="Coordenadas inválidas."),400
    now=time.time();con=conectar();r=con.execute("UPDATE usuarios_vai SET latitude=?,longitude=?,location_seen=?,last_seen=? WHERE id=? AND online=1",(lat,lon,now,now,u["id"]));con.commit();con.close()
    return jsonify(ok=r.rowcount==1,latitude=lat,longitude=lon)


@app.route("/api/motoristas-online")
def api_motoristas_online():return jsonify(online=contar_motoristas_online())


@app.route("/api/corridas-disponiveis")
def api_corridas_disponiveis():
    u=usuario_logado()
    if not u or u["tipo"]!="motorista":return jsonify(corridas=[]),401
    marcar_motoristas_expirados();expirar_corridas_antigas();con=conectar();rows=con.execute("""
      SELECT c.id,c.partida,c.destino,c.valor,c.latitude_partida,c.longitude_partida,c.latitude_destino,c.longitude_destino,c.distancia_km,c.taxa_admin,c.valor_motorista,c.criada_em
      FROM corridas_vai c WHERE c.status='PENDENTE' ORDER BY c.id ASC
    """).fetchall();con.close();return jsonify(corridas=[dict(r) for r in rows])


@app.route("/api/minha-corrida-motorista")
def api_minha_corrida_motorista():
    u=usuario_logado()
    if not u or u["tipo"]!="motorista":return jsonify(corrida=None),401
    con=conectar();r=con.execute("""
      SELECT c.*,p.nome passageiro_nome,p.whatsapp passageiro_whatsapp
      FROM corridas_vai c JOIN usuarios_vai p ON p.id=c.passageiro_id
      WHERE c.motorista_id=? AND c.status IN ('ACEITA','EM_ANDAMENTO') ORDER BY c.id DESC LIMIT 1
    """,(u["id"],)).fetchone();con.close();return jsonify(corrida=dict(r) if r else None)


@app.route("/api/corrida/<int:corrida_id>/aceitar",methods=["POST"])
def aceitar_corrida(corrida_id):
    u=usuario_logado()
    if not u or u["tipo"]!="motorista":return jsonify(ok=False,erro="Acesso negado."),401
    marcar_motoristas_expirados();con=conectar();mot=con.execute("SELECT online FROM usuarios_vai WHERE id=? AND tipo='motorista'",(u["id"],)).fetchone()
    if not mot or not mot["online"]:con.close();return jsonify(ok=False,erro="Você precisa estar online para aceitar."),400
    # Um motorista não pega duas corridas ao mesmo tempo.
    ativa=con.execute("SELECT id FROM corridas_vai WHERE motorista_id=? AND status IN ('ACEITA','EM_ANDAMENTO') LIMIT 1",(u["id"],)).fetchone()
    if ativa:con.close();return jsonify(ok=False,erro="Você já possui uma corrida ativa."),409
    cur=con.execute("UPDATE corridas_vai SET status='ACEITA',motorista_id=?,aceita_em=? WHERE id=? AND status='PENDENTE' AND motorista_id IS NULL",(u["id"],time.time(),corrida_id))
    if cur.rowcount!=1:con.rollback();con.close();return jsonify(ok=False,erro="Essa corrida já foi aceita por outro motorista."),409
    con.commit();r=con.execute("SELECT * FROM corridas_vai WHERE id=?",(corrida_id,)).fetchone();con.close();return jsonify(ok=True,corrida=dict(r))


@app.route("/api/corrida/<int:corrida_id>/iniciar",methods=["POST"])
def iniciar_corrida(corrida_id):
    u=usuario_logado()
    if not u or u["tipo"]!="motorista":return jsonify(ok=False,erro="Acesso negado."),401
    con=conectar();cur=con.execute("UPDATE corridas_vai SET status='EM_ANDAMENTO',iniciada_em=? WHERE id=? AND motorista_id=? AND status='ACEITA'",(time.time(),corrida_id,u["id"]));con.commit();con.close();return jsonify(ok=cur.rowcount==1,erro=None if cur.rowcount==1 else "A corrida não está disponível para iniciar.")


@app.route("/api/corrida/<int:corrida_id>/concluir",methods=["POST"])
def concluir_corrida(corrida_id):
    u=usuario_logado()
    if not u or u["tipo"]!="motorista":return jsonify(ok=False,erro="Acesso negado."),401
    con=conectar();cur=con.execute("UPDATE corridas_vai SET status='CONCLUIDA',concluida_em=? WHERE id=? AND motorista_id=? AND status='EM_ANDAMENTO'",(time.time(),corrida_id,u["id"]));con.commit();con.close();return jsonify(ok=cur.rowcount==1,erro=None if cur.rowcount==1 else "A corrida não está em andamento.")


# ============================================================
# ADMIN - resumo financeiro das corridas
# ============================================================
@app.route("/admin")
def admin():
    if request.args.get("key")!=ADMIN_KEY:return "Acesso negado.",403
    con=conectar();tot=con.execute("""
      SELECT COUNT(*) corridas,
             COALESCE(SUM(valor),0) faturamento,
             COALESCE(SUM(taxa_admin),0) taxa_admin,
             COALESCE(SUM(valor_motorista),0) motorista
      FROM corridas_vai WHERE status='CONCLUIDA'
    """).fetchone();ult=con.execute("""
      SELECT c.id,c.partida,c.destino,c.valor,c.taxa_admin,c.valor_motorista,c.status,c.distancia_km,c.criada_em,p.nome passageiro_nome,m.nome motorista_nome
      FROM corridas_vai c JOIN usuarios_vai p ON p.id=c.passageiro_id LEFT JOIN usuarios_vai m ON m.id=c.motorista_id
      ORDER BY c.id DESC LIMIT 50
    """).fetchall();con.close()
    html=CSS+"""
    <div class="card"><h1>📊 VaiMoto Admin</h1>
    <a class="btn black" href="{{ url_for("motoqueiros",key=request.args.get("key")) }}">🏍️ MOTOQUEIROS</a>
    <div class="row"><div class="money"><b>Corridas concluídas</b><div class="price">{{ t['corridas'] }}</div></div><div class="money"><b>Faturamento</b><div class="price">R$ {{ '%.2f'|format(t['faturamento']) }}</div></div></div>
    <div class="row"><div class="money"><b>🏢 Taxas do app (8%)</b><div class="price">R$ {{ '%.2f'|format(t['taxa_admin']) }}</div></div><div class="money"><b>🏍️ Motoristas (92%)</b><div class="price">R$ {{ '%.2f'|format(t['motorista']) }}</div></div></div>
    <h2>Últimas corridas</h2>{% for c in ult %}<div class="ride"><b>#{{c['id']}}</b> {{c['status']}}<br>{{c['passageiro_nome']}} → {{c['motorista_nome'] or 'sem motorista'}}<br>{{c['partida']}} → {{c['destino']}}<br>R$ {{'%.2f'|format(c['valor'])}} • App R$ {{'%.2f'|format(c['taxa_admin'] or 0)}} • Motorista R$ {{'%.2f'|format(c['valor_motorista'] or 0)}}</div>{% else %}<div class="box">Nenhuma corrida.</div>{% endfor %}</div>
    """
    return render_template_string(html,t=tot,ult=ult)



@app.route("/motoqueiros")
def motoqueiros():
    if request.args.get("key") != ADMIN_KEY:
        return "Acesso negado.",403
    con=conectar()
    motos=con.execute("SELECT id,nome,whatsapp,online,last_seen,latitude,longitude,criado_em,bloqueado FROM usuarios_vai WHERE tipo=\"motorista\" ORDER BY id DESC").fetchall()
    con.close()
    html=CSS+"""
    <div class="card">
      <h1>🏍️ Motoqueiros</h1>
      <a class="btn black" href="{{ url_for(\"admin\",key=request.args.get(\"key\")) }}">⬅️ Voltar ao Admin</a>
      {% for m in motos %}
      <div class="ride">
        <b>{{ m[\"nome\"] }}</b><br>
        📱 {{ m[\"whatsapp\"] }}<br>
        {% if m[\"online\"] %}🟢 ONLINE{% else %}⚪ OFFLINE{% endif %}
        {% if m["bloqueado"] %}🔴 BLOQUEADO{% endif %}<br><br><form method="post" action="{{ url_for("bloquear_motoqueiro",mid=m["id"],key=request.args.get("key")) }}" style="display:inline"><button class="red" type="submit">{% if m["bloqueado"] %}DESBLOQUEAR{% else %}BLOQUEAR{% endif %}</button></form> <form method="post" action="{{ url_for("excluir_motoqueiro",mid=m["id"],key=request.args.get("key")) }}" style="display:inline" onsubmit="return confirm("Excluir este motorista?")"><button class="black" type="submit">EXCLUIR</button></form>
      </div>
      {% else %}
      <div class="box">Nenhum motorista cadastrado.</div>
      {% endfor %}
    </div>
    """
    return render_template_string(html,motos=motos)


@app.route("/motoqueiros/bloquear/<int:mid>",methods=["POST"])
def bloquear_motoqueiro(mid):
    if request.args.get("key") != ADMIN_KEY:return "Acesso negado.",403
    con=conectar()
    m=con.execute("SELECT bloqueado FROM usuarios_vai WHERE id=? AND tipo=\"motorista\"",(mid,)).fetchone()
    if not m: con.close();return "Motorista não encontrado.",404
    novo=0 if m["bloqueado"] else 1
    con.execute("UPDATE usuarios_vai SET bloqueado=?,online=0,last_seen=0,latitude=NULL,longitude=NULL,location_seen=0 WHERE id=? AND tipo=\"motorista\"",(novo,mid))
    con.commit();con.close()
    return redirect(url_for("motoqueiros",key=ADMIN_KEY))

@app.route("/motoqueiros/excluir/<int:mid>",methods=["POST"])
def excluir_motoqueiro(mid):
    if request.args.get("key") != ADMIN_KEY:return "Acesso negado.",403
    con=conectar()
    con.execute("DELETE FROM usuarios_vai WHERE id=? AND tipo=\"motorista\"",(mid,))
    con.commit();con.close()
    return redirect(url_for("motoqueiros",key=ADMIN_KEY))

# ============================================================
# ERROS
# ============================================================
@app.errorhandler(404)
def not_found(e):return "Página não encontrada.",404


if __name__=="__main__":
    inicializar_banco()
    expirar_corridas_antigas()
    print("="*60)
    print("🏍️ VAIMOTO V10 INICIADO")
    print("="*60)
    print(f"Banco: {DB}")
    print("Local: http://127.0.0.1:5000")
    print("Rede:  http://0.0.0.0:5000")
    print("Tarifa: R$ 1,20/km")
    print("Taxa admin: 8% | Motorista: 92%")
    print("IMPORTANTE: use http:// no iPhone/Android, não https://")
    print("="*60)
    app.run(host="0.0.0.0",port=5000,debug=False)

# VAI_DE_MOTO - versao oficial com taxa administrativa de 8%

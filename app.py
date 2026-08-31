TAXA_APP = 0.09
# -*- coding: utf-8 -*-
import os, sqlite3, time, math, json
from functools import wraps
from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
from urllib.parse import quote
from urllib.request import Request, urlopen

DB = "vaimoto.db"
PRECO_KM = 2.00
TAXA = 9.0
MOTORISTA = 91.0
MAX_MOTORISTAS = 20
ONLINE_TIMEOUT = 35

app = Flask(__name__)
app.secret_key = os.environ.get("VAIMOTO_SECRET", "vaimoto-local-2026")

def db():
    c = sqlite3.connect(DB, timeout=15)
    c.row_factory = sqlite3.Row
    return c

def ensure_col(c, table, col, definition):
    cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
    if col not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")

def init():
    c = db()
    c.execute("""CREATE TABLE IF NOT EXISTS usuarios_vai(
        id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL,
        whatsapp TEXT UNIQUE NOT NULL, senha TEXT NOT NULL,
        tipo TEXT NOT NULL, aprovado INTEGER DEFAULT 1,
        online INTEGER DEFAULT 0, latitude REAL, longitude REAL,
        last_seen REAL, criado_em REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS corridas_vai(
        id INTEGER PRIMARY KEY AUTOINCREMENT, passageiro_id INTEGER,
        partida TEXT, destino TEXT, valor REAL, status TEXT DEFAULT 'PENDENTE',
        motorista_id INTEGER, latitude_partida REAL, longitude_partida REAL,
        latitude_destino REAL, longitude_destino REAL, distancia_km REAL,
        preco_km REAL, taxa_admin_percent REAL, taxa_admin REAL,
        valor_motorista REAL, pagamento TEXT DEFAULT 'DINHEIRO',
        pagamento_status TEXT DEFAULT 'NAO_APLICAVEL',
        criada_em REAL, aceita_em REAL, iniciada_em REAL,
        concluida_em REAL, cancelada_em REAL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS saques_vai(
        id INTEGER PRIMARY KEY AUTOINCREMENT, motorista_id INTEGER,
        valor REAL, chave_pix TEXT, status TEXT DEFAULT 'PENDENTE',
        criado_em REAL, pago_em REAL)""")
    # Migração segura da base antiga
    ensure_col(c, "corridas_vai", "pagamento_status", "TEXT DEFAULT 'NAO_APLICAVEL'")
    ensure_col(c, "corridas_vai", "latitude_destino", "REAL")
    ensure_col(c, "corridas_vai", "longitude_destino", "REAL")
    contas = [
        ("Administrador","62993903299","1234","admin",1),
        ("Ricardo","62999999999","1234","motorista",1),
        ("Rodrigo","62988888888","1234","passageiro",1),
    ]
    for n,w,s,t,a in contas:
        if not c.execute("SELECT id FROM usuarios_vai WHERE whatsapp=?", (w,)).fetchone():
            c.execute("""INSERT INTO usuarios_vai
                (nome,whatsapp,senha,tipo,aprovado,criado_em)
                VALUES(?,?,?,?,?,?)""", (n,w,s,t,a,time.time()))
    c.commit(); c.close()

def user():
    uid = session.get("uid")
    if not uid: return None
    c=db(); r=c.execute("SELECT * FROM usuarios_vai WHERE id=?", (uid,)).fetchone(); c.close()
    return r

def need(tipo=None):
    def deco(fn):
        @wraps(fn)
        def wrap(*a, **kw):
            # ADMIN ESPECIAL
            if session.get("tipo") == "admin" and session.get("uid") == "ADMIN":
                if tipo in (None, "admin"):
                    return fn(*a, **kw)

            u = user()

            if not u:
                if request.path.startswith("/api/"):
                    return jsonify(ok=False, erro="Faça login primeiro."), 401
                return redirect(url_for("login"))

            if tipo and u["tipo"] != tipo:
                if request.path.startswith("/api/"):
                    return jsonify(ok=False, erro="Acesso negado."), 403
                return redirect(url_for("login"))

            return fn(*a, **kw)

        return wrap
    return deco


def geocode(q, lat_origem=None, lon_origem=None):
    import requests

    q = str(q).strip()

    if not q:
        return None

    # Aceita latitude,longitude
    try:
        partes = [x.strip() for x in q.replace(";", ",").split(",")]
        lat = float(partes[0])
        lon = float(partes[1])

        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon, q
    except Exception:
        pass

    # Procura endereço usando Nominatim/OpenStreetMap
    try:
        resposta = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": q + ", Brasil",
                "format": "jsonv2",
                "limit": 1,
                "countrycodes": "br"
            },
            headers={
                "User-Agent": "VAI_DE_MOTO/1.0"
            },
            timeout=10
        )

        resposta.raise_for_status()
        dados = resposta.json()

        if dados:
            return (
                float(dados[0]["lat"]),
                float(dados[0]["lon"]),
                dados[0].get("display_name", q)
            )

    except Exception as e:
        print("Erro na geocodificação:", e)

    return None
def hav(a,b,c,d):
    R=6371.0088
    p1,p2=math.radians(a),math.radians(c)
    dp=math.radians(c-a); dl=math.radians(d-b)
    x=math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R*2*math.atan2(math.sqrt(x),math.sqrt(1-x))

def vals(km):
    km = max(0.0, float(km))
    total = round(km * PRECO_KM, 2)
    taxa = round(total * TAXA_APP, 2)
    mot = round(total - taxa, 2)
    return total, taxa, mot
CSS="""<style>
*{box-sizing:border-box}body{margin:0;background:#050505;color:#fff;font-family:Arial,sans-serif}
.wrap{max-width:1050px;width:94%;margin:auto;padding:20px 0 50px}
.head{padding:18px 4%;border-bottom:3px solid #ffd400;background:#111}
.logo{font-size:32px;font-weight:900;color:#ffd400}.sub,.muted{color:#aaa}
.card,.box,.ride{background:#121212;border:1px solid #333;border-radius:22px;padding:20px;margin:16px 0}
.card.light{background:#fff;color:#111}.grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}
.big{font-size:30px;color:#ffd400;font-weight:900}.pill{background:#222;border-radius:99px;padding:9px 14px}
.status{padding:15px;border-radius:14px;text-align:center;font-weight:900;margin:10px 0}.on{background:#073d1b;color:#32ed72}
.off{background:#401010;color:#ff5555}.pend{background:#493d00;color:#ffd400}.acc{background:#073b5d;color:#58bdff}.done{background:#124725;color:#72ee9b}
input,select{width:100%;padding:15px;margin:7px 0 14px;border-radius:14px;border:1px solid #555;background:#0b0b0b;color:#fff;font-size:17px}
.light input,.light select{background:#fff;color:#111;border-color:#bbb}
button,.btn{display:block;width:100%;padding:15px;margin:8px 0;border:0;border-radius:14px;font-size:17px;font-weight:800;text-align:center;text-decoration:none;cursor:pointer}
.yellow{background:#ffd400;color:#000}.green{background:#16a34a;color:#fff}.blue{background:#1976d2;color:#fff}.red{background:#dc2626;color:#fff}.gray{background:#444;color:#fff}
.top{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}
@media(max-width:700px){.grid,.grid3{grid-template-columns:1fr}.logo{font-size:28px}}
a{color:inherit}h1,h2,h3{margin-top:0}
</style>"""

LOGIN=CSS+"""<div class="wrap"><div class="card" style="max-width:500px;margin:50px auto">
<div class="logo">🏍️ VAI<span style="color:#fff">MOTO</span></div><h2>🔐 Entrar</h2>
{% if erro %}<div class="status off">{{erro}}</div>{% endif %}
<form method="post"><input name="whatsapp" placeholder="WhatsApp" required>
<input name="senha" type="password" placeholder="Senha" required><button class="yellow">ENTRAR</button></form>
<a class="btn green" href="/cadastro">📝 CRIAR CONTA</a><a class="btn gray" href="/">VOLTAR</a></div></div>"""

CAD=CSS+"""<div class="wrap"><div class="card" style="max-width:600px;margin:30px auto">
<div class="logo">🏍️ VAI<span style="color:#fff">MOTO</span></div><h2>📝 Cadastro</h2>
{% if erro %}<div class="status off">{{erro}}</div>{% endif %}
<form method="post"><input name="nome" placeholder="Nome" required><input name="whatsapp" placeholder="WhatsApp" required>
<input name="senha" type="password" placeholder="Senha" required>
<select name="tipo"><option value="passageiro">Passageiro</option><option value="motorista">Motorista</option></select>
<button class="yellow">CADASTRAR</button></form><a class="btn gray" href="/login">VOLTAR</a></div></div>"""

PASS=CSS+"""<div class="head"><div class="wrap" style="padding:0"><div class="top">
<div><div class="logo">🏍️ VAI<span style="color:#fff">MOTO</span></div><div class="sub">Área do passageiro</div></div>
<div class="pill">🟢 <b id="on">{{online}}</b> online</div></div></div></div>
<div class="wrap"><div class="card"><h1>Olá, {{u['nome']}} 👋</h1>
<div id="gps" class="status off">📍 GPS aguardando</div>
<button class="blue" onclick="gps()">📍 USAR MINHA LOCALIZAÇÃO</button><div id="coord" class="box">Nenhuma localização capturada.</div></div>
<div class="card light"><h2>🏍️ Solicitar corrida</h2>
<input id="partida" placeholder="Origem / endereço de embarque">
<input id="destino" placeholder="Digite rua, número, bairro e cidade">
<button id="calc" class="blue" onclick="calcular()">🧮 CALCULAR VALOR</button>
<div id="resultado" class="box" style="display:none"><div id="dist"></div><div id="valor" style="font-size:28px;color:#b07b00;font-weight:900"></div><div id="taxa"></div><div id="mot"></div></div>
<h2>💳 Forma de pagamento</h2>
<select id="pagamento" onchange="pagUI()"><option value="DINHEIRO">💵 Dinheiro</option><option value="PIX">📱 PIX</option></select>
<div id="pagAviso" class="box">💵 Você paga ao motorista no final da corrida.</div>
<button id="sol" class="green" onclick="solicitar()" disabled>🏍️ SOLICITAR CORRIDA</button><div id="msg"></div></div>
<div class="card"><h2>📋 Minhas corridas</h2><div id="corridas">Carregando...</div></div></div>
<script>
let lat=null,lon=null,calc=null;
const $=id=>document.getElementById(id);
const br=v=>'R$ '+Number(v||0).toFixed(2).replace('.',',');
function esc(x){return String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))}
function gps(){
 if(!navigator.geolocation)return alert('GPS não disponível.');
 $('gps').textContent='📍 Obtendo localização...';
 navigator.geolocation.getCurrentPosition(p=>{
   lat=p.coords.latitude;lon=p.coords.longitude;$('gps').className='status on';$('gps').textContent='🟢 GPS ativo';
   $('coord').textContent='Latitude: '+lat.toFixed(6)+' | Longitude: '+lon.toFixed(6);
   $('partida').value='Minha localização ('+lat.toFixed(6)+', '+lon.toFixed(6)+')';
   fetch('/api/passageiro/localizacao',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({latitude:lat,longitude:lon})});
 },()=>{ $('gps').className='status off';$('gps').textContent='❌ Permita o GPS';alert('Permita a localização no navegador.')},
 {enableHighAccuracy:true,timeout:15000,maximumAge:5000});
}
function pagUI(){
 const f=$('pagamento').value;
 $('pagAviso').textContent=f==='DINHEIRO'?'💵 Você paga ao motorista no final da corrida. A corrida será liberada imediatamente.':'📱 PIX antecipado. A corrida ficará aguardando confirmação do pagamento antes de aparecer para os motoristas.';
}
async function calcular(){
 if(lat===null)return alert('Primeiro toque em USAR MINHA LOCALIZAÇÃO.');
 let destino=$('destino').value.trim();if(!destino)return alert('Digite o destino.');
 $('calc').disabled=true;$('calc').textContent='🧮 CALCULANDO...';
 try{
   let r=await fetch('/api/calcular-corrida',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({latitude_partida:lat,longitude_partida:lon,destino})});
   let d=await r.json();if(!d.ok)return alert(d.erro||'Não foi possível calcular.');
   calc=d;$('dist').textContent='📏 '+Number(d.distancia_km).toFixed(2).replace('.',',')+' km';
   $('valor').textContent=br(d.valor);$('taxa').textContent='🏢 App 9%: '+br(d.taxa_admin);
   $('mot').textContent='🏍️ Motorista 91%: '+br(d.valor_motorista)+'<br>📍 '+esc(d.destino_encontrado||destino);
   $('resultado').style.display='block';$('sol').disabled=false;
 }catch(e){alert('Erro ao calcular. Verifique a internet.')}
 finally{$('calc').disabled=false;$('calc').textContent='🧮 CALCULAR VALOR';}
}
async function solicitar(){
 if(!calc)return;let partida=$('partida').value.trim(),destino=$('destino').value.trim();
 if(!partida||!destino)return alert('Preencha origem e destino.');
 $('sol').disabled=true;$('sol').textContent='📡 PROCESSANDO...';
 try{
   let r=await fetch('/api/solicitar-corrida',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
     partida,destino,latitude_partida:lat,longitude_partida:lon,latitude_destino:calc.latitude_destino,
     longitude_destino:calc.longitude_destino,distancia_km:calc.distancia_km,forma_pagamento:$('pagamento').value
   })});
   let d=await r.json();if(!d.ok)return alert(d.erro||'Não foi possível solicitar.');
   $('msg').textContent=d.pagamento_status==='AGUARDANDO_PAGAMENTO'?'📱 Corrida #'+d.corrida.id+' criada. Aguardando confirmação do PIX.':'✅ Corrida #'+d.corrida.id+' enviada aos motoristas. Pagamento em dinheiro no final.';
   calc=null;carregar();
 }catch(e){alert('Falha de conexão com o servidor.')}
 finally{$('sol').disabled=true;$('sol').textContent='🏍️ SOLICITAR CORRIDA';}
}
function st(s,p){
 if(p==='AGUARDANDO_PAGAMENTO')return '<div class="status pend">📱 AGUARDANDO PAGAMENTO PIX</div>';
 if(s==='PENDENTE')return '<div class="status pend">🔎 CHAMANDO MOTORISTA...</div>';
 if(s==='ACEITA')return '<div class="status acc">✅ MOTORISTA A CAMINHO</div>';
 if(s==='EM_ANDAMENTO')return '<div class="status acc">🏍️ CORRIDA EM ANDAMENTO</div>';
 if(s==='CONCLUIDA')return '<div class="status done">🏁 CONCLUÍDA</div>';
 return '<div class="status off">❌ CANCELADA</div>';
}
async function carregar(){
 try{
  let d=await(await fetch('/api/minhas-corridas')).json();
  $('corridas').innerHTML=(d.corridas||[]).map(c=>'<div class="ride"><b>🏍️ Corrida #'+c.id+'</b>'+st(c.status,c.pagamento_status)+
  '📍 '+esc(c.partida)+'<br>🏁 '+esc(c.destino)+'<br>📏 '+Number(c.distancia_km||0).toFixed(2)+' km<br><b>'+br(c.valor)+'</b><br>💳 '+esc(c.pagamento)+
  (c.motorista_nome?'<br>🏍️ '+esc(c.motorista_nome)+' | 📞 '+esc(c.motorista_whatsapp):'')+
  ((c.status==='PENDENTE'||c.status==='ACEITA')?'<button class="red" onclick="cancelar('+c.id+')">❌ CANCELAR CORRIDA</button>':'')+'</div>').join('')||'<div class="box">Nenhuma corrida.</div>';
 }catch(e){}
}
async function cancelar(id){if(!confirm('Cancelar corrida?'))return;await fetch('/api/corrida/'+id+'/cancelar',{method:'POST'});carregar();}
async function online(){try{$('on').textContent=(await(await fetch('/api/motoristas-online')).json()).online}catch(e){}}
pagUI();carregar();online();setInterval(carregar,3000);setInterval(online,5000);
</script>"""

MOT=CSS+"""<div class="head"><div class="wrap" style="padding:0"><div class="top">
<div><div class="logo">🏍️ VAI<span style="color:#fff">MOTO</span></div><div class="sub">Área do motorista</div></div>
<a class="btn gray" style="width:auto" href="/logout">SAIR</a></div></div></div>
<div class="wrap"><div class="card"><h2>🏍️ STATUS</h2><div id="status" class="status off">🔴 OFFLINE</div>
<button id="tog" class="yellow" onclick="toggle()">🟢 FICAR ONLINE</button></div>
<div class="grid"><div class="box">💰 Ganhos<div id="gan" class="big">R$ 0,00</div></div><div class="box">🏁 Corridas<div id="q" class="big">0</div></div>
<div class="box">💵 Saldo<div id="saldo" class="big">R$ 0,00</div></div><div class="box">📊 Sua parte<div class="big">91%</div></div></div>
<div class="card"><h2>📍 GPS</h2><div id="gps" class="status off">🔴 GPS aguardando</div><div id="co" class="muted">Fique online para enviar sua localização.</div></div>
<div class="card"><h2>🏍️ CORRIDAS DISPONÍVEIS</h2><div id="disp">Fique online para receber chamadas.</div></div>
<div class="card"><h2>📋 MINHAS CORRIDAS</h2><div id="mine">Carregando...</div></div>
<div class="card"><h2>💸 SOLICITAR SAQUE</h2><input id="sv" type="number" step="0.01" placeholder="Valor"><input id="pix" placeholder="Chave PIX">
<button class="green" onclick="saque()">💸 SOLICITAR SAQUE</button></div></div>
<script>
let on=false,watch=null;
const $=id=>document.getElementById(id);const br=v=>'R$ '+Number(v||0).toFixed(2).replace('.',',');
function esc(x){return String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))}
function seton(v){on=!!v;$('status').className='status '+(on?'on':'off');$('status').textContent=on?'🟢 ONLINE':'🔴 OFFLINE';$('tog').textContent=on?'🔴 FICAR OFFLINE':'🟢 FICAR ONLINE';}
async function toggle(){
 let d=await(await fetch('/api/motorista/status',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({online:!on})})).json();
 if(!d.ok)return alert(d.erro);seton(d.online);if(on)gps();
}
function gps(){
 if(!navigator.geolocation)return alert('GPS não disponível.');
 if(watch!==null)return;
 watch=navigator.geolocation.watchPosition(p=>{
   let la=p.coords.latitude,lo=p.coords.longitude;$('gps').className='status on';$('gps').textContent='🟢 GPS ATIVO';
   $('co').textContent='Latitude: '+la.toFixed(6)+' | Longitude: '+lo.toFixed(6);
   fetch('/api/motorista/heartbeat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({latitude:la,longitude:lo})});
 },()=>{}, {enableHighAccuracy:true,timeout:15000,maximumAge:5000});
}
function btn(c){
 if(c.status==='PENDENTE')return '<button class="green" onclick="aceitar('+c.id+')">✅ ACEITAR CORRIDA</button>';
 if(c.status==='ACEITA')return '<button class="blue" onclick="iniciar('+c.id+')">🏍️ INICIAR CORRIDA</button>';
 if(c.status==='EM_ANDAMENTO')return '<button class="green" onclick="concluir('+c.id+')">🏁 CONCLUIR CORRIDA</button>';
 return '';
}
async function disponiveis(){
 try{let d=await(await fetch('/api/corridas-disponiveis')).json();
 if(!on)return $('disp').innerHTML='<div class="box">🔴 Fique online.</div>';
 $('disp').innerHTML=(d.corridas||[]).map(c=>'<div class="ride"><b>🚕 #'+c.id+'</b><br>📍 '+esc(c.partida)+'<br>🏁 '+esc(c.destino)+
 '<br>📏 '+Number(c.distancia_km).toFixed(2)+' km<br><b class="big">'+br(c.valor)+'</b><br>🏢 App 9% | 🏍️ Você recebe '+br(c.valor_motorista)+
 '<br>💳 '+esc(c.pagamento)+'<a class="btn blue" target="_blank" href="https://www.google.com/maps/search/?api=1&query='+encodeURIComponent(c.latitude_partida+','+c.longitude_partida)+'">📍 IR PARA EMBARQUE</a>'+btn(c)+'</div>').join('')||'<div class="box">Nenhuma corrida pendente.</div>';
 }catch(e){}
}
async function minhas(){try{let d=await(await fetch('/api/motorista/minhas-corridas')).json();$('mine').innerHTML=(d.corridas||[]).map(c=>'<div class="ride"><b>#'+c.id+' '+c.status+'</b><br>'+esc(c.partida)+' → '+esc(c.destino)+'<br>💳 '+esc(c.pagamento)+'<br>🏍️ Seu ganho: '+br(c.valor_motorista)+btn(c)+'</div>').join('')||'Nenhuma.'}catch(e){}}
async function ganhos(){try{let d=await(await fetch('/api/motorista/ganhos')).json();$('gan').textContent=br(d.ganhos_total);$('q').textContent=d.corridas_concluidas;$('saldo').textContent=br(d.saldo_disponivel)}catch(e){}}
async function aceitar(id){let d=await(await fetch('/api/corrida/'+id+'/aceitar',{method:'POST'})).json();if(!d.ok)alert(d.erro);disponiveis();minhas();}
async function iniciar(id){let d=await(await fetch('/api/corrida/'+id+'/iniciar',{method:'POST'})).json();if(!d.ok)alert(d.erro);minhas();}
async function concluir(id){if(!confirm('Confirmar fim da corrida e recebimento do pagamento?'))return;let d=await(await fetch('/api/corrida/'+id+'/concluir',{method:'POST'})).json();if(!d.ok)alert(d.erro);minhas();ganhos();}
async function saque(){let valor=parseFloat($('sv').value),pix=$('pix').value.trim();if(!valor||!pix)return alert('Informe valor e PIX');let d=await(await fetch('/api/motorista/saque',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({valor,chave_pix:pix})})).json();alert(d.ok?'Saque solicitado #'+d.saque.id:d.erro);ganhos();}
fetch('/api/motorista/me').then(r=>r.json()).then(d=>{if(d.ok)seton(d.motorista.online==1)});
disponiveis();minhas();ganhos();setInterval(disponiveis,3000);setInterval(minhas,3000);setInterval(ganhos,5000);
</script>"""

ADMIN=CSS+"""<div class="head"><div class="wrap" style="padding:0"><div class="top"><div><div class="logo">🏍️ VAI<span style="color:#fff">MOTO</span></div><div class="sub">Painel administrativo</div></div><a class="btn gray" style="width:auto" href="/logout">SAIR</a></div></div></div>
<div class="wrap"><div class="grid3"><div class="box">🏍️ Motoristas<div id="m" class="big">0</div></div><div class="box">🟢 Online<div id="o" class="big">0</div></div><div class="box">🚕 Corridas<div id="c" class="big">0</div></div></div>
<div class="card"><h2>👨‍✈️ Motoristas</h2><div id="mot">...</div></div>
<div class="card"><h2>🚕 Corridas</h2><div id="rides">...</div></div>
<div class="card"><h2>💸 Saques</h2><div id="saques">...</div></div></div>
<script>
function esc(x){return String(x??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[m]))}
async function load(){
 let md=await(await fetch('/api/admin/motoristas')).json();$('m').textContent=md.motoristas.length;$('o').textContent=md.motoristas.filter(x=>x.online).length;
 $('mot').innerHTML=md.motoristas.map(x=>'<div class="ride"><b>🏍️ '+esc(x.nome)+'</b><br>📞 '+esc(x.whatsapp)+'<br>'+(x.aprovado?'✅ APROVADO':'⏳ PENDENTE')+' | '+(x.online?'🟢 ONLINE':'🔴 OFFLINE')+
 '<button class="green" onclick="aprovar('+x.id+',1)">✅ APROVAR</button><button class="red" onclick="aprovar('+x.id+',0)">⛔ BLOQUEAR</button><button class="gray" onclick="excluir('+x.id+')">🗑️ EXCLUIR</button></div>').join('')||'Nenhum';
 let cd=await(await fetch('/api/admin/corridas')).json();$('c').textContent=cd.corridas.length;
 $('rides').innerHTML=cd.corridas.map(x=>'<div class="ride"><b>#'+x.id+' '+x.status+'</b><br>👤 '+esc(x.passageiro_nome)+'<br>🏍️ '+esc(x.motorista_nome||'Aguardando')+'<br>'+esc(x.partida)+' → '+esc(x.destino)+
 '<br>R$ '+Number(x.valor).toFixed(2)+' | 💳 '+esc(x.pagamento)+' | '+esc(x.pagamento_status)+
 (x.pagamento==='PIX'&&x.pagamento_status==='AGUARDANDO_PAGAMENTO'?'<button class="green" onclick="pixPago('+x.id+')">✅ CONFIRMAR PIX</button>':'')+'</div>').join('')||'Nenhuma';
 let sd=await(await fetch('/api/admin/saques')).json();$('saques').innerHTML=sd.saques.map(x=>'<div class="ride"><b>#'+x.id+' '+esc(x.motorista_nome)+'</b><br>R$ '+Number(x.valor).toFixed(2)+' | PIX: '+esc(x.chave_pix)+'<br>'+x.status+(x.status==='PENDENTE'?'<button class="green" onclick="pagar('+x.id+')">✅ MARCAR COMO PAGO</button>':'')+'</div>').join('')||'Nenhum';
}
async function aprovar(id,a){let d=await(await fetch('/api/admin/motorista/'+id+'/aprovar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({aprovado:a})})).json();if(!d.ok)alert(d.erro);load();}
async function excluir(id){if(confirm('Excluir motorista?')){await fetch('/api/admin/motorista/'+id+'/excluir',{method:'POST'});load();}}
async function pixPago(id){let d=await(await fetch('/api/admin/corrida/'+id+'/confirmar-pix',{method:'POST'})).json();if(!d.ok)alert(d.erro);load();}
async function pagar(id){if(confirm('Confirmar pagamento do saque?')){let d=await(await fetch('/api/admin/saque/'+id+'/pagar',{method:'POST'})).json();if(!d.ok)alert(d.erro);load();}}
load();setInterval(load,5000);
</script>"""

def online_count():
    try:
        c = db()
        r = c.execute(
            "SELECT COUNT(*) FROM usuarios_vai WHERE tipo=? AND online=1",
            ("motorista",)
        ).fetchone()
        c.close()
        return int(r[0] or 0)
    except Exception:
        return 0

@app.route("/")
def index():
    u=user()
    if not u:
        return render_template_string(CSS+"""<div class="wrap"><div class="card" style="margin-top:70px;text-align:center">
<div class="logo">🏍️ VAI<span style="color:#fff">MOTO</span></div><h1>Transporte de moto local</h1>
<p class="sub">R$ 2,00/km • App 9% • Motorista 91%</p><a class="btn yellow" href="/login">🔐 ENTRAR</a><a class="btn green" href="/cadastro">📝 CADASTRAR</a></div></div>""")
    return redirect("/admin" if u["tipo"]=="admin" else "/motorista" if u["tipo"]=="motorista" else "/passageiro")

@app.route("/login",methods=["GET","POST"])
def login():
    erro=None
    if request.method=="POST":
        c=db();u=c.execute("SELECT * FROM usuarios_vai WHERE whatsapp=? AND senha=?",(request.form.get("whatsapp","").strip(),request.form.get("senha",""))).fetchone();c.close()
        if not u: erro="WhatsApp ou senha incorretos."
        elif u["tipo"]=="motorista" and not u["aprovado"]: erro="Motorista ainda não aprovado."
        else: session["uid"]=u["id"]; return redirect("/")
    return render_template_string(LOGIN,erro=erro)



@app.route("/cadastro",methods=["GET","POST"])
def cadastro():
    erro=None
    if request.method=="POST":
        n=request.form.get("nome","").strip();w=request.form.get("whatsapp","").strip();s=request.form.get("senha","");t=request.form.get("tipo","passageiro")
        if not n or not w or not s: erro="Preencha todos os campos."
        else:
            try:
                c=db()
                if t=="motorista" and c.execute("SELECT COUNT(*) n FROM usuarios_vai WHERE tipo='motorista'").fetchone()["n"]>=MAX_MOTORISTAS:
                    c.close(); erro="Limite de 20 motoristas atingido."
                else:
                    a=0 if t=="motorista" else 1
                    c.execute("INSERT INTO usuarios_vai(nome,whatsapp,senha,tipo,aprovado,criado_em) VALUES(?,?,?,?,?,?)",(n,w,s,t,a,time.time()));c.commit();c.close();return redirect("/login")
            except sqlite3.IntegrityError: erro="WhatsApp já cadastrado."
    return render_template_string(CAD,erro=erro)

@app.route("/logout")
def logout():
    u=user()
    if u and u["tipo"]=="motorista":
        c=db();c.execute("UPDATE usuarios_vai SET online=0 WHERE id=?",(u["id"],));c.commit();c.close()
    session.clear();return redirect("/")

@app.route("/passageiro")
@need("passageiro")
def passageiro(): return render_template_string(PASS,u=user(),online=online_count())

@app.route("/motorista")
@need("motorista")
def motorista(): return render_template_string(MOT,u=user())

@app.route("/admin")
@need("admin")
def admin(): return render_template_string(ADMIN)

@app.route("/api/motoristas-online")
def api_online(): return jsonify(online=online_count())

@app.route("/api/passageiro/localizacao",methods=["POST"])
@need("passageiro")
def ploc():
    d=request.get_json() or {};c=db()
    c.execute("UPDATE usuarios_vai SET latitude=?,longitude=?,last_seen=? WHERE id=?",(d.get("latitude"),d.get("longitude"),time.time(),user()["id"]))
    c.commit();c.close();return jsonify(ok=True)

@app.route("/api/calcular-corrida",methods=["POST"])
@need("passageiro")
def calcular():
    d=request.get_json() or {}; dest=str(d.get("destino","")).strip()
    try: la=float(d["latitude_partida"]);lo=float(d["longitude_partida"])
    except: return jsonify(ok=False,erro="GPS de origem inválido."),400
    if not dest:return jsonify(ok=False,erro="Digite o destino."),400
    geo=geocode(dest,la,lo)
    if not geo:return jsonify(ok=False,erro="Endereço não encontrado. Digite rua, número, bairro e cidade."),400
    la2,lo2,encontrado=geo;km=max(.1,hav(la,lo,la2,lo2));total,taxa,mot=vals(km)
    return jsonify(ok=True,latitude_destino=la2,longitude_destino=lo2,destino_encontrado=encontrado,
        distancia_km=round(km,2),valor=total,taxa_admin=taxa,valor_motorista=mot,fonte_distancia="GPS + geocodificação")

@app.route("/api/solicitar-corrida",methods=["POST"])
@need("passageiro")
def solicitar():
    u=user();d=request.get_json() or {}
    try: la=float(d["latitude_partida"]);lo=float(d["longitude_partida"]);la2=float(d["latitude_destino"]);lo2=float(d["longitude_destino"]);km=float(d["distancia_km"])
    except: return jsonify(ok=False,erro="Dados da corrida inválidos."),400
    partida=str(d.get("partida","")).strip();destino=str(d.get("destino","")).strip()
    if not partida or not destino:return jsonify(ok=False,erro="Origem e destino obrigatórios."),400
    forma=str(d.get("forma_pagamento","DINHEIRO")).upper()
    if forma not in ("DINHEIRO","PIX"):forma="DINHEIRO"
    total,taxa,mot=vals(km);pag_status="NAO_APLICAVEL" if forma=="DINHEIRO" else "AGUARDANDO_PAGAMENTO"
    status="PENDENTE" if forma=="DINHEIRO" else "AGUARDANDO_PAGAMENTO"
    c=db()
    if c.execute("SELECT id FROM corridas_vai WHERE passageiro_id=? AND status IN ('PENDENTE','ACEITA','EM_ANDAMENTO','AGUARDANDO_PAGAMENTO')",(u["id"],)).fetchone():
        c.close();return jsonify(ok=False,erro="Você já possui uma corrida ativa."),409
    cur=c.execute("""INSERT INTO corridas_vai
      (passageiro_id,partida,destino,valor,status,motorista_id,latitude_partida,longitude_partida,
       latitude_destino,longitude_destino,distancia_km,preco_km,taxa_admin_percent,taxa_admin,
       valor_motorista,pagamento,pagamento_status,criada_em)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
      (u["id"],partida,destino,total,status,None,la,lo,la2,lo2,km,PRECO_KM,TAXA,taxa,mot,forma,pag_status,time.time()))
    rid=cur.lastrowid;c.commit();r=c.execute("SELECT * FROM corridas_vai WHERE id=?",(rid,)).fetchone();c.close()
    return jsonify(ok=True,corrida=dict(r),pagamento_status=pag_status)

@app.route("/api/minhas-corridas")
@need("passageiro")
def minhas_p():
    c=db();r=c.execute("""SELECT x.*,m.nome motorista_nome,m.whatsapp motorista_whatsapp
      FROM corridas_vai x LEFT JOIN usuarios_vai m ON m.id=x.motorista_id
      WHERE x.passageiro_id=? ORDER BY x.id DESC LIMIT 30""",(user()["id"],)).fetchall();c.close()
    return jsonify(corridas=[dict(x) for x in r])

@app.route("/api/corrida/<int:i>/cancelar",methods=["POST"])
@need("passageiro")
def cancelar(i):
    c=db();cur=c.execute("""UPDATE corridas_vai SET status='CANCELADA',cancelada_em=?
      WHERE id=? AND passageiro_id=? AND status IN ('PENDENTE','ACEITA','AGUARDANDO_PAGAMENTO')""",(time.time(),i,user()["id"]))
    c.commit();c.close();return jsonify(ok=cur.rowcount==1)

@app.route("/api/motorista/me")
@need("motorista")
def mme(): return jsonify(ok=True,motorista=dict(user()))

@app.route("/api/motorista/status",methods=["POST"])
@need("motorista")
def mstatus():
    d=request.get_json() or {};on=1 if d.get("online") else 0;u=user()
    if on and not u["aprovado"]:return jsonify(ok=False,erro="Motorista não aprovado."),403
    c=db();c.execute("UPDATE usuarios_vai SET online=?,last_seen=? WHERE id=?",(on,time.time(),u["id"]));c.commit();c.close()
    return jsonify(ok=True,online=on)

@app.route("/api/motorista/heartbeat",methods=["POST"])
@need("motorista")
def hb():
    d=request.get_json() or {};c=db();c.execute("""UPDATE usuarios_vai SET latitude=?,longitude=?,last_seen=?
      WHERE id=? AND online=1""",(d.get("latitude"),d.get("longitude"),time.time(),user()["id"]));c.commit();c.close();return jsonify(ok=True)

@app.route("/api/corridas-disponiveis")
@need("motorista")
def disponiveis():
    c=db();r=c.execute("""SELECT id,partida,destino,valor,latitude_partida,longitude_partida,
      latitude_destino,longitude_destino,distancia_km,taxa_admin_percent,valor_motorista,pagamento
      FROM corridas_vai WHERE status='PENDENTE' AND pagamento_status IN ('NAO_APLICAVEL','PAGO') ORDER BY id""").fetchall();c.close()
    return jsonify(corridas=[dict(x) for x in r])

@app.route("/api/corrida/<int:i>/aceitar",methods=["POST"])
@need("motorista")
def aceitar(i):
    u=user()
    if not u["aprovado"] or not u["online"]:return jsonify(ok=False,erro="Fique ONLINE para aceitar."),403
    c=db()
    # Somente uma corrida pode ser aceita pelo motorista por vez
    if c.execute("SELECT id FROM corridas_vai WHERE motorista_id=? AND status IN ('ACEITA','EM_ANDAMENTO')",(u["id"],)).fetchone():
        c.close();return jsonify(ok=False,erro="Você já está em uma corrida ativa."),409
    cur=c.execute("""UPDATE corridas_vai SET status='ACEITA',motorista_id=?,aceita_em=?
      WHERE id=? AND status='PENDENTE'""",(u["id"],time.time(),i))
    c.commit();c.close();return jsonify(ok=cur.rowcount==1,erro=None if cur.rowcount else "Corrida já aceita por outro motorista.")

@app.route("/api/corrida/<int:i>/iniciar",methods=["POST"])
@need("motorista")
def iniciar(i):
    c=db();cur=c.execute("""UPDATE corridas_vai SET status='EM_ANDAMENTO',iniciada_em=?
      WHERE id=? AND motorista_id=? AND status='ACEITA'""",(time.time(),i,user()["id"]));c.commit();c.close()
    return jsonify(ok=cur.rowcount==1)

@app.route("/api/corrida/<int:i>/concluir",methods=["POST"])
@need("motorista")
def concluir(i):
    c=db();cur=c.execute("""UPDATE corridas_vai SET status='CONCLUIDA',concluida_em=?
      WHERE id=? AND motorista_id=? AND status='EM_ANDAMENTO'""",(time.time(),i,user()["id"]));c.commit();c.close()
    return jsonify(ok=cur.rowcount==1)

@app.route("/api/motorista/minhas-corridas")
@need("motorista")
def minhas_m():
    c=db();r=c.execute("SELECT * FROM corridas_vai WHERE motorista_id=? ORDER BY id DESC LIMIT 50",(user()["id"],)).fetchall();c.close()
    return jsonify(corridas=[dict(x) for x in r])

@app.route("/api/motorista/ganhos")
@need("motorista")
def ganhos():
    c=db();g=c.execute("""SELECT COALESCE(SUM(valor_motorista),0) total FROM corridas_vai
      WHERE motorista_id=? AND status='CONCLUIDA'""",(user()["id"],)).fetchone()["total"]
    q=c.execute("""SELECT COUNT(*) q FROM corridas_vai WHERE motorista_id=? AND status='CONCLUIDA'""",(user()["id"],)).fetchone()["q"]
    s=c.execute("""SELECT COALESCE(SUM(valor),0) x FROM saques_vai
      WHERE motorista_id=? AND status IN ('PENDENTE','APROVADO','PAGO')""",(user()["id"],)).fetchone()["x"]
    c.close();return jsonify(ganhos_total=round(g,2),corridas_concluidas=q,saldo_disponivel=round(max(0,g-s),2))

@app.route("/api/motorista/saque",methods=["POST"])
@need("motorista")
def saque():
    d=request.get_json() or {}
    try:v=round(float(d.get("valor",0)),2)
    except:v=0
    pix=str(d.get("chave_pix","")).strip();c=db()
    g=c.execute("SELECT COALESCE(SUM(valor_motorista),0) x FROM corridas_vai WHERE motorista_id=? AND status='CONCLUIDA'",(user()["id"],)).fetchone()["x"]
    s=c.execute("SELECT COALESCE(SUM(valor),0) x FROM saques_vai WHERE motorista_id=? AND status IN ('PENDENTE','APROVADO','PAGO')",(user()["id"],)).fetchone()["x"]
    saldo=g-s
    if v<=0 or not pix or v>saldo:
        c.close();return jsonify(ok=False,erro=f"Saldo insuficiente. Disponível R$ {max(0,saldo):.2f}"),400
    cur=c.execute("INSERT INTO saques_vai(motorista_id,valor,chave_pix,status,criado_em) VALUES(?,?,?,'PENDENTE',?)",(user()["id"],v,pix,time.time()))
    c.commit();r=c.execute("SELECT * FROM saques_vai WHERE id=?",(cur.lastrowid,)).fetchone();c.close()
    return jsonify(ok=True,saque=dict(r))

@app.route("/api/admin/motoristas")
@need("admin")
def am():
    c=db();r=c.execute("SELECT id,nome,whatsapp,aprovado,online FROM usuarios_vai WHERE tipo='motorista' ORDER BY id DESC").fetchall();c.close()
    return jsonify(motoristas=[dict(x) for x in r])

@app.route("/api/admin/motorista/<int:i>/aprovar",methods=["POST"])
@need("admin")
def aa(i):
    a=1 if (request.get_json() or {}).get("aprovado") else 0;c=db();cur=c.execute("UPDATE usuarios_vai SET aprovado=? WHERE id=? AND tipo='motorista'",(a,i));c.commit();c.close()
    return jsonify(ok=cur.rowcount==1)

@app.route("/api/admin/motorista/<int:i>/excluir",methods=["POST"])
@need("admin")
def ax(i):
    c=db();c.execute("UPDATE corridas_vai SET motorista_id=NULL WHERE motorista_id=? AND status='PENDENTE'",(i,))
    c.execute("DELETE FROM saques_vai WHERE motorista_id=? AND status='PENDENTE'",(i,))
    c.execute("DELETE FROM usuarios_vai WHERE id=? AND tipo='motorista'",(i,));c.commit();c.close();return jsonify(ok=True)

@app.route("/api/admin/corridas")
@need("admin")
def ac():
    c=db();r=c.execute("""SELECT x.*,p.nome passageiro_nome,m.nome motorista_nome
      FROM corridas_vai x JOIN usuarios_vai p ON p.id=x.passageiro_id
      LEFT JOIN usuarios_vai m ON m.id=x.motorista_id ORDER BY x.id DESC LIMIT 100""").fetchall();c.close()
    return jsonify(corridas=[dict(x) for x in r])

@app.route("/api/admin/corrida/<int:i>/confirmar-pix",methods=["POST"])
@need("admin")
def confirmar_pix(i):
    c=db()
    cur=c.execute("""UPDATE corridas_vai SET pagamento_status='PAGO',status='PENDENTE'
      WHERE id=? AND pagamento='PIX' AND status='AGUARDANDO_PAGAMENTO'""",(i,))
    c.commit();c.close();return jsonify(ok=cur.rowcount==1)

@app.route("/api/admin/saques")
@need("admin")
def asq():
    c=db();r=c.execute("SELECT s.*,u.nome motorista_nome FROM saques_vai s JOIN usuarios_vai u ON u.id=s.motorista_id ORDER BY s.id DESC").fetchall();c.close()
    return jsonify(saques=[dict(x) for x in r])

@app.route("/api/admin/saque/<int:i>/pagar",methods=["POST"])
@need("admin")
def ap(i):
    c=db();cur=c.execute("UPDATE saques_vai SET status='PAGO',pago_em=? WHERE id=? AND status='PENDENTE'",(time.time(),i));c.commit();c.close()
    return jsonify(ok=cur.rowcount==1)

init()
if __name__=="__main__":
    print("🏍️ VAI_DE_MOTO INICIADO")
    print("Tarifa: R$ 2,00/km | App: 9% | Motorista: 91%")
    print("Admin: 62993903299 / 1234")
    print("Motorista: 62999999999 / 1234")
    print("Passageiro: 62988888888 / 1234")
    app.run(host="0.0.0.0",port=5000,debug=False)

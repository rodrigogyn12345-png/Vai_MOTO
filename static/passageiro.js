alert("JS EXTERNO OK");
window.usarMinhaLocalizacao = async function(){
  const origem = document.getElementById("origem");
  const latInput = document.getElementById("origem_lat");
  const lonInput = document.getElementById("origem_lon");

  if(!navigator.geolocation){
    alert("Este aparelho não suporta localização por GPS.");
    return;
  }

  origem.value = "📍 LOCALIZANDO...";

  navigator.geolocation.getCurrentPosition(
    async function(pos){
      const lat = pos.coords.latitude;
      const lon = pos.coords.longitude;

      latInput.value = lat;
      lonInput.value = lon;
      origem.value = "🔄 BUSCANDO ENDEREÇO...";

      try{
        const resposta = await fetch("/api/endereco-gps", {
          method:"POST",
          headers:{"Content-Type":"application/json"},
          body:JSON.stringify({lat:lat,lon:lon}),
          cache:"no-store",
          credentials:"same-origin"
        });

        const dados = await resposta.json();

        if(dados.ok && dados.endereco){
          origem.value = dados.endereco;
          alert("Localização encontrada!");
        }else{
          origem.value = lat.toFixed(6)+", "+lon.toFixed(6);
          alert("GPS encontrado, mas não foi possível obter o endereço.");
        }
      }catch(e){
        origem.value = lat.toFixed(6)+", "+lon.toFixed(6);
        alert("GPS encontrado, mas houve erro ao buscar o endereço.");
      }
    },
    function(){
      origem.value = "";
      alert("GPS não conseguiu localizar. Ative a localização precisa e tente novamente.");
    },
    {enableHighAccuracy:true,timeout:20000,maximumAge:0}
  );
};

window.buscarDestino = async function(){
  const q = document.getElementById("destino").value.trim();
  const box = document.getElementById("resultado-endereco");

  if(!q){
    alert("Digite o destino.");
    return;
  }

  if(box) box.innerHTML='<div class="alert">🔎 Buscando destino...</div>';

  try{
    const lat=document.getElementById("origem_lat").value;
    const lon=document.getElementById("origem_lon").value;

    const r=await fetch("/api/buscar-enderecos",{
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body:JSON.stringify({q:q,lat:lat,lon:lon}),
      cache:"no-store",
      credentials:"same-origin"
    });

    const d=await r.json();

    if(!d.ok || !d.resultados || !d.resultados.length){
      if(box) box.innerHTML='<div class="alert erro">Endereço não encontrado.</div>';
      return;
    }

    if(box) box.innerHTML="";

    d.resultados.forEach(function(x){
      const b=document.createElement("button");
      b.className="pub-btn";
      b.type="button";
      b.textContent=x.display_name;

      b.onclick=function(){
        document.getElementById("destino").value=x.display_name;
        document.getElementById("dest_lat").value=x.lat;
        document.getElementById("dest_lon").value=x.lon;

        if(box) box.innerHTML='<div class="alert sucesso">Destino selecionado.</div>';
      };

      if(box) box.appendChild(b);
    });
  }catch(e){
    if(box) box.innerHTML='<div class="alert erro">Erro ao buscar destino.</div>';
  }
};


window.msg=function(t,cls="alert"){const e=document.getElementById("mensagem");if(e)e.innerHTML="<div class=\"alert "+cls+"\">"+t+"</div>";};
window.calcular=async function(){const aLat=document.getElementById("origem_lat").value,aLon=document.getElementById("origem_lon").value,dLat=document.getElementById("dest_lat").value,dLon=document.getElementById("dest_lon").value;if(!aLat||!aLon){window.msg("Use o GPS para definir a origem.","erro");return;}if(!dLat||!dLon){window.msg("Busque e selecione o destino.","erro");return;}try{const r=await fetch("/api/calcular-corrida",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({origem_lat:aLat,origem_lon:aLon,dest_lat:dLat,dest_lon:dLon}),credentials:"same-origin",cache:"no-store"}),d=await r.json();if(!d.ok){window.msg(d.erro||"Não foi possível calcular.","erro");return;}const e=document.getElementById("estimativa");if(e){e.style.display="block";e.innerHTML="<b>Distância:</b> "+d.distancia_km.toFixed(2)+" km<br><div class=\"pub-price\">R$ "+d.valor.toFixed(2)+"</div><small>Taxa do aplicativo: R$ "+d.taxa_app.toFixed(2)+" · Motorista: R$ "+d.valor_motorista.toFixed(2)+"</small>";}const b=document.getElementById("solicitar");if(b)b.style.display="block";window._corrida=d;}catch(e){window.msg("Erro ao calcular a corrida. Tente novamente.","erro");}};
console.log("VAI_DE_MOTO: passageiro.js carregado");


window.solicitar = async function solicitar(){
  if(!window._corrida){
    msg("Calcule a corrida primeiro.","erro");
    return;
  }

  const pagamento =
    document.getElementById("pagamento").value;

  if(!["DINHEIRO","PIX","CARTAO"].includes(pagamento)){
    msg("Escolha Dinheiro, PIX ou Cartão.","erro");
    return;
  }

  const botao =
    document.getElementById("solicitar");

  if(botao){
    botao.disabled = true;
    botao.style.opacity = "0.65";

    botao.textContent =
      pagamento === "DINHEIRO"
        ? "⏳ SOLICITANDO..."
        : "⏳ CRIANDO PAGAMENTO...";
  }

  const body = {
    origem:
      document.getElementById("origem").value,

    destino:
      document.getElementById("destino").value,

    origem_lat:
      document.getElementById("origem_lat").value,

    origem_lon:
      document.getElementById("origem_lon").value,

    dest_lat:
      document.getElementById("dest_lat").value,

    dest_lon:
      document.getElementById("dest_lon").value,

    pagamento: pagamento,

    distancia_km:
      window._corrida.distancia_km,

    valor:
      window._corrida.valor,

    taxa_app:
      window._corrida.taxa_app,

    valor_motorista:
      window._corrida.valor_motorista
  };

  try{

    const r = await fetch(
      "/api/solicitar-corrida",
      {
        method:"POST",
        headers:{
          "Content-Type":"application/json"
        },
        credentials:"same-origin",
        body:JSON.stringify(body)
      }
    );

    const d = await r.json();

    if(!d.ok){

      msg(
        d.erro || "Erro ao solicitar corrida.",
        "erro"
      );

      if(botao){
        botao.disabled = false;
        botao.style.opacity = "1";
        atualizarBotaoPagamento();
      }

      return;
    }

    if(d.pagamento === "DINHEIRO"){

      if(botao){
        botao.style.display = "none";
      }

      msg(
        "🏍️ Corrida solicitada!<br><br>" +
        "Estamos procurando um motorista para você.",
        "sucesso"
      );

    }else{

      if(d.checkout_url){

        if(botao){
          botao.style.display = "none";
        }

        msg(
          '<div style="text-align:center">' +
          '<div style="font-size:32px">🔐</div>' +
          '<b>PAGAMENTO NECESSÁRIO</b>' +
          '<br><br>' +
          'Sua corrida ainda NÃO foi liberada para os motoristas.' +
          '<br><br>' +
          '<a href="' + d.checkout_url + '" ' +
          'target="_blank" ' +
          'rel="noopener noreferrer" ' +
          'class="pub-btn pub-green" ' +
          'style="display:block;text-align:center;text-decoration:none">' +
          '💳 PAGAR AGORA' +
          '</a>' +
          '<div style="font-size:12px;margin-top:10px;color:#666">' +
          'Após o pagamento confirmado, a corrida será liberada automaticamente.' +
          '</div>' +
          '</div>',
          "sucesso"
        );

      }else{

        msg(
          "Não foi possível gerar o pagamento.",
          "erro"
        );

        if(botao){
          botao.disabled = false;
          botao.style.opacity = "1";
          atualizarBotaoPagamento();
        }

        return;
      }
    }

    carregarCorridas();
  }catch(e){

    console.error(e);

    msg(
      "Erro de conexão. Tente novamente.",
      "erro"
    );

    if(botao){
      botao.disabled = false;
      botao.style.opacity = "1";
      atualizarBotaoPagamento();
    }
  }
}

  
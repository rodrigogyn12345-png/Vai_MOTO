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

  const textoVeiculoSelecionado =
    document.getElementById("veiculo-demo-selecionado")?.textContent || "";

  const ehCarroDemo =
    window.veiculoDemoSelecionado === "CARRO" ||
    textoVeiculoSelecionado.includes("VAI_DE_CARRO");

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
      ehCarroDemo
        ? "/api/solicitar-corrida-carro"
        : "/api/solicitar-corrida",
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

  

window.carregarCorridas = async function carregarCorridas(){
    try{
      const r = await fetch("/api/minhas-corridas", {
        cache:"no-store",
        credentials:"same-origin"
      });

      const d = await r.json();
      const box = document.getElementById("lista-corridas");

      if(!d.ok){
        box.innerHTML = `
          <div style="padding:18px;text-align:center">
            🔐 Faça login novamente.
          </div>`;
        return;
      }

      if(!Array.isArray(d.corridas) || !d.corridas.length){
        box.innerHTML = `
          <div style="padding:22px;text-align:center;color:#777">
            <div style="font-size:38px">🏍️</div>
            <div style="font-weight:800;margin-top:8px">
              Nenhuma corrida ainda.
            </div>
            <div style="font-size:13px;margin-top:5px">
              Solicite sua primeira corrida acima.
            </div>
          </div>`;
        return;
      }

      function statusInfo(c){
        const status = String(c.status || "").toUpperCase();
        const etapa = String(c.etapa || "").toUpperCase();

        if(status === "AGUARDANDO_PAGAMENTO"){
          return {
            titulo:"AGUARDANDO PAGAMENTO",
            texto:"Finalize o pagamento para liberar a corrida aos motoristas.",
            emoji:"💳",
            progresso:10
          };
        }

        if(status === "PENDENTE"){
          return {
            titulo:"PROCURANDO MOTORISTA",
            texto:"Estamos procurando um motorista disponível.",
            emoji:"🔎",
            progresso:20
          };
        }

        if(status === "ACEITA" && etapa === "CHEGOU"){
          return {
            titulo:"MOTORISTA CHEGOU",
            texto:"Seu motorista está aguardando você.",
            emoji:"📍",
            progresso:60
          };
        }

        if(status === "ACEITA"){
          return {
            titulo:"MOTORISTA A CAMINHO",
            texto:"Seu motorista aceitou a corrida e está indo até você.",
            emoji:"🏍️",
            progresso:40
          };
        }

        if(status === "EM_ANDAMENTO"){
          return {
            titulo:"CORRIDA EM ANDAMENTO",
            texto:"Você está a caminho do destino.",
            emoji:"🚀",
            progresso:80
          };
        }

        if(status === "CONCLUIDA"){
          return {
            titulo:"CORRIDA CONCLUÍDA",
            texto:"Sua corrida foi finalizada.",
            emoji:"✅",
            progresso:100
          };
        }

        if(status === "CANCELADA"){
          return {
            titulo:"CORRIDA CANCELADA",
            texto:"Esta corrida foi cancelada.",
            emoji:"❌",
            progresso:0
          };
        }

        return {
          titulo:status || "CORRIDA",
          texto:"Acompanhando sua corrida.",
          emoji:"🏍️",
          progresso:10
        };
      }

      function telefoneMotorista(telefone){
        if(!telefone) return "";

        const numero = String(telefone).replace(/[^\d+]/g,"");

        return `
          <a href="tel:${numero}"
             style="
               display:inline-flex;
               align-items:center;
               justify-content:center;
               gap:7px;
               margin-top:10px;
               padding:11px 15px;
               border-radius:13px;
               background:#087f23;
               color:white;
               text-decoration:none;
               font-weight:900;
               font-size:14px;
             ">
             📞 LIGAR PARA O MOTORISTA
          </a>`;
      }

      function mapaLink(c){
        const origem = encodeURIComponent(c.origem || "");
        const destino = encodeURIComponent(c.destino || "");

        if(!origem && !destino) return "";

        return `
          <a
            href="https://www.google.com/maps/dir/?api=1&origin=${origem}&destination=${destino}"
            target="_blank"
            rel="noopener"
            style="
              display:inline-flex;
              align-items:center;
              justify-content:center;
              gap:7px;
              margin-top:10px;
              padding:11px 15px;
              border-radius:13px;
              background:#222;
              color:white;
              text-decoration:none;
              font-weight:900;
              font-size:14px;
            ">
            🗺️ VER ROTA NO GOOGLE MAPS
          </a>`;
      }

      function cardAtiva(c){
        const st = statusInfo(c);
        const motorista = c.motorista_nome || "Aguardando motorista";

        return `
          <div style="
            background:#fff;
            color:#111;
            border-radius:22px;
            padding:18px;
            margin-bottom:16px;
            box-shadow:0 8px 28px rgba(0,0,0,.14);
            border:1px solid #eee;
          ">

            <div style="
              display:flex;
              justify-content:space-between;
              align-items:center;
              gap:10px;
              margin-bottom:13px;
            ">
              <div style="font-size:12px;color:#777;font-weight:800">
                CORRIDA #${c.id}
              </div>

              <div style="
                background:#f3f3f3;
                border-radius:20px;
                padding:6px 10px;
                font-size:11px;
                font-weight:900;
              ">
                ${c.pagamento || "DINHEIRO"}
              </div>
            </div>

            <div style="
              text-align:center;
              padding:13px 8px;
              border-radius:17px;
              background:#f7f7f7;
            ">
              <div style="font-size:32px">${st.emoji}</div>

              <div style="
                font-size:18px;
                font-weight:900;
                margin-top:5px;
              ">
                ${st.titulo}
              </div>

              <div style="
                font-size:13px;
                color:#666;
                margin-top:6px;
                line-height:1.35;
              ">
                ${st.texto}
              </div>
            </div>

            <div style="margin:17px 2px 12px">
              <div style="
                height:9px;
                border-radius:20px;
                background:#e5e5e5;
                overflow:hidden;
              ">
                <div style="
                  width:${st.progresso}%;
                  height:100%;
                  border-radius:20px;
                  background:#087f23;
                  transition:width .4s ease;
                "></div>
              </div>

              <div style="
                display:flex;
                justify-content:space-between;
                font-size:10px;
                color:#888;
                margin-top:6px;
                font-weight:700;
              ">
                <span>Solicitada</span>
                <span>Motorista</span>
                <span>Chegou</span>
                <span>Em viagem</span>
                <span>Finalizada</span>
              </div>
            </div>

            <div style="
              border-top:1px solid #eee;
              padding-top:14px;
              margin-top:8px;
            ">

              <div style="
                font-size:12px;
                color:#777;
                font-weight:800;
                margin-bottom:5px;
              ">
                MOTORISTA
              </div>

              <div style="
                display:flex;
                align-items:center;
                gap:10px;
              ">
                <div style="
                  width:44px;
                  height:44px;
                  border-radius:50%;
                  background:#111;
                  color:#ff8a00;
                  display:flex;
                  align-items:center;
                  justify-content:center;
                  font-size:22px;
                ">
                  🏍️
                </div>

                <div>
                  <div style="font-weight:900;font-size:16px">
                    ${motorista}
                  </div>

                  <div style="font-size:12px;color:#777;margin-top:2px">
                    ${c.motorista_telefone || "Aguardando motorista"}
                  </div>
                </div>
              </div>

              ${telefoneMotorista(c.motorista_telefone)}

              <div style="
                margin-top:15px;
                padding:13px;
                border-radius:15px;
                background:#f7f7f7;
                font-size:13px;
                line-height:1.5;
              ">
                <div>
                  📍 <b>Origem:</b><br>
                  ${c.origem || "-"}
                </div>

                <div style="
                  border-left:2px solid #ccc;
                  margin:6px 0 6px 7px;
                  height:12px;
                "></div>

                <div>
                  🏁 <b>Destino:</b><br>
                  ${c.destino || "-"}
                </div>
              </div>

              <div style="
                display:flex;
                justify-content:space-between;
                align-items:center;
                margin-top:15px;
                padding-top:13px;
                border-top:1px solid #eee;
              ">
                <span style="font-size:13px;color:#777">
                  Valor da corrida
                </span>

                <strong style="font-size:21px">
                  R$ ${Number(c.valor || 0).toFixed(2)}
                </strong>
              </div>

              ${mapaLink(c)}

              ${
                (c.status === "PENDENTE" || c.status === "ACEITA")
                ? `
                  <button
                    type="button"
                    onclick="cancelarCorrida(${c.id})"
                    style="
                      width:100%;
                      margin-top:12px;
                      padding:13px;
                      border:0;
                      border-radius:14px;
                      background:#f1f1f1;
                      color:#b00020;
                      font-weight:900;
                      cursor:pointer;
                    ">
                    🔴 CANCELAR CORRIDA
                  </button>
                `
                : ""
              }

            </div>
          </div>
        `;
      }

      function estrelasHtml(nota){
        const n = Number(nota || 0);
        let html = "";

        for(let i = 1; i <= 5; i++){
          html += i <= n ? "★" : "☆";
        }

        return html;
      }

      window.avaliarMotorista = async function(id){
        const modalAntigo = document.getElementById("modal-avaliacao");
        if(modalAntigo) modalAntigo.remove();

        const modal = document.createElement("div");

        modal.id = "modal-avaliacao";

        modal.style.cssText =
          "position:fixed;" +
          "inset:0;" +
          "z-index:999999;" +
          "background:rgba(0,0,0,.72);" +
          "display:flex;" +
          "align-items:flex-end;" +
          "justify-content:center;" +
          "padding:12px;";

        modal.innerHTML = `
          <div style="
            width:100%;
            max-width:520px;
            background:#fff;
            color:#111;
            border-radius:28px;
            padding:22px;
            box-shadow:0 20px 60px rgba(0,0,0,.45);
          ">

            <div style="
              text-align:center;
              font-size:12px;
              color:#888;
              font-weight:900;
              letter-spacing:.5px;
            ">
              SUA OPINIÃO É IMPORTANTE
            </div>

            <div style="
              text-align:center;
              font-size:24px;
              font-weight:900;
              margin-top:6px;
            ">
              ⭐ Avalie seu motorista
            </div>

            <div style="
              text-align:center;
              color:#666;
              font-size:13px;
              margin-top:6px;
            ">
              Como foi sua experiência com a corrida?
            </div>

            <div id="estrelas-avaliacao" style="
              display:flex;
              justify-content:center;
              gap:5px;
              margin:20px 0 8px;
            ">
              ${[1,2,3,4,5].map(i => `
                <button
                  type="button"
                  data-nota="${i}"
                  style="
                    border:0;
                    background:transparent;
                    font-size:43px;
                    line-height:1;
                    color:#bbb;
                    padding:4px;
                    cursor:pointer;
                  "
                >☆</button>
              `).join("")}
            </div>

            <div id="texto-nota" style="
              text-align:center;
              font-size:13px;
              font-weight:800;
              color:#777;
              min-height:20px;
            ">
              Toque nas estrelas
            </div>

            <textarea
              id="comentario-avaliacao"
              maxlength="500"
              placeholder="Comentário opcional..."
              style="
                width:100%;
                min-height:90px;
                margin-top:16px;
                padding:14px;
                border:1px solid #ddd;
                border-radius:16px;
                resize:none;
                font-size:15px;
                font-family:Arial,Helvetica,sans-serif;
                box-sizing:border-box;
                outline:none;
              "
            ></textarea>

            <button
              id="enviar-avaliacao"
              type="button"
              disabled
              style="
                width:100%;
                margin-top:12px;
                padding:15px;
                border:0;
                border-radius:16px;
                background:#087f23;
                color:#fff;
                font-size:16px;
                font-weight:900;
                opacity:.5;
              "
            >
              ⭐ ENVIAR AVALIAÇÃO
            </button>

            <button
              type="button"
              id="fechar-avaliacao"
              style="
                width:100%;
                margin-top:8px;
                padding:13px;
                border:0;
                border-radius:15px;
                background:#f1f1f1;
                color:#555;
                font-weight:800;
              "
            >
              FECHAR
            </button>

          </div>
        `;

        document.body.appendChild(modal);

        let notaSelecionada = 0;

        const botoesEstrela =
          modal.querySelectorAll("[data-nota]");

        const textoNota =
          modal.querySelector("#texto-nota");

        const enviar =
          modal.querySelector("#enviar-avaliacao");

        const comentario =
          modal.querySelector("#comentario-avaliacao");

        const textos = {
          1:"Muito ruim",
          2:"Ruim",
          3:"Regular",
          4:"Muito bom",
          5:"Excelente!"
        };

        botoesEstrela.forEach(btn => {
          btn.addEventListener("click", function(){

            notaSelecionada =
              Number(this.dataset.nota);

            botoesEstrela.forEach(b => {
              const valor =
                Number(b.dataset.nota);

              b.textContent =
                valor <= notaSelecionada
                  ? "★"
                  : "☆";

              b.style.color =
                valor <= notaSelecionada
                  ? "#ff9d00"
                  : "#bbb";
            });

            textoNota.textContent =
              textos[notaSelecionada];

            enviar.disabled = false;
            enviar.style.opacity = "1";
          });
        });

        modal.querySelector("#fechar-avaliacao")
          .addEventListener("click", function(){
            modal.remove();
          });

        enviar.addEventListener("click", async function(){

          if(!notaSelecionada){
            alert("Escolha de 1 a 5 estrelas.");
            return;
          }

          enviar.disabled = true;
          enviar.textContent = "⏳ ENVIANDO...";

          try{

            const r = await fetch(
              "/api/corrida/" + id + "/avaliar",
              {
                method:"POST",
                headers:{
                  "Content-Type":"application/json"
                },
                credentials:"same-origin",
                body:JSON.stringify({
                  nota:notaSelecionada,
                  comentario:comentario.value.trim()
                })
              }
            );

            const d = await r.json();

            if(!d.ok){
              alert(
                d.erro ||
                "Não foi possível enviar a avaliação."
              );

              enviar.disabled = false;
              enviar.textContent =
                "⭐ ENVIAR AVALIAÇÃO";

              return;
            }

            modal.remove();

            msg(
              "⭐ Avaliação enviada com sucesso! Obrigado pela sua opinião.",
              "sucesso"
            );

            await carregarCorridas();

          }catch(e){

            console.error(e);

            alert(
              "Erro de conexão ao enviar a avaliação."
            );

            enviar.disabled = false;
            enviar.textContent =
              "⭐ ENVIAR AVALIAÇÃO";
          }
        });
      };

      function cardHistorico(c){
        const st = statusInfo(c);
        const concluida =
          String(c.status || "").toUpperCase() === "CONCLUIDA";

        const avaliada =
          c.avaliacao_nota !== null &&
          c.avaliacao_nota !== undefined &&
          c.avaliacao_nota !== "";

        let avaliacaoHtml = "";

        if(concluida && !avaliada){

          avaliacaoHtml = `
            <button
              type="button"
              onclick="avaliarMotorista(${c.id})"
              style="
                width:100%;
                margin-top:13px;
                padding:14px;
                border:0;
                border-radius:15px;
                background:#ff9d00;
                color:#111;
                font-weight:900;
                font-size:14px;
                cursor:pointer;
              "
            >
              ⭐ AVALIAR MOTORISTA
            </button>
          `;

        }else if(concluida && avaliada){

          avaliacaoHtml = `
            <div style="
              margin-top:13px;
              padding:12px;
              border-radius:15px;
              background:#fff8e8;
              border:1px solid #ffe1a3;
              text-align:center;
            ">
              <div style="
                color:#ff9d00;
                font-size:25px;
                letter-spacing:2px;
                font-weight:900;
              ">
                ${estrelasHtml(c.avaliacao_nota)}
              </div>

              <div style="
                margin-top:3px;
                font-size:11px;
                color:#777;
                font-weight:800;
              ">
                AVALIAÇÃO ENVIADA
              </div>

              ${
                c.avaliacao_comentario
                ? `
                  <div style="
                    margin-top:7px;
                    color:#555;
                    font-size:12px;
                    line-height:1.4;
                  ">
                    “${String(c.avaliacao_comentario)
                      .replace(/</g,"&lt;")
                      .replace(/>/g,"&gt;")}"
                  </div>
                `
                : ""
              }
            </div>
          `;
        }

        return `
          <div style="
            background:#fff;
            color:#111;
            border-radius:20px;
            padding:16px;
            margin-bottom:11px;
            border:1px solid #e8e8e8;
            box-shadow:0 5px 18px rgba(0,0,0,.07);
          ">

            <div style="
              display:flex;
              justify-content:space-between;
              align-items:center;
              gap:10px;
            ">

              <div>
                <div style="
                  font-size:10px;
                  color:#999;
                  font-weight:900;
                  text-transform:uppercase;
                ">
                  Histórico
                </div>

                <strong style="
                  display:block;
                  margin-top:3px;
                  font-size:15px;
                ">
                  🏍️ Corrida #${c.id}
                </strong>
              </div>

              <span style="
                padding:7px 9px;
                border-radius:12px;
                background:#f5f5f5;
                font-size:10px;
                font-weight:900;
                color:#666;
              ">
                ${st.emoji} ${st.titulo}
              </span>

            </div>

            <div style="
              margin-top:13px;
              padding:12px;
              border-radius:14px;
              background:#f8f8f8;
              font-size:12px;
              color:#555;
              line-height:1.55;
            ">
              📍 <b>Origem:</b><br>
              ${c.origem || "-"}<br><br>

              🏁 <b>Destino:</b><br>
              ${c.destino || "-"}
            </div>

            <div style="
              display:flex;
              align-items:center;
              justify-content:space-between;
              gap:10px;
              margin-top:12px;
              padding-top:12px;
              border-top:1px solid #eee;
            ">

              <div>
                <div style="
                  font-size:10px;
                  color:#999;
                  font-weight:900;
                ">
                  MOTORISTA
                </div>

                <div style="
                  margin-top:3px;
                  font-size:14px;
                  font-weight:900;
                ">
                  🏍️ ${c.motorista_nome || "Sem motorista"}
                </div>
              </div>

              <strong style="
                font-size:19px;
              ">
                R$ ${Number(c.valor || 0).toFixed(2)}
              </strong>

            </div>

            ${avaliacaoHtml}

          </div>
        `;
      }

      const ativas = d.corridas.filter(c =>
        ["PENDENTE","ACEITA","EM_ANDAMENTO"].includes(
          String(c.status || "").toUpperCase()
        )
      );

      const historico = d.corridas.filter(c =>
        !["PENDENTE","ACEITA","EM_ANDAMENTO"].includes(
          String(c.status || "").toUpperCase()
        )
      );

      let html = "";

      if(ativas.length){
        html += `
          <div style="
            font-size:13px;
            font-weight:900;
            color:#555;
            margin-bottom:8px;
            text-transform:uppercase;
          ">
            🚦 Acompanhamento atual
          </div>
        `;

        html += cardAtiva(ativas[0]);
      }

      if(historico.length){
        html += `
          <div style="
            font-size:13px;
            font-weight:900;
            color:#555;
            margin:18px 0 8px;
            text-transform:uppercase;
          ">
            🧾 Histórico de corridas
          </div>
        `;

        html += historico.map(cardHistorico).join("");
      }

      box.innerHTML = html;

    }catch(e){
      console.log("Erro ao carregar corridas:", e);
    }
  }

  
/* SELETOR DE VEICULO - VAI_DE_CARRO */
window.selecionarVeiculoDemo = function(tipo) {
  window.veiculoDemoSelecionado = tipo;

  const texto = document.getElementById("veiculo-demo-selecionado");
  const moto = document.getElementById("btnMotoDemo");
  const carro = document.getElementById("btnCarroDemo");

  if (tipo === "CARRO") {
    if (texto) texto.textContent = "🚗 VAI_DE_CARRO selecionado";
    if (moto) moto.style.opacity = "0.55";
    if (carro) carro.style.opacity = "1";
  } else {
    if (texto) texto.textContent = "🏍️ VAI_DE_MOTO selecionado";
    if (moto) moto.style.opacity = "1";
    if (carro) carro.style.opacity = "0.55";
  }
};

console.log("VAI_DE_MOTO: seletor de veiculo carregado");

/* CLIQUE DIRETO NOS BOTOES DE VEICULO */
document.addEventListener("click", function(event) {
  const botao = event.target.closest("#btnMotoDemo, #btnCarroDemo");
  if (!botao) return;

  if (botao.id === "btnCarroDemo") {
    window.selecionarVeiculoDemo("CARRO");
  } else {
    window.selecionarVeiculoDemo("MOTO");
  }
});

console.log("VAI_DE_MOTO: eventos dos botoes de veiculo ativos");

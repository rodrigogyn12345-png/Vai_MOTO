import re

arquivo = "app.py"

with open(arquivo, "r", encoding="utf-8") as f:
    codigo = f.read()

# Adiciona CPF à tabela de motoristas, caso ainda não exista
marcador = 'conn.execute("""\n        CREATE TABLE IF NOT EXISTS passageiros'

bloco = '''
    # Compatibilidade com versões anteriores
    try:
        conn.execute("ALTER TABLE motoristas ADD COLUMN cpf TEXT")
    except sqlite3.OperationalError:
        pass

'''

if bloco.strip() not in codigo:
    codigo = codigo.replace(marcador, bloco + marcador)

# Substitui a área de motoqueiros
inicio = codigo.index('@app.route("/motoristas", methods=["GET", "POST"])')
fim = codigo.index('@app.route("/passageiros"', inicio)

nova_area = r'''@app.route("/motoristas", methods=["GET", "POST"])
def motoristas():

    if not protegido():
        return redirect("/login")

    conn = db()
    mensagem = ""

    if request.method == "POST":

        acao = request.form.get("acao")

        if acao == "cadastrar":

            total = conn.execute(
                "SELECT COUNT(*) n FROM motoristas"
            ).fetchone()["n"]

            if total >= 20:

                mensagem = "❌ Limite máximo de 20 motoqueiros atingido."

            else:

                nome = request.form.get("nome", "").strip()
                telefone = request.form.get("telefone", "").strip()
                cpf = request.form.get("cpf", "").strip()
                moto = request.form.get("moto", "").strip()
                placa = request.form.get("placa", "").strip()

                if not nome:
                    mensagem = "❌ Informe o nome do motoqueiro."

                else:

                    conn.execute("""
                        INSERT INTO motoristas
                        (nome, telefone, cpf, moto, placa, status, online)
                        VALUES (?, ?, ?, ?, ?, 'pendente', 0)
                    """, (
                        nome,
                        telefone,
                        cpf,
                        moto,
                        placa
                    ))

                    conn.commit()
                    mensagem = "✅ Motoqueiro cadastrado como PENDENTE."

        elif acao == "aprovar":

            motorista_id = request.form.get("id")

            aprovados = conn.execute(
                "SELECT COUNT(*) n FROM motoristas WHERE status='aprovado'"
            ).fetchone()["n"]

            if aprovados >= 20:
                mensagem = "❌ Já existem 20 motoqueiros aprovados."

            else:
                conn.execute(
                    "UPDATE motoristas SET status='aprovado' WHERE id=?",
                    (motorista_id,)
                )
                conn.commit()
                mensagem = "✅ Motoqueiro aprovado."

        elif acao == "bloquear":

            motorista_id = request.form.get("id")

            conn.execute("""
                UPDATE motoristas
                SET status='bloqueado', online=0
                WHERE id=?
            """, (motorista_id,))

            conn.commit()
            mensagem = "🔴 Motoqueiro bloqueado."

        elif acao == "ativar":

            motorista_id = request.form.get("id")

            conn.execute("""
                UPDATE motoristas
                SET status='aprovado'
                WHERE id=?
            """, (motorista_id,))

            conn.commit()
            mensagem = "🟢 Motoqueiro ativado."

        elif acao == "online":

            motorista_id = request.form.get("id")

            conn.execute("""
                UPDATE motoristas
                SET online=1
                WHERE id=? AND status='aprovado'
            """, (motorista_id,))

            conn.commit()
            mensagem = "🟢 Motoqueiro colocado ONLINE."

        elif acao == "offline":

            motorista_id = request.form.get("id")

            conn.execute("""
                UPDATE motoristas
                SET online=0
                WHERE id=?
            """, (motorista_id,))

            conn.commit()
            mensagem = "⚫ Motoqueiro colocado OFFLINE."

    total = conn.execute(
        "SELECT COUNT(*) n FROM motoristas"
    ).fetchone()["n"]

    aprovados = conn.execute(
        "SELECT COUNT(*) n FROM motoristas WHERE status='aprovado'"
    ).fetchone()["n"]

    pendentes = conn.execute(
        "SELECT COUNT(*) n FROM motoristas WHERE status='pendente'"
    ).fetchone()["n"]

    online = conn.execute(
        "SELECT COUNT(*) n FROM motoristas WHERE online=1"
    ).fetchone()["n"]

    lista = conn.execute(
        "SELECT * FROM motoristas ORDER BY id DESC"
    ).fetchall()

    conn.close()

    linhas = ""

    for m in lista:

        if m["status"] == "aprovado":
            status = '<span style="color:green;font-weight:bold">🟢 APROVADO</span>'
        elif m["status"] == "bloqueado":
            status = '<span style="color:red;font-weight:bold">🔴 BLOQUEADO</span>'
        else:
            status = '<span style="color:#b88600;font-weight:bold">🟡 PENDENTE</span>'

        estado_online = "🟢 ONLINE" if m["online"] else "⚫ OFFLINE"

        linhas += f"""
        <tr>
            <td>{m["id"]}</td>
            <td><b>{m["nome"]}</b></td>
            <td>{m["telefone"] or "-"}</td>
            <td>{m["cpf"] or "-"}</td>
            <td>{m["moto"] or "-"}</td>
            <td>{m["placa"] or "-"}</td>
            <td>{status}</td>
            <td>{estado_online}</td>
            <td>

                <form method="POST" style="margin:3px">
                    <input type="hidden" name="id" value="{m["id"]}">
                    <input type="hidden" name="acao" value="aprovar">
                    <button type="submit">✅ Aprovar</button>
                </form>

                <form method="POST" style="margin:3px">
                    <input type="hidden" name="id" value="{m["id"]}">
                    <input type="hidden" name="acao" value="bloquear">
                    <button type="submit">🔴 Bloquear</button>
                </form>

                <form method="POST" style="margin:3px">
                    <input type="hidden" name="id" value="{m["id"]}">
                    <input type="hidden" name="acao" value="online">
                    <button type="submit">🟢 Online</button>
                </form>

                <form method="POST" style="margin:3px">
                    <input type="hidden" name="id" value="{m["id"]}">
                    <input type="hidden" name="acao" value="offline">
                    <button type="submit">⚫ Offline</button>
                </form>

            </td>
        </tr>
        """

    conteudo = f"""
    <h1>🏍️ Motoqueiros</h1>

    <div class="cards">

        <div class="card">
            <h3>🏍️ Cadastrados</h3>
            <div class="numero">{total}/20</div>
        </div>

        <div class="card">
            <h3>🟢 Aprovados</h3>
            <div class="numero">{aprovados}</div>
        </div>

        <div class="card">
            <h3>🟡 Pendentes</h3>
            <div class="numero">{pendentes}</div>
        </div>

        <div class="card">
            <h3>🟢 Online</h3>
            <div class="numero">{online}</div>
        </div>

    </div>

    <p><b>{mensagem}</b></p>

    <div class="card">

        <h2>➕ Cadastrar motoqueiro</h2>

        <form method="POST">

            <input type="hidden" name="acao" value="cadastrar">

            <input
                name="nome"
                placeholder="Nome completo"
                required
            >

            <input
                name="telefone"
                placeholder="Telefone / WhatsApp"
            >

            <input
                name="cpf"
                placeholder="CPF"
            >

            <input
                name="moto"
                placeholder="Modelo da moto"
            >

            <input
                name="placa"
                placeholder="Placa"
            >

            <button type="submit">
                🏍️ CADASTRAR MOTOQUEIRO
            </button>

        </form>

    </div>

    <div class="card">

        <h2>📋 Motoqueiros cadastrados</h2>

        <div style="overflow-x:auto">

        <table>

        <tr>
            <th>ID</th>
            <th>Nome</th>
            <th>Telefone</th>
            <th>CPF</th>
            <th>Moto</th>
            <th>Placa</th>
            <th>Status</th>
            <th>Conexão</th>
            <th>Ações</th>
        </tr>

        {linhas}

        </table>

        </div>

    </div>
    """

    return pagina(conteudo)


'''

codigo = codigo[:inicio] + nova_area + codigo[fim:]

with open(arquivo, "w", encoding="utf-8") as f:
    f.write(codigo)

print("ATUALIZAÇÃO CONCLUÍDA")

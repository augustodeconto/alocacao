"""Resposta incremental de edição (docs/PERFORMANCE.md) — aggregate.impacto_edicao
tem que dar o MESMO número que build_grade() completo daria, só que recortado."""
from fastapi.testclient import TestClient

from app import aggregate, main as app_main, versao
from app.xlsx_import import import_workbook


def _client(conn):
    app_main._conn = conn
    return TestClient(app_main.app)


def test_impacto_bate_com_build_grade_completo(conn, sample_path):
    import_workbook(conn, sample_path)
    conn.execute("UPDATE pessoa SET capacidade_mensal=176 WHERE matricula='73920'")
    conn.commit()
    versao.commit(conn, "baseline")

    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]
    conn.execute(
        "UPDATE alocacao_mes SET horas=123 WHERE alocacao_id=? AND periodo='2026-07-01'", (aid,)
    )
    conn.commit()

    impacto = aggregate.impacto_edicao(conn, [(aid, "2026-07-01")])
    # periodo_ativo=False: impacto_edicao não aplica o filtro de período da tela (ver
    # docs/ESPECIFICACAO.md §8) — opera sobre o histórico todo pros períodos tocados,
    # então a comparação justa é com o build_grade também sem esse recorte.
    completo = aggregate.build_grade(conn, periodo_ativo=False)

    proj = next(p for p in completo["por_projeto"] if p["projeto_id"] == 1)
    grp = next(g for g in proj["grupos"] if g["tipo_alocacao"] == "TecnicaANP")
    pessoa = next(r for r in completo["por_recurso"] if r["matricula"] == "73920")

    assert len(impacto["alocacoes"]) == 1
    a = impacto["alocacoes"][0]
    assert a["horas"] == 123
    assert a["alterado"] is True   # baseline tinha 88

    assert len(impacto["grupos"]) == 1
    g = impacto["grupos"][0]
    assert g["projeto_id"] == 1 and g["tipo_alocacao"] == "TecnicaANP"
    assert g["totais"]["2026-07-01"] == grp["totais"]["2026-07-01"]

    assert len(impacto["projetos"]) == 1
    p = impacto["projetos"][0]
    assert p["projeto_id"] == 1
    assert p["totais"]["2026-07-01"] == proj["totais"]["2026-07-01"]
    assert p["sujo"] == proj["sujo"]

    assert len(impacto["pessoas"]) == 1
    pe = impacto["pessoas"][0]
    assert pe["matricula"] == "73920"
    assert pe["totais"]["2026-07-01"] == pessoa["totais"]["2026-07-01"]
    assert pe["cores"].get("2026-07-01") == pessoa["cores"].get("2026-07-01")


def test_impacto_repinta_outras_linhas_da_mesma_pessoa(conn, sample_path):
    """Uma pessoa com 2 alocações: editar uma tem que listar a OUTRA em
    linhas_para_repintar (a cor/total dela muda mesmo sem editar aquela célula)."""
    import_workbook(conn, sample_path)
    cur = conn.execute(
        "INSERT INTO alocacao (projeto_id, matricula, tipo_alocacao) VALUES (1,'79483','TecnicaEPII')"
    )
    aid2 = cur.lastrowid
    conn.execute(
        "INSERT INTO alocacao_mes (alocacao_id, periodo, horas) VALUES (?, '2026-07-01', 40)", (aid2,)
    )
    conn.commit()
    aid1 = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE matricula='79483' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]

    impacto = aggregate.impacto_edicao(conn, [(aid1, "2026-07-01")])
    pe = impacto["pessoas"][0]
    tipos = sorted(l["tipo_alocacao"] for l in pe["linhas_para_repintar"])
    assert tipos == ["TecnicaANP", "TecnicaEPII"]


def test_endpoint_mes_lote_devolve_impacto_para_edicao_de_valor(conn, sample_path):
    import_workbook(conn, sample_path)
    cli = _client(conn)
    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]
    r = cli.put("/api/alocacao/mes-lote", json={
        "edits": [{"alocacao_id": aid, "periodo": "2026-07-01", "valor": "150"}]
    })
    assert r.status_code == 200
    body = r.json()
    assert "impacto" in body and "estado" not in body
    assert body["impacto"]["alocacoes"][0]["horas"] == 150


def test_endpoint_mes_lote_aceita_alocacao_id_como_string(conn, sample_path):
    """Bug real corrigido em 2026-09-12: o menu de ajuste rápido (botão direito) e o
    resto do EG.cellsPara (colar/arrastar/Delete) mandam `alocacao_id` como STRING
    (vem de dataset.alocacaoId, HTML nunca converte). O SQL casa por afinidade de
    tipo do SQLite normalmente, mas `impacto_edicao` fazia lookup num dict Python
    chaveado pelo id puro — "17253" != 17253 — e devolvia `alocacoes: []`, fazendo a
    tela não aplicar nada, silenciosamente."""
    import_workbook(conn, sample_path)
    cli = _client(conn)
    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]
    r = cli.put("/api/alocacao/mes-lote", json={
        "edits": [{"alocacao_id": str(aid), "periodo": "2026-07-01", "valor": "150"}]
    })
    assert r.status_code == 200
    body = r.json()
    assert "impacto" in body
    assert body["impacto"]["alocacoes"], "alocacoes não pode vir vazio (era o bug)"
    assert body["impacto"]["alocacoes"][0]["horas"] == 150
    assert body["impacto"]["alocacoes"][0]["alocacao_id"] == aid  # int, não string


def test_endpoint_mes_lote_devolve_estado_quando_estrutural(conn, sample_path):
    import_workbook(conn, sample_path)
    cli = _client(conn)
    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]
    pers = [r["periodo"] for r in conn.execute(
        "SELECT periodo FROM alocacao_mes WHERE alocacao_id=?", (aid,)
    )]
    r = cli.put("/api/alocacao/mes-lote", json={
        "edits": [{"alocacao_id": aid, "periodo": p, "valor": "0"} for p in pers]
    })
    assert r.status_code == 200
    body = r.json()
    assert "estado" in body and "impacto" not in body   # zerou tudo -> linha some (estrutural)


def test_endpoint_mes_devolve_impacto_para_edicao_de_valor(conn, sample_path):
    import_workbook(conn, sample_path)
    cli = _client(conn)
    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]
    r = cli.put(f"/api/alocacao/{aid}/mes", json={"periodo": "2026-07-01", "valor": "150"})
    assert r.status_code == 200
    body = r.json()
    assert "impacto" in body and "estado" not in body


def test_endpoint_mes_devolve_estado_quando_remove_a_linha(conn, sample_path):
    import_workbook(conn, sample_path)
    cli = _client(conn)
    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]
    pers = [r["periodo"] for r in conn.execute(
        "SELECT periodo FROM alocacao_mes WHERE alocacao_id=?", (aid,)
    )]
    for p in pers[:-1]:
        cli.put(f"/api/alocacao/{aid}/mes", json={"periodo": p, "valor": "0"})
    r = cli.put(f"/api/alocacao/{aid}/mes", json={"periodo": pers[-1], "valor": "0"})
    assert r.status_code == 200
    body = r.json()
    assert "estado" in body and "impacto" not in body
    assert conn.execute("SELECT 1 FROM alocacao WHERE alocacao_id=?", (aid,)).fetchone() is None


def _segundo_projeto(conn, matricula, tipo, horas):
    """Cria um 2º projeto com uma alocação da mesma pessoa — pro sample_path só ter
    OTIMIZEPLAN, testes de multi-projeto/multi-pessoa precisam disso na mão."""
    conn.execute(
        "INSERT INTO projeto (projeto_id, nome, criado_na_ferramenta) VALUES (2, 'BRAVO 2', 0)"
    )
    cur = conn.execute(
        "INSERT INTO alocacao (projeto_id, matricula, tipo_alocacao) VALUES (2, ?, ?)",
        (matricula, tipo),
    )
    aid = cur.lastrowid
    conn.execute(
        "INSERT INTO alocacao_mes (alocacao_id, periodo, horas) VALUES (?, '2026-08-01', ?)",
        (aid, horas),
    )
    conn.commit()
    return aid


def test_impacto_lote_com_dois_projetos_e_periodos_diferentes(conn, sample_path):
    """Um mes-lote que toca alocações de dois PROJETOS diferentes (e dois períodos
    diferentes) tem que devolver grupo/projeto pra cada um, com o total certo em
    cada período — sem misturar um total no período do outro."""
    import_workbook(conn, sample_path)
    conn.execute("UPDATE pessoa SET capacidade_mensal=176 WHERE matricula='73920'")
    conn.commit()
    aid2 = _segundo_projeto(conn, "73920", "Técnica", 40)
    aid1 = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]

    impacto = aggregate.impacto_edicao(conn, [(aid1, "2026-07-01"), (aid2, "2026-08-01")])
    completo = aggregate.build_grade(conn, periodo_ativo=False)

    assert {g["projeto_id"] for g in impacto["grupos"]} == {1, 2}
    assert {p["projeto_id"] for p in impacto["projetos"]} == {1, 2}
    assert len(impacto["pessoas"]) == 1   # mesma pessoa nos dois -> uma entrada só

    proj1 = next(p for p in completo["por_projeto"] if p["projeto_id"] == 1)
    proj2 = next(p for p in completo["por_projeto"] if p["projeto_id"] == 2)
    imp_proj1 = next(p for p in impacto["projetos"] if p["projeto_id"] == 1)
    imp_proj2 = next(p for p in impacto["projetos"] if p["projeto_id"] == 2)
    assert imp_proj1["totais"]["2026-07-01"] == proj1["totais"]["2026-07-01"]
    assert imp_proj2["totais"]["2026-08-01"] == proj2["totais"]["2026-08-01"]
    # o lote tocou 07/26 E 08/26 (em projetos diferentes) -> o total de CADA projeto
    # é recalculado pra AMBOS os períodos tocados no lote (não só o período da sua
    # própria célula) — outras pessoas do projeto 1 podem ter hora em 08/26 também;
    # o que importa é bater com o build_grade, não ficar restrito à própria célula.
    if "2026-08-01" in imp_proj1["totais"]:
        assert imp_proj1["totais"]["2026-08-01"] == proj1["totais"].get("2026-08-01", 0)
    if "2026-07-01" in imp_proj2["totais"]:
        assert imp_proj2["totais"]["2026-07-01"] == proj2["totais"].get("2026-07-01", 0)

    pessoa = next(r for r in completo["por_recurso"] if r["matricula"] == "73920")
    imp_pessoa = impacto["pessoas"][0]
    assert imp_pessoa["totais"]["2026-07-01"] == pessoa["totais"]["2026-07-01"]
    assert imp_pessoa["totais"]["2026-08-01"] == pessoa["totais"]["2026-08-01"]
    assert imp_pessoa["cores"].get("2026-07-01") == pessoa["cores"].get("2026-07-01")
    assert imp_pessoa["cores"].get("2026-08-01") == pessoa["cores"].get("2026-08-01")
    tipos_repintar = {(l["projeto_id"], l["tipo_alocacao"]) for l in imp_pessoa["linhas_para_repintar"]}
    assert tipos_repintar == {(1, "TecnicaANP"), (2, "Técnica")}


def test_impacto_cor_some_quando_bate_exatamente_a_capacidade(conn, sample_path):
    import_workbook(conn, sample_path)
    conn.execute("UPDATE pessoa SET capacidade_mensal=88 WHERE matricula='73920'")
    conn.commit()
    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]
    conn.execute("UPDATE alocacao_mes SET horas=88 WHERE alocacao_id=? AND periodo='2026-07-01'", (aid,))
    conn.commit()

    impacto = aggregate.impacto_edicao(conn, [(aid, "2026-07-01")])
    completo = aggregate.build_grade(conn, periodo_ativo=False)
    pessoa = next(r for r in completo["por_recurso"] if r["matricula"] == "73920")

    assert "2026-07-01" not in pessoa["cores"]   # == capacidade -> sem cor
    assert impacto["pessoas"][0]["cores"].get("2026-07-01") is None


def test_impacto_totais_alterado_esvazia_quando_volta_a_bater_com_baseline(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "baseline")   # 88h em 2026-07 vira a baseline
    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]

    conn.execute("UPDATE alocacao_mes SET horas=200 WHERE alocacao_id=? AND periodo='2026-07-01'", (aid,))
    conn.commit()
    impacto_mudou = aggregate.impacto_edicao(conn, [(aid, "2026-07-01")])
    assert "2026-07-01" in impacto_mudou["grupos"][0]["totais_alterado"]
    assert "2026-07-01" in impacto_mudou["projetos"][0]["totais_alterado"]
    assert "2026-07-01" in impacto_mudou["pessoas"][0]["totais_alterado"]
    assert impacto_mudou["alocacoes"][0]["alterado"] is True

    conn.execute("UPDATE alocacao_mes SET horas=88 WHERE alocacao_id=? AND periodo='2026-07-01'", (aid,))
    conn.commit()
    impacto_voltou = aggregate.impacto_edicao(conn, [(aid, "2026-07-01")])
    assert impacto_voltou["grupos"][0]["totais_alterado"] == []
    assert impacto_voltou["projetos"][0]["totais_alterado"] == []
    assert impacto_voltou["pessoas"][0]["totais_alterado"] == []
    assert impacto_voltou["alocacoes"][0]["alterado"] is False


def test_impacto_alocacao_nao_commitada_fica_marcada_novo(conn, sample_path):
    """Uma alocação criada agora (sem baseline nenhuma ainda) e editada de novo em
    seguida — não é estrutural (já existe, continua existindo) — tem que continuar
    marcada `novo: true`, igual o build_grade marcaria."""
    import_workbook(conn, sample_path)
    versao.commit(conn, "baseline")   # fecha a baseline SEM a nova pessoa/alocação
    cur = conn.execute(
        "INSERT INTO alocacao (projeto_id, matricula, tipo_alocacao) VALUES (1,'79483','TecnicaEPII')"
    )
    aid = cur.lastrowid
    conn.execute(
        "INSERT INTO alocacao_mes (alocacao_id, periodo, horas) VALUES (?, '2026-07-01', 40)", (aid,)
    )
    conn.commit()

    impacto = aggregate.impacto_edicao(conn, [(aid, "2026-07-01")])
    completo = aggregate.build_grade(conn, periodo_ativo=False)
    grp = next(g for g in completo["por_projeto"][0]["grupos"] if g["tipo_alocacao"] == "TecnicaEPII")
    leaf = next(f for f in grp["filhos"] if f["alocacao_id"] == aid)

    assert impacto["alocacoes"][0]["novo"] is True
    assert leaf["novo"] is True   # confirma que build_grade concorda


def test_impacto_edicao_com_tocados_vazio():
    import sqlite3
    conn = sqlite3.connect(":memory:")
    assert aggregate.impacto_edicao(conn, []) == {
        "alocacoes": [], "grupos": [], "projetos": [], "pessoas": [],
    }


def test_endpoint_mes_lote_com_edicoes_em_dois_projetos_numa_so_chamada(conn, sample_path):
    import_workbook(conn, sample_path)
    aid2 = _segundo_projeto(conn, "73920", "Técnica", 40)
    cli = _client(conn)
    aid1 = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]
    r = cli.put("/api/alocacao/mes-lote", json={"edits": [
        {"alocacao_id": aid1, "periodo": "2026-07-01", "valor": "100"},
        {"alocacao_id": aid2, "periodo": "2026-08-01", "valor": "50"},
    ]})
    assert r.status_code == 200
    body = r.json()["impacto"]
    assert len(body["alocacoes"]) == 2
    assert {a["horas"] for a in body["alocacoes"]} == {100, 50}
    assert {g["projeto_id"] for g in body["grupos"]} == {1, 2}


def test_outras_rotas_continuam_com_estado_completo(conn, sample_path):
    """Fora do escopo desta mudança: qualquer outra rota mutante continua devolvendo
    `estado` completo, nunca `impacto` (docs/PERFORMANCE.md "Fora de escopo")."""
    import_workbook(conn, sample_path)
    cli = _client(conn)
    r = cli.post("/api/projetos", json={"nome": "Projeto novo de teste"})
    assert r.status_code == 200
    body = r.json()
    assert "estado" in body and "impacto" not in body

from app import versao
from app.aggregate import build_grade, cor_pessoa_mes
from app.xlsx_import import import_workbook


def test_cor_sobre_e_subalocacao(conn, sample_path):
    import_workbook(conn, sample_path)
    # Augusto soma em 07/26 = 88 (só TecnicaANP). Sem capacidade -> sem cor.
    assert cor_pessoa_mes(conn) == {} or all(k[0] != "73920" for k in cor_pessoa_mes(conn))

    conn.execute("UPDATE pessoa SET capacidade_mensal=88 WHERE matricula='73920'")
    cores = cor_pessoa_mes(conn)
    assert cores[("73920", "2026-06-01")] == "vermelho"  # 52 < 88 (subalocado)
    assert ("73920", "2026-07-01") not in cores           # 88 == 88
    assert ("73920", "2026-05-01") not in cores           # 0 -> sem cor

    conn.execute("UPDATE pessoa SET capacidade_mensal=40 WHERE matricula='73920'")
    assert cor_pessoa_mes(conn)[("73920", "2026-07-01")] == "amarelo"   # 88 > 40 (superalocado)


def test_grade_agrupada_por_tipo(conn, sample_path):
    import_workbook(conn, sample_path)
    g = build_grade(conn)
    assert len(g["por_projeto"]) == 1
    proj = g["por_projeto"][0]

    # OTIMIZEPLAN tem os grupos TecnicaANP e TecnicaEPII
    tipos = sorted(grp["tipo_alocacao"] for grp in proj["grupos"])
    assert tipos == ["TecnicaANP", "TecnicaEPII"]

    todos = [f for grp in proj["grupos"] for f in grp["filhos"]]
    soma = sum(f["horas"].get("2026-05-01", 0) for f in todos)
    assert proj["totais"]["2026-05-01"] == soma

    for grp in proj["grupos"]:
        nomes = [f["nome"] for f in grp["filhos"]]
        assert nomes == sorted(nomes, key=str.lower)
        gsoma = sum(f["horas"].get("2026-05-01", 0) for f in grp["filhos"])
        assert grp["totais"].get("2026-05-01", 0) == gsoma


def test_grade_marca_alteracao_vs_baseline(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "importa amostra")     # HEAD = estado do arquivo
    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]
    conn.execute("UPDATE alocacao_mes SET horas=123 WHERE alocacao_id=? AND periodo='2026-07-01'", (aid,))
    conn.commit()
    g = build_grade(conn)
    f = next(
        f for grp in g["por_projeto"][0]["grupos"] for f in grp["filhos"]
        if f["matricula"] == "73920" and f["tipo_alocacao"] == "TecnicaANP"
    )
    assert f["alterado"] == ["2026-07-01"]
    assert f["novo"] is False
    assert f["base_horas"]["2026-07-01"] == 88


def test_filtro_periodo_linha_visivel_so_se_coluna_visivel(conn, sample_path):
    """docs/ESPECIFICACAO.md §8 — bug corrigido em 2026-09-12: reproduz o caso real
    (pessoa com hora só num mês passado, nada dali em diante) que o antigo `_fut`
    escondia inteiro mesmo com a coluna daquele mês visível na tela, deixando o total
    do mês "fantasma" sem linha que o explicasse. A linha só pode sumir junto com a
    coluna que a explica — nunca antes."""
    import_workbook(conn, sample_path)
    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]
    # simula "só tem hora num mês passado, nada no futuro" (ex.: férias/desligamento).
    conn.execute("DELETE FROM alocacao_mes WHERE alocacao_id=? AND periodo!='2026-06-01'", (aid,))
    conn.commit()

    def grade_e_filha(**kw):
        g = build_grade(conn, **kw)
        f = next(
            f for grp in g["por_projeto"][0]["grupos"] for f in grp["filhos"]
            if f["alocacao_id"] == aid
        )
        return g, f

    # janela inclui 06/26 -> coluna e linha aparecem juntas.
    g, f = grade_e_filha(periodo_inicial="2026-06-01")
    assert "2026-06-01" in g["periodos"]
    assert f["tem_horas_no_periodo"] is True

    # janela começa em 07/26 -> a coluna 06/26 some, e a linha tem que sumir junto
    # (critério idêntico ao das colunas) — é exatamente o bug que existia antes.
    g, f = grade_e_filha(periodo_inicial="2026-07-01")
    assert "2026-06-01" not in g["periodos"]
    assert f["tem_horas_no_periodo"] is False


def test_filtro_periodo_desligado_mostra_tudo(conn, sample_path):
    """periodo_ativo=False é um estado à parte de "não configurado" — período inicial
    fica genuinamente aberto (None), não cai no padrão de hoje."""
    import_workbook(conn, sample_path)
    g = build_grade(conn, periodo_ativo=False)
    assert g["periodo_ativo"] is False
    assert g["periodo_inicial"] is None
    assert g["periodo_final"] is None
    assert "2026-05-01" in g["periodos"]   # nenhum corte, mês antigo da amostra aparece

    proj = g["por_projeto"][0]
    assert proj["visivel_no_periodo"] is True
    todos = [f for grp in proj["grupos"] for f in grp["filhos"]]
    assert all(f["tem_horas_no_periodo"] for f in todos if any(f["horas"].values()))


def test_grade_por_recurso_agrega_multiplos_tipos(conn, sample_path):
    import_workbook(conn, sample_path)
    # na amostra ninguém tem 2 tipos com horas > 0 (linhas zeradas não viram
    # registro); cria uma 2ª alocação para 79483 e confere a agregação.
    cur = conn.execute(
        "INSERT INTO alocacao (projeto_id, matricula, tipo_alocacao) VALUES (1,'79483','TecnicaEPII')"
    )
    conn.execute(
        "INSERT INTO alocacao_mes (alocacao_id, periodo, horas) VALUES (?, '2026-07-01', 40)",
        (cur.lastrowid,),
    )
    conn.commit()
    g = build_grade(conn)
    gabriel = next(r for r in g["por_recurso"] if r["matricula"] == "79483")
    tipos = sorted(f["tipo_alocacao"] for f in gabriel["filhos"])
    assert tipos == ["TecnicaANP", "TecnicaEPII"]

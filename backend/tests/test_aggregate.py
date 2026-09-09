from app.aggregate import build_grade, cor_pessoa_mes
from app.xlsx_import import import_workbook


def test_cor_sobre_e_subalocacao(conn, sample_path):
    import_workbook(conn, sample_path)
    # Augusto soma em 07/26 = 88 (só TecnicaANP). Sem capacidade -> sem cor.
    assert cor_pessoa_mes(conn) == {} or all(k[0] != "73920" for k in cor_pessoa_mes(conn))

    conn.execute("UPDATE pessoa SET capacidade_mensal=88 WHERE matricula='73920'")
    cores = cor_pessoa_mes(conn)
    assert cores[("73920", "2026-06-01")] == "amarelo"   # 52 < 88
    assert ("73920", "2026-07-01") not in cores           # 88 == 88
    assert ("73920", "2026-05-01") not in cores           # 0 -> sem cor

    conn.execute("UPDATE pessoa SET capacidade_mensal=40 WHERE matricula='73920'")
    assert cor_pessoa_mes(conn)[("73920", "2026-07-01")] == "vermelho"  # 88 > 40


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

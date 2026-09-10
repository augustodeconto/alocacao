from app.xlsx_import import import_workbook


def test_import_sample_basico(conn, sample_path):
    r = import_workbook(conn, sample_path)
    assert r.status == "imported"

    proj = conn.execute("SELECT * FROM projeto").fetchone()
    assert proj["nome"] == "OTIMIZEPLAN"
    assert proj["id_projeto_externo"] == "16045"

    periodos = [x["periodo"] for x in conn.execute(
        "SELECT periodo FROM projeto_periodo ORDER BY ordem")]
    assert periodos[0] == "2026-05-01"
    assert len(periodos) == 10

    # a amostra tem 21 linhas de Alocacao; 9 são inteiramente zeradas
    # ("no rol, sem alocação") e não viram registro.
    assert conn.execute("SELECT COUNT(*) c FROM alocacao").fetchone()["c"] == 12
    assert conn.execute("SELECT COUNT(*) c FROM alocacao_mes WHERE horas<=0").fetchone()["c"] == 0

    # Augusto / TecnicaANP: 0,52,88,88,88,88,88,88,0,0 -> os meses 0 não viram
    # registro (ausência == 0).
    horas = dict(conn.execute(
        """SELECT m.periodo, m.horas FROM alocacao_mes m
           JOIN alocacao a USING(alocacao_id)
           WHERE a.matricula='73920' AND a.tipo_alocacao='TecnicaANP'"""
    ).fetchall())
    assert "2026-05-01" not in horas
    assert "2027-01-01" not in horas
    assert horas["2026-06-01"] == 52  # veio de "=88-36"
    assert horas["2026-07-01"] == 88


def test_capacidade_semeada_de_novos_pesquisadores(conn, sample_path):
    import_workbook(conn, sample_path)
    p = conn.execute("SELECT * FROM pessoa WHERE matricula='36925'").fetchone()
    assert p["carga_diaria"] == 8
    assert p["capacidade_mensal"] == 176  # 8 * 22


def test_reimport_atualiza_working_mantem_baseline(conn, sample_path):
    assert import_workbook(conn, sample_path).status == "imported"
    # move o working (simula edição) e a baseline continua a original
    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]
    conn.execute("UPDATE alocacao_mes SET horas=999 WHERE alocacao_id=? AND periodo='2026-07-01'", (aid,))
    conn.commit()

    r2 = import_workbook(conn, sample_path)
    assert r2.status == "updated"
    assert conn.execute("SELECT COUNT(*) c FROM projeto").fetchone()["c"] == 1
    # working voltou ao valor do arquivo (88), baseline permaneceu 88 também
    h = conn.execute(
        """SELECT m.horas FROM alocacao_mes m JOIN alocacao a USING(alocacao_id)
           WHERE a.matricula='73920' AND a.tipo_alocacao='TecnicaANP' AND m.periodo='2026-07-01'"""
    ).fetchone()["horas"]
    assert h == 88
    assert conn.execute("SELECT COUNT(*) c FROM alocacao").fetchone()["c"] == 12


def test_anotacoes_apenas_da_alocacao(conn, sample_path):
    import_workbook(conn, sample_path)
    textos = [x["texto"] for x in conn.execute("SELECT texto FROM anotacao")]
    assert "50% pesquisador" in textos
    assert all("0 ou 1" not in t for t in textos)  # instruções de dados_projeto não entram
    assert len(textos) == len(set(textos)) or textos.count("50% pesquisador") == 1

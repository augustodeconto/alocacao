"""Versionamento estilo Git: commit / branch / checkout / log / descartar."""
import pytest

from app import versao
from app.xlsx_import import import_workbook


def _julho(conn):
    return conn.execute(
        "SELECT COALESCE(SUM(horas),0) s FROM alocacao_mes WHERE periodo='2026-07-01'"
    ).fetchone()["s"]


def test_seed_cria_main_e_head(conn):
    h = conn.execute("SELECT * FROM head_ WHERE id=1").fetchone()
    assert h["ref_nome"] == "main"
    raiz = conn.execute("SELECT * FROM commit_ WHERE commit_id=?", (h["base_commit_id"],)).fetchone()
    assert raiz["parent_id"] is None and raiz["origem"] == "seed"
    assert not versao.sujo(conn)


def test_import_fica_pendente_e_commit_limpa(conn, sample_path):
    import_workbook(conn, sample_path)
    assert versao.sujo(conn)
    cid = versao.commit(conn, "importa amostra")
    assert not versao.sujo(conn)
    assert versao.log(conn)[0]["commit_id"] == cid
    with pytest.raises(versao.NadaParaCommitar):
        versao.commit(conn, "nada mudou")


def test_branch_isola_edicoes(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    base_julho = _julho(conn)

    versao.branch(conn, "cenario", trocar=True)
    conn.execute("UPDATE alocacao_mes SET horas = horas + 10 WHERE periodo='2026-07-01'")
    conn.commit()
    versao.commit(conn, "sobe julho no cenário")
    cen_julho = _julho(conn)
    assert cen_julho > base_julho

    versao.checkout(conn, "main")
    assert _julho(conn) == base_julho          # main intocada
    versao.checkout(conn, "cenario")
    assert _julho(conn) == cen_julho           # volta ao estado do cenário


def test_checkout_bloqueia_com_working_sujo(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    versao.branch(conn, "outra")
    conn.execute("UPDATE alocacao_mes SET horas = 999 WHERE periodo='2026-07-01'")
    conn.commit()
    with pytest.raises(versao.VersaoSuja):
        versao.checkout(conn, "outra")


def test_descartar_volta_ao_head(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    antes = _julho(conn)
    conn.execute("UPDATE alocacao_mes SET horas = horas + 5 WHERE periodo='2026-07-01'")
    conn.commit()
    assert _julho(conn) != antes
    versao.descartar(conn)
    assert _julho(conn) == antes
    assert not versao.sujo(conn)


def test_log_segue_a_cadeia_de_pais(conn, sample_path):
    import_workbook(conn, sample_path)
    a = versao.commit(conn, "c1")
    conn.execute("UPDATE alocacao_mes SET horas = horas + 1 WHERE periodo='2026-07-01'")
    conn.commit()
    b = versao.commit(conn, "c2")
    ids = [c["commit_id"] for c in versao.log(conn)]
    assert ids[:2] == [b, a]
    assert ids[-1] == 1                         # commit raiz (seed)


def test_nao_apaga_main_nem_branch_atual(conn):
    with pytest.raises(ValueError):
        versao.deletar_branch(conn, "main")
    versao.branch(conn, "tmp", trocar=True)
    with pytest.raises(ValueError):
        versao.deletar_branch(conn, "tmp")
    versao.checkout(conn, "main")
    versao.deletar_branch(conn, "tmp")
    assert conn.execute("SELECT COUNT(*) c FROM ref_").fetchone()["c"] == 1

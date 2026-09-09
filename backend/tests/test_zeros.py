"""Opção B: o banco não guarda registro de alocação zerado."""
from fastapi.testclient import TestClient

from app import main as app_main
from app.xlsx_import import import_workbook


def _client(conn):
    app_main._conn = conn
    return TestClient(app_main.app)


def _aloc(conn, mat, tipo):
    return conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE matricula=? AND tipo_alocacao=?", (mat, tipo)
    ).fetchone()


def test_import_nao_grava_meses_zerados(conn, sample_path):
    import_workbook(conn, sample_path)
    assert conn.execute("SELECT COUNT(*) c FROM alocacao_mes WHERE horas<=0").fetchone()["c"] == 0
    # linha inteiramente zerada da amostra não virou registro
    assert _aloc(conn, "79483", "TecnicaEPII") is None


def test_zerar_todos_os_meses_remove_a_alocacao(conn, sample_path):
    import_workbook(conn, sample_path)
    cli = _client(conn)
    a = _aloc(conn, "73920", "TecnicaANP")["alocacao_id"]
    pers = [r["periodo"] for r in conn.execute(
        "SELECT periodo FROM alocacao_mes WHERE alocacao_id=?", (a,)
    )]
    assert pers
    r = cli.put("/api/alocacao/mes-lote", json={
        "edits": [{"alocacao_id": a, "periodo": p, "valor": "0"} for p in pers]
    })
    assert r.status_code == 200
    assert conn.execute("SELECT 1 FROM alocacao WHERE alocacao_id=?", (a,)).fetchone() is None


def test_undo_recria_alocacao_zerada(conn, sample_path):
    import_workbook(conn, sample_path)
    cli = _client(conn)
    a = _aloc(conn, "73920", "TecnicaANP")["alocacao_id"]
    # zera tudo -> some
    pers = [r["periodo"] for r in conn.execute(
        "SELECT periodo FROM alocacao_mes WHERE alocacao_id=?", (a,)
    )]
    cli.put("/api/alocacao/mes-lote", json={
        "edits": [{"alocacao_id": a, "periodo": p, "valor": "0"} for p in pers]
    })
    assert conn.execute("SELECT 1 FROM alocacao WHERE alocacao_id=?", (a,)).fetchone() is None
    # undo: reaplica com identidade e valor > 0
    r = cli.put("/api/alocacao/mes-lote", json={"edits": [{
        "alocacao_id": a, "periodo": "2026-07-01", "valor": "88",
        "projeto_id": 1, "matricula": "73920", "tipo_alocacao": "TecnicaANP",
    }]})
    assert r.status_code == 200
    novo = _aloc(conn, "73920", "TecnicaANP")
    assert novo is not None
    h = conn.execute(
        "SELECT horas FROM alocacao_mes WHERE alocacao_id=? AND periodo='2026-07-01'",
        (novo["alocacao_id"],),
    ).fetchone()["horas"]
    assert h == 88

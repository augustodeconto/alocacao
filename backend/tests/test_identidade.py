"""Identidade + papel + apelido (docs/COLABORACAO.md "Identidade e papel de acesso").

Escopo desta rodada: a identidade "chega" em todo o sistema (backend resolve X-Autor,
frontend mostra usuário/papel) — NÃO inclui aplicar a matriz de permissão de verdade.
"""
from pathlib import Path

import pytest

from app import db as dbmod
from app import versao

BI2 = Path(__file__).resolve().parents[2] / "amostras" / "bi2"
FILES = sorted(str(p) for p in BI2.glob("*.xlsx")) if BI2.exists() else []


def _cli(conn):
    from fastapi.testclient import TestClient
    from app import main as m
    m._conn = conn
    return TestClient(m.app)


# -- 1. migração -------------------------------------------------------------------
def test_migracao_papel_apelido_idempotente_e_sistema_uma_vez(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(pessoa)")}
    assert {"papel", "apelido"} <= cols

    sistema = conn.execute("SELECT * FROM pessoa WHERE matricula='SISTEMA'").fetchone()
    assert sistema is not None
    assert sistema["papel"] == "admin"
    assert sistema["nome"] == "Sistema"

    # roda de novo — não duplica, não quebra (ALTER idempotente + INSERT OR IGNORE)
    dbmod._migrate(conn)
    conn.commit()
    n = conn.execute("SELECT COUNT(*) c FROM pessoa WHERE matricula='SISTEMA'").fetchone()["c"]
    assert n == 1


def test_papel_apelido_fora_do_versionamento(conn):
    assert "papel" not in versao.PESSOA_COLS
    assert "apelido" not in versao.PESSOA_COLS


# -- 2. usuario_atual em /api/estado -------------------------------------------------
def test_usuario_atual_reflete_x_autor(conn):
    conn.execute(
        "INSERT INTO pessoa (matricula, nome, papel) VALUES ('42', 'Fulano de Tal', 'gp')")
    conn.commit()
    cli = _cli(conn)

    r = cli.get("/api/estado")
    assert r.json()["usuario_atual"] == {"matricula": None, "nome_exibicao": None, "papel": "leitura"}

    r = cli.get("/api/estado", headers={"X-Autor": "42"})
    u = r.json()["usuario_atual"]
    assert u == {"matricula": "42", "nome_exibicao": "Fulano", "papel": "gp"}


def test_usuario_atual_apelido_tem_prioridade_sobre_nome(conn):
    conn.execute(
        "INSERT INTO pessoa (matricula, nome, apelido) VALUES ('43', 'Beltrano da Silva', 'Bel')")
    conn.commit()
    cli = _cli(conn)
    r = cli.get("/api/estado", headers={"X-Autor": "43"})
    assert r.json()["usuario_atual"]["nome_exibicao"] == "Bel"


# -- 3. apelido: self-service, fora do diff ------------------------------------------
def test_editar_apelido_nao_entra_no_diff(conn, sample_path):
    from app.xlsx_import import import_workbook
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    assert not versao.sujo(conn)

    mat = conn.execute("SELECT matricula FROM pessoa LIMIT 1").fetchone()["matricula"]
    # chg_pessoa já tem a linha da pessoa desde o commit "base" (schema nem tem coluna
    # apelido — é fisicamente impossível ela aparecer lá); o que importa é que editar o
    # apelido não gera commit/pendência nem mexe nessa contagem.
    n_chg_antes = conn.execute(
        "SELECT COUNT(*) c FROM chg_pessoa WHERE matricula=?", (mat,)).fetchone()["c"]

    cli = _cli(conn)
    r = cli.put(f"/api/pessoa/{mat}/apelido", json={"apelido": "Apelidão"},
                headers={"X-Autor": mat})
    assert r.status_code == 200
    assert conn.execute(
        "SELECT apelido FROM pessoa WHERE matricula=?", (mat,)).fetchone()["apelido"] == "Apelidão"

    assert not versao.sujo(conn)     # apelido não aparece como pendência de commit
    n_chg_depois = conn.execute(
        "SELECT COUNT(*) c FROM chg_pessoa WHERE matricula=?", (mat,)).fetchone()["c"]
    assert n_chg_depois == n_chg_antes


def test_editar_apelido_alheio_e_recusado(conn):
    conn.execute("INSERT INTO pessoa (matricula, nome) VALUES ('1', 'Um'), ('2', 'Dois')")
    conn.commit()
    cli = _cli(conn)
    r = cli.put("/api/pessoa/2/apelido", json={"apelido": "X"}, headers={"X-Autor": "1"})
    assert r.status_code == 403


# -- 4. autoria da importação do BI --------------------------------------------------
@pytest.mark.skipif(not FILES, reason="amostras/bi2 ausente")
def test_importacao_bi_grava_autor_de_quem_disparou(conn, sample_path):
    from app.bi_import import importar_arquivos
    from app.xlsx_import import import_workbook

    import_workbook(conn, sample_path)
    conn.execute("INSERT OR IGNORE INTO pessoa (matricula, nome) VALUES ('77', 'Quem Importou')")
    conn.commit()

    importar_arquivos(conn, FILES, autor="77")
    commits_bi = [c for c in versao.log(conn, "BI") if c["origem"] == "bi"]
    assert commits_bi, "nenhum commit de BI gerado — checar amostras/bi2"
    assert commits_bi[0]["autor"] == "77"
    assert commits_bi[0]["autor"] != "BI"     # o bug antigo: string fixa, não pessoa real

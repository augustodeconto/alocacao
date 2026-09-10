"""BI v2: arquivo projetos.xlsx (GP), valor/hora, sobrescrita dos campos do BI."""
from pathlib import Path

import pytest

from app.bi_import import importar_arquivos
from app.xlsx_import import import_workbook

BI2 = Path(__file__).resolve().parents[2] / "amostras" / "bi2"
FILES = sorted(str(p) for p in BI2.glob("*.xlsx")) if BI2.exists() else []

pytestmark = pytest.mark.skipif(not FILES, reason="amostras/bi2 ausente")


def test_classifica_projetos_e_ignora_pivot(conn):
    res = {Path(r["arquivo"]).name.split("-")[-1]: r for r in importar_arquivos(conn, FILES)}
    assert res["projetos.xlsx"]["status"] == "ok"
    assert res["projetocolabalocacao.xlsx"]["status"] == "ignorado"
    assert conn.execute("SELECT COUNT(*) c FROM bi_projeto WHERE gestor IS NOT NULL").fetchone()["c"] > 50


def test_gp_sincroniza_no_projeto(conn, sample_path):
    import_workbook(conn, sample_path)          # OTIMIZEPLAN, id_projeto_externo 16045
    importar_arquivos(conn, FILES)
    pr = conn.execute("SELECT gestor_projetos, empresa FROM projeto WHERE id_projeto_externo='16045'").fetchone()
    assert pr["gestor_projetos"]               # veio do projetos.xlsx


def _uma_matricula_do_colabs():
    from app.xlsx_io import Workbook
    f = next(x for x in FILES if "colabs" in x)
    s = Workbook(f).sheet("Export")
    return str(int(s.cell(2, 1)))


def test_valor_hora_e_sobrescrita(conn, sample_path):
    import_workbook(conn, sample_path)
    mat = _uma_matricula_do_colabs()
    conn.execute("INSERT OR IGNORE INTO pessoa (matricula, nome) VALUES (?, 'Z')", (mat,))
    conn.execute("UPDATE pessoa SET area='X', tipo_contrato='Y' WHERE matricula=?", (mat,))
    conn.commit()

    importar_arquivos(conn, FILES)
    p = conn.execute("SELECT area, tipo_contrato FROM pessoa WHERE matricula=?", (mat,)).fetchone()
    assert p["area"] != "X" and p["tipo_contrato"] != "Y"   # BI sobrescreveu
    # AUGUSTO (73920) está no colabmescusto -> ganhou valor/hora
    vh = conn.execute("SELECT valor_hora FROM pessoa WHERE matricula='73920'").fetchone()["valor_hora"]
    assert vh and vh > 0
    assert conn.execute("SELECT COUNT(*) c FROM pessoa WHERE valor_hora > 0").fetchone()["c"] > 10


def test_bi_monta_projetos_e_alocacao(conn, sample_path):
    from app import versao

    import_workbook(conn, sample_path)
    versao.commit(conn, "importa amostra")     # sem edições pendentes
    importar_arquivos(conn, FILES)

    assert conn.execute("SELECT COUNT(*) c FROM projeto").fetchone()["c"] > 50
    assert conn.execute("SELECT COUNT(DISTINCT projeto_id) c FROM alocacao").fetchone()["c"] > 20

    # BI = 1 commit na main; nada pendente -> working == estado do BI
    assert versao.tip_commit(conn, "main") == conn.execute(
        "SELECT base_commit_id FROM head_ WHERE id=1").fetchone()["base_commit_id"]
    assert versao.log(conn)[0]["origem"] == "bi"
    assert not versao.sujo(conn)
    w = conn.execute("SELECT COUNT(*) c FROM alocacao WHERE projeto_id=1").fetchone()["c"]
    b = conn.execute("SELECT COUNT(*) c FROM baseline_alocacao WHERE projeto_id=1").fetchone()["c"]
    assert w == b and w > 0


def test_importar_projeto_recusa_extrato_bi(conn, sample_path):
    from fastapi.testclient import TestClient
    from app import main as m
    m._conn = conn
    cli = TestClient(m.app)
    with open(FILES[0], "rb") as f:                 # b72400f4-colabs.xlsx
        r = cli.post("/api/importar-projeto", files={"arquivos": ("colabs.xlsx", f)})
    assert r.status_code == 200
    assert r.json()["resultados"][0]["status"] == "skipped"
    with open(sample_path, "rb") as f:
        r = cli.post("/api/importar-projeto", files={"arquivos": ("proj.xlsx", f)})
    assert r.json()["resultados"][0]["status"] == "imported"


def test_bi_nao_toca_branch_de_cenario(conn, sample_path):
    from app import versao

    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    versao.branch(conn, "cenario", trocar=True)     # HEAD sai da main
    aid = conn.execute("SELECT alocacao_id FROM alocacao WHERE projeto_id=1 LIMIT 1").fetchone()["alocacao_id"]
    conn.execute("INSERT INTO alocacao_mes (alocacao_id,periodo,horas) VALUES (?,?,5555) "
                 "ON CONFLICT(alocacao_id,periodo) DO UPDATE SET horas=5555", (aid, "2026-07-01"))
    conn.commit()
    tip_cenario = versao.tip_commit(conn, "cenario")
    p1 = lambda: sorted((r["matricula"], r["tipo_alocacao"], r["periodo"], r["horas"])
                        for r in conn.execute("SELECT a.matricula,a.tipo_alocacao,m.periodo,m.horas "
                                              "FROM alocacao_mes m JOIN alocacao a USING(alocacao_id) "
                                              "WHERE a.projeto_id=1"))
    p1_antes = p1()

    importar_arquivos(conn, FILES)

    assert versao.log(conn, "main")[0]["origem"] == "bi"        # BI foi pra main
    assert versao.tip_commit(conn, "cenario") == tip_cenario    # cenário não mexeu
    assert conn.execute("SELECT ref_nome FROM head_ WHERE id=1").fetchone()["ref_nome"] == "cenario"
    assert p1() == p1_antes                                     # working do usuário intacto
    assert conn.execute("SELECT COUNT(*) c FROM alocacao WHERE projeto_id<>1").fetchone()["c"] == 0


def test_bi_preserva_edicao_de_alocacao_pendente(conn, sample_path):
    from app import versao

    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    # edição de alocação NÃO commitada
    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE projeto_id=1 AND tipo_alocacao='TecnicaANP' LIMIT 1"
    ).fetchone()["alocacao_id"]
    conn.execute("INSERT INTO alocacao_mes (alocacao_id,periodo,horas) VALUES (?,?,4242) "
                 "ON CONFLICT(alocacao_id,periodo) DO UPDATE SET horas=4242", (aid, "2026-07-01"))
    conn.commit()

    importar_arquivos(conn, FILES)

    # a edição sobreviveu no working, ancorada no BI novo (aparece como pendente)
    assert conn.execute(
        "SELECT horas FROM alocacao_mes WHERE alocacao_id IN "
        "(SELECT alocacao_id FROM alocacao WHERE projeto_id=1 AND tipo_alocacao='TecnicaANP') "
        "AND periodo='2026-07-01' AND horas=4242").fetchone() is not None
    assert versao.sujo(conn)
    assert versao.log(conn)[0]["origem"] == "bi"

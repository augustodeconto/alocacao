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

    import_workbook(conn, sample_path)  # OTIMIZEPLAN já editável, com working
    versao.commit(conn, "importa amostra")
    working_antes = conn.execute("SELECT COUNT(*) c FROM alocacao WHERE projeto_id=1").fetchone()["c"]
    importar_arquivos(conn, FILES)

    # muitos projetos no grid (não só o OTIMIZEPLAN)
    assert conn.execute("SELECT COUNT(*) c FROM projeto").fetchone()["c"] > 50
    # projetos novos ganharam working = HEAD
    assert conn.execute("SELECT COUNT(DISTINCT projeto_id) c FROM alocacao").fetchone()["c"] > 20
    # OTIMIZEPLAN manteve o working editado; o HEAD foi para o do BI
    assert conn.execute("SELECT COUNT(*) c FROM alocacao WHERE projeto_id=1").fetchone()["c"] == working_antes
    log = versao.log(conn)
    assert log[0]["origem"] == "bi"          # rebuild do BI registrou um commit

    # descartar -> working volta ao HEAD (que agora é o do BI)
    versao.descartar(conn, 1)
    w = conn.execute("SELECT COUNT(*) c FROM alocacao WHERE projeto_id=1").fetchone()["c"]
    b = conn.execute("SELECT COUNT(*) c FROM baseline_alocacao WHERE projeto_id=1").fetchone()["c"]
    assert w == b

from pathlib import Path

import pytest

from app.bi_import import importar_arquivos
from app.xlsx_import import import_workbook

BI = Path(__file__).resolve().parents[2] / "amostras" / "bi"
FILES = [
    str(BI / "4a330340-equipe.xlsx"),
    str(BI / "4925a66d-colabmescusto.xlsx"),
    str(BI / "7a78b7af-projetomescusto.xlsx"),
    str(BI / "a68c2a97-projetocolabalocacao.xlsx"),
    str(BI / "b9fb7877-colabprojetoalocacao.xlsx"),
]

pytestmark = pytest.mark.skipif(not BI.exists(), reason="amostras/bi ausente")


def test_bi_carrega_e_classifica(conn):
    res = {r["arquivo"]: r for r in importar_arquivos(conn, FILES)}
    assert res["4a330340-equipe.xlsx"]["status"] == "ok"
    assert res["4925a66d-colabmescusto.xlsx"]["linhas"] == 4093
    assert res["a68c2a97-projetocolabalocacao.xlsx"]["status"] == "ignorado"
    assert res["b9fb7877-colabprojetoalocacao.xlsx"]["status"] == "ignorado"


def test_bi_enriquece_pessoa_sem_sobrescrever_capacidade(conn, sample_path):
    import_workbook(conn, sample_path)
    # capacidade editada à mão não pode ser sobrescrita pelo BI
    conn.execute("UPDATE pessoa SET capacidade_mensal=99 WHERE matricula='73920'")
    importar_arquivos(conn, FILES)
    p = conn.execute("SELECT * FROM pessoa WHERE matricula='73920'").fetchone()
    assert p["capacidade_mensal"] == 99
    assert p["area"] is not None  # área veio do BI
    # quem não tinha capacidade recebeu do BI
    algum = conn.execute(
        "SELECT COUNT(*) c FROM pessoa WHERE capacidade_mensal IS NOT NULL AND area IS NOT NULL"
    ).fetchone()["c"]
    assert algum > 100


def test_bi_custo_todos_com_matricula_e_soma(conn):
    importar_arquivos(conn, FILES)
    total, semmat = conn.execute(
        "SELECT COUNT(*), SUM(matricula IS NULL) FROM bi_custo"
    ).fetchone()
    assert total == 4093
    assert semmat == 0  # normalização sem acento casa 'SIMÃO' com 'SIMAO'

    c = conn.execute(
        """SELECT ROUND(SUM(custo)) FROM bi_custo
           WHERE id_projetos=16045 AND periodo='2026-09-01'"""
    ).fetchone()[0]
    assert c and c > 0

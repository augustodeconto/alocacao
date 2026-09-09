import datetime as dt
import zipfile

from app import db as dbmod
from app.xlsx_export import exportar_projeto
from app.xlsx_import import import_workbook
from app.xlsx_io import Workbook, serial_to_date


def _export(conn, tmp_path, sample_path):
    import_workbook(conn, sample_path)
    return exportar_projeto(conn, 1, str(tmp_path / "out"), sample_path)


def test_export_comeca_no_mes_da_geracao(conn, tmp_path, sample_path):
    out = _export(conn, tmp_path, sample_path)
    hoje = dt.date.today().replace(day=1)

    dp = Workbook(out).sheet("dados_projeto")
    assert dp.cell(2, 8) == hoje.month   # Mês Inicio da alocação
    assert dp.cell(2, 9) == hoje.year    # Ano Inicio da alocação

    aloc = Workbook(out).sheet("Alocacao")
    assert serial_to_date(aloc.cell(1, 5)) == hoje  # 1ª coluna de mês (E1)


def test_export_roundtrip_preserva_horas_do_mes_atual_em_diante(conn, tmp_path, sample_path):
    out = _export(conn, tmp_path, sample_path)
    hoje = dt.date.today().replace(day=1).isoformat()

    conn2 = dbmod.init_db(tmp_path / "t2.db")
    import_workbook(conn2, out)

    def total(c, mat, tipo):
        return c.execute(
            """SELECT COALESCE(SUM(m.horas),0) s FROM alocacao_mes m
               JOIN alocacao a USING(alocacao_id)
               WHERE a.matricula=? AND a.tipo_alocacao=? AND m.periodo >= ?""",
            (mat, tipo, hoje),
        ).fetchone()["s"]

    for mat, tipo in [("73920", "TecnicaANP"), ("79483", "TecnicaANP"), ("69416", "TecnicaANP")]:
        assert total(conn, mat, tipo) == total(conn2, mat, tipo)
    assert conn2.execute("SELECT COUNT(*) c FROM alocacao").fetchone()["c"] == 12
    conn2.close()


def test_export_dropa_comentarios_e_calcchain(conn, tmp_path, sample_path):
    out = _export(conn, tmp_path, sample_path)
    with zipfile.ZipFile(out) as z:
        names = z.namelist()
    assert not any("comments" in n.lower() for n in names)
    assert not any("threadedcomment" in n.lower() for n in names)
    assert not any("vmldrawing" in n.lower() for n in names)
    assert "xl/calcChain.xml" not in names


def test_export_preserva_planilha4_e_dropdowns(conn, tmp_path, sample_path):
    out = _export(conn, tmp_path, sample_path)
    wb = Workbook(out)
    assert wb.has_sheets("Planilha4", "dados_projeto", "Alocacao", "Novos_Pesquisadores")
    plan = wb.sheet("Planilha4")
    assert plan.cell(2, 16) == "Técnica"   # Tipo_alocacao P2
    with zipfile.ZipFile(out) as z:
        sheet3 = z.read("xl/worksheets/sheet3.xml").decode()
    assert "dataValidation" in sheet3       # dropdown da coluna B preservado


def test_export_escreve_dados_projeto_do_banco(conn, tmp_path, sample_path):
    out = _export(conn, tmp_path, sample_path)
    dp = Workbook(out).sheet("dados_projeto")
    assert dp.cell(2, 1) == 16045
    assert dp.cell(2, 2) == "OTIMIZEPLAN"

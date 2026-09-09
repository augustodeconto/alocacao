"""Versionamento: diff linha-de-base x working decide zero vs. omissão no export."""
import datetime as dt

from app import baseline
from app.aggregate import build_grade
from app.xlsx_export import exportar_projeto
from app.xlsx_import import import_workbook
from app.xlsx_io import Workbook


def _linhas_alocacao(path):
    s = Workbook(path).sheet("Alocacao")
    out = {}
    for r in range(2, s.max_row + 1):
        v = s.row_values(r)
        if v[0] in (None, ""):
            continue
        out[(str(v[0]), v[1])] = [x or 0 for x in v[4:]]
    return out


def test_import_captura_baseline(conn, sample_path):
    import_workbook(conn, sample_path)
    n = conn.execute("SELECT COUNT(*) c FROM baseline_alocacao WHERE projeto_id=1").fetchone()["c"]
    assert n == conn.execute("SELECT COUNT(*) c FROM alocacao WHERE projeto_id=1").fetchone()["c"] == 12
    assert conn.execute("SELECT origem FROM baseline_meta WHERE projeto_id=1").fetchone()["origem"] == "import"


def test_remocao_de_quem_estava_na_baseline_sai_zerada(conn, tmp_path, sample_path):
    import_workbook(conn, sample_path)
    # remove Augusto/TecnicaANP (estava na baseline)
    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE projeto_id=1 AND matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]
    conn.execute("DELETE FROM alocacao WHERE alocacao_id=?", (aid,))
    conn.commit()

    # aparece riscada na grade, fora dos totais
    proj = build_grade(conn)["por_projeto"][0]
    rem = [f for grp in proj["grupos"] for f in grp["filhos"] if f["removido"]]
    assert any(f["matricula"] == "73920" and f["tipo_alocacao"] == "TecnicaANP" for f in rem)

    out = exportar_projeto(conn, 1, str(tmp_path / "o"), sample_path)
    linhas = _linhas_alocacao(out)
    assert ("73920", "TecnicaANP") in linhas
    assert set(linhas[("73920", "TecnicaANP")]) == {0}   # tudo zero


def test_alocacao_nova_e_removida_na_mesma_sessao_nao_sai(conn, tmp_path, sample_path):
    import_workbook(conn, sample_path)
    cur = conn.execute(
        "INSERT INTO alocacao (projeto_id, matricula, tipo_alocacao) VALUES (1,'99999','Econômica')"
    )
    conn.execute("INSERT OR IGNORE INTO pessoa (matricula, nome) VALUES ('99999','FULANO')")
    conn.commit()
    conn.execute("DELETE FROM alocacao WHERE alocacao_id=?", (cur.lastrowid,))
    conn.commit()

    out = exportar_projeto(conn, 1, str(tmp_path / "o"), sample_path)
    assert ("99999", "Econômica") not in _linhas_alocacao(out)


def test_export_avanca_baseline(conn, tmp_path, sample_path):
    import_workbook(conn, sample_path)
    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE projeto_id=1 AND matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]
    conn.execute("DELETE FROM alocacao WHERE alocacao_id=?", (aid,))
    conn.commit()
    exportar_projeto(conn, 1, str(tmp_path / "o"), sample_path)

    assert conn.execute("SELECT origem FROM baseline_meta WHERE projeto_id=1").fetchone()["origem"] == "export"
    # depois do export, não há mais "removidas" pendentes
    assert baseline.removidas(conn, 1) == []
    out2 = exportar_projeto(conn, 1, str(tmp_path / "o2"), sample_path)
    assert ("73920", "TecnicaANP") not in _linhas_alocacao(out2)


def test_restaurar_desfaz_remocao(conn, sample_path):
    import_workbook(conn, sample_path)
    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE projeto_id=1 AND matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]
    horas_antes = dict(conn.execute(
        "SELECT periodo, horas FROM alocacao_mes WHERE alocacao_id=?", (aid,)
    ).fetchall())
    conn.execute("DELETE FROM alocacao WHERE alocacao_id=?", (aid,))
    conn.commit()

    baseline.restaurar(conn, 1, "73920", "TecnicaANP")
    aid2 = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE projeto_id=1 AND matricula='73920' AND tipo_alocacao='TecnicaANP'"
    ).fetchone()["alocacao_id"]
    horas_depois = dict(conn.execute(
        "SELECT periodo, horas FROM alocacao_mes WHERE alocacao_id=?", (aid2,)
    ).fetchall())
    assert horas_depois == horas_antes

"""Tabela única `pessoa` + aba Novos_Pesquisadores gerada do diff vs. HEAD."""
from app import versao
from app.xlsx_export import exportar_projeto
from app.xlsx_import import import_workbook
from app.xlsx_io import Workbook


def _novos_rows(path):
    s = Workbook(path).sheet("Novos_Pesquisadores")
    out = []
    for r in range(2, s.max_row + 1):
        v = s.row_values(r)
        if v and v[0] not in (None, ""):
            out.append(v)
    return out


def test_sem_tabela_pessoa_nova(conn):
    t = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "pessoa_nova" not in t
    assert "pessoa" in t


def test_novos_pesquisadores_vai_para_pessoa(conn, sample_path):
    import_workbook(conn, sample_path)
    p = conn.execute("SELECT * FROM pessoa WHERE matricula='36925'").fetchone()  # Larissa
    assert p["carga_diaria"] == 8
    assert p["capacidade_mensal"] == 176
    assert p["equipe"] == "Embarcados"
    assert p["area"] == "Software"
    assert (p["tipo_contrato"] or "").startswith("Bolsista")


def test_export_novos_sai_do_diff(conn, tmp_path, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "importa amostra")           # HEAD passa a conter tudo

    # sem mudanças -> aba Novos_Pesquisadores vazia
    out1 = exportar_projeto(conn, 1, str(tmp_path / "a"), sample_path)
    assert _novos_rows(out1) == []

    # muda a carga de alguém que está alocado no projeto -> aparece
    mat = conn.execute(
        "SELECT matricula FROM alocacao WHERE projeto_id=1 LIMIT 1"
    ).fetchone()["matricula"]
    conn.execute("UPDATE pessoa SET carga_diaria=6, capacidade_mensal=132 WHERE matricula=?", (mat,))
    conn.commit()

    out2 = exportar_projeto(conn, 1, str(tmp_path / "b"), sample_path)
    rows = _novos_rows(out2)
    assert len(rows) == 1 and str(rows[0][0]) == mat

    # commit -> HEAD avança -> volta a ficar vazia
    versao.commit(conn, "ajusta carga")
    out3 = exportar_projeto(conn, 1, str(tmp_path / "c"), sample_path)
    assert _novos_rows(out3) == []


def test_head_tem_pessoa_e_projeto(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "importa amostra")
    n_pessoa = conn.execute(
        """SELECT COUNT(*) c FROM base_pessoa
           WHERE matricula IN (SELECT matricula FROM alocacao WHERE projeto_id=1)"""
    ).fetchone()["c"]
    n_aloc_pessoas = conn.execute(
        "SELECT COUNT(DISTINCT matricula) c FROM alocacao WHERE projeto_id=1"
    ).fetchone()["c"]
    assert n_pessoa == n_aloc_pessoas
    bp = conn.execute("SELECT * FROM base_projeto WHERE projeto_id=1").fetchone()
    assert bp["nome"] == "OTIMIZEPLAN" and bp["mes_inicio"] == 5
    assert versao.pessoas_alteradas(conn, 1) == []

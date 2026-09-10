"""Multiusuário: rascunho por (autor, branch) + os 4 níveis do 'salvar'."""
import pytest

from app import versao
from app.xlsx_import import import_workbook


def _tip(conn, ref="main"):
    return versao.tip_commit(conn, ref)


def _cel_key(conn):
    r = conn.execute(
        """SELECT a.projeto_id p, a.matricula m, a.tipo_alocacao t
           FROM alocacao a JOIN alocacao_mes mm USING (alocacao_id)
           WHERE mm.periodo='2026-07-01' LIMIT 1"""
    ).fetchone()
    return f"{r['p']}|{r['m']}|{r['t']}|2026-07-01"


def _commit_direto(conn, mensagem, mudanca):
    """Aplica `mudanca(conn)` no working e commita — simula outro usuário."""
    mudanca(conn)
    conn.commit()
    return versao.commit(conn, mensagem)


def test_nivel_1_sem_ninguem_mexendo(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    k = _cel_key(conn)
    base = _tip(conn)
    res = versao.commitar_rascunho(conn, "ana", "main", {"mes": {k: 123}}, base)
    assert res["nivel"] == 1 and res["commit_id"]
    # a ref andou; o rascunho seria apagado pelo endpoint (aqui testamos a função)
    assert versao.tip_commit(conn, "main") == res["commit_id"]


def _outra_matricula(conn, mat):
    return conn.execute(
        "SELECT DISTINCT matricula FROM alocacao WHERE matricula<>? LIMIT 1", (mat,)
    ).fetchone()["matricula"]


def test_nivel_2_sem_intersecao(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    k = _cel_key(conn)                       # rascunho da ana mexe numa alocação
    outra = _outra_matricula(conn, k.split("|")[1])
    base = _tip(conn)
    # outro usuário mexe numa PESSOA diferente (linha que a ana não tocou)
    _commit_direto(conn, "beto: capacidade",
                   lambda c: c.execute("UPDATE pessoa SET capacidade_mensal=123 WHERE matricula=?", (outra,)))
    res = versao.commitar_rascunho(conn, "ana", "main", {"mes": {k: 50}}, base)
    assert res["nivel"] == 2 and res["commit_id"]
    assert len(res["incorporados"]) == 1


def test_nivel_3_intersecao_sem_conflito_pede_confirmacao(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    k = _cel_key(conn)
    pid, mat, tipo, _ = k.split("|")
    base = _tip(conn)
    # outro usuário mexe em AGOSTO da MESMA alocação -> mesma linha, sem conflito de célula
    def muda_agosto(c):
        aid = c.execute("SELECT alocacao_id FROM alocacao WHERE projeto_id=? AND matricula=? AND tipo_alocacao=?",
                        (int(pid), mat, tipo)).fetchone()["alocacao_id"]
        c.execute("INSERT INTO alocacao_mes (alocacao_id,periodo,horas) VALUES (?,?,50) "
                  "ON CONFLICT(alocacao_id,periodo) DO UPDATE SET horas = horas + 50", (aid, "2026-08-01"))
    _commit_direto(conn, "beto: agosto", muda_agosto)

    res = versao.commitar_rascunho(conn, "ana", "main", {"mes": {k: 60}}, base)
    assert res["nivel"] == 3 and "digest" in res and "commit_id" not in res
    assert res["digest"]["commits"][0]["mensagem"] == "beto: agosto"

    res2 = versao.commitar_rascunho(conn, "ana", "main", {"mes": {k: 60}}, base, confirmar=True)
    assert res2["nivel"] == 3 and res2["commit_id"]


def test_nivel_4_conflito_real(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    k = _cel_key(conn)
    base = _tip(conn)
    _commit_direto(conn, "beto: julho=200",
                   lambda c: c.execute("UPDATE alocacao_mes SET horas=200 WHERE periodo='2026-07-01' AND alocacao_id IN "
                                       "(SELECT alocacao_id FROM alocacao WHERE (projeto_id||'|'||matricula||'|'||tipo_alocacao)=?)",
                                       (k.rsplit("|", 1)[0],)))
    res = versao.commitar_rascunho(conn, "ana", "main", {"mes": {k: 100}}, base)
    assert res["nivel"] == 4 and "commit_id" not in res
    (cf,) = res["conflitos"]
    assert cf["ours"] == 100 and cf["theirs"] == 200


def test_sempre_revisar_promove_nivel_2(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    conn.execute("INSERT INTO usuario (nome, sempre_revisar, criado_em) VALUES ('ana', 1, '2026-01-01')")
    conn.commit()
    k = _cel_key(conn)
    outra = _outra_matricula(conn, k.split("|")[1])
    base = _tip(conn)
    _commit_direto(conn, "beto: capacidade",
                   lambda c: c.execute("UPDATE pessoa SET capacidade_mensal=99 WHERE matricula=?", (outra,)))
    res = versao.commitar_rascunho(conn, "ana", "main", {"mes": {k: 50}}, base)
    assert res["nivel"] == 2 and "digest" in res and "commit_id" not in res
    res2 = versao.commitar_rascunho(conn, "ana", "main", {"mes": {k: 50}}, base, confirmar=True)
    assert res2["commit_id"]


def test_rascunho_vazio_erro(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    with pytest.raises(versao.NadaParaCommitar):
        versao.commitar_rascunho(conn, "ana", "main", {"mes": {}}, _tip(conn))


def test_endpoints_rascunho(conn, sample_path):
    from fastapi.testclient import TestClient
    from app import main as m
    m._conn = conn
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    cli = TestClient(m.app)

    cli.post("/api/usuarios", json={"nome": "ana"})
    assert any(u["nome"] == "ana" for u in cli.get("/api/usuarios").json()["usuarios"])

    k = _cel_key(conn)
    r = cli.put("/api/rascunho", json={"autor": "ana", "ref": "main", "edicoes": {"mes": {k: 77}}})
    assert r.status_code == 200
    got = cli.get("/api/rascunho", params={"autor": "ana", "ref": "main"}).json()["rascunhos"]
    assert got and got[0]["edicoes"]["mes"][k] == 77

    r = cli.post("/api/rascunho/commitar", json={"autor": "ana", "ref": "main"})
    assert r.status_code == 200 and r.json()["commit_id"]
    # rascunho consumido
    assert cli.get("/api/rascunho", params={"autor": "ana", "ref": "main"}).json()["rascunhos"] == []

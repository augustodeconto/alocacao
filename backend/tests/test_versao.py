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


def test_resetar_move_topo_e_devolve_conteudo_como_pendencia(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    base_id = versao.tip_commit(conn, "main")
    base_julho = _julho(conn)

    conn.execute("UPDATE alocacao_mes SET horas = horas + 10 WHERE periodo='2026-07-01'")
    conn.commit()
    versao.commit(conn, "sobe julho")
    depois_julho = _julho(conn)
    assert depois_julho != base_julho

    versao.resetar(conn, "main", base_id)

    assert versao.tip_commit(conn, "main") == base_id       # topo voltou
    h = conn.execute("SELECT * FROM head_ WHERE id=1").fetchone()
    assert h["base_commit_id"] == base_id                   # head também (main é a branch aberta)
    assert _julho(conn) == depois_julho                     # conteúdo NÃO se perdeu no working...
    assert versao.sujo(conn)                                # ...mas volta como pendência de verdade
    d = versao.diff_pendente(conn)
    assert d["mes"]                                          # a célula de julho aparece no diff

    # dá pra commitar de novo em cima do novo topo, normalmente
    novo_id = versao.commit(conn, "sobe julho de novo")
    assert conn.execute(
        "SELECT parent_id FROM commit_ WHERE commit_id=?", (novo_id,)
    ).fetchone()["parent_id"] == base_id


def test_resetar_exige_ancestral_e_working_limpo(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    base_id = versao.tip_commit(conn, "main")

    versao.branch(conn, "lado", trocar=True)
    conn.execute("UPDATE alocacao_mes SET horas = 999 WHERE periodo='2026-07-01'")
    conn.commit()
    lado_id = versao.commit(conn, "lado")

    versao.checkout(conn, "main")
    with pytest.raises(ValueError):
        versao.resetar(conn, "main", lado_id)    # não é ancestral de main
    with pytest.raises(ValueError):
        versao.resetar(conn, "main", base_id)    # base_id == topo atual, não é "voltar"

    conn.execute("UPDATE alocacao_mes SET horas = 1 WHERE periodo='2026-08-01'")
    conn.commit()
    versao.commit(conn, "avança main")           # agora base_id é ancestral de verdade do topo

    conn.execute("UPDATE alocacao_mes SET horas = 2 WHERE periodo='2026-08-01'")
    conn.commit()
    with pytest.raises(versao.VersaoSuja):
        versao.resetar(conn, "main", base_id)    # working sujo bloqueia, igual checkout


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


def _uma_celula(conn):
    r = conn.execute(
        """SELECT a.projeto_id p, a.matricula m, a.tipo_alocacao t
           FROM alocacao a JOIN alocacao_mes mm USING (alocacao_id)
           WHERE mm.periodo='2026-07-01' LIMIT 1"""
    ).fetchone()
    return r["p"], r["m"], r["t"]


def _set_cel(conn, pid, mat, tipo, per, val):
    aid = conn.execute(
        "SELECT alocacao_id FROM alocacao WHERE projeto_id=? AND matricula=? AND tipo_alocacao=?",
        (pid, mat, tipo),
    ).fetchone()["alocacao_id"]
    conn.execute(
        "INSERT INTO alocacao_mes (alocacao_id, periodo, horas) VALUES (?,?,?) "
        "ON CONFLICT(alocacao_id, periodo) DO UPDATE SET horas=excluded.horas",
        (aid, per, val),
    )
    conn.commit()


def _cel(conn, pid, mat, tipo, per):
    r = conn.execute(
        """SELECT horas FROM alocacao_mes WHERE periodo=? AND alocacao_id IN
           (SELECT alocacao_id FROM alocacao WHERE projeto_id=? AND matricula=? AND tipo_alocacao=?)""",
        (per, pid, mat, tipo),
    ).fetchone()
    return r["horas"] if r else 0


def test_merge_sem_conflito_e_fast_forward(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    pid, mat, tipo = _uma_celula(conn)

    versao.branch(conn, "A", trocar=True)
    _set_cel(conn, pid, mat, tipo, "2026-09-01", 40)     # mês novo, só na A
    versao.commit(conn, "A: setembro")

    versao.checkout(conn, "main")
    # main não mudou -> merge A é fast-forward
    res = versao.merge(conn, "A")
    assert res["status"] == "fast-forward"
    assert _cel(conn, pid, mat, tipo, "2026-09-01") == 40

    # agora divergem em meses diferentes -> merge de verdade, sem conflito
    versao.checkout(conn, "A")
    _set_cel(conn, pid, mat, tipo, "2026-10-01", 8)
    versao.commit(conn, "A: outubro")
    versao.checkout(conn, "main")
    _set_cel(conn, pid, mat, tipo, "2026-11-01", 9)
    versao.commit(conn, "main: novembro")
    res = versao.merge(conn, "A")
    assert res["status"] == "ok"
    assert _cel(conn, pid, mat, tipo, "2026-10-01") == 8
    assert _cel(conn, pid, mat, tipo, "2026-11-01") == 9
    assert versao.log(conn)[0]["origem"] == "merge"


def test_merge_conflito_resolve_e_conclui(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    pid, mat, tipo = _uma_celula(conn)

    versao.branch(conn, "B", trocar=True)
    _set_cel(conn, pid, mat, tipo, "2026-07-01", 100)
    versao.commit(conn, "B: julho 100")
    versao.checkout(conn, "main")
    _set_cel(conn, pid, mat, tipo, "2026-07-01", 200)
    versao.commit(conn, "main: julho 200")

    res = versao.merge(conn, "B")
    assert res["status"] == "conflito"
    (cf,) = res["conflitos"]
    assert cf["ours"] == 200 and cf["theirs"] == 100
    assert _cel(conn, pid, mat, tipo, "2026-07-01") == 200        # OURS enquanto pendente

    import pytest
    with pytest.raises(ValueError):
        versao.concluir_merge(conn, "cedo demais")                # conflito aberto

    versao.resolver_conflito(conn, cf["id"], valor=150)
    cid = versao.concluir_merge(conn, "merge B")
    assert _cel(conn, pid, mat, tipo, "2026-07-01") == 150
    mc = conn.execute("SELECT parent_id, merge_parent_id, origem FROM commit_ WHERE commit_id=?",
                      (cid,)).fetchone()
    assert mc["merge_parent_id"] is not None and mc["origem"] == "merge"
    assert not versao.sujo(conn)
    assert conn.execute("SELECT COUNT(*) c FROM merge_estado").fetchone()["c"] == 0


def test_merge_abortar_restaura(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    pid, mat, tipo = _uma_celula(conn)
    versao.branch(conn, "C", trocar=True)
    _set_cel(conn, pid, mat, tipo, "2026-07-01", 1)
    versao.commit(conn, "C")
    versao.checkout(conn, "main")
    _set_cel(conn, pid, mat, tipo, "2026-07-01", 2)
    versao.commit(conn, "main")
    versao.merge(conn, "C")
    assert conn.execute("SELECT COUNT(*) c FROM merge_estado").fetchone()["c"] == 1
    versao.abortar_merge(conn)
    assert _cel(conn, pid, mat, tipo, "2026-07-01") == 2          # OURS de volta
    assert conn.execute("SELECT COUNT(*) c FROM merge_estado").fetchone()["c"] == 0
    assert not versao.sujo(conn)


def test_commit_bloqueado_durante_merge(conn, sample_path):
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")
    pid, mat, tipo = _uma_celula(conn)
    versao.branch(conn, "E", trocar=True)
    _set_cel(conn, pid, mat, tipo, "2026-07-01", 1)
    versao.commit(conn, "E")
    versao.checkout(conn, "main")
    _set_cel(conn, pid, mat, tipo, "2026-07-01", 2)
    versao.commit(conn, "main")
    versao.merge(conn, "E")
    import pytest
    with pytest.raises(versao.MergeEmAndamento):
        versao.commit(conn, "não pode")
    with pytest.raises(versao.MergeEmAndamento):
        versao.checkout(conn, "E")


def test_checkout_nao_deixa_pessoa_de_outro_branch_pendurada(conn, sample_path):
    """Bug real corrigido em 2026-09-12: criar uma pessoa nova num branch, commitar,
    e trocar pra um branch que nunca teve essa pessoa mostrava "1 alteração não
    salva" (a pessoa "ADICIONADA") mesmo sem nenhuma edição feita ali — porque
    escrever_working faz upsert de pessoa mas nunca apaga (docstring dela), então a
    linha ficava pendurada na tabela viva depois do checkout."""
    import_workbook(conn, sample_path)
    versao.commit(conn, "base")

    versao.branch(conn, "cenario", trocar=True)
    conn.execute("INSERT INTO pessoa (matricula, nome) VALUES ('99999', 'joao da silva')")
    conn.commit()
    versao.commit(conn, "nova pessoa")

    versao.checkout(conn, "main")
    assert not versao.sujo(conn)   # não pode sobrar pendência só por causa do checkout
    assert conn.execute("SELECT 1 FROM pessoa WHERE matricula='99999'").fetchone() is None

    # e volta a existir ao reabrir o branch onde ela pertence de fato
    versao.checkout(conn, "cenario")
    assert not versao.sujo(conn)
    assert conn.execute("SELECT nome FROM pessoa WHERE matricula='99999'").fetchone()["nome"] == "joao da silva"


def test_checkout_preserva_pessoa_sem_alocacao_que_pertence_ao_proprio_commit(conn, sample_path):
    """Uma pessoa cadastrada (e commitada) sem nenhuma alocação — ainda não estafada
    em projeto nenhum — não pode ser apagada por um checkout pro MESMO commit em que
    foi criada: ela pertence a E["pessoa"] mesmo sem aparecer em E["alocacao"]. Só é
    candidata a limpeza quem NÃO pertence ao commit de destino (ver teste acima)."""
    import_workbook(conn, sample_path)
    conn.execute("INSERT INTO pessoa (matricula, nome) VALUES ('88888', 'sem alocacao ainda')")
    conn.commit()
    versao.commit(conn, "cadastra pessoa sem projeto")

    versao.checkout(conn, "main")   # reabre o mesmo commit em que ela foi criada
    assert not versao.sujo(conn)
    assert conn.execute("SELECT 1 FROM pessoa WHERE matricula='88888'").fetchone() is not None


def test_nao_apaga_main_nem_branch_atual(conn):
    with pytest.raises(ValueError):
        versao.deletar_branch(conn, "main")
    versao.branch(conn, "tmp", trocar=True)
    with pytest.raises(ValueError):
        versao.deletar_branch(conn, "tmp")
    versao.checkout(conn, "main")
    versao.deletar_branch(conn, "tmp")
    assert conn.execute("SELECT COUNT(*) c FROM ref_").fetchone()["c"] == 1

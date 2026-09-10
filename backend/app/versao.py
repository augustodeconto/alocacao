"""Versionamento estilo Git do plano (ver docs/VERSIONAMENTO.md).

Modelo
------
- As 5 tabelas *working* (`projeto`, `pessoa`, `alocacao`, `alocacao_mes`,
  `projeto_periodo`) são a materialização do checkout atual + edições não commitadas.
- `commit_` / `ref_` / `head_` = grafo de commits + branches + ponteiro do checkout.
- `chg_*` = delta de cada commit vs. o 1º pai (chave natural; `deleted=1` = lápide).
- Cache do estado materializado do HEAD:
    alocação -> `baseline_alocacao` / `baseline_alocacao_mes` (schema já global)
    campos   -> `base_projeto` / `base_pessoa` / `base_projeto_periodo`
  `commit` compara working x cache; `checkout` reconstrói working+cache do grafo.

Fase 1: commit / branch / checkout / descartar / log / diff. Merge = fase 2.
Commits são **globais** (um snapshot do plano inteiro).
"""
from __future__ import annotations

import datetime as _dt
import json as _json
import sqlite3

PROJETO_COLS = [
    "nome", "empresa", "status", "id_status", "matricula_gp", "id_filial",
    "cenario1", "cenario2", "cenario3", "gestor_projetos",
]
PESSOA_COLS = [
    "nome", "situacao", "equipe", "area", "tipo_contrato", "inicio_contrato",
    "fim_contrato", "formacao", "id_filial", "carga_diaria", "capacidade_mensal",
    "remuneracao", "inicio_vigencia", "valor_hora",
]


class VersaoSuja(Exception):
    """Há mudanças não commitadas — checkout/branch-switch bloqueado."""


class NadaParaCommitar(Exception):
    pass


class BranchProtegida(Exception):
    """Commit direto numa branch protegida (ex.: main)."""


class MergeEmAndamento(Exception):
    """Há um merge sem concluir — resolva/conclua ou aborte antes."""


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# snapshots de estado (dicts simples)
# --------------------------------------------------------------------------- #
def _estado_working(conn: sqlite3.Connection) -> dict:
    e = {"projeto": {}, "pessoa": {}, "alocacao": {}, "mes": {}, "periodo": {}}
    for r in conn.execute(f"SELECT projeto_id, {','.join(PROJETO_COLS)} FROM projeto"):
        e["projeto"][r["projeto_id"]] = {c: r[c] for c in PROJETO_COLS}
    for r in conn.execute(f"SELECT matricula, {','.join(PESSOA_COLS)} FROM pessoa"):
        e["pessoa"][r["matricula"]] = {c: r[c] for c in PESSOA_COLS}
    for r in conn.execute("SELECT projeto_id, matricula, tipo_alocacao FROM alocacao"):
        e["alocacao"][(r["projeto_id"], r["matricula"], r["tipo_alocacao"])] = True
    for r in conn.execute(
        """SELECT a.projeto_id p, a.matricula m, a.tipo_alocacao t, mm.periodo per, mm.horas h
           FROM alocacao_mes mm JOIN alocacao a USING (alocacao_id) WHERE mm.horas > 0"""
    ):
        e["mes"][(r["p"], r["m"], r["t"], r["per"])] = r["h"]
    for r in conn.execute("SELECT projeto_id, periodo, ordem FROM projeto_periodo"):
        e["periodo"][(r["projeto_id"], r["periodo"])] = r["ordem"]
    return e


def _estado_cache(conn: sqlite3.Connection) -> dict:
    e = {"projeto": {}, "pessoa": {}, "alocacao": {}, "mes": {}, "periodo": {}}
    for r in conn.execute(f"SELECT projeto_id, {','.join(PROJETO_COLS)} FROM base_projeto"):
        e["projeto"][r["projeto_id"]] = {c: r[c] for c in PROJETO_COLS}
    for r in conn.execute(f"SELECT matricula, {','.join(PESSOA_COLS)} FROM base_pessoa"):
        e["pessoa"][r["matricula"]] = {c: r[c] for c in PESSOA_COLS}
    for r in conn.execute("SELECT projeto_id, matricula, tipo_alocacao FROM baseline_alocacao"):
        e["alocacao"][(r["projeto_id"], r["matricula"], r["tipo_alocacao"])] = True
    for r in conn.execute(
        "SELECT projeto_id, matricula, tipo_alocacao, periodo, horas FROM baseline_alocacao_mes WHERE horas > 0"
    ):
        e["mes"][(r["projeto_id"], r["matricula"], r["tipo_alocacao"], r["periodo"])] = r["horas"]
    for r in conn.execute("SELECT projeto_id, periodo, ordem FROM base_projeto_periodo"):
        e["periodo"][(r["projeto_id"], r["periodo"])] = r["ordem"]
    return e


# --------------------------------------------------------------------------- #
# diff entre dois estados  ->  delta
# --------------------------------------------------------------------------- #
def _dif_campos(row: dict, base: dict | None, cols: list[str]) -> bool:
    if base is None:
        return True
    return any((row[c] if row[c] != "" else None) != (base[c] if base[c] != "" else None) for c in cols)


def _delta(base: dict, novo: dict) -> dict:
    d = {"projeto": [], "pessoa": [], "aloc_add": [], "aloc_del": [], "mes": [],
         "per_set": [], "per_del": []}
    for pid, row in novo["projeto"].items():
        if _dif_campos(row, base["projeto"].get(pid), PROJETO_COLS):
            d["projeto"].append((pid, row))
    for mat, row in novo["pessoa"].items():
        if _dif_campos(row, base["pessoa"].get(mat), PESSOA_COLS):
            d["pessoa"].append((mat, row))
    d["aloc_add"] = sorted(novo["alocacao"].keys() - base["alocacao"].keys())
    d["aloc_del"] = sorted(base["alocacao"].keys() - novo["alocacao"].keys())
    for k in set(novo["mes"]) | set(base["mes"]):
        nv = novo["mes"].get(k)
        if nv != base["mes"].get(k):
            d["mes"].append((k, nv))          # nv is None  ->  voltou a 0 (lápide)
    for k, o in novo["periodo"].items():
        if base["periodo"].get(k) != o:
            d["per_set"].append((k, o))
    d["per_del"] = sorted(base["periodo"].keys() - novo["periodo"].keys())
    return d


def _delta_vazio(d: dict) -> bool:
    return not any(d[k] for k in d)


def _resumo(d: dict) -> dict:
    return {
        "projeto": len(d["projeto"]), "pessoa": len(d["pessoa"]),
        "alocacoes_novas": len(d["aloc_add"]), "alocacoes_removidas": len(d["aloc_del"]),
        "celulas": len(d["mes"]), "janela": len(d["per_set"]) + len(d["per_del"]),
    }


# --------------------------------------------------------------------------- #
# gravar delta:  chg_*  +  cache
# --------------------------------------------------------------------------- #
def _grava_chg(conn: sqlite3.Connection, cid: int, d: dict) -> None:
    ph_pr = ",".join("?" * len(PROJETO_COLS))
    for pid, row in d["projeto"]:
        conn.execute(
            f"INSERT INTO chg_projeto (commit_id, projeto_id, deleted, {','.join(PROJETO_COLS)}) "
            f"VALUES (?,?,0,{ph_pr})", (cid, pid, *[row[c] for c in PROJETO_COLS]))
    ph_pe = ",".join("?" * len(PESSOA_COLS))
    for mat, row in d["pessoa"]:
        conn.execute(
            f"INSERT INTO chg_pessoa (commit_id, matricula, deleted, {','.join(PESSOA_COLS)}) "
            f"VALUES (?,?,0,{ph_pe})", (cid, mat, *[row[c] for c in PESSOA_COLS]))
    for (pid, mat, tipo) in d["aloc_add"]:
        conn.execute("INSERT INTO chg_alocacao (commit_id, projeto_id, matricula, tipo_alocacao, deleted) "
                     "VALUES (?,?,?,?,0)", (cid, pid, mat, tipo))
    for (pid, mat, tipo) in d["aloc_del"]:
        conn.execute("INSERT INTO chg_alocacao (commit_id, projeto_id, matricula, tipo_alocacao, deleted) "
                     "VALUES (?,?,?,?,1)", (cid, pid, mat, tipo))
    for ((pid, mat, tipo, per), v) in d["mes"]:
        if v is None:
            conn.execute("INSERT INTO chg_alocacao_mes (commit_id, projeto_id, matricula, tipo_alocacao, "
                         "periodo, deleted, horas) VALUES (?,?,?,?,?,1,NULL)", (cid, pid, mat, tipo, per))
        else:
            conn.execute("INSERT INTO chg_alocacao_mes (commit_id, projeto_id, matricula, tipo_alocacao, "
                         "periodo, deleted, horas) VALUES (?,?,?,?,?,0,?)", (cid, pid, mat, tipo, per, v))
    for ((pid, per), o) in d["per_set"]:
        conn.execute("INSERT INTO chg_projeto_periodo (commit_id, projeto_id, periodo, deleted, ordem) "
                     "VALUES (?,?,?,0,?)", (cid, pid, per, o))
    for (pid, per) in d["per_del"]:
        conn.execute("INSERT INTO chg_projeto_periodo (commit_id, projeto_id, periodo, deleted, ordem) "
                     "VALUES (?,?,?,1,NULL)", (cid, pid, per))


def _aplica_cache(conn: sqlite3.Connection, d: dict) -> None:
    for pid, row in d["projeto"]:
        sets = ",".join(f"{c}=excluded.{c}" for c in PROJETO_COLS)
        conn.execute(
            f"INSERT INTO base_projeto (projeto_id, {','.join(PROJETO_COLS)}) "
            f"VALUES (?, {','.join('?' * len(PROJETO_COLS))}) "
            f"ON CONFLICT(projeto_id) DO UPDATE SET {sets}",
            (pid, *[row[c] for c in PROJETO_COLS]))
    for mat, row in d["pessoa"]:
        sets = ",".join(f"{c}=excluded.{c}" for c in PESSOA_COLS)
        conn.execute(
            f"INSERT INTO base_pessoa (matricula, {','.join(PESSOA_COLS)}) "
            f"VALUES (?, {','.join('?' * len(PESSOA_COLS))}) "
            f"ON CONFLICT(matricula) DO UPDATE SET {sets}",
            (mat, *[row[c] for c in PESSOA_COLS]))
    for (pid, mat, tipo) in d["aloc_add"]:
        conn.execute("INSERT OR IGNORE INTO baseline_alocacao (projeto_id, matricula, tipo_alocacao) "
                     "VALUES (?,?,?)", (pid, mat, tipo))
    for (pid, mat, tipo) in d["aloc_del"]:
        conn.execute("DELETE FROM baseline_alocacao WHERE projeto_id=? AND matricula=? AND tipo_alocacao=?",
                     (pid, mat, tipo))
        conn.execute("DELETE FROM baseline_alocacao_mes WHERE projeto_id=? AND matricula=? AND tipo_alocacao=?",
                     (pid, mat, tipo))
    for ((pid, mat, tipo, per), v) in d["mes"]:
        if v is None:
            conn.execute("DELETE FROM baseline_alocacao_mes WHERE projeto_id=? AND matricula=? "
                         "AND tipo_alocacao=? AND periodo=?", (pid, mat, tipo, per))
        else:
            conn.execute(
                "INSERT INTO baseline_alocacao_mes (projeto_id, matricula, tipo_alocacao, periodo, horas) "
                "VALUES (?,?,?,?,?) ON CONFLICT(projeto_id, matricula, tipo_alocacao, periodo) "
                "DO UPDATE SET horas=excluded.horas", (pid, mat, tipo, per, v))
    for ((pid, per), o) in d["per_set"]:
        conn.execute("INSERT INTO base_projeto_periodo (projeto_id, periodo, ordem) VALUES (?,?,?) "
                     "ON CONFLICT(projeto_id, periodo) DO UPDATE SET ordem=excluded.ordem", (pid, per, o))
    for (pid, per) in d["per_del"]:
        conn.execute("DELETE FROM base_projeto_periodo WHERE projeto_id=? AND periodo=?", (pid, per))


# --------------------------------------------------------------------------- #
# materialização de um commit  ->  estado completo
# --------------------------------------------------------------------------- #
def materializar(conn: sqlite3.Connection, commit_id: int) -> dict:
    chain: list[int] = []
    seen: set[int] = set()
    c = commit_id
    while c is not None and c not in seen:
        seen.add(c)
        chain.append(c)
        row = conn.execute("SELECT parent_id FROM commit_ WHERE commit_id=?", (c,)).fetchone()
        c = row["parent_id"] if row else None

    proj: dict = {}
    pess: dict = {}
    aloc: dict = {}
    mes: dict = {}
    per: dict = {}
    for cid in chain:                       # chain[0] = mais recente; primeiro a ver vence
        for r in conn.execute("SELECT * FROM chg_projeto WHERE commit_id=?", (cid,)):
            proj.setdefault(r["projeto_id"],
                            None if r["deleted"] else {k: r[k] for k in PROJETO_COLS})
        for r in conn.execute("SELECT * FROM chg_pessoa WHERE commit_id=?", (cid,)):
            pess.setdefault(r["matricula"],
                            None if r["deleted"] else {k: r[k] for k in PESSOA_COLS})
        for r in conn.execute("SELECT * FROM chg_alocacao WHERE commit_id=?", (cid,)):
            aloc.setdefault((r["projeto_id"], r["matricula"], r["tipo_alocacao"]),
                            None if r["deleted"] else True)
        for r in conn.execute("SELECT * FROM chg_alocacao_mes WHERE commit_id=?", (cid,)):
            mes.setdefault((r["projeto_id"], r["matricula"], r["tipo_alocacao"], r["periodo"]),
                           None if r["deleted"] else r["horas"])
        for r in conn.execute("SELECT * FROM chg_projeto_periodo WHERE commit_id=?", (cid,)):
            per.setdefault((r["projeto_id"], r["periodo"]),
                           None if r["deleted"] else r["ordem"])

    E: dict = {
        "projeto": {k: v for k, v in proj.items() if v is not None},
        "pessoa": {k: v for k, v in pess.items() if v is not None},
        "alocacao": {k: True for k, v in aloc.items() if v},
        "periodo": {k: v for k, v in per.items() if v is not None},
    }
    E["mes"] = {
        k: v for k, v in mes.items()
        if v is not None and v > 0 and (k[0], k[1], k[2]) in E["alocacao"]
    }
    return E


# --------------------------------------------------------------------------- #
# escrever um estado no working (+ cache)
# --------------------------------------------------------------------------- #
def _cache_set(conn: sqlite3.Connection, E: dict, escopo: int | None) -> None:
    if escopo is None:
        conn.execute("DELETE FROM baseline_alocacao")
        conn.execute("DELETE FROM baseline_alocacao_mes")
        conn.execute("DELETE FROM base_projeto_periodo")
    else:
        conn.execute("DELETE FROM baseline_alocacao WHERE projeto_id=?", (escopo,))
        conn.execute("DELETE FROM baseline_alocacao_mes WHERE projeto_id=?", (escopo,))
        conn.execute("DELETE FROM base_projeto_periodo WHERE projeto_id=?", (escopo,))
    for (pid, mat, tipo) in E["alocacao"]:
        if escopo is None or pid == escopo:
            conn.execute("INSERT INTO baseline_alocacao (projeto_id, matricula, tipo_alocacao) "
                         "VALUES (?,?,?)", (pid, mat, tipo))
    for (pid, mat, tipo, p), h in E["mes"].items():
        if escopo is None or pid == escopo:
            conn.execute("INSERT INTO baseline_alocacao_mes (projeto_id, matricula, tipo_alocacao, "
                         "periodo, horas) VALUES (?,?,?,?,?)", (pid, mat, tipo, p, h))
    for (pid, p), o in E["periodo"].items():
        if escopo is None or pid == escopo:
            conn.execute("INSERT INTO base_projeto_periodo (projeto_id, periodo, ordem) VALUES (?,?,?)",
                         (pid, p, o))
    if escopo is None:
        conn.execute("DELETE FROM base_projeto")
        for pid, row in E["projeto"].items():
            conn.execute(
                f"INSERT INTO base_projeto (projeto_id, {','.join(PROJETO_COLS)}) "
                f"VALUES (?, {','.join('?' * len(PROJETO_COLS))})",
                (pid, *[row[c] for c in PROJETO_COLS]))
        conn.execute("DELETE FROM base_pessoa")
        for mat, row in E["pessoa"].items():
            conn.execute(
                f"INSERT INTO base_pessoa (matricula, {','.join(PESSOA_COLS)}) "
                f"VALUES (?, {','.join('?' * len(PESSOA_COLS))})",
                (mat, *[row[c] for c in PESSOA_COLS]))


def escrever_working(conn: sqlite3.Connection, E: dict, escopo: int | None = None) -> None:
    """Escreve o estado E **só no working** (não toca no cache). Alocação/mês/janela:
    substituição total no escopo. projeto/pessoa: upsert (nunca apaga linha)."""
    def dentro(pid: int) -> bool:
        return escopo is None or pid == escopo

    if escopo is None:
        for pid, row in E["projeto"].items():
            conn.execute(
                f"UPDATE projeto SET {','.join(f'{c}=?' for c in PROJETO_COLS)} WHERE projeto_id=?",
                (*[row[c] for c in PROJETO_COLS], pid))
        for mat, row in E["pessoa"].items():
            conn.execute("INSERT OR IGNORE INTO pessoa (matricula, nome) VALUES (?,?)",
                         (mat, row["nome"] or mat))
            conn.execute(
                f"UPDATE pessoa SET {','.join(f'{c}=?' for c in PESSOA_COLS)} WHERE matricula=?",
                (*[row[c] for c in PESSOA_COLS], mat))

    if escopo is None:
        conn.execute("DELETE FROM alocacao")
        conn.execute("DELETE FROM projeto_periodo")
    else:
        conn.execute("DELETE FROM alocacao WHERE projeto_id=?", (escopo,))
        conn.execute("DELETE FROM projeto_periodo WHERE projeto_id=?", (escopo,))

    ids: dict[tuple, int] = {}
    for (pid, mat, tipo) in E["alocacao"]:
        if dentro(pid):
            cur = conn.execute("INSERT INTO alocacao (projeto_id, matricula, tipo_alocacao) "
                               "VALUES (?,?,?)", (pid, mat, tipo))
            ids[(pid, mat, tipo)] = cur.lastrowid
    for (pid, mat, tipo, p), h in E["mes"].items():
        aid = ids.get((pid, mat, tipo))
        if aid and h and h > 0 and dentro(pid):
            conn.execute("INSERT INTO alocacao_mes (alocacao_id, periodo, horas) VALUES (?,?,?)",
                         (aid, p, h))
    for (pid, p), o in E["periodo"].items():
        if dentro(pid):
            conn.execute("INSERT INTO projeto_periodo (projeto_id, periodo, ordem) VALUES (?,?,?)",
                         (pid, p, o))


def aplicar_ao_working(conn: sqlite3.Connection, E: dict, escopo: int | None = None) -> None:
    """Escreve E no working **e** no cache (usado por checkout / descartar)."""
    escrever_working(conn, E, escopo)
    _cache_set(conn, E, escopo)


def aplicar_delta(E: dict, d: dict) -> dict:
    """Devolve E com o delta `d` (formato de `_delta`) aplicado por cima."""
    out = {k: dict(E[k]) for k in ("projeto", "pessoa", "alocacao", "mes", "periodo")}
    for (pid, row) in d["projeto"]:
        out["projeto"][pid] = row
    for (mat, row) in d["pessoa"]:
        out["pessoa"][mat] = row
    for k in d["aloc_add"]:
        out["alocacao"][k] = True
    for k in d["aloc_del"]:
        out["alocacao"].pop(k, None)
        for mk in [mk for mk in out["mes"] if mk[:3] == k]:
            out["mes"].pop(mk, None)
    for (k, v) in d["mes"]:
        if v and v > 0:
            out["mes"][k] = v
        else:
            out["mes"].pop(k, None)
    for (k, o) in d["per_set"]:
        out["periodo"][k] = o
    for k in d["per_del"]:
        out["periodo"].pop(k, None)
    out["mes"] = {
        k: v for k, v in out["mes"].items()
        if v and v > 0 and (k[0], k[1], k[2]) in out["alocacao"]
    }
    return out


# --------------------------------------------------------------------------- #
# API pública
# --------------------------------------------------------------------------- #
def garantir_inicializado(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM head_").fetchone():
        return
    agora = _now()
    cid = conn.execute(
        "INSERT INTO commit_ (parent_id, merge_parent_id, autor, mensagem, criado_em, origem) "
        "VALUES (NULL, NULL, 'sistema', 'Estado inicial', ?, 'seed')", (agora,)
    ).lastrowid
    # cache: alocação já vem de baseline_alocacao*; campos vêm do working atual
    conn.execute("DELETE FROM base_projeto")
    conn.execute(f"INSERT INTO base_projeto (projeto_id, {','.join(PROJETO_COLS)}) "
                 f"SELECT projeto_id, {','.join(PROJETO_COLS)} FROM projeto")
    conn.execute("DELETE FROM base_pessoa")
    conn.execute(f"INSERT INTO base_pessoa (matricula, {','.join(PESSOA_COLS)}) "
                 f"SELECT matricula, {','.join(PESSOA_COLS)} FROM pessoa")
    conn.execute("DELETE FROM base_projeto_periodo")
    conn.execute("INSERT INTO base_projeto_periodo (projeto_id, periodo, ordem) "
                 "SELECT projeto_id, periodo, ordem FROM projeto_periodo")
    # commit raiz = tudo que está no cache, como 'adicionado'
    cache = _estado_cache(conn)
    _grava_chg(conn, cid, _delta({"projeto": {}, "pessoa": {}, "alocacao": {}, "mes": {},
                                  "periodo": {}}, cache))
    conn.execute("INSERT INTO ref_ (nome, commit_id, criado_em) VALUES ('main', ?, ?)", (cid, agora))
    conn.execute("INSERT INTO head_ (id, ref_nome, base_commit_id) VALUES (1, 'main', ?)", (cid,))


def _head(conn: sqlite3.Connection) -> sqlite3.Row:
    garantir_inicializado(conn)
    return conn.execute("SELECT ref_nome, base_commit_id FROM head_ WHERE id=1").fetchone()


def diff_pendente(conn: sqlite3.Connection) -> dict:
    """Delta working x cache (o que um `commit` gravaria)."""
    return _delta(_estado_cache(conn), _estado_working(conn))


def sujo(conn: sqlite3.Connection) -> bool:
    return not _delta_vazio(diff_pendente(conn))


def _protegidas(conn: sqlite3.Connection) -> set[str]:
    """Branches que não aceitam commit direto. Default {'main'};
    sobrescrevível por preferencias.chave='branches_protegidas' (csv)."""
    r = conn.execute("SELECT valor FROM preferencias WHERE chave='branches_protegidas'").fetchone()
    if r and (r["valor"] or "").strip():
        return {x.strip() for x in r["valor"].split(",") if x.strip()}
    return {"main"}


def branch_protegida(conn: sqlite3.Connection, nome: str | None = None) -> bool:
    nome = nome or _head(conn)["ref_nome"]
    return nome in _protegidas(conn)


def commit(conn: sqlite3.Connection, mensagem: str, autor: str = "", origem: str = "manual") -> int:
    _sem_merge(conn)
    h = _head(conn)
    d = _delta(_estado_cache(conn), _estado_working(conn))
    if _delta_vazio(d):
        raise NadaParaCommitar("nada para commitar")
    cid = conn.execute(
        "INSERT INTO commit_ (parent_id, merge_parent_id, autor, mensagem, criado_em, origem) "
        "VALUES (?, NULL, ?, ?, ?, ?)",
        (h["base_commit_id"], autor or None, mensagem, _now(), origem),
    ).lastrowid
    _grava_chg(conn, cid, d)
    _aplica_cache(conn, d)
    conn.execute("UPDATE ref_ SET commit_id=? WHERE nome=?", (cid, h["ref_nome"]))
    conn.execute("UPDATE head_ SET base_commit_id=? WHERE id=1", (cid,))
    conn.commit()
    return cid


def commit_transicao_cache(conn: sqlite3.Connection, mensagem: str, origem: str,
                           cache_antes: dict) -> int | None:
    """Registra um commit para uma mudança feita DIRETO no cache (ex.: rebuild do BI).
    O working não é tocado; suas edições passam a diferir do novo commit."""
    h = _head(conn)
    d = _delta(cache_antes, _estado_cache(conn))
    if _delta_vazio(d):
        return None
    cid = conn.execute(
        "INSERT INTO commit_ (parent_id, merge_parent_id, autor, mensagem, criado_em, origem) "
        "VALUES (?, NULL, 'sistema', ?, ?, ?)",
        (h["base_commit_id"], mensagem, _now(), origem),
    ).lastrowid
    _grava_chg(conn, cid, d)
    conn.execute("UPDATE ref_ SET commit_id=? WHERE nome=?", (cid, h["ref_nome"]))
    conn.execute("UPDATE head_ SET base_commit_id=? WHERE id=1", (cid,))
    conn.commit()
    return cid


def snapshot_cache(conn: sqlite3.Connection) -> dict:
    return _estado_cache(conn)


def commitar_estado_bi(conn: sqlite3.Connection, mensagem: str) -> int | None:
    """Commit **na `main`** = estado atual do cache (montado pelo rebuild do BI).
    Sempre `main`, sempre `origem='bi'`, sem 3-way — o BI é a autoridade do que cobre.
    Não toca em working/rascunho de ninguém. Se o HEAD legado está na `main`,
    avança `head_.base_commit_id` junto (o cache já reflete o BI)."""
    parent = tip_commit(conn, "main")
    d = _delta(materializar(conn, parent), _estado_cache(conn))
    if _delta_vazio(d):
        return None
    cid = conn.execute(
        "INSERT INTO commit_ (parent_id, merge_parent_id, autor, mensagem, criado_em, origem) "
        "VALUES (?, NULL, 'BI', ?, ?, 'bi')",
        (parent, mensagem, _now()),
    ).lastrowid
    _grava_chg(conn, cid, d)
    conn.execute("UPDATE ref_ SET commit_id=? WHERE nome='main'", (cid,))
    hrow = conn.execute("SELECT ref_nome FROM head_ WHERE id=1").fetchone()
    if hrow and hrow["ref_nome"] == "main":
        conn.execute("UPDATE head_ SET base_commit_id=? WHERE id=1", (cid,))
    conn.commit()
    return cid


def descartar(conn: sqlite3.Connection, projeto_id: int | None = None) -> None:
    """working := HEAD (reset --hard). `projeto_id` = descarte parcial (só a
    alocação/janela daquele projeto)."""
    h = _head(conn)
    E = materializar(conn, h["base_commit_id"])
    aplicar_ao_working(conn, E, escopo=projeto_id)
    if projeto_id is None:
        conn.execute("UPDATE projeto SET alterado_em=NULL")
    else:
        conn.execute("UPDATE projeto SET alterado_em=NULL WHERE projeto_id=?", (projeto_id,))
    conn.commit()


def checkout(conn: sqlite3.Connection, ref: str) -> None:
    _sem_merge(conn)
    if sujo(conn):
        raise VersaoSuja("há mudanças não commitadas — faça commit ou descarte antes")
    r = conn.execute("SELECT commit_id FROM ref_ WHERE nome=?", (ref,)).fetchone()
    if r is None:
        raise KeyError(ref)
    E = materializar(conn, r["commit_id"])
    aplicar_ao_working(conn, E, escopo=None)
    conn.execute("UPDATE projeto SET alterado_em=NULL")
    conn.execute("UPDATE head_ SET ref_nome=?, base_commit_id=? WHERE id=1", (ref, r["commit_id"]))
    conn.commit()


def branch(conn: sqlite3.Connection, nome: str, a_partir: str | None = None,
           trocar: bool = False, mover_pendencias: bool = False) -> None:
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("nome da branch é obrigatório")
    if conn.execute("SELECT 1 FROM ref_ WHERE nome=?", (nome,)).fetchone():
        raise ValueError(f"branch '{nome}' já existe")

    if mover_pendencias:
        # "git switch -c": cria o branch no HEAD atual e leva o working junto.
        # Não re-materializa (checkout apagaria as pendências).
        base = _head(conn)["base_commit_id"]
        conn.execute("INSERT INTO ref_ (nome, commit_id, criado_em) VALUES (?,?,?)", (nome, base, _now()))
        conn.execute("UPDATE head_ SET ref_nome=? WHERE id=1", (nome,))
        conn.commit()
        return

    if a_partir not in (None, ""):
        # aceita nome de branch OU commit_id (número) como ponto de partida
        src = conn.execute("SELECT commit_id FROM ref_ WHERE nome=?", (str(a_partir),)).fetchone()
        if src is not None:
            cid = src["commit_id"]
        elif str(a_partir).lstrip("-").isdigit() and conn.execute(
            "SELECT 1 FROM commit_ WHERE commit_id=?", (int(a_partir),)
        ).fetchone():
            cid = int(a_partir)
        else:
            raise KeyError(a_partir)
    else:
        cid = _head(conn)["base_commit_id"]
    conn.execute("INSERT INTO ref_ (nome, commit_id, criado_em) VALUES (?,?,?)", (nome, cid, _now()))
    conn.commit()
    if trocar:
        checkout(conn, nome)


def deletar_branch(conn: sqlite3.Connection, nome: str) -> None:
    if nome == "main":
        raise ValueError("não dá para apagar a branch main")
    if _head(conn)["ref_nome"] == nome:
        raise ValueError("não dá para apagar a branch em que você está")
    if not conn.execute("SELECT 1 FROM ref_ WHERE nome=?", (nome,)).fetchone():
        raise KeyError(nome)
    conn.execute("DELETE FROM ref_ WHERE nome=?", (nome,))
    conn.commit()


def log(conn: sqlite3.Connection, ref: str | None = None, limite: int = 100) -> list[dict]:
    if ref:
        r = conn.execute("SELECT commit_id FROM ref_ WHERE nome=?", (ref,)).fetchone()
        if r is None:
            raise KeyError(ref)
        c = r["commit_id"]
    else:
        c = _head(conn)["base_commit_id"]
    out: list[dict] = []
    seen: set[int] = set()
    while c is not None and c not in seen and len(out) < limite:
        seen.add(c)
        row = conn.execute(
            "SELECT commit_id, parent_id, merge_parent_id, autor, mensagem, criado_em, origem "
            "FROM commit_ WHERE commit_id=?", (c,)
        ).fetchone()
        if row is None:
            break
        out.append(dict(row))
        c = row["parent_id"]
    return out


def estado_repo(conn: sqlite3.Connection) -> dict:
    h = _head(conn)
    refs = [
        dict(r) for r in conn.execute(
            "SELECT r.nome, r.commit_id, c.mensagem, c.criado_em, c.origem "
            "FROM ref_ r JOIN commit_ c ON c.commit_id = r.commit_id ORDER BY r.nome"
        )
    ]
    d = diff_pendente(conn)
    me = conn.execute("SELECT * FROM merge_estado WHERE id=1").fetchone()
    merge_info = None
    if me is not None:
        n = conn.execute("SELECT COUNT(*) t, SUM(resolvido) r FROM merge_conflito").fetchone()
        merge_info = {"origem": me["origem"], "conflitos": n["t"],
                      "resolvidos": n["r"] or 0}
    prot = _protegidas(conn)
    return {
        "branch": h["ref_nome"],
        "head_commit": h["base_commit_id"],
        "sujo": not _delta_vazio(d),
        "pendente": _resumo(d),
        "refs": refs,
        "merge": merge_info,
        "protegida": h["ref_nome"] in prot,
        "protegidas": sorted(prot),
    }


def grafo(conn: sqlite3.Connection, limite: int = 1000) -> dict:
    """DAG completo (todos os commits) + estado do repo — para a UI de grafo."""
    commits = [
        dict(r) for r in conn.execute(
            "SELECT commit_id, parent_id, merge_parent_id, autor, mensagem, criado_em, origem "
            "FROM commit_ ORDER BY commit_id DESC LIMIT ?", (limite,)
        )
    ]
    return {"commits": commits, **estado_repo(conn)}


# --------------------------------------------------------------------------- #
# diff estruturado (para a tela) — de/para = commit_id ou None(=working)
# --------------------------------------------------------------------------- #
def _estado_de(conn: sqlite3.Connection, ref) -> tuple[dict, dict | None]:
    """`ref` = None -> working; senão commit_id. Devolve (estado, info-commit|None)."""
    if ref is None:
        return _estado_working(conn), None
    cid = int(ref)
    info = conn.execute(
        "SELECT commit_id, parent_id, autor, mensagem, criado_em, origem FROM commit_ WHERE commit_id=?",
        (cid,),
    ).fetchone()
    if info is None:
        raise KeyError(cid)
    return materializar(conn, cid), dict(info)


def diff(conn: sqlite3.Connection, de=None, para=None) -> dict:
    """Diff estruturado agrupado pelas 4 tabelas versionadas.

    `para` None = working; `de` None = 1º pai de `para` (ou HEAD, se para=working).
    """
    para_state, para_info = _estado_de(conn, para)
    if de is None:
        if para is None:
            de = _head(conn)["base_commit_id"]
        else:
            de = para_info["parent_id"]
    de_state, de_info = (({"projeto": {}, "pessoa": {}, "alocacao": {}, "mes": {}, "periodo": {}}, None)
                         if de is None else _estado_de(conn, de))

    nomes_pr = {r["projeto_id"]: r["nome"] for r in conn.execute("SELECT projeto_id, nome FROM projeto")}
    nomes_pe = {r["matricula"]: r["nome"] for r in conn.execute("SELECT matricula, nome FROM pessoa")}

    def npr(pid, st):
        row = st["projeto"].get(pid)
        return (row and row.get("nome")) or nomes_pr.get(pid) or f"#{pid}"

    def npe(mat, st):
        row = st["pessoa"].get(mat)
        return (row and row.get("nome")) or nomes_pe.get(mat) or mat

    # -- projetos / pessoas: campo a campo --
    def campos(cols, a, b):
        return [{"campo": c, "de": a.get(c) if a else None, "para": b.get(c) if b else None}
                for c in cols if (a or {}).get(c) != (b or {}).get(c)]

    projetos = []
    for pid in sorted(set(de_state["projeto"]) | set(para_state["projeto"]), key=lambda x: str(x)):
        a, b = de_state["projeto"].get(pid), para_state["projeto"].get(pid)
        if a == b:
            continue
        tag = "adicionada" if a is None else "removida" if b is None else "alterada"
        projetos.append({"projeto_id": pid, "nome": npr(pid, para_state if b else de_state),
                         "tag": tag, "campos": campos(PROJETO_COLS, a, b)})

    pessoas = []
    for mat in sorted(set(de_state["pessoa"]) | set(para_state["pessoa"])):
        a, b = de_state["pessoa"].get(mat), para_state["pessoa"].get(mat)
        if a == b:
            continue
        tag = "adicionada" if a is None else "removida" if b is None else "alterada"
        pessoas.append({"matricula": mat, "nome": npe(mat, para_state if b else de_state),
                        "tag": tag, "campos": campos(PESSOA_COLS, a, b)})

    # -- alocações: agrupa por (projeto, matricula, tipo) --
    linhas: dict[tuple, dict] = {}
    all_keys = set(de_state["alocacao"]) | set(para_state["alocacao"])
    all_keys |= {(k[0], k[1], k[2]) for k in set(de_state["mes"]) | set(para_state["mes"])}
    for (pid, mat, tp) in all_keys:
        em_a = (pid, mat, tp) in de_state["alocacao"]
        em_b = (pid, mat, tp) in para_state["alocacao"]
        meses = []
        pers = sorted({k[3] for k in set(de_state["mes"]) | set(para_state["mes"])
                       if k[:3] == (pid, mat, tp)})
        for per in pers:
            va = de_state["mes"].get((pid, mat, tp, per), 0)
            vb = para_state["mes"].get((pid, mat, tp, per), 0)
            if va != vb:
                meses.append({"periodo": per, "de": va, "para": vb})
        if em_a == em_b and not meses:
            continue
        tag = "adicionada" if not em_a and em_b else "removida" if em_a and not em_b else "alterada"
        linhas[(pid, mat, tp)] = {
            "projeto": npr(pid, para_state if em_b else de_state),
            "pessoa": npe(mat, para_state if em_b else de_state),
            "tipo_alocacao": tp, "tag": tag, "meses": meses,
        }
    alocacoes = [linhas[k] for k in sorted(linhas, key=lambda k: (linhas[k]["projeto"].lower(),
                                                                  linhas[k]["pessoa"].lower(), k[2]))]

    # -- janela de meses --
    janela = []
    for (pid, per) in sorted(set(de_state["periodo"]) | set(para_state["periodo"]), key=lambda x: (str(x[0]), x[1])):
        a = (pid, per) in de_state["periodo"]
        b = (pid, per) in para_state["periodo"]
        if a != b:
            janela.append({"projeto": npr(pid, para_state if b else de_state),
                           "periodo": per, "tag": "adicionada" if b else "removida"})

    n_cel = sum(len(l["meses"]) for l in alocacoes)
    return {
        "de": {"commit_id": de, **({"mensagem": de_info["mensagem"]} if de_info else {"mensagem": "(raiz)"})}
        if de is not None else {"commit_id": None, "mensagem": "(raiz)"},
        "para": {"commit_id": para, "mensagem": para_info["mensagem"] if para_info else "alterações não commitadas",
                 "autor": para_info["autor"] if para_info else None,
                 "criado_em": para_info["criado_em"] if para_info else None},
        "resumo": {
            "projetos": len(projetos), "pessoas": len(pessoas),
            "alocacoes": len(alocacoes), "celulas": n_cel,
            "alocacoes_add": sum(1 for l in alocacoes if l["tag"] == "adicionada"),
            "alocacoes_rem": sum(1 for l in alocacoes if l["tag"] == "removida"),
            "janela": len(janela),
        },
        "projetos": projetos, "pessoas": pessoas, "alocacoes": alocacoes, "janela": janela,
    }


# --------------------------------------------------------------------------- #
# merge 3-way (Fase 2)
# --------------------------------------------------------------------------- #
def _sem_merge(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM merge_estado WHERE id=1").fetchone():
        raise MergeEmAndamento("merge em andamento — conclua ou aborte antes")


def _ancestrais(conn: sqlite3.Connection, c: int) -> dict[int, int]:
    """commit_id -> distância mínima a partir de `c` (inclui `c` em 0)."""
    dist = {c: 0}
    fila = [c]
    while fila:
        x = fila.pop(0)
        row = conn.execute("SELECT parent_id, merge_parent_id FROM commit_ WHERE commit_id=?", (x,)).fetchone()
        if row is None:
            continue
        for p in (row["parent_id"], row["merge_parent_id"]):
            if p is not None and p not in dist:
                dist[p] = dist[x] + 1
                fila.append(p)
    return dist


def _merge_base(conn: sqlite3.Connection, a: int, b: int) -> int | None:
    da, db = _ancestrais(conn, a), _ancestrais(conn, b)
    comum = set(da) & set(db)
    if not comum:
        return None
    return min(comum, key=lambda x: (da[x] + db[x], -x))


_VAZIO = {"projeto": {}, "pessoa": {}, "alocacao": {}, "mes": {}, "periodo": {}}


def _merge3(Eb: dict, Eo: dict, Et: dict) -> tuple[dict, list[tuple]]:
    """3-way. Devolve (estado mesclado com OURS nos conflitos, lista de conflitos)."""
    m = {k: dict(Eo[k]) for k in _VAZIO}
    conf: list[tuple] = []

    for k in set(Eo["alocacao"]) | set(Et["alocacao"]) | set(Eb["alocacao"]):
        o, t, base = k in Eo["alocacao"], k in Et["alocacao"], k in Eb["alocacao"]
        keep = o if o == t else (t if o == base else o)
        if keep:
            m["alocacao"][k] = True
        else:
            m["alocacao"].pop(k, None)

    for k in set(Eo["mes"]) | set(Et["mes"]) | set(Eb["mes"]):
        o, t, base = Eo["mes"].get(k), Et["mes"].get(k), Eb["mes"].get(k)
        if o == t:
            v = o
        elif o == base:
            v = t
        elif t == base:
            v = o
        else:
            v = o
            conf.append(("mes", k, base, o, t))
        if v and v > 0:
            m["mes"][k] = v
        else:
            m["mes"].pop(k, None)
    m["mes"] = {k: v for k, v in m["mes"].items() if (k[0], k[1], k[2]) in m["alocacao"]}

    for dom in ("projeto", "pessoa"):
        for k in set(Eo[dom]) | set(Et[dom]) | set(Eb[dom]):
            o, t, base = Eo[dom].get(k), Et[dom].get(k), Eb[dom].get(k)
            if o == t:
                row = o
            elif o == base:
                row = t
            elif t == base:
                row = o
            else:
                row = o
                conf.append((dom, k, base, o, t))
            if row is not None:
                m[dom][k] = row
            else:
                m[dom].pop(k, None)

    for k, ordv in Et["periodo"].items():          # janela: OURS vence, união
        m["periodo"].setdefault(k, ordv)
    return m, conf


def _rotulo(conn: sqlite3.Connection, dom: str, k) -> str:
    if dom == "projeto":
        r = conn.execute("SELECT nome FROM projeto WHERE projeto_id=?", (k,)).fetchone()
        return r["nome"] if r else f"projeto {k}"
    if dom == "pessoa":
        r = conn.execute("SELECT nome FROM pessoa WHERE matricula=?", (k,)).fetchone()
        return r["nome"] if r else str(k)
    pid, mat, tipo, per = k
    nome = conn.execute("SELECT nome FROM pessoa WHERE matricula=?", (mat,)).fetchone()
    pnome = conn.execute("SELECT nome FROM projeto WHERE projeto_id=?", (pid,)).fetchone()
    mm = f"{per[5:7]}/{per[:4]}"
    return f"{(nome['nome'] if nome else mat)} · {tipo} · {(pnome['nome'] if pnome else pid)} · {mm}"


def _conflito_dict(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"], "dominio": r["dominio"], "chave": _json.loads(r["chave"]),
        "rotulo": r["rotulo"], "resolvido": bool(r["resolvido"]),
        "base": _json.loads(r["base_val"]), "ours": _json.loads(r["our_val"]),
        "theirs": _json.loads(r["their_val"]),
        "valor_final": _json.loads(r["valor_final"]) if r["valor_final"] else None,
    }


def listar_conflitos(conn: sqlite3.Connection) -> list[dict]:
    return [_conflito_dict(r) for r in conn.execute("SELECT * FROM merge_conflito ORDER BY dominio, id")]


def merge(conn: sqlite3.Connection, origem: str, autor: str = "") -> dict:
    _sem_merge(conn)
    if sujo(conn):
        raise VersaoSuja("faça commit ou descarte antes de mesclar")
    h = _head(conn)
    if origem == h["ref_nome"]:
        raise ValueError("não dá para mesclar a branch nela mesma")
    r = conn.execute("SELECT commit_id FROM ref_ WHERE nome=?", (origem,)).fetchone()
    if r is None:
        raise KeyError(origem)
    ours, theirs = h["base_commit_id"], r["commit_id"]

    anc_ours = _ancestrais(conn, ours)
    if theirs == ours or theirs in anc_ours:
        return {"status": "em-dia"}
    if ours in _ancestrais(conn, theirs):                       # fast-forward
        aplicar_ao_working(conn, materializar(conn, theirs), escopo=None)
        conn.execute("UPDATE projeto SET alterado_em=NULL")
        conn.execute("UPDATE ref_ SET commit_id=? WHERE nome=?", (theirs, h["ref_nome"]))
        conn.execute("UPDATE head_ SET base_commit_id=? WHERE id=1", (theirs,))
        conn.commit()
        return {"status": "fast-forward", "commit_id": theirs}

    base = _merge_base(conn, ours, theirs)
    Eb = materializar(conn, base) if base else {k: {} for k in _VAZIO}
    Eo, Et = materializar(conn, ours), materializar(conn, theirs)
    mesclado, conf = _merge3(Eb, Eo, Et)

    aplicar_ao_working(conn, mesclado, escopo=None)
    conn.execute(
        "INSERT OR REPLACE INTO merge_estado (id, origem, theirs_commit, base_commit, criado_em) "
        "VALUES (1,?,?,?,?)", (origem, theirs, base if base else ours, _now()))
    conn.execute("DELETE FROM merge_conflito")
    for (dom, k, bv, ov, tv) in conf:
        conn.execute(
            "INSERT INTO merge_conflito (dominio, chave, rotulo, base_val, our_val, their_val) "
            "VALUES (?,?,?,?,?,?)",
            (dom, _json.dumps(k), _rotulo(conn, dom, k),
             _json.dumps(bv), _json.dumps(ov), _json.dumps(tv)))
    conn.commit()

    if not conf:
        cid = concluir_merge(conn, f"Merge da branch '{origem}'", autor=autor)
        return {"status": "ok", "commit_id": cid}
    return {"status": "conflito", "conflitos": listar_conflitos(conn)}


def resolver_conflito(conn: sqlite3.Connection, cid: int, *, lado: str | None = None,
                      valor=None) -> None:
    r = conn.execute("SELECT * FROM merge_conflito WHERE id=?", (cid,)).fetchone()
    if r is None:
        raise KeyError(cid)
    if lado == "ours":
        final = _json.loads(r["our_val"])
    elif lado == "theirs":
        final = _json.loads(r["their_val"])
    else:
        final = valor
    dom, k = r["dominio"], _json.loads(r["chave"])

    if dom == "mes":
        pid, mat, tipo, per = k
        v = 0 if final in (None, "", "0") else int(round(float(final)))
        if v > 0:
            conn.execute("INSERT OR IGNORE INTO alocacao (projeto_id, matricula, tipo_alocacao) "
                         "VALUES (?,?,?)", (pid, mat, tipo))
            aid = conn.execute("SELECT alocacao_id FROM alocacao WHERE projeto_id=? AND matricula=? "
                               "AND tipo_alocacao=?", (pid, mat, tipo)).fetchone()["alocacao_id"]
            conn.execute("INSERT INTO alocacao_mes (alocacao_id, periodo, horas) VALUES (?,?,?) "
                         "ON CONFLICT(alocacao_id, periodo) DO UPDATE SET horas=excluded.horas",
                         (aid, per, v))
        else:
            conn.execute("DELETE FROM alocacao_mes WHERE periodo=? AND alocacao_id IN "
                         "(SELECT alocacao_id FROM alocacao WHERE projeto_id=? AND matricula=? "
                         "AND tipo_alocacao=?)", (per, pid, mat, tipo))
            final = 0
    elif dom in ("projeto", "pessoa"):
        cols = PROJETO_COLS if dom == "projeto" else PESSOA_COLS
        row = final if isinstance(final, dict) else _json.loads(final)
        if dom == "projeto":
            conn.execute(f"UPDATE projeto SET {','.join(f'{c}=?' for c in cols)} WHERE projeto_id=?",
                         (*[row.get(c) for c in cols], k))
        else:
            conn.execute("INSERT OR IGNORE INTO pessoa (matricula, nome) VALUES (?,?)",
                         (k, row.get("nome") or k))
            conn.execute(f"UPDATE pessoa SET {','.join(f'{c}=?' for c in cols)} WHERE matricula=?",
                         (*[row.get(c) for c in cols], k))

    conn.execute("UPDATE merge_conflito SET resolvido=1, valor_final=? WHERE id=?",
                 (_json.dumps(final), cid))
    conn.commit()


def concluir_merge(conn: sqlite3.Connection, mensagem: str, autor: str = "") -> int:
    me = conn.execute("SELECT * FROM merge_estado WHERE id=1").fetchone()
    if me is None:
        raise ValueError("nenhum merge em andamento")
    pend = conn.execute("SELECT COUNT(*) c FROM merge_conflito WHERE resolvido=0").fetchone()["c"]
    if pend:
        raise ValueError(f"{pend} conflito(s) sem resolver")
    h = _head(conn)
    ours = h["base_commit_id"]
    final = _estado_working(conn)
    d = _delta(materializar(conn, ours), final)
    cid = conn.execute(
        "INSERT INTO commit_ (parent_id, merge_parent_id, autor, mensagem, criado_em, origem) "
        "VALUES (?, ?, ?, ?, ?, 'merge')",
        (ours, me["theirs_commit"], autor or None,
         mensagem or f"Merge da branch '{me['origem']}'", _now()),
    ).lastrowid
    _grava_chg(conn, cid, d)
    _cache_set(conn, final, escopo=None)
    conn.execute("UPDATE ref_ SET commit_id=? WHERE nome=?", (cid, h["ref_nome"]))
    conn.execute("UPDATE head_ SET base_commit_id=? WHERE id=1", (cid,))
    conn.execute("DELETE FROM merge_conflito")
    conn.execute("DELETE FROM merge_estado")
    conn.commit()
    return cid


def abortar_merge(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM merge_estado WHERE id=1").fetchone() is None:
        raise ValueError("nenhum merge em andamento")
    aplicar_ao_working(conn, materializar(conn, _head(conn)["base_commit_id"]), escopo=None)
    conn.execute("UPDATE projeto SET alterado_em=NULL")
    conn.execute("DELETE FROM merge_conflito")
    conn.execute("DELETE FROM merge_estado")
    conn.commit()


# --------------------------------------------------------------------------- #
# rascunhos (multiusuário — ver docs/COLABORACAO.md)
# --------------------------------------------------------------------------- #
def tip_commit(conn: sqlite3.Connection, ref: str) -> int:
    r = conn.execute("SELECT commit_id FROM ref_ WHERE nome=?", (ref,)).fetchone()
    if r is None:
        raise KeyError(ref)
    return r["commit_id"]


def _split(s: str) -> list[str]:
    return s.split("|")


def _aplicar_edicoes(E: dict, ed: dict) -> dict:
    """Estado materializado + edit-set do rascunho -> estado 'OURS' sintético."""
    out = {k: dict(E[k]) for k in ("projeto", "pessoa", "alocacao", "mes", "periodo")}
    for s, h in (ed.get("mes") or {}).items():
        p = _split(s)
        key = (int(p[0]), p[1], p[2], p[3])
        if h in (None, 0, "0", ""):
            out["mes"].pop(key, None)
        else:
            out["mes"][key] = int(round(float(h)))
    for t in (ed.get("aloc_add") or []):
        out["alocacao"][(int(t[0]), t[1], t[2])] = True
    for t in (ed.get("aloc_del") or []):
        k = (int(t[0]), t[1], t[2])
        out["alocacao"].pop(k, None)
        for mk in [mk for mk in out["mes"] if mk[:3] == k]:
            out["mes"].pop(mk, None)
    for mat, fields in (ed.get("pessoa") or {}).items():
        base = dict(out["pessoa"].get(mat) or {c: None for c in PESSOA_COLS})
        base.update({c: fields[c] for c in fields if c in PESSOA_COLS})
        out["pessoa"][mat] = base
    for pid, fields in (ed.get("projeto") or {}).items():
        pid = int(pid)
        base = dict(out["projeto"].get(pid) or {c: None for c in PROJETO_COLS})
        base.update({c: fields[c] for c in fields if c in PROJETO_COLS})
        out["projeto"][pid] = base
    for s, o in (ed.get("janela") or {}).items():
        p = _split(s)
        key = (int(p[0]), p[1])
        if o in (None, ""):
            out["periodo"].pop(key, None)
        else:
            out["periodo"][key] = int(o)
    out["mes"] = {
        k: v for k, v in out["mes"].items()
        if v and v > 0 and (k[0], k[1], k[2]) in out["alocacao"]
    }
    return out


# "footprint" no nível de LINHA/entidade (não da célula): a alocação inteira,
# a pessoa, o projeto. Nível 3 = os dois lados mexeram na mesma linha.
def _footprint_delta(d: dict) -> set:
    fp: set = set()
    for (k, _) in d["mes"]:
        fp.add(("aloc", k[0], k[1], k[2]))
    for k in d["aloc_add"] + d["aloc_del"]:
        fp.add(("aloc",) + k)
    for (pid, _) in d["projeto"]:
        fp.add(("projeto", pid))
    for (mat, _) in d["pessoa"]:
        fp.add(("pessoa", mat))
    for (k, _) in d["per_set"]:
        fp.add(("projeto", k[0]))
    for k in d["per_del"]:
        fp.add(("projeto", k[0]))
    return fp


def _footprint_edicoes(ed: dict) -> set:
    fp: set = set()
    for s in (ed.get("mes") or {}):
        p = _split(s)
        fp.add(("aloc", int(p[0]), p[1], p[2]))
    for t in (ed.get("aloc_add") or []) + (ed.get("aloc_del") or []):
        fp.add(("aloc", int(t[0]), t[1], t[2]))
    for pid in (ed.get("projeto") or {}):
        fp.add(("projeto", int(pid)))
    for mat in (ed.get("pessoa") or {}):
        fp.add(("pessoa", mat))
    for s in (ed.get("janela") or {}):
        p = _split(s)
        fp.add(("projeto", int(p[0])))
    return fp


def _commits_entre(conn: sqlite3.Connection, base: int, tip: int) -> list[dict]:
    out: list[dict] = []
    c = tip
    while c is not None and c != base and len(out) < 200:
        r = conn.execute(
            "SELECT commit_id, parent_id, autor, mensagem, criado_em, origem FROM commit_ WHERE commit_id=?",
            (c,),
        ).fetchone()
        if r is None:
            break
        out.append({k: r[k] for k in ("commit_id", "autor", "mensagem", "criado_em", "origem")})
        c = r["parent_id"]
    return out


def _digest(conn: sqlite3.Connection, base: int, tip: int) -> dict:
    d = _delta(materializar(conn, base), materializar(conn, tip))
    return {
        "commits": _commits_entre(conn, base, tip),
        "resumo": _resumo(d),
        "pessoas": [_rotulo(conn, "pessoa", m) for (m, _) in d["pessoa"]][:30],
        "projetos": [_rotulo(conn, "projeto", p) for (p, _) in d["projeto"]][:30],
    }


def commitar_rascunho(conn: sqlite3.Connection, autor: str, ref: str, edicoes: dict,
                      base_commit_id: int, *, confirmar: bool = False,
                      mensagem: str = "") -> dict:
    """Promove um rascunho a commit. Devolve:
      {nivel:4, conflitos:[...]}         -> conflito real, não commitou
      {nivel:2|3, digest:{...}}          -> precisa de `confirmar=True`
      {nivel, commit_id, incorporados}   -> commitou
    """
    tip = tip_commit(conn, ref)
    Eb = materializar(conn, base_commit_id)
    Et = materializar(conn, tip)
    Eo = _aplicar_edicoes(Eb, edicoes)
    if _delta_vazio(_delta(Eb, Eo)):
        raise NadaParaCommitar("rascunho sem mudanças")

    merged, conf = _merge3(Eb, Eo, Et)
    if conf:
        return {"nivel": 4, "conflitos": [
            {"dominio": dom, "chave": list(k) if isinstance(k, tuple) else k,
             "rotulo": _rotulo(conn, dom, k), "base": bv, "ours": ov, "theirs": tv}
            for (dom, k, bv, ov, tv) in conf]}

    if tip == base_commit_id:
        nivel = 1
    elif _footprint_delta(_delta(Eb, Et)).isdisjoint(_footprint_edicoes(edicoes)):
        nivel = 2
    else:
        nivel = 3

    row = conn.execute("SELECT sempre_revisar FROM usuario WHERE nome=?", (autor,)).fetchone()
    sempre = bool(row and row["sempre_revisar"])
    if (nivel == 3 or (nivel == 2 and sempre)) and not confirmar:
        return {"nivel": nivel, "digest": _digest(conn, base_commit_id, tip)}

    d = _delta(Et, merged)
    cid = conn.execute(
        "INSERT INTO commit_ (parent_id, merge_parent_id, autor, mensagem, criado_em, origem) "
        "VALUES (?, NULL, ?, ?, ?, 'manual')",
        (tip, autor or None, mensagem or f"Alterações de {autor or 'anônimo'}", _now()),
    ).lastrowid
    _grava_chg(conn, cid, d)
    conn.execute("UPDATE ref_ SET commit_id=? WHERE nome=?", (cid, ref))
    conn.execute("DELETE FROM rascunho WHERE autor=? AND ref_nome=?", (autor, ref))

    hrow = conn.execute("SELECT ref_nome FROM head_ WHERE id=1").fetchone()
    if hrow and hrow["ref_nome"] == ref:      # branch legada -> mantém working+cache em dia
        aplicar_ao_working(conn, materializar(conn, cid), escopo=None)
        conn.execute("UPDATE head_ SET base_commit_id=? WHERE id=1", (cid,))
    conn.commit()
    return {"nivel": nivel, "commit_id": cid,
            "incorporados": _commits_entre(conn, base_commit_id, tip)}


# --------------------------------------------------------------------------- #
# leituras usadas pelo export (substituem baseline.removidas / pessoas_alteradas)
# --------------------------------------------------------------------------- #
def removidas(conn: sqlite3.Connection, projeto_id: int) -> list[dict]:
    """Alocações que estão no HEAD e sumiram do working -> saem ZERADAS no .xlsx."""
    rows = conn.execute(
        """SELECT b.matricula AS matricula, b.tipo_alocacao AS tipo_alocacao
           FROM baseline_alocacao b
           WHERE b.projeto_id = ?
             AND NOT EXISTS (SELECT 1 FROM alocacao a
                             WHERE a.projeto_id = b.projeto_id AND a.matricula = b.matricula
                               AND a.tipo_alocacao = b.tipo_alocacao)""",
        (projeto_id,),
    ).fetchall()
    out = []
    for r in rows:
        horas = {
            m["periodo"]: m["horas"]
            for m in conn.execute(
                "SELECT periodo, horas FROM baseline_alocacao_mes "
                "WHERE projeto_id=? AND matricula=? AND tipo_alocacao=?",
                (projeto_id, r["matricula"], r["tipo_alocacao"]),
            )
        }
        out.append({"matricula": r["matricula"], "tipo_alocacao": r["tipo_alocacao"], "horas": horas})
    return out


def pessoas_alteradas(conn: sqlite3.Connection, projeto_id: int) -> list[dict]:
    """Pessoas alocadas no projeto cujos campos diferem do HEAD -> aba Novos_Pesquisadores."""
    base = {r["matricula"]: dict(r) for r in conn.execute("SELECT * FROM base_pessoa")}
    out: list[dict] = []
    for p in conn.execute(
        "SELECT p.* FROM pessoa p "
        "WHERE p.matricula IN (SELECT matricula FROM alocacao WHERE projeto_id=?) ORDER BY p.nome",
        (projeto_id,),
    ):
        pd = dict(p)
        b = base.get(pd["matricula"])
        if b is None or any((pd.get(c) or None) != (b.get(c) or None) for c in PESSOA_COLS):
            out.append(pd)
    return out


def restaurar_alocacao(conn: sqlite3.Connection, projeto_id: int, matricula: str,
                       tipo_alocacao: str) -> int:
    """Recria no working uma alocação removida, com os valores do HEAD."""
    cur = conn.execute("INSERT INTO alocacao (projeto_id, matricula, tipo_alocacao) VALUES (?,?,?)",
                       (projeto_id, matricula, tipo_alocacao))
    aid = cur.lastrowid
    conn.execute(
        "INSERT INTO alocacao_mes (alocacao_id, periodo, horas) "
        "SELECT ?, periodo, horas FROM baseline_alocacao_mes "
        "WHERE projeto_id=? AND matricula=? AND tipo_alocacao=? AND horas > 0",
        (aid, projeto_id, matricula, tipo_alocacao),
    )
    conn.commit()
    return aid

"""Carrega os extratos do BI e MONTA o banco de trabalho.

Arquivos (detectados pelo cabeçalho):
  - colabs (equipe)   -> `pessoa` (sobrescreve área/contrato/situação/carga/valor_hora)
  - projetos          -> `bi_projeto` + tabela `projeto` (todos, com GP/empresa/status)
  - colabmescusto     -> `bi_custo` + **reconstrói a BASELINE** (alocação por projeto/
                         pessoa/mês/tipo, todos os meses) e `pessoa.valor_hora`
  - projetomescusto   -> `bi_projeto`

Recarga do BI = SEMPRE a mesma coisa: monta o estado completo do BI e grava
**um commit na `main`** (`origem='bi'`), pegando os valores do BI como estão
(sem 3-way). Não encosta no working/rascunho de ninguém — `importar_arquivos`
tira uma foto do working antes e a reaplica por cima do estado do BI depois, de
modo que suas edições pendentes seguem visíveis (agora ancoradas no BI novo).
Para largar as edições e pegar o BI: `POST /api/descartar-tudo`.

Uso CLI:  python -m app.bi_import <arquivo.xlsx> [<arquivo.xlsx> ...]
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import sys
import unicodedata
from pathlib import Path

from .xlsx_io import Workbook


def _norm(s: object) -> str:
    """casefold + colapsa espaços + remove acentos (nomes do BI x planejamento
    divergem em acentuação: 'SIMÃO' vs 'SIMAO')."""
    if s is None:
        return ""
    t = unicodedata.normalize("NFKD", " ".join(str(s).split()))
    return "".join(c for c in t if not unicodedata.combining(c)).casefold()


def _first_sheet(wb: Workbook):
    return wb.sheet(wb.sheet_names[0])


def _header_index(sheet) -> dict[str, int]:
    return {_norm(v): i for i, v in enumerate(sheet.row_values(1), start=1) if _norm(v)}


def _iso_month(v: object) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        from .xlsx_io import serial_to_date
        d = serial_to_date(v)
    else:
        s = str(v).strip()
        d = None
        for fmt in ("%d/%m/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%y"):
            try:
                d = _dt.datetime.strptime(s, fmt).date()
                break
            except ValueError:
                pass
        if d is None:
            return None
    return d.replace(day=1).isoformat()


def _num(v: object) -> float:
    try:
        return float(str(v).replace(",", ".")) if v not in (None, "") else 0.0
    except ValueError:
        return 0.0


def classify(sheet) -> str | None:
    # _header_index já passa pelo _norm (sem acento): "carga horária" -> "carga horaria"
    h = set(_header_index(sheet))
    if {"matricula", "colaborador", "carga horaria"} <= h:
        return "equipe"
    if {"idprojetos", "nomecolaborador", "custo"} <= h:
        return "colabmescusto"
    if {"idprojetos", "nomeprojeto", "gestor de projetos"} <= h:
        return "projetos"
    if {"idprojetos", "nomeprojeto", "custo"} <= h:
        return "projetomescusto"
    return None


# -- loaders -----------------------------------------------------------
def load_equipe(conn: sqlite3.Connection, sheet) -> dict:
    h = _header_index(sheet)
    n_new = n_upd = 0
    for r in range(2, sheet.max_row + 1):
        row = sheet.row_values(r)
        if not row or row[h["matricula"] - 1] in (None, ""):
            continue

        def g(col):
            i = h.get(col)
            return row[i - 1] if i and i - 1 < len(row) else None

        mv = g("matricula")
        matricula = str(int(mv)) if isinstance(mv, float) and mv.is_integer() else str(mv).strip()
        nome = (g("colaborador") or "").strip()
        carga = _num(g("carga horaria")) or None
        fim = _iso_month(g("fim contrato")) if g("fim contrato") else None
        cur = conn.execute("SELECT matricula, capacidade_mensal FROM pessoa WHERE matricula=?", (matricula,)).fetchone()
        if cur is None:
            conn.execute("INSERT INTO pessoa (matricula, nome) VALUES (?,?)", (matricula, nome or matricula))
            n_new += 1
        else:
            n_upd += 1
        situacao = (g("ativo") or "").strip() or None
        # BI é autoridade: sobrescreve os campos que vêm dele.
        # capacidade_mensal só é preenchida se estava vazia (não clobber de ajuste manual).
        conn.execute(
            f"""UPDATE pessoa SET
                   nome = CASE WHEN nome IS NULL OR nome='' OR nome=matricula THEN ? ELSE nome END,
                   area=?, tipo_contrato=?, situacao=?, fim_contrato=?, carga_diaria=?,
                   ativo=?,
                   capacidade_mensal=COALESCE(capacidade_mensal, {'?' if carga else 'NULL'})
               WHERE matricula=?""",
            (
                nome or matricula, g("area"), g("tipo de contrato"), situacao, fim, carga,
                0 if situacao == "Desligado" else 1,
                *((int(round(carga * 22)),) if carga else ()),
                matricula,
            ),
        )
    conn.commit()
    return {"arquivo": "colabs", "novos": n_new, "atualizados": n_upd}


_SIT_RANK = {"Ativo": 0, "Planejado": 1, "Desligado": 2, None: 3}


def _name_to_matricula(conn: sqlite3.Connection) -> dict:
    """nome normalizado -> matrícula. Nome ambíguo: prefere Ativo, depois matrícula maior."""
    cand: dict[str, list] = {}
    for r in conn.execute("SELECT matricula, nome, situacao FROM pessoa"):
        cand.setdefault(_norm(r["nome"]), []).append((r["matricula"], r["situacao"]))
    out = {}
    for nome, lst in cand.items():
        lst.sort(key=lambda t: (_SIT_RANK.get(t[1], 3), -int(t[0]) if str(t[0]).isdigit() else 0))
        out[nome] = lst[0][0]
    return out


def load_colabmescusto(conn: sqlite3.Connection, sheet) -> dict:
    h = _header_index(sheet)
    by_name = _name_to_matricula(conn)
    conn.execute("DELETE FROM bi_custo")
    inseridos = sem_matricula = 0
    rate: dict[str, tuple[str, float]] = {}  # matricula -> (periodo_mais_recente, valor_hora)
    for r in range(2, sheet.max_row + 1):
        row = sheet.row_values(r)
        if not row or row[h["nomecolaborador"] - 1] in (None, ""):
            continue

        def g(col):
            i = h.get(col)
            return row[i - 1] if i and i - 1 < len(row) else None

        periodo = _iso_month(g("data"))
        if not periodo:
            continue
        nome = str(g("nomecolaborador")).strip()
        matricula = by_name.get(_norm(nome))
        if not matricula:
            sem_matricula += 1
        horas, custo = _num(g("horas")), _num(g("custo"))
        conn.execute(
            """INSERT OR REPLACE INTO bi_custo
               (id_projetos, matricula, nome_colaborador, periodo, tipo_alocacao, horas, custo)
               VALUES (?,?,?,?,?,?,?)""",
            (int(_num(g("idprojetos"))), matricula, nome, periodo,
             (g("tipo_alocacao") or "").strip() or None, horas, custo),
        )
        inseridos += 1
        # valor/hora = custo/horas do mês mais recente com dados
        if matricula and horas > 0 and custo > 0:
            prev = rate.get(matricula)
            if prev is None or periodo >= prev[0]:
                rate[matricula] = (periodo, round(custo / horas, 2))
    for matricula, (_, vh) in rate.items():
        conn.execute("UPDATE pessoa SET valor_hora=? WHERE matricula=?", (vh, matricula))
    conn.commit()

    rb = _rebuild_baseline(conn)
    return {"arquivo": "colabmescusto", "linhas": inseridos, "sem_matricula": sem_matricula,
            "valor_hora": len(rate), **rb}


def _meses_contiguos(ini: str, fim: str) -> list[str]:
    y, m = int(ini[:4]), int(ini[5:7])
    ey, em = int(fim[:4]), int(fim[5:7])
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}-01")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def _sync_base_campos(conn: sqlite3.Connection) -> None:
    """Cache global de campos (base_pessoa / base_projeto) := estado atual do
    working. O BI já atualizou `pessoa` e `projeto` antes deste ponto, então isso
    grava os valores do BI como 'commitados'."""
    from . import versao as _v

    conn.execute("DELETE FROM base_projeto")
    conn.execute(f"INSERT INTO base_projeto (projeto_id, {','.join(_v.PROJETO_COLS)}) "
                 f"SELECT projeto_id, {','.join(_v.PROJETO_COLS)} FROM projeto")
    conn.execute("DELETE FROM base_pessoa")
    conn.execute(f"INSERT INTO base_pessoa (matricula, {','.join(_v.PESSOA_COLS)}) "
                 f"SELECT matricula, {','.join(_v.PESSOA_COLS)} FROM pessoa")


def _rebuild_baseline(conn: sqlite3.Connection) -> dict:
    """Monta o estado completo do BI no cache (alocação + janela + campos) a partir
    do `bi_custo` e registra **um commit na `main`** (`origem='bi'`). Não toca no
    working de ninguém — `importar_arquivos` restaura o working depois."""
    from . import versao as _v

    ext_to_pid = {
        str(r["id_projeto_externo"]): r["projeto_id"]
        for r in conn.execute(
            "SELECT projeto_id, id_projeto_externo FROM projeto WHERE id_projeto_externo IS NOT NULL"
        )
    }
    nome_bi = {r["id_projetos"]: r["nome_projeto"] for r in conn.execute("SELECT id_projetos, nome_projeto FROM bi_projeto")}
    # cria projeto mínimo p/ idProjetos do bi_custo que não veio no projetos.xlsx
    for idp in {r["id_projetos"] for r in conn.execute("SELECT DISTINCT id_projetos FROM bi_custo WHERE id_projetos > 0")}:
        if str(idp) in ext_to_pid:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO projeto (id_projeto_externo, nome) VALUES (?,?)",
            (str(idp), nome_bi.get(idp) or f"#{idp}"),
        )
        ext_to_pid[str(idp)] = conn.execute(
            "SELECT projeto_id FROM projeto WHERE id_projeto_externo=?", (str(idp),)
        ).fetchone()["projeto_id"]

    grupos: dict[tuple, dict[str, int]] = {}
    meses_proj: dict[int, set] = {}
    for r in conn.execute(
        "SELECT id_projetos, matricula, tipo_alocacao, periodo, horas FROM bi_custo "
        "WHERE matricula IS NOT NULL AND id_projetos > 0"
    ):
        pid = ext_to_pid.get(str(r["id_projetos"]))
        if pid is None:
            continue
        tipo = (r["tipo_alocacao"] or "Técnica").strip()
        grupos.setdefault((pid, r["matricula"], tipo), {})[r["periodo"]] = int(round(r["horas"] or 0))
        meses_proj.setdefault(pid, set()).add(r["periodo"])

    pids = set(meses_proj)
    for pid in pids:
        conn.execute("DELETE FROM baseline_alocacao WHERE projeto_id=?", (pid,))
        conn.execute("DELETE FROM baseline_alocacao_mes WHERE projeto_id=?", (pid,))
        conn.execute("DELETE FROM projeto_periodo WHERE projeto_id=?", (pid,))
        conn.execute("DELETE FROM base_projeto_periodo WHERE projeto_id=?", (pid,))
        ms = sorted(meses_proj[pid])
        full = _meses_contiguos(ms[0], ms[-1])
        conn.executemany(
            "INSERT INTO projeto_periodo (projeto_id, periodo, ordem) VALUES (?,?,?)",
            [(pid, p, i) for i, p in enumerate(full)],
        )
        conn.executemany(
            "INSERT INTO base_projeto_periodo (projeto_id, periodo, ordem) VALUES (?,?,?)",
            [(pid, p, i) for i, p in enumerate(full)],
        )

    for (pid, mat, tipo), mm in grupos.items():
        mm = {p: h for p, h in mm.items() if h > 0}
        if not mm:
            continue  # não guardamos alocação zerada
        conn.execute(
            "INSERT INTO baseline_alocacao (projeto_id, matricula, tipo_alocacao) VALUES (?,?,?)",
            (pid, mat, tipo),
        )
        conn.executemany(
            "INSERT INTO baseline_alocacao_mes (projeto_id, matricula, tipo_alocacao, periodo, horas) VALUES (?,?,?,?,?)",
            [(pid, mat, tipo, p, h) for p, h in mm.items()],
        )

    _sync_base_campos(conn)
    conn.commit()

    cid = _v.commitar_estado_bi(conn, f"Importação BI {_dt.date.today():%Y-%m-%d}")
    return {"projetos_baseline": len(pids), "commit_bi": cid}


def load_projetos(conn: sqlite3.Connection, sheet) -> dict:
    """projetos.xlsx: idProjetos, NomeProjeto, Empresa, Status, Gestor de Projetos.
    Popula bi_projeto e sincroniza `projeto.gestor_projetos` / empresa / status por id."""
    h = _header_index(sheet)

    def col(*names):
        for n in names:
            if n in h:
                return h[n]
        return None

    ci, cn = col("idprojetos"), col("nomeprojeto")
    ce, cs, cg = col("empresa"), col("status"), col("gestor de projetos")
    n = 0
    for r in range(2, sheet.max_row + 1):
        row = sheet.row_values(r)
        if not row or ci - 1 >= len(row) or row[ci - 1] in (None, ""):
            continue

        def gv(c):
            return row[c - 1] if c and c - 1 < len(row) else None

        pid = int(_num(row[ci - 1]))
        nome, empresa, status, gestor = _s(gv(cn)), _s(gv(ce)), _s(gv(cs)), _s(gv(cg))
        conn.execute(
            """INSERT INTO bi_projeto (id_projetos, nome_projeto, empresa, status, gestor)
               VALUES (?,?,?,?,?)
               ON CONFLICT(id_projetos) DO UPDATE SET
                 nome_projeto=excluded.nome_projeto, empresa=excluded.empresa,
                 status=excluded.status, gestor=excluded.gestor""",
            (pid, nome, empresa, status, gestor),
        )
        # tabela de trabalho: o projeto do BI vira `projeto` (editável no grid)
        conn.execute(
            """INSERT INTO projeto (id_projeto_externo, nome, empresa, status, gestor_projetos)
               VALUES (?,?,?,?,?)
               ON CONFLICT(id_projeto_externo) DO UPDATE SET
                 nome=excluded.nome, empresa=excluded.empresa, status=excluded.status,
                 gestor_projetos=excluded.gestor_projetos""",
            (str(pid), nome or f"#{pid}", empresa, status, gestor),
        )
        n += 1
    # sincroniza nos projetos de planejamento (por id_projeto_externo)
    conn.execute(
        """UPDATE projeto SET
             gestor_projetos = (SELECT gestor FROM bi_projeto b
                                WHERE b.id_projetos = CAST(projeto.id_projeto_externo AS INTEGER)),
             empresa = COALESCE((SELECT empresa FROM bi_projeto b
                                 WHERE b.id_projetos = CAST(projeto.id_projeto_externo AS INTEGER)), empresa),
             status = COALESCE((SELECT status FROM bi_projeto b
                                WHERE b.id_projetos = CAST(projeto.id_projeto_externo AS INTEGER)), status)
           WHERE id_projeto_externo IS NOT NULL"""
    )
    conn.commit()
    return {"arquivo": "projetos", "projetos": n}


def _s(v):
    return str(v).strip() if v not in (None, "") else None


def load_projetomescusto(conn: sqlite3.Connection, sheet) -> dict:
    h = _header_index(sheet)
    vistos = {}
    for r in range(2, sheet.max_row + 1):
        row = sheet.row_values(r)
        i_id, i_nome = h["idprojetos"] - 1, h["nomeprojeto"] - 1
        if not row or i_id >= len(row) or row[i_id] in (None, ""):
            continue
        vistos[int(_num(row[i_id]))] = (row[i_nome] if i_nome < len(row) else None)
    for pid, nome in vistos.items():
        conn.execute(
            "INSERT INTO bi_projeto (id_projetos, nome_projeto) VALUES (?,?) "
            "ON CONFLICT(id_projetos) DO UPDATE SET nome_projeto=excluded.nome_projeto",
            (pid, nome),
        )
    conn.commit()
    return {"arquivo": "projetomescusto", "projetos": len(vistos)}


def importar_arquivos(conn: sqlite3.Connection, paths: list[str]) -> list[dict]:
    """Ordena para carregar `equipe` antes de `colabmescusto` (precisa do nome→matrícula).
    Envelopa tudo: fotografa o working e as edições pendentes antes, deixa os loaders
    montarem o estado do BI e commitarem na `main`, e reaplica as edições do usuário
    por cima do estado do BI no final."""
    from . import versao as _v

    classificados = []
    for p in paths:
        try:
            kind = classify(_first_sheet(Workbook(str(p))))
        except Exception as e:  # noqa: BLE001
            classificados.append((p, None, str(e)))
            continue
        classificados.append((p, kind, None))

    ordem = {"equipe": 0, "projetos": 1, "projetomescusto": 2, "colabmescusto": 3, None: 9}
    classificados.sort(key=lambda t: ordem[t[1]])

    hrow = conn.execute("SELECT ref_nome FROM head_ WHERE id=1").fetchone()
    head_ref = hrow["ref_nome"] if hrow else "main"
    cache0 = _v.snapshot_cache(conn)
    work0 = _v._estado_working(conn)
    d0 = _v._delta(cache0, work0)
    # só as edições de ALOCAÇÃO/JANELA do usuário são reancoradas. Campos de
    # pessoa/projeto pertencem ao BI — se o extrato traz, o BI ganha.
    user_d = {
        "projeto": [], "pessoa": [],
        "aloc_add": d0["aloc_add"], "aloc_del": d0["aloc_del"], "mes": d0["mes"],
        "per_set": d0["per_set"], "per_del": d0["per_del"],
    }

    out: list[dict] = []
    houve_bi = False
    for p, kind, err in classificados:
        if err:
            out.append({"arquivo": Path(p).name, "status": "erro", "detail": err})
            continue
        if kind is None:
            out.append({"arquivo": Path(p).name, "status": "ignorado", "detail": "cabeçalho não reconhecido"})
            continue
        sheet = _first_sheet(Workbook(str(p)))
        fn = {"equipe": load_equipe, "colabmescusto": load_colabmescusto,
              "projetomescusto": load_projetomescusto, "projetos": load_projetos}[kind]
        res = fn(conn, sheet)
        res["status"] = "ok"
        res["arquivo"] = Path(p).name
        out.append(res)
        houve_bi = True

    if houve_bi:
        if head_ref == "main":
            # HEAD está na main: cache == estado do BI (montado pelos loaders);
            # reaplica as edições de alocação/janela do usuário por cima.
            _v.escrever_working(conn, _v.aplicar_delta(_v._estado_cache(conn), user_d))
        else:
            # HEAD está numa branch de cenário: o BI commitou na `main`, mas o
            # cache/working da branch do usuário NÃO podem ser tocados. Restaura.
            _v._cache_set(conn, cache0, None)
            _v.escrever_working(conn, work0)
        conn.commit()
    return out


if __name__ == "__main__":
    from . import db as dbmod

    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    conn = dbmod.init_db()
    for r in importar_arquivos(conn, sys.argv[1:]):
        print(r)

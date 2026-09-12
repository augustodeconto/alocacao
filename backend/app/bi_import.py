"""Carrega os extratos do BI e MONTA o banco de trabalho.

Arquivos (detectados pelo cabeçalho):
  - colabs (equipe)   -> `pessoa` (sobrescreve área/contrato/situação/carga/valor_hora)
  - projetos          -> `bi_projeto` + tabela `projeto` (todos, com GP/empresa/status)
  - colabmescusto     -> `bi_custo` + **reconstrói a BASELINE** (alocação por projeto/
                         pessoa/mês/tipo, todos os meses) e `pessoa.valor_hora`
  - projetomescusto   -> `bi_projeto`

Recarga do BI = SEMPRE a mesma coisa, em **duas fases**:
  1. `preparar_importacao` — lê os arquivos, monta o estado-alvo do BI e um
     relatório (o que mudaria), guarda em `bi_pendente`. NÃO commita nada;
     working/cache voltam exatamente como estavam.
  2. `confirmar_importacao` — só roda se o usuário concordar com o relatório:
     grava **um commit no branch `BI`** (`origem='bi'`), pegando os valores do
     BI como estão (sem 3-way) — a `main` faz fast-forward se ainda não tiver
     divergido. `descartar_pendente` cancela sem commitar.
Não encosta no working/rascunho de ninguém: as edições de alocação/janela
pendentes seguem visíveis, agora ancoradas no BI novo (se a main andou junto).
Para largar as edições e pegar o BI: `POST /api/descartar-tudo`.

Uso CLI:  python -m app.bi_import <arquivo.xlsx> [<arquivo.xlsx> ...]
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import sys
import unicodedata
from pathlib import Path

from . import db as dbmod
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
    mats: set[str] = set()
    for r in range(2, sheet.max_row + 1):
        row = sheet.row_values(r)
        if not row or row[h["matricula"] - 1] in (None, ""):
            continue

        def g(col):
            i = h.get(col)
            return row[i - 1] if i and i - 1 < len(row) else None

        mv = g("matricula")
        matricula = str(int(mv)) if isinstance(mv, float) and mv.is_integer() else str(mv).strip()
        # Power BI às vezes deixa uma legenda de rodapé ("Filtros aplicados: ...") na
        # última linha exportada, na mesma coluna da matrícula — não é gente. Matrícula
        # de verdade é um token curto, sem quebra de linha.
        if not matricula or "\n" in matricula or len(matricula) > 20:
            continue
        mats.add(matricula)
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
    return {"arquivo": "colabs", "novos": n_new, "atualizados": n_upd, "_matriculas": mats}


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
    # o commit do BI é montado depois, uma vez, por importar_arquivos()
    return {"arquivo": "colabmescusto", "linhas": inseridos, "sem_matricula": sem_matricula,
            "valor_hora": len(rate)}


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


# campos que o BI é dono (o resto de projeto/pessoa pertence ao usuário e NÃO
# pode ser arrastado para dentro do commit do BI)
_BI_PROJETO_FIELDS = ["nome", "empresa", "id_status", "gestor_projetos"]
_BI_PESSOA_FIELDS = ["nome", "situacao", "area", "tipo_contrato", "fim_contrato",
                     "carga_diaria", "valor_hora"]


def _montar_e_bi(conn: sqlite3.Connection, fp_matriculas: set[str]) -> tuple[dict, dict]:
    """Monta o **estado-alvo do BI** como dict (não commita, não mexe em cache/working):
        materializar(topo do branch `BI`)
        + alocação/janela do `bi_custo` (substitui a dos projetos que o BI cobre)
        + campos do BI (só `_BI_*_FIELDS`, só nos IDs/matrículas dos extratos).
    Devolve (E, resumo) — o resumo é o relatório que o usuário confirma antes do commit."""
    from . import versao as _v

    E = _v.materializar(conn, _v.tip_commit(conn, "BI"))

    ext_to_pid = {
        str(r["id_projeto_externo"]): r["projeto_id"]
        for r in conn.execute(
            "SELECT projeto_id, id_projeto_externo FROM projeto WHERE id_projeto_externo IS NOT NULL"
        )
    }
    nome_bi = {r["id_projetos"]: r["nome_projeto"]
               for r in conn.execute("SELECT id_projetos, nome_projeto FROM bi_projeto")}
    bi_ext_ids = {str(r["id_projetos"]) for r in conn.execute("SELECT id_projetos FROM bi_projeto")}
    bi_ext_ids |= {str(r["id_projetos"]) for r in conn.execute(
        "SELECT DISTINCT id_projetos FROM bi_custo WHERE id_projetos > 0")}
    # projeto mínimo p/ idProjetos do bi_custo que não veio no projetos.xlsx (estrutural)
    for idp in {r["id_projetos"] for r in conn.execute(
            "SELECT DISTINCT id_projetos FROM bi_custo WHERE id_projetos > 0")}:
        if str(idp) in ext_to_pid:
            continue
        conn.execute("INSERT OR IGNORE INTO projeto (id_projeto_externo, nome) VALUES (?,?)",
                     (str(idp), nome_bi.get(idp) or f"#{idp}"))
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

    # alocação + janela: substitui inteira a dos projetos que o BI cobre
    for pid, meses in meses_proj.items():
        E["alocacao"] = {k: v for k, v in E["alocacao"].items() if k[0] != pid}
        E["mes"] = {k: v for k, v in E["mes"].items() if k[0] != pid}
        E["periodo"] = {k: v for k, v in E["periodo"].items() if k[0] != pid}
        ms = sorted(meses)
        for i, p in enumerate(_meses_contiguos(ms[0], ms[-1])):
            E["periodo"][(pid, p)] = i
    for (pid, mat, tipo), mm in grupos.items():
        mm = {p: h for p, h in mm.items() if h > 0}
        if not mm:
            continue
        E["alocacao"][(pid, mat, tipo)] = True
        for p, h in mm.items():
            E["mes"][(pid, mat, tipo, p)] = h

    # campos de PROJETO — só os do BI, só nos projetos que o BI trouxe
    pcols = _v.PROJETO_COLS
    for ext, pid in ext_to_pid.items():
        if ext not in bi_ext_ids:
            continue
        row = conn.execute(f"SELECT {','.join(pcols)} FROM projeto WHERE projeto_id=?", (pid,)).fetchone()
        if row is None:
            continue
        if pid in E["projeto"]:
            for c in _BI_PROJETO_FIELDS:
                E["projeto"][pid][c] = row[c]
        else:
            E["projeto"][pid] = {c: row[c] for c in pcols}   # projeto novo do BI -> tudo

    # campos de PESSOA — só os do BI, só nas matrículas dos extratos
    ecols = _v.PESSOA_COLS
    for mat in fp_matriculas:
        row = conn.execute(f"SELECT {','.join(ecols)} FROM pessoa WHERE matricula=?", (mat,)).fetchone()
        if row is None:
            continue
        if mat in E["pessoa"]:
            for c in _BI_PESSOA_FIELDS:
                E["pessoa"][mat][c] = row[c]
        else:
            E["pessoa"][mat] = {c: row[c] for c in ecols}

    bi_tip = _v.tip_commit(conn, "BI")
    d = _v._delta(_v.materializar(conn, bi_tip), E)
    total_custo = conn.execute("SELECT COUNT(*), COALESCE(SUM(horas),0) FROM bi_custo").fetchone()
    atribuido = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(horas),0) FROM bi_custo WHERE matricula IS NOT NULL").fetchone()
    resumo = {
        **_v._resumo(d),
        "mudou": not _v._delta_vazio(d),
        "projetos_no_bi": len(meses_proj),
        "linhas_custo": total_custo[0], "linhas_sem_matricula": total_custo[0] - atribuido[0],
        "horas_totais": round(total_custo[1]), "horas_atribuidas": round(atribuido[1]),
    }
    return E, resumo


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
        # tabela de trabalho: o projeto do BI vira `projeto` (editável no grid).
        # status não é coluna própria — resolve pro id via catalogo(tipo='status')
        # (o BI só manda o texto; id_status é a única fonte de verdade na base).
        id_status = dbmod.resolver_id_status(conn, status)
        conn.execute(
            """INSERT INTO projeto (id_projeto_externo, nome, empresa, id_status, gestor_projetos)
               VALUES (?,?,?,?,?)
               ON CONFLICT(id_projeto_externo) DO UPDATE SET
                 nome=excluded.nome, empresa=excluded.empresa, id_status=excluded.id_status,
                 gestor_projetos=excluded.gestor_projetos""",
            (str(pid), nome or f"#{pid}", empresa, id_status, gestor),
        )
        n += 1
    # sincroniza nos projetos de planejamento (por id_projeto_externo). status não é
    # coluna própria — resolve o texto do BI pro id via catalogo antes de gravar.
    conn.execute(
        """UPDATE projeto SET
             gestor_projetos = (SELECT gestor FROM bi_projeto b
                                WHERE b.id_projetos = CAST(projeto.id_projeto_externo AS INTEGER)),
             empresa = COALESCE((SELECT empresa FROM bi_projeto b
                                 WHERE b.id_projetos = CAST(projeto.id_projeto_externo AS INTEGER)), empresa),
             id_status = COALESCE(
                 (SELECT cat.id FROM bi_projeto b
                  JOIN catalogo cat ON cat.tipo='status' AND lower(cat.texto)=lower(b.status)
                  WHERE b.id_projetos = CAST(projeto.id_projeto_externo AS INTEGER)),
                 id_status)
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


def _ser_key(k: tuple) -> str:
    return "|".join(str(x) for x in k)


def _serializar_estado(E: dict) -> dict:
    return {
        "projeto": {str(k): v for k, v in E["projeto"].items()},
        "pessoa": dict(E["pessoa"]),
        "alocacao": [_ser_key(k) for k in E["alocacao"]],
        "mes": {_ser_key(k): v for k, v in E["mes"].items()},
        "periodo": {_ser_key(k): v for k, v in E["periodo"].items()},
    }


def _desserializar_estado(d: dict) -> dict:
    def aloc_k(s):
        pid, mat, tipo = s.split("|", 2)
        return (int(pid), mat, tipo)

    def mes_k(s):
        pid, mat, tipo, per = s.split("|", 3)
        return (int(pid), mat, tipo, per)

    def per_k(s):
        pid, per = s.split("|", 1)
        return (int(pid), per)

    return {
        "projeto": {int(k): v for k, v in d["projeto"].items()},
        "pessoa": dict(d["pessoa"]),
        "alocacao": {aloc_k(s): True for s in d["alocacao"]},
        "mes": {mes_k(s): v for s, v in d["mes"].items()},
        "periodo": {per_k(s): v for s, v in d["periodo"].items()},
    }


def _carregar_arquivos(conn: sqlite3.Connection, paths: list[str]) -> tuple[list[dict], set[str], bool]:
    """Roda os loaders (equipe -> projetos -> projetomescusto -> colabmescusto) e devolve
    (resultados, matrículas tocadas pelo `equipe`, se veio `colabmescusto`)."""
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

    fp_mat: set[str] = set()
    houve_custo = False
    out: list[dict] = []
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
        if kind == "equipe":
            fp_mat |= res.pop("_matriculas", set())
        if kind == "colabmescusto":
            houve_custo = True
        res["status"] = "ok"
        res["arquivo"] = Path(p).name
        out.append(res)
    return out, fp_mat, houve_custo


def _podar_estrutural_novo(conn: sqlite3.Connection, pids_antes: set[int], mats_antes: set[str]) -> None:
    """`_carregar_arquivos`/`_montar_e_bi` criam projeto/pessoa de verdade quando o
    extrato traz gente/projeto novo (precisam existir pra montar o E_bi). Se a leitura
    não vai virar commit agora, desfaz essas criações — senão ficam sobras invisíveis
    (sem alocação, fora do cache) até a próxima confirmação."""
    for r in conn.execute("SELECT projeto_id FROM projeto").fetchall():
        if r["projeto_id"] not in pids_antes:
            conn.execute("DELETE FROM projeto WHERE projeto_id=?", (r["projeto_id"],))
    for r in conn.execute("SELECT matricula FROM pessoa").fetchall():
        if r["matricula"] not in mats_antes:
            conn.execute("DELETE FROM pessoa WHERE matricula=?", (r["matricula"],))


def preparar_importacao(conn: sqlite3.Connection, paths: list[str]) -> dict:
    """Fase 1 (leitura): carrega os arquivos, monta o estado-alvo do BI e o relatório,
    e guarda em `bi_pendente`. **Não commita nada** — working/cache voltam exatamente
    como estavam (inclusive projeto/pessoa novos que a leitura precisou criar).
    `confirmar_importacao` (fase 2) é quem efetiva."""
    from . import versao as _v
    import json as _json

    _v.garantir_bi(conn)
    cache0 = _v.snapshot_cache(conn)
    work0 = _v._estado_working(conn)
    pids_antes = {r["projeto_id"] for r in conn.execute("SELECT projeto_id FROM projeto")}
    mats_antes = {r["matricula"] for r in conn.execute("SELECT matricula FROM pessoa")}

    out, fp_mat, houve_custo = _carregar_arquivos(conn, paths)
    conn.execute("DELETE FROM bi_pendente")

    if not houve_custo:
        # colabs/projetos sem colab-mes-custo: nada pra relatar/commitar ainda.
        _v.escrever_working(conn, work0)
        _podar_estrutural_novo(conn, pids_antes, mats_antes)
        conn.commit()
        out.append({"arquivo": "(sem colab-mes-custo)", "status": "ok",
                    "detail": "campos no staging; importe junto com o proj-colab-custo p/ gerar o relatório"})
        return {"resultados": out, "pendente": False}

    fp_mat |= {r["matricula"] for r in conn.execute(
        "SELECT DISTINCT matricula FROM bi_custo WHERE matricula IS NOT NULL")}
    E_bi, resumo = _montar_e_bi(conn, fp_mat)
    # projeto/pessoa que a leitura precisou criar pra montar o E_bi (guardado pro caso
    # de descartar depois — só existem de verdade se a importação for confirmada)
    pids_novos = sorted({r["projeto_id"] for r in conn.execute("SELECT projeto_id FROM projeto")} - pids_antes)
    mats_novos = sorted({r["matricula"] for r in conn.execute("SELECT matricula FROM pessoa")} - mats_antes)

    # nada é aplicado ainda: working/cache voltam ao que eram antes da leitura
    _v._cache_set(conn, cache0, None)
    _v.escrever_working(conn, work0)

    if resumo["mudou"]:
        guardado = {**resumo, "_pids_novos": pids_novos, "_mats_novos": mats_novos}
        conn.execute(
            "INSERT INTO bi_pendente (id, mensagem, e_bi_json, resumo_json, criado_em) VALUES (1,?,?,?,?)",
            (f"Importação do Publicado em {_dt.date.today():%Y-%m-%d}", _json.dumps(_serializar_estado(E_bi)),
             _json.dumps(guardado), _dt.datetime.now().isoformat(timespec="seconds")),
        )
    else:
        # não vai ficar pendente -> desfaz qualquer projeto/pessoa estrutural criado agora
        _podar_estrutural_novo(conn, pids_antes, mats_antes)
    conn.commit()
    out.append({"arquivo": "(relatório BI)", "status": "ok", **resumo})
    return {"resultados": out, "pendente": resumo["mudou"], "resumo": resumo}


def confirmar_importacao(conn: sqlite3.Connection, autor: str = "") -> dict:
    """Fase 2: efetiva o `bi_pendente` de `preparar_importacao` — commit no branch `BI`
    (+ fast-forward da `main` se ainda estiver colada). `autor` = pessoa que disparou o
    upload (X-Autor da requisição) — a importação é sempre manual hoje, nunca `SISTEMA`."""
    from . import versao as _v
    import json as _json

    row = conn.execute("SELECT * FROM bi_pendente WHERE id=1").fetchone()
    if row is None:
        raise ValueError("nada pendente para confirmar — importe os arquivos primeiro")
    E_bi = _desserializar_estado(_json.loads(row["e_bi_json"]))

    hrow = conn.execute("SELECT ref_nome FROM head_ WHERE id=1").fetchone()
    head_ref = hrow["ref_nome"] if hrow else "main"
    cache0 = _v.snapshot_cache(conn)
    work0 = _v._estado_working(conn)
    d0 = _v._delta(cache0, work0)
    user_d = {
        "projeto": [], "pessoa": [],
        "aloc_add": d0["aloc_add"], "aloc_del": d0["aloc_del"], "mes": d0["mes"],
        "per_set": d0["per_set"], "per_del": d0["per_del"],
    }
    main_antes = _v.tip_commit(conn, "main")

    res = _v.commitar_estado_bi(conn, E_bi, row["mensagem"], autor=autor)
    main_ff = _v.tip_commit(conn, "main") != main_antes
    if head_ref == "main" and main_ff:
        _v._cache_set(conn, E_bi, None)
        _v.escrever_working(conn, _v.aplicar_delta(E_bi, user_d))
    else:
        _v._cache_set(conn, cache0, None)
        _v.escrever_working(conn, work0)
    conn.execute("DELETE FROM bi_pendente")
    conn.commit()
    return {"commit_bi": (res or {}).get("commit_id"), "fast_forward": (res or {}).get("fast_forward")}


def descartar_pendente(conn: sqlite3.Connection) -> None:
    """Cancela um `bi_pendente` sem commitar nada. Desfaz também o projeto/pessoa
    estrutural que a leitura teve que criar pra montar o relatório."""
    import json as _json

    row = conn.execute("SELECT resumo_json FROM bi_pendente WHERE id=1").fetchone()
    if row is not None:
        info = _json.loads(row["resumo_json"])
        for pid in info.get("_pids_novos") or ():
            conn.execute("DELETE FROM projeto WHERE projeto_id=?", (pid,))
        for mat in info.get("_mats_novos") or ():
            conn.execute("DELETE FROM pessoa WHERE matricula=?", (mat,))
    conn.execute("DELETE FROM bi_pendente")
    conn.commit()


def importar_arquivos(conn: sqlite3.Connection, paths: list[str], autor: str = "") -> list[dict]:
    """Atalho sem relatório/confirmação: prepara e já confirma na hora. Usado pelo
    endpoint "esperto" (`/api/importar-upload`) e pelo uso via CLI (CLI não tem X-Autor —
    fica sem autor, é esperado)."""
    res = preparar_importacao(conn, paths)
    if res["pendente"]:
        c = confirmar_importacao(conn, autor=autor)
        for r in res["resultados"]:
            if r.get("arquivo") == "(relatório BI)":
                r["arquivo"] = "(commit BI)"
                r.update(c)
    return res["resultados"]


if __name__ == "__main__":
    from . import db as dbmod

    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    conn = dbmod.init_db()
    for r in importar_arquivos(conn, sys.argv[1:]):
        print(r)

"""Parse a project .xlsx (4 sheets) into the internal DB.

No re-import: if a project with the same ``id_projeto_externo`` already exists the
file is skipped (the caller gets ``status='skipped'``).
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import zipfile
from pathlib import Path

from lxml import etree

from .xlsx_io import Sheet, Workbook, serial_to_date

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_TC_NS = "http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments"


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _q_pkg(tag: str) -> str:
    return _q(_PKG_REL, tag)


PROJECT_SHEETS = ("dados_projeto", "Alocacao", "Planilha4")

# Planilha4 catalog layout: (catalog name, text column, id column or None)
_CATALOG_COLS = {
    "equipe": (1, 2),
    "contrato": (4, 5),
    "area": (7, 8),
    "ativo": (11, None),
    "ensino": (13, None),
    "tipo_alocacao": (16, 17),
    "status": (19, 20),
}


def is_project_workbook(path: str | Path) -> bool:
    try:
        with Workbook(str(path)) as wb:
            return wb.has_sheets(*PROJECT_SHEETS)
    except (zipfile.BadZipFile, etree.XMLSyntaxError, KeyError):
        return False


def _norm(v: object) -> str:
    return str(v).strip().lower() if v is not None else ""


def _header_map(sheet: Sheet, header_row: int = 1) -> dict[str, int]:
    out: dict[str, int] = {}
    for col, val in enumerate(sheet.row_values(header_row), start=1):
        key = _norm(val)
        if key and key not in out:
            out[key] = col
    return out


def _matricula(v: object) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _to_int(v: object) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def _iso(v: object) -> str | None:
    """Best-effort text -> ISO date. Accepts Excel serials and dd/mm/yyyy text."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return serial_to_date(v).isoformat()
    s = str(v).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return _dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return s  # keep as-is; not critical data


class ImportResult:
    def __init__(self, status: str, projeto_id: int | None, nome: str, detail: str = ""):
        self.status = status  # 'imported' | 'skipped'
        self.projeto_id = projeto_id
        self.nome = nome
        self.detail = detail

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "projeto_id": self.projeto_id,
            "nome": self.nome,
            "detail": self.detail,
        }


def import_workbook(conn: sqlite3.Connection, path: str | Path) -> ImportResult:
    path = str(Path(path).resolve())
    with Workbook(path) as wb:
        if not wb.has_sheets(*PROJECT_SHEETS):
            return ImportResult("skipped", None, Path(path).name, "não é arquivo de projeto")

        dp = wb.sheet("dados_projeto")
        aloc = wb.sheet("Alocacao")
        plan = wb.sheet("Planilha4")
        novos = wb.sheet("Novos_Pesquisadores") if "Novos_Pesquisadores" in wb.sheet_names else None

        proj = _read_dados_projeto(dp)
        externo = proj["id_projeto_externo"]
        existente = None
        if externo:
            r = conn.execute(
                "SELECT projeto_id FROM projeto WHERE id_projeto_externo = ?", (str(externo),)
            ).fetchone()
            existente = r["projeto_id"] if r else None

        periodos = _read_month_headers(aloc)
        cur = conn.cursor()
        if existente is None:
            projeto_id = _insert_projeto(cur, proj, path, periodos)
            novo = True
        else:
            projeto_id = existente
            _update_projeto(cur, projeto_id, proj, path, periodos)
            novo = False

        _insert_catalogos(cur, plan)
        rows = _read_alocacoes(aloc, periodos)
        _insert_alocacoes(cur, projeto_id, rows)   # substitui o working desse projeto
        pn = _read_novos(novos) if novos is not None else []
        _seed_pessoas(cur, rows, pn)
        _upsert_pessoas_planilha(cur, pn)
        cur.execute("DELETE FROM anotacao WHERE projeto_id=? AND autor='importado'", (projeto_id,))
        _import_comments(cur, wb, projeto_id, aloc)
        conn.commit()
        if novo:
            # projeto que não veio do BI: o arquivo também vira a baseline inicial
            from . import baseline
            baseline.capturar(conn, projeto_id, "import")

    return ImportResult(
        "updated" if existente else "imported", projeto_id, proj["nome"],
        f"{len(rows)} alocações, {len(periodos)} meses"
        + ("" if novo else " (working atualizado; baseline mantida)"),
    )


# -- readers ---------------------------------------------------------------
def _read_dados_projeto(dp: Sheet) -> dict:
    h = _header_map(dp)

    def g(*names):
        for n in names:
            if n in h:
                return dp.cell(2, h[n])
        return None

    return {
        "id_projeto_externo": _matricula(g("id_projeto")),
        "nome": str(g("nomeprojeto") or "").strip() or "(sem nome)",
        "empresa": g("empresa"),
        "status": g("status"),
        "id_status": _to_int(g("idstatus")),
        "matricula_gp": _matricula(g("matricula_gp")),
        "id_filial": _to_int(g("id_filial")) or 62,
        "mes_inicio": _to_int(g("mês inicio da alocação", "mes inicio da alocação", "mês início da alocação")),
        "ano_inicio": _to_int(g("ano inicio da alocação", "ano início da alocação")),
        "cenario1": _to_int(g("cenario 1", "cenário 1")),
        "cenario2": _to_int(g("cenario 2", "cenário 2")),
        "cenario3": _to_int(g("cenario 3", "cenário 3")),
    }


def _read_month_headers(aloc: Sheet) -> list[str]:
    """Return ISO 'YYYY-MM-01' for every numeric serial in the header row,
    left to right, positionally ordered."""
    out: list[str] = []
    for col in range(5, aloc.max_col + 1):  # columns E..
        v = aloc.cell(1, col)
        if isinstance(v, (int, float)) and v > 0:
            d = serial_to_date(v).replace(day=1)
            out.append(d.isoformat())
    return out


def _read_alocacoes(aloc: Sheet, periodos: list[str]) -> list[dict]:
    h = _header_map(aloc)
    col_mat = h.get("matricula", 1)
    col_tipo = h.get("tipo alocacao", h.get("tipo alocacao ", 2))
    col_perfil = h.get("perfil", 4)
    first_month_col = 5
    rows: list[dict] = []
    for rownum, _ in aloc.iter_data_rows(2):
        matricula = _matricula(aloc.cell(rownum, col_mat))
        tipo = aloc.cell(rownum, col_tipo)
        tipo = str(tipo).strip() if tipo not in (None, "") else None
        if not matricula or not tipo:
            continue
        perfil = aloc.cell(rownum, col_perfil)
        meses: dict[str, int] = {}
        for i, periodo in enumerate(periodos):
            v = aloc.cell(rownum, first_month_col + i)
            iv = _to_int(v)
            if iv is not None:
                meses[periodo] = max(iv, 0)
        rows.append({
            "matricula": matricula,
            "tipo_alocacao": tipo,
            "perfil": str(perfil).strip() if perfil not in (None, "") else "",
            "meses": meses,
        })
    return rows


def _read_novos(novos: Sheet) -> list[dict]:
    h = _header_map(novos)

    def col(*names, default=None):
        for n in names:
            if n in h:
                return h[n]
        return default

    c = {
        "matricula": col("matricula", default=1),
        "nome": col("nome", default=2),
        "ativo": col("ativo", default=3),
        "equipe": col("equipe", default=4),
        "area": col("area", default=6),
        "contrato": col("contrato", default=8),
        "inicio_contrato": col("iníciocontrato", "iniciocontrato", default=10),
        "fim_contrato": col("fimcontrato", default=11),
        "formacao": col("formacao", "formação", default=12),
        "id_filial": col("id_filial", default=13),
        "carga_diaria": col("cargahoraria diária", "cargahoraria diaria", default=14),
        "remuneracao": col("remuneração", "remuneracao", default=15),
        "inicio_vigencia": col("iniciovigencia", "iníciovigência", default=16),
    }
    out: list[dict] = []
    for rownum, _ in novos.iter_data_rows(2):
        matricula = _matricula(novos.cell(rownum, c["matricula"]))
        if not matricula:
            continue
        out.append({
            "matricula": matricula,
            "nome": _s(novos.cell(rownum, c["nome"])),
            "ativo": _s(novos.cell(rownum, c["ativo"])),
            "equipe": _s(novos.cell(rownum, c["equipe"])),
            "area": _s(novos.cell(rownum, c["area"])),
            "contrato": _s(novos.cell(rownum, c["contrato"])),
            "inicio_contrato": _iso(novos.cell(rownum, c["inicio_contrato"])),
            "fim_contrato": _iso(novos.cell(rownum, c["fim_contrato"])),
            "formacao": _s(novos.cell(rownum, c["formacao"])),
            "id_filial": _to_int(novos.cell(rownum, c["id_filial"])),
            "carga_diaria": _to_float(novos.cell(rownum, c["carga_diaria"])),
            "remuneracao": _to_float(novos.cell(rownum, c["remuneracao"])),
            "inicio_vigencia": _iso(novos.cell(rownum, c["inicio_vigencia"])),
        })
    return out


def _s(v: object) -> str | None:
    return str(v).strip() if v not in (None, "") else None


def _to_float(v: object) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


# -- writers -------------------------------------------------------------
def _insert_projeto(cur, proj: dict, path: str, periodos: list[str]) -> int:
    cur.execute(
        """INSERT INTO projeto
           (id_projeto_externo, nome, empresa, status, id_status, matricula_gp,
            id_filial, mes_inicio, ano_inicio, cenario1, cenario2, cenario3,
            arquivo_origem, criado_na_ferramenta)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
        (
            proj["id_projeto_externo"], proj["nome"], proj["empresa"], proj["status"],
            proj["id_status"], proj["matricula_gp"], proj["id_filial"],
            proj["mes_inicio"] or (int(periodos[0][5:7]) if periodos else 1),
            proj["ano_inicio"] or (int(periodos[0][0:4]) if periodos else 2025),
            proj["cenario1"], proj["cenario2"], proj["cenario3"],
            path,
        ),
    )
    projeto_id = cur.lastrowid
    cur.executemany(
        "INSERT INTO projeto_periodo (projeto_id, periodo, ordem) VALUES (?,?,?)",
        [(projeto_id, p, i) for i, p in enumerate(periodos)],
    )
    return projeto_id


def _update_projeto(cur, projeto_id: int, proj: dict, path: str, periodos: list[str]) -> None:
    """Projeto já existe (veio do BI ou de um import anterior): atualiza os campos e a
    janela de meses com o arquivo; NÃO mexe na baseline."""
    cur.execute(
        """UPDATE projeto SET
             nome=?, empresa=?, status=?, id_status=?, matricula_gp=?, id_filial=?,
             mes_inicio=?, ano_inicio=?, cenario1=?, cenario2=?, cenario3=?, arquivo_origem=?
           WHERE projeto_id=?""",
        (
            proj["nome"], proj["empresa"], proj["status"], proj["id_status"],
            proj["matricula_gp"], proj["id_filial"],
            proj["mes_inicio"] or (int(periodos[0][5:7]) if periodos else 1),
            proj["ano_inicio"] or (int(periodos[0][0:4]) if periodos else 2025),
            proj["cenario1"], proj["cenario2"], proj["cenario3"], path, projeto_id,
        ),
    )
    if periodos:
        cur.execute("DELETE FROM projeto_periodo WHERE projeto_id=?", (projeto_id,))
        cur.executemany(
            "INSERT INTO projeto_periodo (projeto_id, periodo, ordem) VALUES (?,?,?)",
            [(projeto_id, p, i) for i, p in enumerate(periodos)],
        )


def _insert_catalogos(cur, plan: Sheet) -> None:
    for name, (text_col, id_col) in _CATALOG_COLS.items():
        for rownum, _ in plan.iter_data_rows(2):
            text = plan.cell(rownum, text_col)
            if text in (None, ""):
                continue
            cid = _to_int(plan.cell(rownum, id_col)) if id_col else None
            cur.execute(
                "INSERT OR IGNORE INTO catalogo (tipo, id, texto) VALUES (?,?,?)",
                (name, cid, str(text).strip()),
            )


def _insert_alocacoes(cur, projeto_id: int, rows: list[dict]) -> None:
    # o arquivo é o estado completo do working desse projeto: zera e recria.
    # Não gravamos horas 0 (ausência de mês == 0). Linha inteiramente zerada
    # não vira registro — não polui as grades nem a baseline no próximo commit.
    cur.execute("DELETE FROM alocacao WHERE projeto_id=?", (projeto_id,))  # cascade -> alocacao_mes
    for r in rows:
        meses = {p: h for p, h in r["meses"].items() if h and h > 0}
        if not meses:
            continue
        cur.execute(
            """INSERT INTO alocacao (projeto_id, matricula, tipo_alocacao)
               VALUES (?,?,?)
               ON CONFLICT(projeto_id, matricula, tipo_alocacao) DO NOTHING""",
            (projeto_id, r["matricula"], r["tipo_alocacao"]),
        )
        aloc_id = cur.execute(
            "SELECT alocacao_id FROM alocacao WHERE projeto_id=? AND matricula=? AND tipo_alocacao=?",
            (projeto_id, r["matricula"], r["tipo_alocacao"]),
        ).fetchone()["alocacao_id"]
        cur.executemany(
            "INSERT OR REPLACE INTO alocacao_mes (alocacao_id, periodo, horas) VALUES (?,?,?)",
            [(aloc_id, p, h) for p, h in meses.items()],
        )


_NOVOS_TO_PESSOA = {
    "nome": "nome", "ativo": "situacao", "equipe": "equipe", "area": "area",
    "contrato": "tipo_contrato", "inicio_contrato": "inicio_contrato",
    "fim_contrato": "fim_contrato", "formacao": "formacao", "id_filial": "id_filial",
    "carga_diaria": "carga_diaria", "remuneracao": "remuneracao",
    "inicio_vigencia": "inicio_vigencia",
}


def _upsert_pessoas_planilha(cur, pn: list[dict]) -> None:
    """Linhas da aba Novos_Pesquisadores -> tabela única `pessoa` (não há pessoa_nova).
    A planilha é a fonte na importação; não sobrescreve valores já preenchidos."""
    for p in pn:
        cur.execute(
            "INSERT OR IGNORE INTO pessoa (matricula, nome) VALUES (?,?)",
            (p["matricula"], p.get("nome") or p["matricula"]),
        )
        sets, args = [], []
        for src, dst in _NOVOS_TO_PESSOA.items():
            v = p.get(src)
            if v not in (None, ""):
                sets.append(f"{dst}=COALESCE({dst}, ?)" if dst != "nome" else "nome=?")
                args.append(v)
        if p.get("carga_diaria"):
            sets.append("capacidade_mensal=COALESCE(capacidade_mensal, ?)")
            args.append(int(round(float(p["carga_diaria"]) * 22)))
        if sets:
            args.append(p["matricula"])
            cur.execute(f"UPDATE pessoa SET {', '.join(sets)} WHERE matricula=?", args)


def _seed_pessoas(cur, rows: list[dict], pn: list[dict]) -> None:
    carga_por_matricula = {p["matricula"]: p["carga_diaria"] for p in pn if p["carga_diaria"]}
    nome_novos = {p["matricula"]: p["nome"] for p in pn if p["nome"]}
    for r in rows:
        nome = r["perfil"] or nome_novos.get(r["matricula"]) or r["matricula"]
        cur.execute(
            "INSERT OR IGNORE INTO pessoa (matricula, nome) VALUES (?,?)",
            (r["matricula"], nome),
        )
        cur.execute(
            "UPDATE pessoa SET nome=? WHERE matricula=? AND (nome IS NULL OR nome='' OR nome=matricula)",
            (nome, r["matricula"]),
        )
    for matricula, carga in carga_por_matricula.items():
        cur.execute(
            "INSERT OR IGNORE INTO pessoa (matricula, nome) VALUES (?,?)",
            (matricula, nome_novos.get(matricula, matricula)),
        )
        cur.execute(
            """UPDATE pessoa SET carga_diaria=?, capacidade_mensal=COALESCE(capacidade_mensal, ?)
               WHERE matricula=? AND carga_diaria IS NULL""",
            (carga, int(round(carga * 22)), matricula),
        )


def _import_comments(cur, wb: Workbook, projeto_id: int, aloc: Sheet) -> None:
    """Planning notes from the Alocacao sheet -> anotacao rows.

    Only the Alocacao sheet's comments are planning notes; comments on
    dados_projeto etc. are data-entry instructions and are ignored. Threaded
    comments (clean text) are preferred over the legacy mirror.
    """
    from .xlsx_io import split_ref

    aloc_target = wb._sheet_targets.get("Alocacao", "")
    if not aloc_target:
        return
    base = aloc_target.rsplit("/", 1)[-1]
    rels_part = aloc_target.rsplit("/", 1)[0] + f"/_rels/{base}.rels"
    try:
        rels = etree.fromstring(wb._zip.read(rels_part))
    except (KeyError, etree.XMLSyntaxError):
        return

    legacy_parts, threaded_parts = [], []
    for r in rels.findall(_q_pkg("Relationship")):
        rtype, target = r.get("Type", ""), r.get("Target", "")
        part = "xl/" + target.replace("../", "")
        if rtype.endswith("/comments"):
            legacy_parts.append(part)
        elif "threadedcomment" in rtype.lower():
            threaded_parts.append(part)

    now = _dt.datetime.now().isoformat(timespec="seconds")
    seen: set[tuple[str | None, str]] = set()

    def add(ref: str | None, text: str) -> None:
        text = text.strip()
        if not text:
            return
        matricula = None
        if ref:
            try:
                rr, _ = split_ref(ref)
                matricula = _matricula(aloc.cell(rr, 1))
            except ValueError:
                pass
        key = (matricula, text)
        if key in seen:
            return
        seen.add(key)
        cur.execute(
            "INSERT INTO anotacao (projeto_id, matricula, texto, autor, criado_em) VALUES (?,?,?,?,?)",
            (projeto_id, matricula, text[:2000], "importado", now),
        )

    used_threaded = False
    for part in threaded_parts:
        try:
            root = etree.fromstring(wb._zip.read(part))
        except (KeyError, etree.XMLSyntaxError):
            continue
        for el in root.iter():
            if etree.QName(el).localname != "threadedComment":
                continue
            used_threaded = True
            t = el.find(_q(_TC_NS, "text"))
            add(el.get("ref"), t.text if t is not None and t.text else "")

    if not used_threaded:
        for part in legacy_parts:
            try:
                root = etree.fromstring(wb._zip.read(part))
            except (KeyError, etree.XMLSyntaxError):
                continue
            for el in root.iter(_q(_MAIN, "comment")):
                raw = "".join(el.itertext())
                # strip the "[Comentário encadeado] ... Comentário:" boilerplate
                if "Comentário:" in raw:
                    raw = raw.split("Comentário:", 1)[1]
                add(el.get("ref"), raw)

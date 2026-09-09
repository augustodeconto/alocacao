"""Surgical export: DB -> .xlsx.

Start from the project's origin file (or the derived template), rewrite only the
``Alocacao`` and ``Novos_Pesquisadores`` sheet data, drop calcChain and every
comment part, and leave everything else (Planilha4, dados_projeto, styles,
dropdowns) byte-for-byte.
"""
from __future__ import annotations

import datetime as _dt
import shutil
import sqlite3
import zipfile
from pathlib import Path

from lxml import etree

from .xlsx_io import date_to_serial, index_to_col

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_CT = "http://schemas.openxmlformats.org/package/2006/content-types"


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _local(el) -> str:
    return etree.QName(el).localname


_DROP_PREFIXES = (
    "xl/comments",
    "xl/threadedComments/",
    "xl/drawings/vmlDrawing",
    "xl/persons/",
    "xl/calcChain.xml",
)


def _should_drop(part: str) -> bool:
    return any(part.startswith(p) or part == p for p in _DROP_PREFIXES)


class _Package:
    def __init__(self, path: str):
        with zipfile.ZipFile(path) as z:
            self.parts: dict[str, bytes] = {i.filename: z.read(i.filename) for i in z.infolist()}

    def tree(self, part: str):
        return etree.fromstring(self.parts[part])

    def set_tree(self, part: str, root) -> None:
        self.parts[part] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    def sheet_part(self, sheet_name: str) -> str:
        wb = self.tree("xl/workbook.xml")
        rels = self.tree("xl/_rels/workbook.xml.rels")
        rid_target = {r.get("Id"): r.get("Target") for r in rels.findall(_q(_PKG_REL, "Relationship"))}
        for sh in wb.find(_q(_MAIN, "sheets")).findall(_q(_MAIN, "sheet")):
            if sh.get("name") == sheet_name:
                t = rid_target[sh.get(f"{{{_PKG_REL.replace('/package/', '/officeDocument/')}}}id")]
                return t if t.startswith("xl/") else "xl/" + t.lstrip("/")
        raise KeyError(sheet_name)

    def write(self, out_path: str) -> None:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in self.parts.items():
                z.writestr(name, data)


# The relationship namespace used on <sheet r:id> is the officeDocument one.
_REL_OD = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _sheet_part(pkg: _Package, sheet_name: str) -> str:
    wb = pkg.tree("xl/workbook.xml")
    rels = pkg.tree("xl/_rels/workbook.xml.rels")
    rid_target = {r.get("Id"): r.get("Target") for r in rels.findall(_q(_PKG_REL, "Relationship"))}
    for sh in wb.find(_q(_MAIN, "sheets")).findall(_q(_MAIN, "sheet")):
        rid = sh.get(_q(_REL_OD, "id"))
        t = rid_target[rid]
        if sh.get("name") == sheet_name:
            return t if t.startswith("xl/") else "xl/" + t.lstrip("/")
    raise KeyError(sheet_name)


def _new_cell(parent, col: int, row: int, *, value=None, text=None, formula=None, style=None):
    c = etree.SubElement(parent, _q(_MAIN, "c"))
    c.set("r", f"{index_to_col(col)}{row}")
    if style is not None:
        c.set("s", str(style))
    if text is not None:
        c.set("t", "inlineStr")
        is_el = etree.SubElement(c, _q(_MAIN, "is"))
        t_el = etree.SubElement(is_el, _q(_MAIN, "t"))
        t_el.text = str(text)
        t_el.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    elif formula is not None:
        f_el = etree.SubElement(c, _q(_MAIN, "f"))
        f_el.text = formula
    elif value is not None:
        v_el = etree.SubElement(c, _q(_MAIN, "v"))
        v_el.text = str(value)
    return c


def _rewrite_alocacao(pkg: _Package, part: str, periodos: list[str], linhas: list[dict]) -> None:
    root = pkg.tree(part)
    data = root.find(_q(_MAIN, "sheetData"))

    # style ids to reuse: month header (date), from the old E1 cell
    header_style = None
    first = data.find(_q(_MAIN, "row"))
    if first is not None:
        for c in first.findall(_q(_MAIN, "c")):
            if c.get("r", "").rstrip("0123456789") == "E":
                header_style = c.get("s")

    # keep the A1..D1 header cells, replace the rest
    keep_header: list = []
    if first is not None:
        for c in first.findall(_q(_MAIN, "c")):
            col_letters = c.get("r", "").rstrip("0123456789")
            if col_letters in ("A", "B", "C", "D"):
                keep_header.append(c)

    for row in list(data):
        data.remove(row)

    hrow = etree.SubElement(data, _q(_MAIN, "row"))
    hrow.set("r", "1")
    for c in keep_header:
        hrow.append(c)
    for i, per in enumerate(periodos):
        d = _dt.date.fromisoformat(per)
        _new_cell(hrow, 5 + i, 1, value=date_to_serial(d), style=header_style)

    rownum = 1
    for ln in linhas:
        rownum += 1
        r = etree.SubElement(data, _q(_MAIN, "row"))
        r.set("r", str(rownum))
        _new_cell(r, 1, rownum, value=ln["matricula"])
        _new_cell(r, 2, rownum, text=ln["tipo_alocacao"])
        _new_cell(
            r, 3, rownum,
            formula=f"IF(ISBLANK(B{rownum}),\"\",VLOOKUP(B{rownum},Planilha4!$P$2:$Q$25,2,FALSE))",
        )
        _new_cell(r, 4, rownum, text=ln["nome"])
        for i, per in enumerate(periodos):
            _new_cell(r, 5 + i, rownum, value=int(ln["horas"].get(per, 0)))

    last_col = index_to_col(4 + len(periodos))
    dim = root.find(_q(_MAIN, "dimension"))
    if dim is not None:
        dim.set("ref", f"A1:{last_col}{rownum}")
    pkg.set_tree(part, root)


def _rewrite_dados_projeto(pkg: _Package, part: str, pr, mes_inicio: int, ano_inicio: int) -> None:
    """Row 2 of dados_projeto (header row 1 kept as-is).

    `Mês/Ano Inicio da alocação` são reescritos com o mês/ano da **primeira coluna**
    da aba Alocacao — o importador usa esses campos como referência, não lê o
    cabeçalho das colunas.
    """
    root = pkg.tree(part)
    data = root.find(_q(_MAIN, "sheetData"))
    first = data.find(_q(_MAIN, "row"))
    header = list(first.findall(_q(_MAIN, "c"))) if first is not None else []
    for row in list(data):
        data.remove(row)
    hrow = etree.SubElement(data, _q(_MAIN, "row"))
    hrow.set("r", "1")
    for c in header:
        hrow.append(c)

    r2 = etree.SubElement(data, _q(_MAIN, "row"))
    r2.set("r", "2")
    vals = [
        pr["id_projeto_externo"], pr["nome"], pr["empresa"], pr["status"], pr["id_status"],
        pr["matricula_gp"], pr["id_filial"], mes_inicio, ano_inicio,
        pr["cenario1"], pr["cenario2"], pr["cenario3"],
    ]
    for i, v in enumerate(vals, start=1):
        if v is None or v == "":
            continue
        if isinstance(v, (int, float)) or (isinstance(v, str) and v.isdigit()):
            _new_cell(r2, i, 2, value=v)
        else:
            _new_cell(r2, i, 2, text=str(v))
    dim = root.find(_q(_MAIN, "dimension"))
    if dim is not None:
        dim.set("ref", f"A1:{index_to_col(len(vals))}2")
    pkg.set_tree(part, root)


def _rewrite_novos(pkg: _Package, part: str, pessoas: list[dict]) -> None:
    # sempre reescreve: sem pessoas alteradas, a aba fica só com o cabeçalho
    root = pkg.tree(part)
    data = root.find(_q(_MAIN, "sheetData"))
    first = data.find(_q(_MAIN, "row"))
    header_cells = list(first.findall(_q(_MAIN, "c"))) if first is not None else []

    for row in list(data):
        data.remove(row)
    hrow = etree.SubElement(data, _q(_MAIN, "row"))
    hrow.set("r", "1")
    for c in header_cells:
        hrow.append(c)

    # fixed column layout (matches the sample template)
    rownum = 1
    for p in pessoas:
        rownum += 1
        r = etree.SubElement(data, _q(_MAIN, "row"))
        r.set("r", str(rownum))
        _new_cell(r, 1, rownum, value=p["matricula"])
        _new_cell(r, 2, rownum, text=p.get("nome") or "")
        _new_cell(r, 4, rownum, text=p.get("equipe") or "")
        _new_cell(r, 5, rownum, formula=f"IF(ISBLANK(D{rownum}),\"\",VLOOKUP(D{rownum},Planilha4!$A$2:$B$11,2,FALSE))")
        _new_cell(r, 6, rownum, text=p.get("area") or "")
        _new_cell(r, 7, rownum, formula=f"IF(ISBLANK(F{rownum}),\"\",VLOOKUP(F{rownum},Planilha4!$G$2:$H$17,2,FALSE))")
        _new_cell(r, 3, rownum, text=p.get("situacao") or "")
        _new_cell(r, 8, rownum, text=p.get("tipo_contrato") or p.get("contrato") or "")
        _new_cell(r, 9, rownum, formula=f"IF(ISBLANK(H{rownum}),\"\",VLOOKUP(H{rownum},Planilha4!$D$2:$E$30,2,FALSE))")
        if p.get("inicio_contrato"):
            _new_cell(r, 10, rownum, text=p["inicio_contrato"])
        if p.get("fim_contrato"):
            _new_cell(r, 11, rownum, text=p["fim_contrato"])
        if p.get("inicio_vigencia"):
            _new_cell(r, 16, rownum, text=p["inicio_vigencia"])
        if p.get("formacao"):
            _new_cell(r, 12, rownum, text=p["formacao"])
        if p.get("id_filial") is not None:
            _new_cell(r, 13, rownum, value=int(p["id_filial"]))
        if p.get("carga_diaria") is not None:
            _new_cell(r, 14, rownum, value=p["carga_diaria"])
        if p.get("remuneracao") is not None:
            _new_cell(r, 15, rownum, value=p["remuneracao"])
    pkg.set_tree(part, root)


def _drop_comment_parts(pkg: _Package) -> None:
    dropped = {name for name in pkg.parts if _should_drop(name)}
    for name in dropped:
        del pkg.parts[name]

    # relationships pointing at dropped parts
    for name in list(pkg.parts):
        if not name.endswith(".rels"):
            continue
        root = etree.fromstring(pkg.parts[name])
        changed = False
        for rel in list(root.findall(_q(_PKG_REL, "Relationship"))):
            target = rel.get("Target", "").replace("../", "")
            rtype = rel.get("Type", "").lower()
            if (
                "xl/" + target in dropped
                or target in {d.split("/")[-1] for d in dropped}
                or "comment" in rtype
                or "vmldrawing" in rtype
                or "calcchain" in rtype
                or rtype.endswith("/person")
            ):
                root.remove(rel)
                changed = True
        if changed:
            pkg.parts[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)

    # [Content_Types].xml: drop overrides for dropped parts + the vml default
    ct = etree.fromstring(pkg.parts["[Content_Types].xml"])
    for ov in list(ct.findall(_q(_CT, "Override"))):
        pn = ov.get("PartName", "").lstrip("/")
        if pn in dropped or "comments" in pn or "threadedComment" in pn or pn == "xl/calcChain.xml":
            ct.remove(ov)
    for de in list(ct.findall(_q(_CT, "Default"))):
        if de.get("Extension", "").lower() == "vml":
            ct.remove(de)
    pkg.parts["[Content_Types].xml"] = etree.tostring(ct, xml_declaration=True, encoding="UTF-8", standalone=True)

    # sheet XML: remove <legacyDrawing> (VML comment anchor) + <x:...> refs
    for name in list(pkg.parts):
        if not (name.startswith("xl/worksheets/sheet") and name.endswith(".xml")):
            continue
        root = etree.fromstring(pkg.parts[name])
        changed = False
        for el in list(root):
            if _local(el) in ("legacyDrawing",):
                root.remove(el)
                changed = True
        if changed:
            pkg.parts[name] = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _force_recalc(pkg: _Package) -> None:
    root = pkg.tree("xl/workbook.xml")
    calc = root.find(_q(_MAIN, "calcPr"))
    if calc is None:
        calc = etree.SubElement(root, _q(_MAIN, "calcPr"))
    calc.set("fullCalcOnLoad", "1")
    calc.attrib.pop("calcId", None)
    pkg.set_tree("xl/workbook.xml", root)


def exportar_projeto(
    conn: sqlite3.Connection, projeto_id: int, destino_pasta: str, template_path: str,
    inicio: str | None = None,
) -> str:
    pr = conn.execute("SELECT * FROM projeto WHERE projeto_id=?", (projeto_id,)).fetchone()
    if pr is None:
        raise KeyError(projeto_id)
    base = pr["arquivo_origem"] if pr["arquivo_origem"] and Path(pr["arquivo_origem"]).exists() else template_path
    if not base or not Path(base).exists():
        raise FileNotFoundError("sem arquivo de origem nem template")

    # colunas de mês do arquivo gerado:
    #   início = mês da geração por padrão; pode ser antecipado via `inicio`
    #            ('YYYY-MM' ou 'YYYY-MM-DD'). O importador usa dados_projeto.Mês/Ano
    #            Inicio como referência; meses anteriores ao início não vão pro arquivo.
    #   fim    = último mês com horas lançadas (>= início)
    #   sequência mensal contígua entre os dois (o importador assume colunas contíguas)
    if inicio:
        _s = inicio if len(inicio) > 7 else inicio + "-01"
        inicio = _dt.date.fromisoformat(_s).replace(day=1)
    else:
        inicio = _dt.date.today().replace(day=1)
    ultimo_dado = conn.execute(
        """SELECT MAX(p) FROM (
               SELECT m.periodo AS p FROM alocacao_mes m JOIN alocacao a USING(alocacao_id)
               WHERE a.projeto_id=?
               UNION ALL
               SELECT periodo FROM baseline_alocacao_mes WHERE projeto_id=?
           )""",
        (projeto_id, projeto_id),
    ).fetchone()[0]
    fim = inicio
    if ultimo_dado:
        d = _dt.date.fromisoformat(ultimo_dado)
        if d > fim:
            fim = d
    periodos = []
    d = inicio
    while d <= fim:
        periodos.append(d.isoformat())
        d = (d.replace(day=28) + _dt.timedelta(days=4)).replace(day=1)
    nomes = {r["matricula"]: r["nome"] for r in conn.execute("SELECT matricula, nome FROM pessoa")}
    alocs = conn.execute(
        "SELECT alocacao_id, matricula, tipo_alocacao FROM alocacao WHERE projeto_id=?", (projeto_id,)
    ).fetchall()
    horas: dict[int, dict[str, int]] = {}
    for r in conn.execute(
        """SELECT m.alocacao_id AS aid, m.periodo AS periodo, m.horas AS horas
           FROM alocacao_mes m JOIN alocacao a USING (alocacao_id) WHERE a.projeto_id=?""",
        (projeto_id,),
    ):
        horas.setdefault(r["aid"], {})[r["periodo"]] = r["horas"]

    linhas = [
        {
            "matricula": a["matricula"],
            "tipo_alocacao": a["tipo_alocacao"],
            "nome": nomes.get(a["matricula"], a["matricula"]),
            "horas": horas.get(a["alocacao_id"], {}),
        }
        for a in alocs
    ]
    # alocações que estavam na baseline e não estão mais no working: saem ZERADAS
    # (sinaliza "remover" para o importador). Inclui o caso em que o usuário zerou
    # todos os meses da linha — a alocação some do working e reaparece aqui zerada.
    # Toda alocação presente no working é exportada, inclusive as novas.
    from . import baseline
    for rem in baseline.removidas(conn, projeto_id):
        linhas.append({
            "matricula": rem["matricula"],
            "tipo_alocacao": rem["tipo_alocacao"],
            "nome": nomes.get(rem["matricula"], rem["matricula"]),
            "horas": {},  # tudo 0
        })
    linhas.sort(key=lambda l: (l["nome"].lower(), l["tipo_alocacao"].lower()))

    # aba Novos_Pesquisadores = pessoas do projeto novas/alteradas vs. baseline
    pessoas_novas = baseline.pessoas_alteradas(conn, projeto_id)

    p0 = _dt.date.fromisoformat(periodos[0])
    pkg = _Package(base)
    _rewrite_dados_projeto(pkg, _sheet_part(pkg, "dados_projeto"), pr, p0.month, p0.year)
    _rewrite_alocacao(pkg, _sheet_part(pkg, "Alocacao"), periodos, linhas)
    if "Novos_Pesquisadores" in _all_sheet_names(pkg):
        _rewrite_novos(pkg, _sheet_part(pkg, "Novos_Pesquisadores"), pessoas_novas)
    _drop_comment_parts(pkg)
    _force_recalc(pkg)

    Path(destino_pasta).mkdir(parents=True, exist_ok=True)
    out_path = str(Path(destino_pasta) / nome_arquivo_projeto(pr["nome"]))
    if Path(out_path).exists():
        shutil.copy2(out_path, out_path + ".bak")
    pkg.write(out_path)

    conn.execute(
        "UPDATE projeto SET exportado_em=? WHERE projeto_id=?",
        (_dt.datetime.now().isoformat(timespec="seconds"), projeto_id),
    )
    conn.commit()
    # export = commit: a baseline avança para o estado atual
    baseline.capturar(conn, projeto_id, "export")
    return out_path


def _all_sheet_names(pkg: _Package) -> list[str]:
    wb = pkg.tree("xl/workbook.xml")
    return [sh.get("name") for sh in wb.find(_q(_MAIN, "sheets")).findall(_q(_MAIN, "sheet"))]


def _slug(name: str) -> str:
    """Nome de projeto seguro para arquivo, preservando espaços (padrão dos arquivos
    atuais, ex.: '20260615 Catarina A2.xlsx')."""
    keep = "".join("" if ch in '\\/:*?"<>|' else ch for ch in name)
    return " ".join(keep.split()) or "Projeto"


def nome_arquivo_projeto(nome: str, data: _dt.date | None = None) -> str:
    return f"{data or _dt.date.today():%Y%m%d} {_slug(nome)}.xlsx"

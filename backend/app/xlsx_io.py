"""Minimal read side of the .xlsx format.

Only what the importer needs: open the zip, resolve sheet names, and read cell
values with shared strings and cached formula results. No styling, no dates as
objects -- callers convert serials themselves via ``serial_to_date``.
"""
from __future__ import annotations

import datetime as _dt
import re
import zipfile
from dataclasses import dataclass

from lxml import etree

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _q(ns: str, tag: str) -> str:
    return f"{{{ns}}}{tag}"


def _resolve_part(base_dir: str, target: str) -> str:
    """Resolve an OPC relationship Target against the .rels file's base dir.
    Handles absolute ('/xl/..'), relative ('worksheets/..') and '../' targets."""
    if target.startswith("/"):
        return target.lstrip("/")
    parts = (base_dir.rstrip("/").split("/") if base_dir.strip("/") else []) + target.split("/")
    stack: list[str] = []
    for p in parts:
        if p in ("", "."):
            continue
        if p == "..":
            if stack:
                stack.pop()
        else:
            stack.append(p)
    return "/".join(stack)


_EPOCH = _dt.date(1899, 12, 30)  # Excel's day 0 (with the 1900 leap-year bug baked in)


def serial_to_date(serial: float) -> _dt.date:
    return _EPOCH + _dt.timedelta(days=int(round(serial)))


def date_to_serial(d: _dt.date) -> int:
    return (d - _EPOCH).days


_CELL_RE = re.compile(r"^([A-Z]+)(\d+)$")


def col_to_index(col: str) -> int:
    """``A`` -> 1, ``Z`` -> 26, ``AA`` -> 27."""
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n


def index_to_col(index: int) -> str:
    out = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        out = chr(65 + rem) + out
    return out


def split_ref(ref: str) -> tuple[int, int]:
    """``B7`` -> (row=7, col=2)."""
    m = _CELL_RE.match(ref)
    if not m:
        raise ValueError(f"bad cell ref {ref!r}")
    return int(m.group(2)), col_to_index(m.group(1))


@dataclass
class Cell:
    value: object  # str | float | int | None
    formula: str | None = None


class Sheet:
    def __init__(self, name: str, rows: dict[int, dict[int, Cell]], max_col: int):
        self.name = name
        self.rows = rows
        self.max_col = max_col

    @property
    def max_row(self) -> int:
        return max(self.rows, default=0)

    def cell(self, row: int, col: int) -> object:
        r = self.rows.get(row)
        if not r:
            return None
        c = r.get(col)
        return c.value if c else None

    def row_values(self, row: int) -> list[object]:
        r = self.rows.get(row, {})
        return [r[c].value if c in r else None for c in range(1, self.max_col + 1)]

    def iter_data_rows(self, start_row: int):
        for rownum in sorted(self.rows):
            if rownum < start_row:
                continue
            yield rownum, self.rows[rownum]


class Workbook:
    def __init__(self, path: str):
        self.path = path
        self._zip = zipfile.ZipFile(path)
        self._shared = self._read_shared_strings()
        self._sheet_targets = self._read_sheet_index()

    def close(self) -> None:
        self._zip.close()

    def __enter__(self) -> "Workbook":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- index ---------------------------------------------------------------
    def _read_shared_strings(self) -> list[str]:
        try:
            xml = self._zip.read("xl/sharedStrings.xml")
        except KeyError:
            return []
        root = etree.fromstring(xml)
        out: list[str] = []
        for si in root.findall(_q(_MAIN, "si")):
            out.append("".join(t.text or "" for t in si.iter(_q(_MAIN, "t"))))
        return out

    def _read_sheet_index(self) -> dict[str, str]:
        wb = etree.fromstring(self._zip.read("xl/workbook.xml"))
        rels = etree.fromstring(self._zip.read("xl/_rels/workbook.xml.rels"))
        rid_to_target = {
            r.get("Id"): r.get("Target")
            for r in rels.findall(_q(_PKG_REL, "Relationship"))
        }
        out: dict[str, str] = {}
        for sh in wb.find(_q(_MAIN, "sheets")).findall(_q(_MAIN, "sheet")):
            rid = sh.get(_q(_REL, "id"))
            out[sh.get("name")] = _resolve_part("xl/", rid_to_target[rid])
        return out

    @property
    def sheet_names(self) -> list[str]:
        return list(self._sheet_targets)

    def has_sheets(self, *names: str) -> bool:
        return all(n in self._sheet_targets for n in names)

    # -- cells -------------------------------------------------------------
    def sheet(self, name: str) -> Sheet:
        target = self._sheet_targets[name]
        root = etree.fromstring(self._zip.read(target))
        data = root.find(_q(_MAIN, "sheetData"))
        rows: dict[int, dict[int, Cell]] = {}
        max_col = 0
        auto_row = 0
        for row_el in data.findall(_q(_MAIN, "row")):
            auto_row += 1
            rownum = int(row_el.get("r") or auto_row)
            cells: dict[int, Cell] = {}
            col_cursor = 0
            for c in row_el.findall(_q(_MAIN, "c")):
                ref = c.get("r")
                if ref:
                    _, colnum = split_ref(ref)
                else:
                    colnum = col_cursor + 1  # cells may omit r=, rely on order
                col_cursor = colnum
                max_col = max(max_col, colnum)
                cells[colnum] = self._parse_cell(c)
            if cells:
                rows[rownum] = cells
        return Sheet(name, rows, max_col)

    def _parse_cell(self, c) -> Cell:
        ctype = c.get("t")
        f_el = c.find(_q(_MAIN, "f"))
        v_el = c.find(_q(_MAIN, "v"))
        formula = f_el.text if f_el is not None else None

        if ctype == "inlineStr":
            is_el = c.find(_q(_MAIN, "is"))
            text = "".join(t.text or "" for t in is_el.iter(_q(_MAIN, "t"))) if is_el is not None else ""
            return Cell(text, formula)

        if v_el is None or v_el.text is None:
            return Cell(None, formula)
        raw = v_el.text

        if ctype == "s":
            return Cell(self._shared[int(raw)], formula)
        if ctype == "str":  # cached string result of a formula
            return Cell(raw, formula)
        if ctype == "b":
            return Cell(raw not in ("0", "false"), formula)
        # default: numeric (may be a cached formula result)
        try:
            num = float(raw)
        except ValueError:
            return Cell(raw, formula)
        if num.is_integer():
            num = int(num)
        return Cell(num, formula)

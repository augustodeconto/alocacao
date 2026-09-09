"""Derive a blank project template from the sample workbook (first run only)."""
from __future__ import annotations

from pathlib import Path

from lxml import etree

from .xlsx_export import _Package, _q, _MAIN, _sheet_part


def _clear_data_rows(pkg: _Package, sheet_name: str, keep_row1: bool = True) -> None:
    part = _sheet_part(pkg, sheet_name)
    root = pkg.tree(part)
    data = root.find(_q(_MAIN, "sheetData"))
    for row in list(data):
        if keep_row1 and row.get("r") == "1":
            continue
        data.remove(row)
    pkg.set_tree(part, root)


def ensure_template(sample_path: str | Path, template_path: str | Path) -> Path:
    template_path = Path(template_path)
    if template_path.exists():
        return template_path
    sample_path = Path(sample_path)
    if not sample_path.exists():
        raise FileNotFoundError(f"amostra não encontrada: {sample_path}")

    pkg = _Package(str(sample_path))
    for sheet in ("dados_projeto", "Alocacao", "Novos_Pesquisadores"):
        try:
            _clear_data_rows(pkg, sheet)
        except KeyError:
            pass
    template_path.parent.mkdir(parents=True, exist_ok=True)
    pkg.write(str(template_path))
    return template_path

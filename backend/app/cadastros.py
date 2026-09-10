"""CRUD leve das tabelas de cadastro (`projeto`, `pessoa`) + leitura do `catalogo`.

Isolado do resto da API de propósito: expõe um ``APIRouter`` que o ``main`` inclui
com duas linhas. Reaproveita a conexão SQLite e o lock do ``main`` via ``bind()``
(uma única conexão de escrita para o processo inteiro).
"""
from __future__ import annotations

import datetime as _dt
import sqlite3
import threading

from fastapi import APIRouter, Body, HTTPException

router = APIRouter(prefix="/api/cadastro", tags=["cadastro"])

_conn: sqlite3.Connection | None = None
_lock: threading.RLock | None = None


def bind(conn: sqlite3.Connection, lock: threading.RLock) -> None:
    """Chamado pelo ``main`` no start para compartilhar conexão + lock."""
    global _conn, _lock
    _conn, _lock = conn, lock


def _db() -> sqlite3.Connection:
    if _conn is None or _lock is None:
        raise HTTPException(500, "cadastros não inicializado")
    return _conn


# -- projeto -----------------------------------------------------------------
# campos que a tela deixa editar (o resto é derivado / controle interno)
# mes_inicio / ano_inicio NÃO entram aqui: são só o parâmetro interno da
# planilha (mês/ano da 1ª coluna da aba Alocacao), não um atributo do projeto.
_PROJETO_EDITAVEL = {
    "id_projeto_externo": str,
    "nome": str,
    "empresa": str,
    "status": str,
    "id_status": int,
    "matricula_gp": str,
    "id_filial": int,
    "cenario1": int,
    "cenario2": int,
    "cenario3": int,
}
_PROJETO_COLS = (
    "projeto_id, id_projeto_externo, nome, empresa, status, id_status, matricula_gp, "
    "id_filial, cenario1, cenario2, cenario3, arquivo_origem, "
    "criado_na_ferramenta, exportado_em, alterado_em"
)


def _coerce(valor, tipo):
    if valor in (None, ""):
        return None
    if tipo is int:
        return int(float(str(valor).replace(",", ".")))
    return str(valor).strip()


@router.get("/projetos")
def listar_projetos():
    conn = _db()
    with _lock:  # type: ignore[union-attr]
        return {
            "colunas": _PROJETO_COLS.replace(" ", "").split(","),
            "editaveis": list(_PROJETO_EDITAVEL),
            "linhas": [
                dict(r)
                for r in conn.execute(
                    f"SELECT {_PROJETO_COLS} FROM projeto ORDER BY nome"
                )
            ],
        }


@router.put("/projetos/{projeto_id}")
def editar_projeto(projeto_id: int, payload: dict = Body(...)):
    conn = _db()
    with _lock:  # type: ignore[union-attr]
        if not conn.execute(
            "SELECT 1 FROM projeto WHERE projeto_id=?", (projeto_id,)
        ).fetchone():
            raise HTTPException(404, "projeto não encontrado")
        sets, args = [], []
        for campo, tipo in _PROJETO_EDITAVEL.items():
            if campo not in payload:
                continue
            val = _coerce(payload[campo], tipo)
            if campo == "nome" and not val:
                raise HTTPException(422, "nome não pode ficar vazio")
            if campo == "id_projeto_externo" and val is not None:
                dup = conn.execute(
                    "SELECT 1 FROM projeto WHERE id_projeto_externo=? AND projeto_id<>?",
                    (val, projeto_id),
                ).fetchone()
                if dup:
                    raise HTTPException(409, "já existe projeto com esse Id_projeto")
            sets.append(f"{campo}=?")
            args.append(val)
            if campo == "matricula_gp":
                # nome do GP resolvido da matrícula (usado pelo filtro de GP na grade)
                r = conn.execute("SELECT nome FROM pessoa WHERE matricula=?", (val,)).fetchone() if val else None
                sets.append("gestor_projetos=?")
                args.append(r["nome"] if r else None)
        if not sets:
            raise HTTPException(422, "nada para atualizar")
        sets.append("alterado_em=?")
        args.append(_dt.datetime.now().isoformat(timespec="seconds"))
        args.append(projeto_id)
        conn.execute(f"UPDATE projeto SET {', '.join(sets)} WHERE projeto_id=?", args)
        conn.commit()
        row = conn.execute(
            f"SELECT {_PROJETO_COLS} FROM projeto WHERE projeto_id=?", (projeto_id,)
        ).fetchone()
        return {"linha": dict(row)}


# -- pessoa ----------------------------------------------------------------
_PESSOA_EDITAVEL = {
    "nome": str,
    "carga_diaria": float,
    "capacidade_mensal": int,
    "ativo": int,
    "area": str,
    "tipo_contrato": str,
    "situacao": str,
    "fim_contrato": str,
}
_PESSOA_COLS = (
    "matricula, nome, carga_diaria, capacidade_mensal, ativo, area, tipo_contrato, "
    "situacao, fim_contrato"
)


def _coerce_pessoa(valor, tipo):
    if valor in (None, ""):
        return None
    if tipo is int:
        return int(float(str(valor).replace(",", ".")))
    if tipo is float:
        return float(str(valor).replace(",", "."))
    return str(valor).strip()


@router.get("/pessoas")
def listar_pessoas():
    conn = _db()
    with _lock:  # type: ignore[union-attr]
        return {
            "colunas": _PESSOA_COLS.replace(" ", "").split(","),
            "editaveis": list(_PESSOA_EDITAVEL),
            "linhas": [
                dict(r)
                for r in conn.execute(
                    f"SELECT {_PESSOA_COLS} FROM pessoa ORDER BY nome"
                )
            ],
        }


@router.put("/pessoas/{matricula}")
def editar_pessoa_cadastro(matricula: str, payload: dict = Body(...)):
    conn = _db()
    matricula = matricula.strip()
    with _lock:  # type: ignore[union-attr]
        if not conn.execute(
            "SELECT 1 FROM pessoa WHERE matricula=?", (matricula,)
        ).fetchone():
            raise HTTPException(404, "pessoa não encontrada")
        sets, args = [], []
        for campo, tipo in _PESSOA_EDITAVEL.items():
            if campo not in payload:
                continue
            val = _coerce_pessoa(payload[campo], tipo)
            if campo == "nome" and not val:
                raise HTTPException(422, "nome não pode ficar vazio")
            if campo == "ativo":
                val = 1 if val else 0
            sets.append(f"{campo}=?")
            args.append(val)
        if not sets:
            raise HTTPException(422, "nada para atualizar")
        args.append(matricula)
        conn.execute(
            f"UPDATE pessoa SET {', '.join(sets)} WHERE matricula=?", args
        )
        conn.commit()
        row = conn.execute(
            f"SELECT {_PESSOA_COLS} FROM pessoa WHERE matricula=?", (matricula,)
        ).fetchone()
        return {"linha": dict(row)}


# -- catalogo (somente leitura) -----------------------------------------
@router.get("/catalogo")
def listar_catalogo():
    conn = _db()
    with _lock:  # type: ignore[union-attr]
        out: dict[str, list] = {}
        for r in conn.execute(
            "SELECT tipo, id, texto FROM catalogo ORDER BY tipo, id, texto"
        ):
            out.setdefault(r["tipo"], []).append({"id": r["id"], "texto": r["texto"]})
        return {"catalogo": out}

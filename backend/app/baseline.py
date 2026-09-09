"""Linha de base por projeto = último 'commit' (importação ou export).

O export compara baseline x estado atual ('working') para decidir:
  - alocação sumida (estava na baseline, não está mais) -> linha zerada no .xlsx
  - alocação nunca vista na baseline e já removida        -> nem entra no .xlsx
"""
from __future__ import annotations

import datetime as _dt
import sqlite3

# campos versionados de pessoa (o export de Novos_Pesquisadores sai do diff destes)
PESSOA_COLS = [
    "nome", "situacao", "equipe", "area", "tipo_contrato", "inicio_contrato",
    "fim_contrato", "formacao", "id_filial", "carga_diaria", "capacidade_mensal",
    "remuneracao", "inicio_vigencia",
]
PROJETO_COLS = [
    "nome", "empresa", "status", "id_status", "matricula_gp", "id_filial",
    "mes_inicio", "ano_inicio", "cenario1", "cenario2", "cenario3",
]


def _snap_pessoa_projeto(conn: sqlite3.Connection, projeto_id: int) -> None:
    cols = ", ".join(PESSOA_COLS)
    conn.execute("DELETE FROM baseline_pessoa WHERE projeto_id=?", (projeto_id,))
    conn.execute(
        f"""INSERT INTO baseline_pessoa (projeto_id, matricula, {cols})
            SELECT ?, p.matricula, {', '.join('p.' + c for c in PESSOA_COLS)}
            FROM pessoa p
            WHERE p.matricula IN (SELECT matricula FROM alocacao WHERE projeto_id=?)""",
        (projeto_id, projeto_id),
    )
    pcols = ", ".join(PROJETO_COLS)
    conn.execute("DELETE FROM baseline_projeto WHERE projeto_id=?", (projeto_id,))
    conn.execute(
        f"""INSERT INTO baseline_projeto (projeto_id, {pcols})
            SELECT projeto_id, {pcols} FROM projeto WHERE projeto_id=?""",
        (projeto_id,),
    )


def backfill_extras(conn: sqlite3.Connection, projeto_id: int) -> None:
    """Só o snapshot de pessoa/projeto — para projetos que já tinham baseline de
    alocação antes do versionamento se estender a pessoa/projeto."""
    _snap_pessoa_projeto(conn, projeto_id)
    conn.commit()


def capturar(conn: sqlite3.Connection, projeto_id: int, origem: str) -> None:
    """Congela o estado atual do projeto (alocação + pessoas + campos do projeto)
    como nova linha de base."""
    conn.execute("DELETE FROM baseline_alocacao WHERE projeto_id=?", (projeto_id,))
    conn.execute("DELETE FROM baseline_alocacao_mes WHERE projeto_id=?", (projeto_id,))
    conn.execute(
        """INSERT INTO baseline_alocacao (projeto_id, matricula, tipo_alocacao)
           SELECT projeto_id, matricula, tipo_alocacao FROM alocacao a
           WHERE projeto_id=?
             AND EXISTS (SELECT 1 FROM alocacao_mes m
                         WHERE m.alocacao_id=a.alocacao_id AND m.horas>0)""",
        (projeto_id,),
    )
    conn.execute(
        """INSERT INTO baseline_alocacao_mes (projeto_id, matricula, tipo_alocacao, periodo, horas)
           SELECT a.projeto_id, a.matricula, a.tipo_alocacao, m.periodo, m.horas
           FROM alocacao_mes m JOIN alocacao a USING (alocacao_id)
           WHERE a.projeto_id=? AND m.horas>0""",
        (projeto_id,),
    )
    _snap_pessoa_projeto(conn, projeto_id)
    conn.execute(
        """INSERT INTO baseline_meta (projeto_id, criado_em, origem) VALUES (?,?,?)
           ON CONFLICT(projeto_id) DO UPDATE SET criado_em=excluded.criado_em, origem=excluded.origem""",
        (projeto_id, _dt.datetime.now().isoformat(timespec="seconds"), origem),
    )
    conn.commit()


def pessoas_alteradas(conn: sqlite3.Connection, projeto_id: int) -> list[dict]:
    """Pessoas alocadas no projeto cujos campos mudaram vs. a baseline (ou que
    não estavam na baseline). É o conteúdo da aba Novos_Pesquisadores no export."""
    base = {
        r["matricula"]: dict(r)
        for r in conn.execute("SELECT * FROM baseline_pessoa WHERE projeto_id=?", (projeto_id,))
    }
    out: list[dict] = []
    for p in conn.execute(
        """SELECT p.* FROM pessoa p
           WHERE p.matricula IN (SELECT matricula FROM alocacao WHERE projeto_id=?)
           ORDER BY p.nome""",
        (projeto_id,),
    ):
        pd = dict(p)
        b = base.get(pd["matricula"])
        if b is None or any((pd.get(c) or None) != (b.get(c) or None) for c in PESSOA_COLS):
            out.append(pd)
    return out


def removidas(conn: sqlite3.Connection, projeto_id: int) -> list[dict]:
    """Alocações que existiam na baseline e não existem mais no working.
    Retorna [{matricula, tipo_alocacao, horas: {periodo: horas_da_baseline}}]."""
    rows = conn.execute(
        """SELECT b.matricula AS matricula, b.tipo_alocacao AS tipo_alocacao
           FROM baseline_alocacao b
           WHERE b.projeto_id = ?
             AND NOT EXISTS (
                 SELECT 1 FROM alocacao a
                 WHERE a.projeto_id = b.projeto_id
                   AND a.matricula = b.matricula
                   AND a.tipo_alocacao = b.tipo_alocacao)""",
        (projeto_id,),
    ).fetchall()
    out = []
    for r in rows:
        horas = {
            m["periodo"]: m["horas"]
            for m in conn.execute(
                """SELECT periodo, horas FROM baseline_alocacao_mes
                   WHERE projeto_id=? AND matricula=? AND tipo_alocacao=?""",
                (projeto_id, r["matricula"], r["tipo_alocacao"]),
            )
        }
        out.append({"matricula": r["matricula"], "tipo_alocacao": r["tipo_alocacao"], "horas": horas})
    return out


def restaurar_working(conn: sqlite3.Connection, projeto_id: int) -> None:
    """Descarta todas as mudanças do projeto: working (alocação + campos de pessoa
    e do projeto) volta a ser exatamente a baseline."""
    conn.execute("DELETE FROM alocacao WHERE projeto_id=?", (projeto_id,))  # cascade -> alocacao_mes
    for b in conn.execute(
        """SELECT matricula, tipo_alocacao FROM baseline_alocacao b
           WHERE projeto_id=?
             AND EXISTS (SELECT 1 FROM baseline_alocacao_mes m
                         WHERE m.projeto_id=b.projeto_id AND m.matricula=b.matricula
                           AND m.tipo_alocacao=b.tipo_alocacao AND m.horas>0)""",
        (projeto_id,),
    ).fetchall():
        cur = conn.execute(
            "INSERT INTO alocacao (projeto_id, matricula, tipo_alocacao) VALUES (?,?,?)",
            (projeto_id, b["matricula"], b["tipo_alocacao"]),
        )
        conn.execute(
            """INSERT INTO alocacao_mes (alocacao_id, periodo, horas)
               SELECT ?, periodo, horas FROM baseline_alocacao_mes
               WHERE projeto_id=? AND matricula=? AND tipo_alocacao=? AND horas>0""",
            (cur.lastrowid, projeto_id, b["matricula"], b["tipo_alocacao"]),
        )
    # campos de pessoa e do projeto
    set_p = ", ".join(f"{c}=b.{c}" for c in PESSOA_COLS)
    conn.execute(
        f"""UPDATE pessoa SET {set_p}
            FROM baseline_pessoa b
            WHERE b.projeto_id=? AND b.matricula=pessoa.matricula""",
        (projeto_id,),
    )
    set_pr = ", ".join(f"{c}=b.{c}" for c in PROJETO_COLS)
    conn.execute(
        f"UPDATE projeto SET {set_pr} FROM baseline_projeto b WHERE b.projeto_id=projeto.projeto_id "
        f"AND projeto.projeto_id=?",
        (projeto_id,),
    )
    conn.execute(
        "UPDATE projeto SET alterado_em=NULL, exportado_em=exportado_em WHERE projeto_id=?",
        (projeto_id,),
    )
    conn.commit()


def restaurar(conn: sqlite3.Connection, projeto_id: int, matricula: str, tipo_alocacao: str) -> int:
    """Recria no working uma alocação removida, com os valores da baseline. Devolve alocacao_id."""
    cur = conn.execute(
        "INSERT INTO alocacao (projeto_id, matricula, tipo_alocacao) VALUES (?,?,?)",
        (projeto_id, matricula, tipo_alocacao),
    )
    aloc_id = cur.lastrowid
    conn.execute(
        """INSERT INTO alocacao_mes (alocacao_id, periodo, horas)
           SELECT ?, periodo, horas FROM baseline_alocacao_mes
           WHERE projeto_id=? AND matricula=? AND tipo_alocacao=? AND horas>0""",
        (aloc_id, projeto_id, matricula, tipo_alocacao),
    )
    conn.commit()
    return aloc_id

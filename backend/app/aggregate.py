"""Read-side aggregations for the two grids and the discrepancy colouring."""
from __future__ import annotations

import datetime as _dt
import sqlite3


def periodos_union(conn: sqlite3.Connection) -> list[str]:
    """Todos os meses visíveis: janela configurada de qualquer projeto +
    qualquer mês que já tenha horas lançadas (inclusive fora da janela)."""
    rows = conn.execute(
        """SELECT periodo FROM projeto_periodo
           UNION
           SELECT periodo FROM alocacao_mes
           ORDER BY periodo"""
    ).fetchall()
    return [r["periodo"] for r in rows]


def _periodos_por_projeto(conn: sqlite3.Connection) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for r in conn.execute(
        "SELECT projeto_id, periodo FROM projeto_periodo ORDER BY projeto_id, ordem"
    ):
        out.setdefault(r["projeto_id"], []).append(r["periodo"])
    return out


def cor_pessoa_mes(
    conn: sqlite3.Connection, periodos: list[str] | None = None
) -> dict[tuple[str, str], str]:
    """(matricula, periodo) -> 'vermelho' | 'amarelo'. Ausente = sem cor.

    - total > capacidade                                   -> vermelho
    - 0 < total < capacidade                               -> amarelo
    - total == 0, pessoa ATIVA, mês >= mês atual e dentro   -> amarelo
      do contrato (subalocado dentro do horizonte)
    - mês POSTERIOR ao fim do contrato da pessoa            -> sem cor
      (fim_contrato está desatualizado em boa parte da base;
      não marcamos vermelho pra não gerar falso positivo)
    - total == capacidade / capacidade desconhecida /       -> sem cor
      mês passado sem horas
    """
    pessoas = {
        r["matricula"]: r
        for r in conn.execute(
            "SELECT matricula, capacidade_mensal, ativo, situacao, fim_contrato FROM pessoa"
        )
    }
    totals: dict[tuple[str, str], int] = {}
    for r in conn.execute(
        """SELECT a.matricula AS matricula, m.periodo AS periodo, SUM(m.horas) AS horas
           FROM alocacao_mes m JOIN alocacao a USING (alocacao_id)
           GROUP BY a.matricula, m.periodo"""
    ):
        totals[(r["matricula"], r["periodo"])] = r["horas"]

    if periodos is None:
        periodos = periodos_union(conn)
    hoje = _dt.date.today().replace(day=1).isoformat()

    def ativa(pe) -> bool:
        if pe["ativo"] is not None and not int(pe["ativo"]):
            return False
        return (pe["situacao"] or "Ativo").strip().lower() not in ("desligado", "planejado")

    out: dict[tuple[str, str], str] = {}

    # meses com horas lançadas: regra sobre o total real
    for (mat, per), total in totals.items():
        pe = pessoas.get(mat)
        if pe is None:
            continue
        fim = (pe["fim_contrato"] or "").strip() or None
        if fim and per > fim:
            # Contrato vencido: sem cor. Nem vermelho (fim_contrato desatualizado
            # em boa parte da base -> falso positivo em quem está 100%), nem
            # amarelo (não se espera alocação após o fim do contrato).
            continue
        c = pe["capacidade_mensal"]
        if not c:
            continue
        if total > c:
            out[(mat, per)] = "vermelho"
        elif 0 < total < c:
            out[(mat, per)] = "amarelo"
        elif total == 0 and per >= hoje and ativa(pe):
            out[(mat, per)] = "amarelo"           # subalocado dentro do horizonte

    # meses sem nenhuma linha de horas: subalocado se a pessoa está ativa,
    # do mês atual em diante e dentro do contrato
    for mat, pe in pessoas.items():
        c = pe["capacidade_mensal"]
        if not c or not ativa(pe):
            continue
        fim = (pe["fim_contrato"] or "").strip() or None
        for per in periodos:
            if per >= hoje and (not fim or per <= fim) and (mat, per) not in totals:
                out[(mat, per)] = "amarelo"
    return out


def _horas_por_alocacao(conn: sqlite3.Connection) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    for r in conn.execute("SELECT alocacao_id, periodo, horas FROM alocacao_mes"):
        out.setdefault(r["alocacao_id"], {})[r["periodo"]] = r["horas"]
    return out


def build_grade(conn: sqlite3.Connection) -> dict:
    periodos = periodos_union(conn)
    pp = _periodos_por_projeto(conn)
    horas = _horas_por_alocacao(conn)
    cores = cor_pessoa_mes(conn, periodos)
    nomes = {r["matricula"]: r["nome"] for r in conn.execute("SELECT matricula, nome FROM pessoa")}
    caps = {
        r["matricula"]: r["capacidade_mensal"]
        for r in conn.execute("SELECT matricula, capacidade_mensal FROM pessoa")
    }

    alocs = conn.execute(
        """SELECT a.alocacao_id, a.projeto_id, a.matricula, a.tipo_alocacao,
                  p.nome AS projeto_nome
           FROM alocacao a JOIN projeto p USING (projeto_id)"""
    ).fetchall()

    # -- linha de base (último commit) para o diff na tela --------------------
    base_exists: set[tuple] = {
        (r["projeto_id"], r["matricula"], r["tipo_alocacao"])
        for r in conn.execute("SELECT projeto_id, matricula, tipo_alocacao FROM baseline_alocacao")
    }
    base_mes: dict[tuple, dict[str, int]] = {}
    base_proj_tot: dict[int, dict[str, int]] = {}
    base_grupo_tot: dict[tuple, dict[str, int]] = {}
    base_pessoa_tot: dict[str, dict[str, int]] = {}
    for r in conn.execute("SELECT projeto_id, matricula, tipo_alocacao, periodo, horas FROM baseline_alocacao_mes"):
        pid, mat, tipo, per, h = (
            r["projeto_id"], r["matricula"], r["tipo_alocacao"], r["periodo"], r["horas"]
        )
        base_mes.setdefault((pid, mat, tipo), {})[per] = h
        for d in (base_proj_tot.setdefault(pid, {}),
                  base_grupo_tot.setdefault((pid, tipo), {}),
                  base_pessoa_tot.setdefault(mat, {})):
            d[per] = d.get(per, 0) + h

    def tot_diff(cur: dict, base: dict) -> list[str]:
        return sorted(p for p in set(cur) | set(base) if cur.get(p, 0) != base.get(p, 0))

    working_keys: set[tuple] = {(a["projeto_id"], a["matricula"], a["tipo_alocacao"]) for a in alocs}
    removidas_por_proj: dict[int, list[tuple]] = {}
    for (pid, mat, tipo) in base_exists - working_keys:
        removidas_por_proj.setdefault(pid, []).append((mat, tipo))

    def diff(pid: int, mat: str, tipo: str, h: dict[str, int]) -> dict:
        """novo? / lista de meses alterados / valores da baseline (p/ tooltip)."""
        key = (pid, mat, tipo)
        bh = base_mes.get(key, {})
        alterado = sorted(p for p in set(h) | set(bh) if h.get(p, 0) != bh.get(p, 0))
        return {"novo": key not in base_exists, "alterado": alterado, "base_horas": bh}

    def cores_de(matricula: str) -> dict[str, str]:
        return {per: cores[(matricula, per)] for per in periodos if (matricula, per) in cores}

    # -- por projeto --------------------------------------------------------
    por_projeto: list[dict] = []
    projetos = conn.execute(
        "SELECT projeto_id, nome, gestor_projetos, status, arquivo_origem, exportado_em, "
        "alterado_em, criado_na_ferramenta FROM projeto ORDER BY nome"
    ).fetchall()
    hoje = _dt.date.today().replace(day=1).isoformat()

    def _fut(h: dict[str, int]) -> bool:
        """Tem hora > 0 do mês atual em diante? (linha 'histórica' se não)."""
        return any(v > 0 and per >= hoje for per, v in h.items())

    alocs_por_proj: dict[int, list] = {}
    for a in alocs:
        alocs_por_proj.setdefault(a["projeto_id"], []).append(a)

    for pr in projetos:
        pid = pr["projeto_id"]
        grupos_map: dict[str, dict] = {}

        def _grupo(tipo: str) -> dict:
            return grupos_map.setdefault(tipo, {"tipo_alocacao": tipo, "totais": {}, "filhos": []})

        for a in alocs_por_proj.get(pid, []):
            h = horas.get(a["alocacao_id"], {})
            g = _grupo(a["tipo_alocacao"])
            for per, v in h.items():
                g["totais"][per] = g["totais"].get(per, 0) + v
            g["filhos"].append({
                "alocacao_id": a["alocacao_id"],
                "matricula": a["matricula"],
                "nome": nomes.get(a["matricula"], a["matricula"]),
                "tipo_alocacao": a["tipo_alocacao"],
                "capacidade_mensal": caps.get(a["matricula"]),
                "horas": h,
                "cores": cores_de(a["matricula"]),
                "removido": False,
                "tem_horas_futuras": _fut(h),
                **diff(pid, a["matricula"], a["tipo_alocacao"], h),
            })
        for (mat, tipo) in removidas_por_proj.get(pid, []):
            _grupo(tipo)["filhos"].append({
                "alocacao_id": None,
                "matricula": mat,
                "nome": nomes.get(mat, mat),
                "tipo_alocacao": tipo,
                "capacidade_mensal": caps.get(mat),
                "horas": base_mes.get((pid, mat, tipo), {}),
                "cores": {},
                "tem_horas_futuras": _fut(base_mes.get((pid, mat, tipo), {})),
                "removido": True,   # sai zerado no próximo arquivo; fora dos totais
                "novo": False, "alterado": [], "base_horas": base_mes.get((pid, mat, tipo), {}),
            })

        totais: dict[str, int] = {}
        grupos = sorted(grupos_map.values(), key=lambda g: g["tipo_alocacao"].lower())
        for g in grupos:
            g["filhos"].sort(key=lambda f: (f["removido"], f["nome"].lower()))
            g["modificado"] = any(f["novo"] or f["alterado"] or f["removido"] for f in g["filhos"])
            g["totais_base"] = base_grupo_tot.get((pid, g["tipo_alocacao"]), {})
            g["totais_alterado"] = tot_diff(g["totais"], g["totais_base"])
            for per, v in g["totais"].items():
                totais[per] = totais.get(per, 0) + v

        tem_futuro = any(v > 0 and per >= hoje for per, v in totais.items())
        por_projeto.append({
            "projeto_id": pid,
            "nome": pr["nome"],
            "gestor_projetos": pr["gestor_projetos"],
            "status": pr["status"],
            "encerrado": (pr["status"] or "").strip().lower() == "encerrado",
            "tem_futuro": tem_futuro or bool(pr["criado_na_ferramenta"]),
            "periodos_projeto": pp.get(pid, []),
            "sujo": bool(pr["alterado_em"] and (not pr["exportado_em"] or pr["alterado_em"] > pr["exportado_em"])),
            "totais": totais,
            "totais_base": base_proj_tot.get(pid, {}),
            "totais_alterado": tot_diff(totais, base_proj_tot.get(pid, {})),
            "grupos": grupos,
        })

    # -- por recurso ------------------------------------------------------
    por_matricula: dict[str, list] = {}
    for a in alocs:
        por_matricula.setdefault(a["matricula"], []).append(a)

    por_recurso: list[dict] = []
    for matricula in sorted(por_matricula, key=lambda m: nomes.get(m, m).lower()):
        filhos = []
        totais: dict[str, int] = {}
        rows = sorted(
            por_matricula[matricula],
            key=lambda a: (a["projeto_nome"].lower(), a["tipo_alocacao"].lower()),
        )
        for a in rows:
            h = horas.get(a["alocacao_id"], {})
            for per, v in h.items():
                totais[per] = totais.get(per, 0) + v
            filhos.append({
                "alocacao_id": a["alocacao_id"],
                "projeto_id": a["projeto_id"],
                "projeto_nome": a["projeto_nome"],
                "tipo_alocacao": a["tipo_alocacao"],
                "periodos_projeto": pp.get(a["projeto_id"], []),
                "horas": h,
                "removido": False,
                "tem_horas_futuras": _fut(h),
                **diff(a["projeto_id"], matricula, a["tipo_alocacao"], h),
            })
        por_recurso.append({
            "matricula": matricula,
            "nome": nomes.get(matricula, matricula),
            "capacidade_mensal": caps.get(matricula),
            "tem_futuro": any(v > 0 and per >= hoje for per, v in totais.items()),
            "totais": totais,
            "totais_base": base_pessoa_tot.get(matricula, {}),
            "totais_alterado": tot_diff(totais, base_pessoa_tot.get(matricula, {})),
            "cores": cores_de(matricula),
            "filhos": filhos,
        })

    return {"periodos": periodos, "por_projeto": por_projeto, "por_recurso": por_recurso}

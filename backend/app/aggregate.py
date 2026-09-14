"""Read-side aggregations for the two grids and the discrepancy colouring."""
from __future__ import annotations

import datetime as _dt
import sqlite3


def periodos_union(
    conn: sqlite3.Connection, periodo_inicial: str | None = None, periodo_final: str | None = None
) -> list[str]:
    """Meses visíveis: janela configurada de qualquer projeto + qualquer mês que já
    tenha horas lançadas (inclusive fora da janela), recortado pelo filtro de período
    da tela (docs/ESPECIFICACAO.md §8 "Filtros da tela"). `periodo_final=None` é
    "aberto" de verdade — vai até o último mês com dado, sem sentinela nenhuma."""
    q = "SELECT periodo FROM projeto_periodo UNION SELECT periodo FROM alocacao_mes"
    cond, args = [], []
    if periodo_inicial:
        cond.append("periodo >= ?"); args.append(periodo_inicial)
    if periodo_final:
        cond.append("periodo <= ?"); args.append(periodo_final)
    if cond:
        q = f"SELECT periodo FROM ({q}) WHERE {' AND '.join(cond)}"
    rows = conn.execute(q + " ORDER BY periodo", args).fetchall()
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

    Subalocação é mais grave que superalocação pra planejamento de capacidade (recurso
    ocioso é pior que recurso sobrecarregado) — por isso o vermelho, mais forte, é dela:

    - 0 < total < capacidade                               -> vermelho  (subalocado)
    - total == 0, pessoa ATIVA, mês >= mês atual e dentro   -> vermelho  (subalocado,
      do contrato                                                        sem nenhuma hora)
    - total > capacidade                                   -> amarelo  (superalocado)
    - mês POSTERIOR ao fim do contrato da pessoa            -> sem cor
      (fim_contrato está desatualizado em boa parte da base;
      não marcamos como subalocado pra não gerar falso positivo)
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
            # vermelho (não se espera alocação após o fim do contrato).
            continue
        c = pe["capacidade_mensal"]
        if not c:
            continue
        if total > c:
            out[(mat, per)] = "amarelo"           # superalocado
        elif 0 < total < c:
            out[(mat, per)] = "vermelho"          # subalocado
        elif total == 0 and per >= hoje and ativa(pe):
            out[(mat, per)] = "vermelho"          # subalocado, sem nenhuma hora

    # meses sem nenhuma linha de horas: subalocado se a pessoa está ativa,
    # do mês atual em diante e dentro do contrato
    for mat, pe in pessoas.items():
        c = pe["capacidade_mensal"]
        if not c or not ativa(pe):
            continue
        fim = (pe["fim_contrato"] or "").strip() or None
        for per in periodos:
            if per >= hoje and (not fim or per <= fim) and (mat, per) not in totals:
                out[(mat, per)] = "vermelho"      # subalocado
    return out


def _horas_por_alocacao(conn: sqlite3.Connection) -> dict[int, dict[str, int]]:
    out: dict[int, dict[str, int]] = {}
    for r in conn.execute("SELECT alocacao_id, periodo, horas FROM alocacao_mes"):
        out.setdefault(r["alocacao_id"], {})[r["periodo"]] = r["horas"]
    return out


def _diff_context(conn: sqlite3.Connection) -> dict:
    """Estruturas de baseline (último commit) — extraídas de `build_grade` em
    2026-09-12 pra ficarem compartilhadas com `impacto_edicao` (docs/PERFORMANCE.md),
    sem duplicar a regra de "o que é novo/alterado vs. baseline"."""
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

    def diff(pid: int, mat: str, tipo: str, h: dict[str, int]) -> dict:
        """novo? / lista de meses alterados / valores da baseline (p/ tooltip)."""
        key = (pid, mat, tipo)
        bh = base_mes.get(key, {})
        alterado = sorted(p for p in set(h) | set(bh) if h.get(p, 0) != bh.get(p, 0))
        return {"novo": key not in base_exists, "alterado": alterado, "base_horas": bh}

    return {
        "base_exists": base_exists, "base_mes": base_mes, "base_proj_tot": base_proj_tot,
        "base_grupo_tot": base_grupo_tot, "base_pessoa_tot": base_pessoa_tot, "diff": diff,
    }


def impacto_edicao(conn: sqlite3.Connection, tocados: list[tuple[int, str]]) -> dict:
    """Árvore de impacto de uma edição de célula(s) — ver docs/PERFORMANCE.md.
    `tocados` = [(alocacao_id, periodo), ...] realmente escritos na requisição.

    Só é válido quando nenhuma `alocacao` foi criada ou removida por esta edição —
    a linha já existia antes e continua existindo depois, só o valor de uma ou mais
    células dela (e o que isso propaga) mudou. `main.py` decide isso e cai pro
    `_estado()` completo no caso raro de criação/remoção de linha (mais barato e
    seguro do que tentar corrigir a árvore renderizada no cliente por cima de uma
    mudança estrutural)."""
    if not tocados:
        return {"alocacoes": [], "grupos": [], "projetos": [], "pessoas": []}

    aids = sorted({aid for aid, _ in tocados})
    ph = ",".join("?" * len(aids))
    identidade = {
        r["alocacao_id"]: (r["projeto_id"], r["matricula"], r["tipo_alocacao"])
        for r in conn.execute(
            f"SELECT alocacao_id, projeto_id, matricula, tipo_alocacao FROM alocacao "
            f"WHERE alocacao_id IN ({ph})", aids,
        )
    }
    horas = _horas_por_alocacao(conn)
    ctx = _diff_context(conn)
    periodos_tocados = sorted({per for _, per in tocados})
    pt_set = set(periodos_tocados)

    alocacoes_out = []
    for aid, per in tocados:
        ident = identidade.get(aid)
        if ident is None:
            continue   # defensivo — não deveria acontecer neste caminho (ver docstring)
        pid, mat, tipo = ident
        h_atual = horas.get(aid, {}).get(per, 0)
        base_val = ctx["base_mes"].get((pid, mat, tipo), {}).get(per, 0)
        alocacoes_out.append({
            "alocacao_id": aid, "periodo": per, "projeto_id": pid,
            "matricula": mat, "tipo_alocacao": tipo,
            "horas": h_atual, "base_horas": base_val, "alterado": h_atual != base_val,
            "novo": (pid, mat, tipo) not in ctx["base_exists"],
        })

    def _somar_tocados(aids_do_grupo: list[int], base: dict[str, int]) -> tuple[dict, list]:
        totais: dict[str, int] = {}
        for a in aids_do_grupo:
            for per, v in horas.get(a, {}).items():
                if per in pt_set:
                    totais[per] = totais.get(per, 0) + v
        alterado = sorted(p for p in periodos_tocados if totais.get(p, 0) != base.get(p, 0))
        return totais, alterado

    grupos_tocados = sorted({(pid, tipo) for pid, mat, tipo in identidade.values()})
    grupos_out = []
    for pid, tipo in grupos_tocados:
        aids_grupo = [r["alocacao_id"] for r in conn.execute(
            "SELECT alocacao_id FROM alocacao WHERE projeto_id=? AND tipo_alocacao=?", (pid, tipo))]
        totais, alterado = _somar_tocados(aids_grupo, ctx["base_grupo_tot"].get((pid, tipo), {}))
        grupos_out.append({"projeto_id": pid, "tipo_alocacao": tipo, "totais": totais, "totais_alterado": alterado})

    projetos_tocados = sorted({pid for pid, mat, tipo in identidade.values()})
    projetos_out = []
    for pid in projetos_tocados:
        aids_proj = [r["alocacao_id"] for r in conn.execute(
            "SELECT alocacao_id FROM alocacao WHERE projeto_id=?", (pid,))]
        totais, alterado = _somar_tocados(aids_proj, ctx["base_proj_tot"].get(pid, {}))
        pr = conn.execute(
            "SELECT alterado_em, exportado_em FROM projeto WHERE projeto_id=?", (pid,)
        ).fetchone()
        sujo = bool(pr["alterado_em"] and (not pr["exportado_em"] or pr["alterado_em"] > pr["exportado_em"]))
        projetos_out.append({"projeto_id": pid, "totais": totais, "totais_alterado": alterado, "sujo": sujo})

    matriculas_tocadas = sorted({mat for pid, mat, tipo in identidade.values()})
    cores = cor_pessoa_mes(conn, periodos_tocados)
    pessoas_out = []
    for mat in matriculas_tocadas:
        aids_pessoa = [r["alocacao_id"] for r in conn.execute(
            "SELECT alocacao_id FROM alocacao WHERE matricula=?", (mat,))]
        totais, alterado = _somar_tocados(aids_pessoa, ctx["base_pessoa_tot"].get(mat, {}))
        cores_pessoa = {per: cores[(mat, per)] for per in periodos_tocados if (mat, per) in cores}
        # cor/total pintam TODA célula da pessoa naquele mês, mesmo em projetos que
        # esta edição não tocou diretamente (aggregate.cor_pessoa_mes) — o cliente
        # precisa saber quais outras linhas repintar, mesmo sem diff de valor nelas.
        linhas = [
            {"projeto_id": r["projeto_id"], "tipo_alocacao": r["tipo_alocacao"]}
            for r in conn.execute(
                "SELECT DISTINCT projeto_id, tipo_alocacao FROM alocacao WHERE matricula=?", (mat,)
            )
        ]
        pessoas_out.append({
            "matricula": mat, "totais": totais, "totais_alterado": alterado,
            "cores": cores_pessoa, "linhas_para_repintar": linhas,
        })

    return {
        "alocacoes": alocacoes_out, "grupos": grupos_out,
        "projetos": projetos_out, "pessoas": pessoas_out,
    }


def build_grade(
    conn: sqlite3.Connection,
    periodo_inicial: str | None = None,
    periodo_final: str | None = None,
    periodo_ativo: bool = True,
) -> dict:
    """Filtro de período (docs/ESPECIFICACAO.md §8) — substitui o antigo "só ativos".
    `periodo_inicial` default = mês atual; `periodo_final` default = aberto (None de
    verdade, nunca sentinela). Linha (projeto/pessoa/alocação) some da tela só se
    NENHUM mês dentro de [periodo_inicial, periodo_final] tiver hora dela — mesmo
    critério das colunas, então nunca existe total visível sem a linha que o explica.
    `periodo_ativo=False` desliga o filtro inteiro (mostra tudo, sem corte nenhum) —
    equivale a período inicial TAMBÉM aberto, não só "não configurado ainda"; por isso é
    um parâmetro à parte, e não apenas `periodo_inicial=None` (que continua significando
    "não veio nada do cliente, usa o padrão de hoje")."""
    hoje = _dt.date.today().replace(day=1).isoformat()
    if periodo_ativo:
        periodo_inicial = periodo_inicial or hoje
    else:
        periodo_inicial = None
        periodo_final = None
    periodos = periodos_union(conn, periodo_inicial, periodo_final)
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
    ctx = _diff_context(conn)
    base_exists, base_mes = ctx["base_exists"], ctx["base_mes"]
    base_proj_tot, base_grupo_tot, base_pessoa_tot = (
        ctx["base_proj_tot"], ctx["base_grupo_tot"], ctx["base_pessoa_tot"]
    )
    diff = ctx["diff"]

    def tot_diff(cur: dict, base: dict) -> list[str]:
        return sorted(p for p in set(cur) | set(base) if cur.get(p, 0) != base.get(p, 0))

    working_keys: set[tuple] = {(a["projeto_id"], a["matricula"], a["tipo_alocacao"]) for a in alocs}
    removidas_por_proj: dict[int, list[tuple]] = {}
    for (pid, mat, tipo) in base_exists - working_keys:
        removidas_por_proj.setdefault(pid, []).append((mat, tipo))

    def cores_de(matricula: str) -> dict[str, str]:
        return {per: cores[(matricula, per)] for per in periodos if (matricula, per) in cores}

    # -- por projeto --------------------------------------------------------
    por_projeto: list[dict] = []
    # status não é coluna própria — sempre derivado de id_status via catalogo.
    projetos = conn.execute(
        "SELECT p.projeto_id, p.nome, p.matricula_gp, p.gestor_projetos, cat.texto AS status, "
        "p.arquivo_origem, p.exportado_em, p.alterado_em, p.criado_na_ferramenta "
        "FROM projeto p LEFT JOIN catalogo cat ON cat.tipo='status' AND cat.id=p.id_status "
        "ORDER BY p.nome"
    ).fetchall()

    def _visivel_no_periodo(h: dict[str, int]) -> bool:
        """Tem hora > 0 em algum mês dentro de [periodo_inicial, periodo_final] — o
        MESMO critério das colunas. Substitui o antigo `_fut` (só olhava "futuro" a
        partir de hoje e escondia linha inteira mesmo com hora real num mês passado
        que estava sendo exibido — bug corrigido em 2026-09-12, ver Histórico)."""
        return any(
            v > 0
            and (not periodo_inicial or per >= periodo_inicial)
            and (not periodo_final or per <= periodo_final)
            for per, v in h.items()
        )

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
                "tem_horas_no_periodo": _visivel_no_periodo(h),
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
                "tem_horas_no_periodo": _visivel_no_periodo(base_mes.get((pid, mat, tipo), {})),
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

        por_projeto.append({
            "projeto_id": pid,
            "nome": pr["nome"],
            "matricula_gp": pr["matricula_gp"],
            "gestor_projetos": pr["gestor_projetos"],
            "status": pr["status"],
            "encerrado": (pr["status"] or "").strip().lower() == "encerrado",
            "visivel_no_periodo": _visivel_no_periodo(totais) or bool(pr["criado_na_ferramenta"]),
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
                "tem_horas_no_periodo": _visivel_no_periodo(h),
                **diff(a["projeto_id"], matricula, a["tipo_alocacao"], h),
            })
        por_recurso.append({
            "matricula": matricula,
            "nome": nomes.get(matricula, matricula),
            "capacidade_mensal": caps.get(matricula),
            "visivel_no_periodo": _visivel_no_periodo(totais),
            "totais": totais,
            "totais_base": base_pessoa_tot.get(matricula, {}),
            "totais_alterado": tot_diff(totais, base_pessoa_tot.get(matricula, {})),
            "cores": cores_de(matricula),
            "filhos": filhos,
        })

    return {
        "periodos": periodos, "por_projeto": por_projeto, "por_recurso": por_recurso,
        # valores resolvidos (periodo_inicial default = hoje; periodo_final = None de
        # verdade quando aberto) — a tela usa isso pra preencher os dois seletores.
        "periodo_inicial": periodo_inicial, "periodo_final": periodo_final,
        "periodo_ativo": periodo_ativo,
    }

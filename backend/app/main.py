"""FastAPI app: import folder of .xlsx, edit the two grids, export back.

Local single-user app; one shared SQLite connection guarded by a lock.
"""
from __future__ import annotations

import datetime as _dt
import threading
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import db as dbmod
from . import versao
from .aggregate import build_grade
from .templates import ensure_template
from .xlsx_export import exportar_projeto, nome_arquivo_projeto
from .xlsx_import import import_workbook, is_project_workbook

ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT / "frontend"
SAMPLE_PATH = ROOT / "amostras" / "ed425bf6-20260518_Otimizeplan.xlsx"
TEMPLATE_PATH = ROOT / "templates" / "projeto_template.xlsx"
UPLOAD_DIR = ROOT / "uploads"
EXPORT_DIR = ROOT / "exports"
UPLOAD_DIR.mkdir(exist_ok=True)
EXPORT_DIR.mkdir(exist_ok=True)
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    for i in range(1, 1000):
        cand = p.with_name(f"{p.stem}-{i}{p.suffix}")
        if not cand.exists():
            return cand
    return p

_conn = dbmod.init_db()
_lock = threading.RLock()


versao.garantir_inicializado(_conn)
_conn.commit()

app = FastAPI(title="Planejamento de Alocação")


def _touch_projeto(projeto_id: int) -> None:
    _conn.execute(
        "UPDATE projeto SET alterado_em=? WHERE projeto_id=?",
        (_dt.datetime.now().isoformat(timespec="seconds"), projeto_id),
    )


def _grade() -> dict:
    return build_grade(_conn)


def _catalogos() -> dict:
    out: dict[str, list] = {}
    for r in _conn.execute("SELECT tipo, id, texto FROM catalogo ORDER BY tipo, id, texto"):
        out.setdefault(r["tipo"], []).append({"id": r["id"], "texto": r["texto"]})
    return out


def _pessoas() -> list[dict]:
    return [dict(r) for r in _conn.execute("SELECT * FROM pessoa ORDER BY nome")]


def _projetos() -> list[dict]:
    return [
        dict(r)
        for r in _conn.execute(
            """SELECT projeto_id, id_projeto_externo, nome, empresa, status, gestor_projetos,
                      mes_inicio, ano_inicio, arquivo_origem, criado_na_ferramenta,
                      exportado_em, alterado_em
               FROM projeto ORDER BY nome"""
        )
    ]


def _estado() -> dict:
    return {
        "projetos": _projetos(),
        "grade": _grade(),
        "catalogos": _catalogos(),
        "pessoas": _pessoas(),
        "versao": versao.estado_repo(_conn),
    }


def _parse_valor(valor, capacidade: int | None) -> int:
    if valor is None:
        return 0
    s = str(valor).strip().replace(",", ".")
    if s == "":
        return 0
    if s.endswith("%"):
        if not capacidade:
            raise HTTPException(422, "capacidade da pessoa não cadastrada — não dá para usar %")
        return max(0, round(float(s[:-1]) / 100 * capacidade))
    return max(0, round(float(s)))


# -- bootstrap / state --------------------------------------------------
@app.get("/api/estado")
def get_estado():
    with _lock:
        return _estado()


# -- import ----------------------------------------------------------------
@app.get("/api/varrer")
def varrer(pasta: str):
    p = Path(pasta).expanduser()
    if not p.is_dir():
        raise HTTPException(404, f"pasta não encontrada: {pasta}")
    externos = {
        str(r["id_projeto_externo"])
        for r in _conn.execute("SELECT id_projeto_externo FROM projeto")
    }
    origens = {r["arquivo_origem"] for r in _conn.execute("SELECT arquivo_origem FROM projeto")}
    achados = []
    for f in sorted(p.glob("*.xlsx")):
        if f.name.startswith("~$"):
            continue
        if not is_project_workbook(f):
            continue
        achados.append({"arquivo": str(f), "nome": f.name, "importado": str(f) in origens})
    return {"pasta": str(p), "arquivos": achados}


@app.post("/api/importar")
def importar(payload: dict = Body(...)):
    arquivos = payload.get("arquivos")
    pasta = payload.get("pasta")
    with _lock:
        alvos: list[str] = []
        if pasta:
            p = Path(pasta).expanduser()
            if not p.is_dir():
                raise HTTPException(404, f"pasta não encontrada: {pasta}")
            alvos = [str(f) for f in sorted(p.glob("*.xlsx")) if not f.name.startswith("~$")]
        alvos += list(arquivos or [])
        if not alvos:
            raise HTTPException(422, "nada para importar")
        resultados = []
        for a in alvos:
            if not is_project_workbook(a):
                resultados.append({"status": "skipped", "nome": Path(a).name, "detail": "não é arquivo de projeto"})
                continue
            resultados.append(import_workbook(_conn, a).as_dict())
        return {"resultados": resultados, "estado": _estado()}


@app.post("/api/importar-upload")
async def importar_upload(arquivos: list[UploadFile] = File(...)):
    """Upload de um ou mais .xlsx do navegador. Classifica cada arquivo:
    planilha de projeto -> import de planejamento; extrato do BI -> import do BI."""
    from .bi_import import classify as bi_classify
    from .bi_import import importar_arquivos as bi_importar
    from .xlsx_io import Workbook

    recebidos = [(uf.filename or "arquivo.xlsx", await uf.read()) for uf in arquivos]
    with _lock:
        resultados: list[dict] = []
        bi_pendentes: list[str] = []
        for nome, data in recebidos:
            dest = _unique_path(UPLOAD_DIR / Path(nome).name)
            dest.write_bytes(data)
            if is_project_workbook(dest):
                r = import_workbook(_conn, str(dest)).as_dict()
                r["nome"] = nome
                resultados.append(r)
                continue
            kind = None
            try:
                wb = Workbook(str(dest))
                kind = bi_classify(wb.sheet(wb.sheet_names[0]))
            except Exception:  # noqa: BLE001
                kind = None
            if kind:
                bi_pendentes.append(str(dest))
            else:
                dest.unlink(missing_ok=True)
                resultados.append({"status": "skipped", "nome": nome,
                                   "detail": "não reconhecido (nem projeto, nem extrato do BI)"})
        if bi_pendentes:
            resultados.extend(bi_importar(_conn, bi_pendentes))
        return {"resultados": resultados, "estado": _estado()}


@app.post("/api/importar-bi")
async def importar_bi(arquivos: list[UploadFile] = File(...)):
    """Extratos do BI (equipe / colabmescusto / projetomescusto) -> banco.
    Enriquece `pessoa` (capacidade, área, contrato) e popula `bi_projeto` / `bi_custo`."""
    from .bi_import import importar_arquivos

    recebidos = [(uf.filename or "bi.xlsx", await uf.read()) for uf in arquivos]
    with _lock:
        salvos = []
        for nome, data in recebidos:
            dest = _unique_path(UPLOAD_DIR / ("bi_" + Path(nome).name))
            dest.write_bytes(data)
            salvos.append(str(dest))
        resultados = importar_arquivos(_conn, salvos)
        return {"resultados": resultados, "estado": _estado()}


@app.get("/api/custo/projeto")
def custo_projeto(de: str | None = None, ate: str | None = None, por: str | None = None):
    """Custo (e horas) por projeto por mês. ``por`` = 'tipo_alocacao' | 'area' para
    subdividir. Datas em 'YYYY-MM'."""
    de = (de + "-01") if de and len(de) == 7 else de
    ate = (ate + "-01") if ate and len(ate) == 7 else ate
    grp = {"tipo_alocacao": "b.tipo_alocacao", "area": "pe.area"}.get(por)
    sel_grp = f"{grp} AS grupo," if grp else "NULL AS grupo,"
    join = "LEFT JOIN pessoa pe ON pe.matricula = b.matricula" if por == "area" else ""
    where, args = [], []
    if de:
        where.append("b.periodo >= ?"); args.append(de)
    if ate:
        where.append("b.periodo <= ?"); args.append(ate)
    wsql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = _conn.execute(
        f"""SELECT b.id_projetos AS id_projetos,
                   COALESCE(bp.nome_projeto, pr.nome, '#' || b.id_projetos) AS nome_projeto,
                   {sel_grp}
                   b.periodo AS periodo,
                   ROUND(SUM(b.custo), 2) AS custo,
                   ROUND(SUM(b.horas), 2) AS horas
            FROM bi_custo b
            LEFT JOIN bi_projeto bp ON bp.id_projetos = b.id_projetos
            LEFT JOIN projeto pr ON pr.id_projeto_externo = CAST(b.id_projetos AS TEXT)
            {join}
            {wsql}
            GROUP BY b.id_projetos, nome_projeto, grupo, b.periodo
            ORDER BY nome_projeto, grupo, b.periodo""",
        args,
    ).fetchall()
    periodos = sorted({r["periodo"] for r in rows})
    linhas: dict[tuple, dict] = {}
    for r in rows:
        k = (r["id_projetos"], r["grupo"])
        d = linhas.setdefault(k, {
            "id_projetos": r["id_projetos"], "nome_projeto": r["nome_projeto"],
            "grupo": r["grupo"], "custo": {}, "horas": {}, "total_custo": 0.0,
        })
        d["custo"][r["periodo"]] = r["custo"]
        d["horas"][r["periodo"]] = r["horas"]
        d["total_custo"] += r["custo"] or 0
    return {"periodos": periodos, "linhas": list(linhas.values())}


# -- projetos ---------------------------------------------------------------
@app.post("/api/projetos")
def criar_projeto(payload: dict = Body(...)):
    with _lock:
        nome = (payload.get("nome") or "").strip()
        if not nome:
            raise HTTPException(422, "nome é obrigatório")
        mes = int(payload.get("mes_inicio") or 1)
        ano = int(payload.get("ano_inicio") or _dt.date.today().year)
        n_meses = int(payload.get("meses") or 10)
        externo = payload.get("id_projeto_externo")
        if externo and _conn.execute(
            "SELECT 1 FROM projeto WHERE id_projeto_externo=?", (str(externo),)
        ).fetchone():
            raise HTTPException(409, "já existe projeto com esse Id_projeto")

        cur = _conn.cursor()
        cur.execute(
            """INSERT INTO projeto
               (id_projeto_externo, nome, empresa, status, id_status, matricula_gp,
                id_filial, mes_inicio, ano_inicio, cenario1, cenario2, cenario3,
                criado_na_ferramenta, alterado_em)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?)""",
            (
                str(externo) if externo else None, nome, payload.get("empresa"),
                payload.get("status"), payload.get("id_status"), payload.get("matricula_gp"),
                int(payload.get("id_filial") or 62), mes, ano,
                payload.get("cenario1"), payload.get("cenario2"), payload.get("cenario3"),
                _dt.datetime.now().isoformat(timespec="seconds"),
            ),
        )
        projeto_id = cur.lastrowid
        base = _dt.date(ano, mes, 1)
        periodos = []
        y, m = ano, mes
        for i in range(n_meses):
            periodos.append((projeto_id, _dt.date(y, m, 1).isoformat(), i))
            m += 1
            if m > 12:
                m, y = 1, y + 1
        cur.executemany(
            "INSERT INTO projeto_periodo (projeto_id, periodo, ordem) VALUES (?,?,?)", periodos
        )
        _conn.commit()
        return {"projeto_id": projeto_id, "estado": _estado()}


@app.delete("/api/projetos/{projeto_id}")
def remover_projeto(projeto_id: int):
    with _lock:
        _conn.execute("DELETE FROM projeto WHERE projeto_id=?", (projeto_id,))
        _conn.commit()
        return {"estado": _estado()}


@app.post("/api/projetos/{projeto_id}/exportar")
def exportar(projeto_id: int, payload: dict = Body(default={})):
    """Grava o .xlsx numa pasta do servidor (uso em localhost / automação)."""
    with _lock:
        pr = _conn.execute("SELECT * FROM projeto WHERE projeto_id=?", (projeto_id,)).fetchone()
        if pr is None:
            raise HTTPException(404, "projeto não encontrado")
        destino = payload.get("pasta")
        if not destino and pr["arquivo_origem"]:
            destino = str(Path(pr["arquivo_origem"]).parent)
        if not destino:
            raise HTTPException(422, "informe a pasta de destino")
        ensure_template(SAMPLE_PATH, TEMPLATE_PATH)
        try:
            caminho = exportar_projeto(
                _conn, projeto_id, destino, str(TEMPLATE_PATH), inicio=payload.get("inicio")
            )
        except (FileNotFoundError, KeyError) as e:
            raise HTTPException(422, str(e))
        return {"arquivo": caminho, "estado": _estado()}


@app.get("/api/projetos/{projeto_id}/download")
def baixar(projeto_id: int, inicio: str | None = None):
    """Gera o .xlsx e devolve como download (funciona de qualquer máquina).
    ``inicio`` ('YYYY-MM') antecipa o 1º mês do arquivo (default = mês atual)."""
    with _lock:
        pr = _conn.execute("SELECT nome FROM projeto WHERE projeto_id=?", (projeto_id,)).fetchone()
        if pr is None:
            raise HTTPException(404, "projeto não encontrado")
        ensure_template(SAMPLE_PATH, TEMPLATE_PATH)
        try:
            caminho = exportar_projeto(
                _conn, projeto_id, str(EXPORT_DIR), str(TEMPLATE_PATH), inicio=inicio
            )
        except (FileNotFoundError, KeyError) as e:
            raise HTTPException(422, str(e))
    return FileResponse(caminho, filename=Path(caminho).name, media_type=_XLSX_MIME)


@app.post("/api/exportar")
def exportar_varios(payload: dict = Body(...)):
    """Gera vários projetos de uma vez. 1 id -> .xlsx; vários -> .zip para download."""
    import io
    import zipfile as _zip

    from fastapi.responses import Response

    ids = payload.get("projeto_ids") or []
    inicio = payload.get("inicio")
    if not ids:
        raise HTTPException(422, "selecione ao menos um projeto")
    with _lock:
        ensure_template(SAMPLE_PATH, TEMPLATE_PATH)
        gerados: list[str] = []
        for pid in ids:
            try:
                gerados.append(exportar_projeto(
                    _conn, int(pid), str(EXPORT_DIR), str(TEMPLATE_PATH), inicio=inicio
                ))
            except (FileNotFoundError, KeyError) as e:
                raise HTTPException(422, f"projeto {pid}: {e}")

    if len(gerados) == 1:
        return FileResponse(gerados[0], filename=Path(gerados[0]).name, media_type=_XLSX_MIME)

    buf = io.BytesIO()
    with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as z:
        for caminho in gerados:
            z.write(caminho, Path(caminho).name)
    nome_zip = f"{_dt.date.today():%Y%m%d} alocacoes ({len(gerados)}).zip"
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{nome_zip}"'},
    )


# -- alocação -------------------------------------------------------------
@app.post("/api/alocacao")
def criar_alocacao(payload: dict = Body(...)):
    with _lock:
        projeto_id = payload["projeto_id"]
        matricula = str(payload["matricula"]).strip()
        tipo = str(payload["tipo_alocacao"]).strip()
        nome = (payload.get("nome") or "").strip()
        if not _conn.execute("SELECT 1 FROM projeto WHERE projeto_id=?", (projeto_id,)).fetchone():
            raise HTTPException(404, "projeto não encontrado")
        row = _conn.execute("SELECT nome FROM pessoa WHERE matricula=?", (matricula,)).fetchone()
        if row is None:
            _conn.execute(
                "INSERT INTO pessoa (matricula, nome) VALUES (?,?)", (matricula, nome or matricula)
            )
        elif nome and row["nome"] in (None, "", matricula):
            _conn.execute("UPDATE pessoa SET nome=? WHERE matricula=?", (nome, matricula))
        try:
            cur = _conn.execute(
                "INSERT INTO alocacao (projeto_id, matricula, tipo_alocacao) VALUES (?,?,?)",
                (projeto_id, matricula, tipo),
            )
        except dbmod.sqlite3.IntegrityError:
            raise HTTPException(409, "essa pessoa já tem esse tipo de alocação nesse projeto")
        _touch_projeto(projeto_id)
        _conn.commit()
        return {"alocacao_id": cur.lastrowid, "estado": _estado()}


@app.put("/api/alocacao/mes-lote")
def editar_mes_lote(payload: dict = Body(...)):
    """Aplica várias edições de célula de uma vez (arrastar/colar do estilo Excel)."""
    edits = payload.get("edits") or []
    with _lock:
        zerados: set[int] = set()
        recriados: set[int] = set()
        for e in edits:
            aid = e.get("alocacao_id", e.get("alocacaoId"))
            periodo = str(e.get("periodo") or "")
            if len(periodo) == 7:
                periodo += "-01"
            try:
                _dt.date.fromisoformat(periodo)
            except ValueError:
                continue
            row = _conn.execute(
                """SELECT a.projeto_id AS projeto_id, p.capacidade_mensal AS cap
                   FROM alocacao a JOIN pessoa p USING (matricula) WHERE a.alocacao_id=?""",
                (aid,),
            ).fetchone()
            if row is None:
                # alocação sumiu (linha foi zerada por completo). Se a edição traz a
                # identidade e um valor > 0 — caso típico de undo/redo/colar —, recria.
                ident = (e.get("projeto_id"), e.get("matricula"), e.get("tipo_alocacao"))
                v = str(e.get("valor") or "").strip()
                if not all(ident) or v in ("", "0") or v.startswith("-"):
                    continue
                _conn.execute(
                    """INSERT INTO alocacao (projeto_id, matricula, tipo_alocacao) VALUES (?,?,?)
                       ON CONFLICT(projeto_id, matricula, tipo_alocacao) DO NOTHING""",
                    ident,
                )
                aid = _conn.execute(
                    "SELECT alocacao_id FROM alocacao WHERE projeto_id=? AND matricula=? AND tipo_alocacao=?",
                    ident,
                ).fetchone()["alocacao_id"]
                row = _conn.execute(
                    """SELECT a.projeto_id AS projeto_id, p.capacidade_mensal AS cap
                       FROM alocacao a JOIN pessoa p USING (matricula) WHERE a.alocacao_id=?""",
                    (aid,),
                ).fetchone()
                if row is None:
                    continue
                recriados.add(aid)
            horas = _parse_valor(e.get("valor"), row["cap"])
            if horas > 0:
                _conn.execute(
                    "INSERT OR REPLACE INTO alocacao_mes (alocacao_id, periodo, horas) VALUES (?,?,?)",
                    (aid, periodo, horas),
                )
            else:
                # zerar == remover o registro do mês (ausência de linha == 0)
                _conn.execute(
                    "DELETE FROM alocacao_mes WHERE alocacao_id=? AND periodo=?", (aid, periodo)
                )
                zerados.add(aid)
            _touch_projeto(row["projeto_id"])
        # alocação que ficou sem nenhum mês (zerada, ou recriada sem valor efetivo)
        # some do working
        for aid in zerados | recriados:
            if not _conn.execute(
                "SELECT 1 FROM alocacao_mes WHERE alocacao_id=? LIMIT 1", (aid,)
            ).fetchone():
                _conn.execute("DELETE FROM alocacao WHERE alocacao_id=?", (aid,))
        _conn.commit()
        return {"estado": _estado()}


@app.put("/api/alocacao/{alocacao_id}/tipo")
def mudar_tipo(alocacao_id: int, payload: dict = Body(...)):
    """Move a pessoa para outro tipo de alocação (equipe) no mesmo projeto."""
    with _lock:
        novo = str(payload.get("tipo_alocacao") or "").strip()
        if not novo:
            raise HTTPException(422, "tipo_alocacao é obrigatório")
        row = _conn.execute(
            "SELECT projeto_id, matricula, tipo_alocacao FROM alocacao WHERE alocacao_id=?",
            (alocacao_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "alocação não encontrada")
        if row["tipo_alocacao"] == novo:
            return {"estado": _estado()}
        if _conn.execute(
            "SELECT 1 FROM alocacao WHERE projeto_id=? AND matricula=? AND tipo_alocacao=?",
            (row["projeto_id"], row["matricula"], novo),
        ).fetchone():
            raise HTTPException(409, "essa pessoa já tem esse tipo nesse projeto")
        _conn.execute("UPDATE alocacao SET tipo_alocacao=? WHERE alocacao_id=?", (novo, alocacao_id))
        _touch_projeto(row["projeto_id"])
        _conn.commit()
        return {"estado": _estado()}


@app.delete("/api/alocacao/{alocacao_id}")
def remover_alocacao(alocacao_id: int):
    """Remove a pessoa do projeto. Se ela existia na linha de base, o próximo
    arquivo gerado a inclui zerada (sinal de remoção); senão, apenas some."""
    with _lock:
        row = _conn.execute(
            "SELECT projeto_id FROM alocacao WHERE alocacao_id=?", (alocacao_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(404, "alocação não encontrada")
        _conn.execute("DELETE FROM alocacao WHERE alocacao_id=?", (alocacao_id,))
        _touch_projeto(row["projeto_id"])
        _conn.commit()
        return {"estado": _estado()}


@app.post("/api/projetos/{projeto_id}/descartar")
def descartar_projeto(projeto_id: int):
    """Descarta as mudanças de alocação/janela do projeto (working := HEAD)."""
    with _lock:
        if not _conn.execute("SELECT 1 FROM projeto WHERE projeto_id=?", (projeto_id,)).fetchone():
            raise HTTPException(404, "projeto não encontrado")
        versao.descartar(_conn, projeto_id)
        return {"estado": _estado()}


@app.post("/api/descartar-tudo")
def descartar_tudo():
    """reset --hard: working := HEAD (descarta TODAS as mudanças pendentes)."""
    with _lock:
        versao.descartar(_conn)
        return {"estado": _estado()}


@app.post("/api/projetos/{projeto_id}/marcar-baseline")
def marcar_baseline_removido(projeto_id: int):
    """Removido: commits agora são globais. Use POST /api/versao/commit."""
    raise HTTPException(
        409, "commits são globais agora — use POST /api/versao/commit "
             "(ou POST /api/projetos/{id}/descartar para reverter só este projeto)")


@app.post("/api/projetos/{projeto_id}/restaurar-alocacao")
def restaurar_alocacao_endpoint(projeto_id: int, payload: dict = Body(...)):
    """Desfaz uma remoção: recria a alocação com os valores do HEAD."""
    matricula = str(payload.get("matricula") or "").strip()
    tipo = str(payload.get("tipo_alocacao") or "").strip()
    with _lock:
        existe = _conn.execute(
            "SELECT 1 FROM baseline_alocacao WHERE projeto_id=? AND matricula=? AND tipo_alocacao=?",
            (projeto_id, matricula, tipo),
        ).fetchone()
        if not existe:
            raise HTTPException(404, "essa alocação não está no HEAD")
        if _conn.execute(
            "SELECT 1 FROM alocacao WHERE projeto_id=? AND matricula=? AND tipo_alocacao=?",
            (projeto_id, matricula, tipo),
        ).fetchone():
            raise HTTPException(409, "alocação já existe")
        versao.restaurar_alocacao(_conn, projeto_id, matricula, tipo)
        _touch_projeto(projeto_id)
        _conn.commit()
        return {"estado": _estado()}


# -- versionamento (branch / commit / checkout / log) ------------------
@app.get("/api/versao/estado")
def versao_estado():
    with _lock:
        return versao.estado_repo(_conn)


@app.get("/api/versao/log")
def versao_log(ref: str | None = None, limite: int = 100):
    with _lock:
        try:
            return {"commits": versao.log(_conn, ref, limite)}
        except KeyError:
            raise HTTPException(404, f"branch '{ref}' não existe")


@app.post("/api/versao/commit")
def versao_commit(payload: dict = Body(...)):
    msg = str(payload.get("mensagem") or "").strip()
    if not msg:
        raise HTTPException(422, "mensagem do commit é obrigatória")
    with _lock:
        try:
            cid = versao.commit(_conn, msg, autor=str(payload.get("autor") or "").strip())
        except versao.NadaParaCommitar:
            raise HTTPException(409, "nada para commitar")
        return {"commit_id": cid, "estado": _estado()}


@app.post("/api/versao/branch")
def versao_branch(payload: dict = Body(...)):
    nome = str(payload.get("nome") or "").strip()
    with _lock:
        try:
            versao.branch(_conn, nome, a_partir=payload.get("a_partir"),
                          trocar=bool(payload.get("trocar")))
        except ValueError as e:
            raise HTTPException(409, str(e))
        except KeyError as e:
            raise HTTPException(404, f"branch {e} não existe")
        return {"estado": _estado()}


@app.post("/api/versao/checkout")
def versao_checkout(payload: dict = Body(...)):
    ref = str(payload.get("ref") or "").strip()
    with _lock:
        try:
            versao.checkout(_conn, ref)
        except versao.VersaoSuja as e:
            raise HTTPException(409, str(e))
        except KeyError:
            raise HTTPException(404, f"branch '{ref}' não existe")
        return {"estado": _estado()}


@app.delete("/api/versao/branch/{nome}")
def versao_deletar_branch(nome: str):
    with _lock:
        try:
            versao.deletar_branch(_conn, nome)
        except ValueError as e:
            raise HTTPException(409, str(e))
        except KeyError:
            raise HTTPException(404, f"branch '{nome}' não existe")
        return {"estado": _estado()}


@app.post("/api/versao/merge")
def versao_merge(payload: dict = Body(...)):
    origem = str(payload.get("origem") or "").strip()
    with _lock:
        try:
            res = versao.merge(_conn, origem, autor=str(payload.get("autor") or "").strip())
        except versao.VersaoSuja as e:
            raise HTTPException(409, str(e))
        except versao.MergeEmAndamento as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(409, str(e))
        except KeyError:
            raise HTTPException(404, f"branch '{origem}' não existe")
        return {**res, "estado": _estado()}


@app.get("/api/versao/conflitos")
def versao_conflitos():
    with _lock:
        return {"conflitos": versao.listar_conflitos(_conn)}


@app.post("/api/versao/conflito/resolver")
def versao_resolver_conflito(payload: dict = Body(...)):
    try:
        cid = int(payload["id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(422, "id do conflito é obrigatório")
    with _lock:
        try:
            versao.resolver_conflito(
                _conn, cid, lado=payload.get("lado"), valor=payload.get("valor"))
        except KeyError:
            raise HTTPException(404, "conflito não encontrado")
        return {"estado": _estado()}


@app.post("/api/versao/merge/concluir")
def versao_concluir_merge(payload: dict = Body(...)):
    with _lock:
        try:
            cid = versao.concluir_merge(
                _conn, str(payload.get("mensagem") or "").strip(),
                autor=str(payload.get("autor") or "").strip())
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"commit_id": cid, "estado": _estado()}


@app.post("/api/versao/merge/abortar")
def versao_abortar_merge():
    with _lock:
        try:
            versao.abortar_merge(_conn)
        except ValueError as e:
            raise HTTPException(409, str(e))
        return {"estado": _estado()}


@app.put("/api/alocacao/{alocacao_id}/mes")
def editar_mes(alocacao_id: int, payload: dict = Body(...)):
    with _lock:
        row = _conn.execute(
            """SELECT a.projeto_id AS projeto_id, a.matricula AS matricula,
                      p.capacidade_mensal AS cap
               FROM alocacao a JOIN pessoa p USING (matricula)
               WHERE a.alocacao_id=?""",
            (alocacao_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "alocação não encontrada")
        periodo = payload["periodo"]
        if len(periodo) == 7:  # 'YYYY-MM'
            periodo = periodo + "-01"
        try:
            _dt.date.fromisoformat(periodo)
        except ValueError:
            raise HTTPException(422, f"período inválido: {periodo}")
        # Edição livre: meses fora da janela configurada do projeto são aceitos
        # (aparecem tingidos na grade e entram no .xlsx no export).
        horas = _parse_valor(payload.get("valor"), row["cap"])
        if horas > 0:
            _conn.execute(
                "INSERT OR REPLACE INTO alocacao_mes (alocacao_id, periodo, horas) VALUES (?,?,?)",
                (alocacao_id, periodo, horas),
            )
        else:
            # zerar == remover o registro do mês (ausência de linha == 0)
            _conn.execute(
                "DELETE FROM alocacao_mes WHERE alocacao_id=? AND periodo=?", (alocacao_id, periodo)
            )
            if not _conn.execute(
                "SELECT 1 FROM alocacao_mes WHERE alocacao_id=? LIMIT 1", (alocacao_id,)
            ).fetchone():
                _conn.execute("DELETE FROM alocacao WHERE alocacao_id=?", (alocacao_id,))
        _touch_projeto(row["projeto_id"])
        _conn.commit()
        return {"alocacao_id": alocacao_id, "periodo": periodo, "horas": horas, "estado": _estado()}


# -- pessoa (cadastro de capacidade) ------------------------------------
@app.put("/api/pessoa/{matricula}")
def editar_pessoa(matricula: str, payload: dict = Body(...)):
    with _lock:
        matricula = matricula.strip()
        row = _conn.execute("SELECT 1 FROM pessoa WHERE matricula=?", (matricula,)).fetchone()
        if row is None:
            _conn.execute(
                "INSERT INTO pessoa (matricula, nome) VALUES (?,?)",
                (matricula, payload.get("nome") or matricula),
            )
        sets, args = [], []
        if "nome" in payload and payload["nome"]:
            sets.append("nome=?"); args.append(payload["nome"].strip())
        if "carga_diaria" in payload:
            cd = payload["carga_diaria"]
            sets.append("carga_diaria=?"); args.append(cd)
            if payload.get("capacidade_mensal") in (None, ""):
                sets.append("capacidade_mensal=?")
                args.append(round(float(cd) * 22) if cd not in (None, "") else None)
        if payload.get("capacidade_mensal") not in (None, ""):
            sets.append("capacidade_mensal=?"); args.append(int(payload["capacidade_mensal"]))
        if "ativo" in payload:
            sets.append("ativo=?"); args.append(1 if payload["ativo"] else 0)
        if sets:
            args.append(matricula)
            _conn.execute(f"UPDATE pessoa SET {', '.join(sets)} WHERE matricula=?", args)
        _conn.commit()
        return {"estado": _estado()}


# -- cadastros (tabelas projeto / pessoa / catalogo) -------------------
from . import cadastros as _cadastros  # noqa: E402

_cadastros.bind(_conn, _lock)
app.include_router(_cadastros.router)


# -- static frontend ----------------------------------------------------
if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")

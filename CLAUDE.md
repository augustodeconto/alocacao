# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Local single-user web app for **monthly researcher-allocation planning**. The internal
**SQLite database is the source of truth**; `.xlsx` files are import/export interchange only.
The user edits, month by month, how many hours each person is allocated to each project
(subdivided by `tipo_alocacao` — the project's cost bucket / "equipe"), across two
synchronized tree grids (by project, by resource).

`docs/ESPECIFICACAO.md` is a **living spec** — the user requires it to be kept in sync as
development proceeds, including its "Histórico de mudanças" section. `docs/VERSIONAMENTO.md`
is the agreed (not yet implemented) plan to evolve the versioning into real Git-style
branches + merge.

## Commands

```bash
./run.sh                    # bootstraps .venv if missing, runs uvicorn on 0.0.0.0:8731
HOST=127.0.0.1 ./run.sh     # bind to localhost only
PORT=9000 ./run.sh          # override port
```
`run.sh` uses `.venv/bin/python -m uvicorn` (not the console script) so it survives the
project folder being renamed. `--reload` watches both `backend/` and `frontend/`.

```bash
cd backend && python -m pytest -q                       # full suite (needs .venv active, or use ../.venv/bin/python)
cd backend && python -m pytest tests/test_export.py -q  # one file
cd backend && python -m pytest tests/test_export.py::test_export_roundtrip_preserva_horas_do_mes_atual_em_diante -q
```
Tests read fixtures from `amostras/` (`conftest.py` points at `amostras/ed425bf6-20260518_Otimizeplan.xlsx`).
No linter or formatter is configured.

The DB lives at `backend/alocacao.db` (gitignored). Delete it to reset — it is recreated on
startup from `SCHEMA` + `_migrate()`. Deps: `backend/requirements.txt`.

## Architecture

### Server shape
`backend/app/main.py` holds two module globals: `_conn` (one shared `sqlite3` connection,
`check_same_thread=False`) and `_lock` (`threading.RLock`). **Every mutating endpoint runs
inside `with _lock:`** and calls `_conn.commit()`. The frontend is served as `StaticFiles`.
On startup: `db.init_db()` runs the idempotent `SCHEMA`, then `_migrate()` (idempotent
`ALTER`s + one-time data migrations gated by `PRAGMA user_version`), then
`_backfill_baseline()` guarantees every project has a baseline snapshot.

`backend/app/cadastros.py` is a separate `APIRouter` (projeto / pessoa / catalogo CRUD)
mounted into `main.py`; kept out of the main editor UI deliberately.

Frontend is vanilla ES modules, **no build step, no framework**: `main.js` (state + render),
`grid-excel.js` (Excel-like cell selection/editing/undo/paste layer), `scroll-sync.js`,
`api.js`.

### The two data planes (Git-like versioning) — central concept
- **working**: `projeto`, `pessoa`, `alocacao`, `alocacao_mes`, `projeto_periodo`. What the
  user edits.
- **`baseline_*`** (`baseline_meta/projeto/pessoa/alocacao/alocacao_mes`): the last "commit"
  per project — a full snapshot, not deltas. Captured on import of a new project and on
  every export (**export = commit**).
- `backend/app/baseline.py`: `capturar` (snapshot working → baseline), `restaurar_working`
  (discard: working := baseline), `removidas` (in baseline, gone from working → exported
  zeroed as a "remove" signal), `pessoas_alteradas` (drives the `Novos_Pesquisadores` sheet).
- All on-screen diff markers (corner triangles, "novo" band, strikethrough) and the export's
  zeroed rows are computed as **working vs baseline**.

### `alocacao` vs `alocacao_mes`
`alocacao` = the identity of one grid line: `(projeto_id, matricula, tipo_alocacao)` unique.
`alocacao_mes` = hours per month for that line, long format (`periodo` = `'YYYY-MM-01'`).
`projeto_periodo` = the project's configured month window (separate table because the window
is a property of the project, independent of where hours exist); drives which months render
un-hatched and which columns the exported `.xlsx` gets.

### Option B invariant — no zeroed allocation records
`alocacao_mes` and `baseline_alocacao_mes` only ever hold `horas > 0`. **Absence of a row
means 0.** Clearing a cell deletes that month row; zeroing every month deletes the
`alocacao` (and it then exports zeroed via `baseline.removidas()` if it was in the baseline).
Enforced in `xlsx_import._insert_alocacoes`, `bi_import._rebuild_baseline`, and the
`/api/alocacao/mes-lote` + `/api/alocacao/{id}/mes` endpoints. `PRAGMA user_version = 1`
was the one-time prune of legacy zero rows. The exported `.xlsx` is unchanged — it still
writes `0` for months with no hours.

### xlsx I/O is surgical
`xlsx_io.py` (read) and `xlsx_export.py` (write) manipulate the `.xlsx` zip/XML directly
with `zipfile` + `lxml` — **not openpyxl** (insufficient fidelity). Export writes inline
strings (`t="inlineStr"`, avoids touching `sharedStrings.xml`), drops `calcChain.xml` and
all comment parts, and leaves everything else byte-for-byte (styles, `dataValidation`
dropdowns, `Planilha4` catalogs, `dados_projeto` template rows). Excel date serials use
epoch 1899-12-30 (`serial_to_date` / `date_to_serial`).

### Three import paths (all via `POST /api/importar-upload`, routed by sheet content)
1. **Per-project `.xlsx`** (`dados_projeto` + `Alocacao` + `Planilha4`) → `xlsx_import.py` →
   updates **working only** (replaces that project's allocations; does NOT touch baseline).
   Existing project → status `updated`; new project → `imported` + `baseline.capturar`.
2. **BI extracts** → `bi_import.py`. BI is the **authority of the baseline**:
   `colabmescusto.xlsx` rebuilds `baseline_alocacao*` for all months (jan/2025→jun/2028) and
   derives `pessoa.valor_hora`; `colabs.xlsx` → `pessoa` fields; `projetos.xlsx` → `projeto`
   + `bi_projeto` (all ~123 projects). Working edits are kept; a project with empty working
   gets a copy of its rebuilt baseline. Name→matricula match is accent-stripped; ambiguous
   → prefer Ativo, then higher matricula.
3. `bi_custo` / `bi_projeto` are **raw staging tables, not versioned**.

### `aggregate.py`
`build_grade(conn)` builds the entire two-grid payload in one pass: `por_projeto` is a
3-level tree (projeto → `tipo_alocacao` group → pessoa), `por_recurso` is pessoa →
project/tipo, each with totals, `totais_base` / `totais_alterado` for diff markers, and the
divergence colors. `cor_pessoa_mes` sums a person's hours across **all** projects for a
month: red if over `capacidade_mensal` or hours land after `fim_contrato`; yellow if under.
Percent is never persisted — always `round(horas / capacidade_mensal * 100)`.

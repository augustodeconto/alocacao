"""SQLite schema and connection helper. The DB is the source of truth;
.xlsx is import/export only (see the spec)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "alocacao.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS projeto (
    projeto_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    id_projeto_externo  TEXT UNIQUE,
    nome                TEXT NOT NULL,
    empresa             TEXT,
    status              TEXT,
    id_status           INTEGER,
    matricula_gp        TEXT,
    id_filial           INTEGER DEFAULT 62,
    mes_inicio          INTEGER NOT NULL,
    ano_inicio          INTEGER NOT NULL,
    cenario1            INTEGER,
    cenario2            INTEGER,
    cenario3            INTEGER,
    arquivo_origem      TEXT,
    criado_na_ferramenta INTEGER NOT NULL DEFAULT 0,
    exportado_em        TEXT,
    alterado_em         TEXT
);

CREATE TABLE IF NOT EXISTS projeto_periodo (
    projeto_id  INTEGER NOT NULL REFERENCES projeto(projeto_id) ON DELETE CASCADE,
    periodo     TEXT NOT NULL,               -- 'YYYY-MM-01'
    ordem       INTEGER NOT NULL,            -- column position in the sheet (0-based)
    PRIMARY KEY (projeto_id, periodo)
);

CREATE TABLE IF NOT EXISTS alocacao (
    alocacao_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    projeto_id      INTEGER NOT NULL REFERENCES projeto(projeto_id) ON DELETE CASCADE,
    matricula       TEXT NOT NULL,
    tipo_alocacao   TEXT NOT NULL,
    UNIQUE (projeto_id, matricula, tipo_alocacao)
);

CREATE TABLE IF NOT EXISTS alocacao_mes (
    alocacao_id INTEGER NOT NULL REFERENCES alocacao(alocacao_id) ON DELETE CASCADE,
    periodo     TEXT NOT NULL,               -- 'YYYY-MM-01'
    horas       INTEGER NOT NULL DEFAULT 0 CHECK (horas >= 0),
    PRIMARY KEY (alocacao_id, periodo)
);

-- tabela única de pessoas (pesquisadores). Não há "pessoa_nova": a aba
-- Novos_Pesquisadores do .xlsx é gerada no export a partir do diff (pessoa
-- nova ou com campo alterado vs. baseline).
CREATE TABLE IF NOT EXISTS pessoa (
    matricula           TEXT PRIMARY KEY,
    nome                TEXT NOT NULL,
    situacao            TEXT,          -- Ativo | Planejado | Desligado
    equipe              TEXT,          -- equipe organizacional (Nome_equipe)
    area                TEXT,
    tipo_contrato       TEXT,          -- "Contrato" / "Tipo de contrato"
    inicio_contrato     TEXT,
    fim_contrato        TEXT,
    formacao            TEXT,
    id_filial           INTEGER,
    carga_diaria        REAL,
    capacidade_mensal   INTEGER,
    remuneracao         REAL,
    inicio_vigencia     TEXT,
    ativo               INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS catalogo (
    tipo   TEXT NOT NULL,      -- tipo_alocacao | status | equipe | area | contrato | ensino | ativo
    id     INTEGER,
    texto  TEXT NOT NULL,
    PRIMARY KEY (tipo, texto)
);

CREATE TABLE IF NOT EXISTS anotacao (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    projeto_id    INTEGER NOT NULL REFERENCES projeto(projeto_id) ON DELETE CASCADE,
    matricula     TEXT,
    tipo_alocacao TEXT,
    periodo       TEXT,
    texto         TEXT NOT NULL,
    autor         TEXT,
    criado_em     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS preferencias (
    chave TEXT PRIMARY KEY,
    valor TEXT
);

-- dados vindos do BI (import separado, ver bi_import.py) ------------------
CREATE TABLE IF NOT EXISTS bi_projeto (
    id_projetos  INTEGER PRIMARY KEY,
    nome_projeto TEXT,
    empresa      TEXT,
    status       TEXT,
    gestor       TEXT
);

CREATE TABLE IF NOT EXISTS bi_custo (
    id_projetos      INTEGER NOT NULL,
    matricula        TEXT,
    nome_colaborador TEXT NOT NULL,
    periodo          TEXT NOT NULL,          -- 'YYYY-MM-01'
    tipo_alocacao    TEXT,
    horas            REAL NOT NULL DEFAULT 0,
    custo            REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (id_projetos, nome_colaborador, periodo, tipo_alocacao)
);

-- linha de base = último "commit" do projeto (importação ou export confirmado).
-- O diff baseline -> working decide, no export, o que sai zerado vs. o que nem sai.
CREATE TABLE IF NOT EXISTS baseline_meta (
    projeto_id INTEGER PRIMARY KEY REFERENCES projeto(projeto_id) ON DELETE CASCADE,
    criado_em  TEXT NOT NULL,
    origem     TEXT NOT NULL            -- 'import' | 'export'
);
CREATE TABLE IF NOT EXISTS baseline_alocacao (
    projeto_id    INTEGER NOT NULL REFERENCES projeto(projeto_id) ON DELETE CASCADE,
    matricula     TEXT NOT NULL,
    tipo_alocacao TEXT NOT NULL,
    PRIMARY KEY (projeto_id, matricula, tipo_alocacao)
);
CREATE TABLE IF NOT EXISTS baseline_alocacao_mes (
    projeto_id    INTEGER NOT NULL REFERENCES projeto(projeto_id) ON DELETE CASCADE,
    matricula     TEXT NOT NULL,
    tipo_alocacao TEXT NOT NULL,
    periodo       TEXT NOT NULL,
    horas         INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (projeto_id, matricula, tipo_alocacao, periodo)
);
-- snapshot dos campos de pessoa (só quem está alocado no projeto) e do projeto
CREATE TABLE IF NOT EXISTS baseline_pessoa (
    projeto_id INTEGER NOT NULL REFERENCES projeto(projeto_id) ON DELETE CASCADE,
    matricula  TEXT NOT NULL,
    nome TEXT, situacao TEXT, equipe TEXT, area TEXT, tipo_contrato TEXT,
    inicio_contrato TEXT, fim_contrato TEXT, formacao TEXT, id_filial INTEGER,
    carga_diaria REAL, capacidade_mensal INTEGER, remuneracao REAL, inicio_vigencia TEXT,
    PRIMARY KEY (projeto_id, matricula)
);
CREATE TABLE IF NOT EXISTS baseline_projeto (
    projeto_id INTEGER PRIMARY KEY REFERENCES projeto(projeto_id) ON DELETE CASCADE,
    nome TEXT, empresa TEXT, status TEXT, id_status INTEGER, matricula_gp TEXT,
    id_filial INTEGER, mes_inicio INTEGER, ano_inicio INTEGER,
    cenario1 INTEGER, cenario2 INTEGER, cenario3 INTEGER
);

CREATE INDEX IF NOT EXISTS ix_alocacao_projeto ON alocacao(projeto_id);
CREATE INDEX IF NOT EXISTS ix_alocacao_matricula ON alocacao(matricula);
CREATE INDEX IF NOT EXISTS ix_alocacao_mes_periodo ON alocacao_mes(periodo);
CREATE INDEX IF NOT EXISTS ix_bi_custo_proj ON bi_custo(id_projetos, periodo);
CREATE INDEX IF NOT EXISTS ix_bi_custo_matricula ON bi_custo(matricula, periodo);
"""

# colunas acrescentadas a `pessoa` depois da v1 (migração idempotente)
_PESSOA_EXTRA = {
    "situacao": "TEXT", "equipe": "TEXT", "area": "TEXT", "tipo_contrato": "TEXT",
    "inicio_contrato": "TEXT", "fim_contrato": "TEXT", "formacao": "TEXT",
    "id_filial": "INTEGER", "remuneracao": "REAL", "inicio_vigencia": "TEXT",
}

# pessoa_nova -> pessoa (campo por campo, sem sobrescrever o que já existe)
_PN_TO_PESSOA = {
    "nome": "nome", "ativo": "situacao", "equipe": "equipe", "area": "area",
    "contrato": "tipo_contrato", "inicio_contrato": "inicio_contrato",
    "fim_contrato": "fim_contrato", "formacao": "formacao", "id_filial": "id_filial",
    "carga_diaria": "carga_diaria", "remuneracao": "remuneracao",
    "inicio_vigencia": "inicio_vigencia",
}


def _migrate(conn: sqlite3.Connection) -> None:
    have = {r["name"] for r in conn.execute("PRAGMA table_info(pessoa)")}
    for col, typ in {**_PESSOA_EXTRA, "valor_hora": "REAL"}.items():
        if col not in have:
            conn.execute(f"ALTER TABLE pessoa ADD COLUMN {col} {typ}")

    have_pr = {r["name"] for r in conn.execute("PRAGMA table_info(projeto)")}
    if "gestor_projetos" not in have_pr:
        conn.execute("ALTER TABLE projeto ADD COLUMN gestor_projetos TEXT")

    have_bp = {r["name"] for r in conn.execute("PRAGMA table_info(bi_projeto)")}
    for col in ("empresa", "status", "gestor"):
        if col not in have_bp:
            conn.execute(f"ALTER TABLE bi_projeto ADD COLUMN {col} TEXT")

    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "pessoa_nova" in tables:
        for r in conn.execute("SELECT * FROM pessoa_nova"):
            conn.execute(
                "INSERT OR IGNORE INTO pessoa (matricula, nome) VALUES (?,?)",
                (r["matricula"], r["nome"] or r["matricula"]),
            )
            sets, args = [], []
            for src, dst in _PN_TO_PESSOA.items():
                val = r[src] if src in r.keys() else None
                if val not in (None, ""):
                    sets.append(f"{dst}=COALESCE({dst}, ?)")
                    args.append(val)
            if sets:
                args.append(r["matricula"])
                conn.execute(f"UPDATE pessoa SET {', '.join(sets)} WHERE matricula=?", args)
        conn.execute("DROP TABLE pessoa_nova")

    # v1: o banco não guarda mais registros de alocação zerados.
    # Ausência de linha em alocacao_mes == 0 horas. Poda o que já existe.
    if conn.execute("PRAGMA user_version").fetchone()[0] < 1:
        conn.execute("DELETE FROM alocacao_mes WHERE horas <= 0")
        conn.execute("DELETE FROM baseline_alocacao_mes WHERE horas <= 0")
        conn.execute(
            """DELETE FROM alocacao
               WHERE NOT EXISTS (SELECT 1 FROM alocacao_mes m
                                 WHERE m.alocacao_id = alocacao.alocacao_id)"""
        )
        conn.execute(
            """DELETE FROM baseline_alocacao
               WHERE NOT EXISTS (SELECT 1 FROM baseline_alocacao_mes m
                                 WHERE m.projeto_id = baseline_alocacao.projeto_id
                                   AND m.matricula = baseline_alocacao.matricula
                                   AND m.tipo_alocacao = baseline_alocacao.tipo_alocacao)"""
        )
        conn.execute("PRAGMA user_version = 1")
    conn.commit()


def connect(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate(conn)
    return conn

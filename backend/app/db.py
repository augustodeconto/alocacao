"""SQLite schema and connection helper. The DB is the source of truth;
.xlsx is import/export only (see the spec)."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# perfil: `ALOCACAO_DB` (setado pelo run.sh) escolhe prod vs dev; senão, o de sempre.
_ENV_DB = os.environ.get("ALOCACAO_DB")
DEFAULT_DB_PATH = (
    Path(_ENV_DB).expanduser() if _ENV_DB
    else Path(__file__).resolve().parent.parent / "alocacao.db"
)

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

-- ===== versionamento estilo Git (ver docs/VERSIONAMENTO.md) =====
-- grafo de commits + branches + ponteiro do checkout
CREATE TABLE IF NOT EXISTS commit_ (
    commit_id       INTEGER PRIMARY KEY,
    parent_id       INTEGER REFERENCES commit_(commit_id),
    merge_parent_id INTEGER REFERENCES commit_(commit_id),   -- 2º pai, fase 2
    autor           TEXT,
    mensagem        TEXT,
    criado_em       TEXT NOT NULL,
    origem          TEXT NOT NULL       -- manual | import | export | bi | merge | seed
);
CREATE TABLE IF NOT EXISTS ref_ (
    nome      TEXT PRIMARY KEY,         -- 'main', 'cenario-...'
    commit_id INTEGER NOT NULL REFERENCES commit_(commit_id),
    criado_em TEXT,
    nota      TEXT
);
CREATE TABLE IF NOT EXISTS head_ (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    ref_nome       TEXT NOT NULL REFERENCES ref_(nome),
    base_commit_id INTEGER NOT NULL REFERENCES commit_(commit_id)
);
-- delta de cada commit vs. o 1º pai; chave natural; deleted=1 = lápide
CREATE TABLE IF NOT EXISTS chg_projeto (
    commit_id INTEGER NOT NULL REFERENCES commit_(commit_id),
    projeto_id INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
    nome TEXT, empresa TEXT, status TEXT, id_status INTEGER, matricula_gp TEXT,
    id_filial INTEGER,
    cenario1 INTEGER, cenario2 INTEGER, cenario3 INTEGER, gestor_projetos TEXT,
    PRIMARY KEY (commit_id, projeto_id)
);
CREATE TABLE IF NOT EXISTS chg_pessoa (
    commit_id INTEGER NOT NULL REFERENCES commit_(commit_id),
    matricula TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
    nome TEXT, situacao TEXT, equipe TEXT, area TEXT, tipo_contrato TEXT,
    inicio_contrato TEXT, fim_contrato TEXT, formacao TEXT, id_filial INTEGER,
    carga_diaria REAL, capacidade_mensal INTEGER, remuneracao REAL,
    inicio_vigencia TEXT, valor_hora REAL,
    PRIMARY KEY (commit_id, matricula)
);
CREATE TABLE IF NOT EXISTS chg_alocacao (
    commit_id INTEGER NOT NULL REFERENCES commit_(commit_id),
    projeto_id INTEGER NOT NULL, matricula TEXT NOT NULL, tipo_alocacao TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (commit_id, projeto_id, matricula, tipo_alocacao)
);
CREATE TABLE IF NOT EXISTS chg_alocacao_mes (
    commit_id INTEGER NOT NULL REFERENCES commit_(commit_id),
    projeto_id INTEGER NOT NULL, matricula TEXT NOT NULL, tipo_alocacao TEXT NOT NULL,
    periodo TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, horas INTEGER,
    PRIMARY KEY (commit_id, projeto_id, matricula, tipo_alocacao, periodo)
);
CREATE TABLE IF NOT EXISTS chg_projeto_periodo (
    commit_id INTEGER NOT NULL REFERENCES commit_(commit_id),
    projeto_id INTEGER NOT NULL, periodo TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0, ordem INTEGER,
    PRIMARY KEY (commit_id, projeto_id, periodo)
);
-- cache do estado materializado do HEAD.
--   alocação: baseline_alocacao / baseline_alocacao_mes (schema já global)
--   campos:   base_projeto / base_pessoa / base_projeto_periodo
-- O diff cache -> working é o que a tela pinta e o que um `commit` grava.
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
CREATE TABLE IF NOT EXISTS base_projeto (
    projeto_id INTEGER PRIMARY KEY,
    nome TEXT, empresa TEXT, status TEXT, id_status INTEGER, matricula_gp TEXT,
    id_filial INTEGER,
    cenario1 INTEGER, cenario2 INTEGER, cenario3 INTEGER, gestor_projetos TEXT
);
CREATE TABLE IF NOT EXISTS base_pessoa (
    matricula TEXT PRIMARY KEY,
    nome TEXT, situacao TEXT, equipe TEXT, area TEXT, tipo_contrato TEXT,
    inicio_contrato TEXT, fim_contrato TEXT, formacao TEXT, id_filial INTEGER,
    carga_diaria REAL, capacidade_mensal INTEGER, remuneracao REAL,
    inicio_vigencia TEXT, valor_hora REAL
);
CREATE TABLE IF NOT EXISTS base_projeto_periodo (
    projeto_id INTEGER NOT NULL, periodo TEXT NOT NULL, ordem INTEGER,
    PRIMARY KEY (projeto_id, periodo)
);
-- merge em andamento (fase 2): equivale ao MERGE_HEAD do git
CREATE TABLE IF NOT EXISTS merge_estado (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    origem        TEXT NOT NULL,
    theirs_commit INTEGER NOT NULL,
    base_commit   INTEGER NOT NULL,
    criado_em     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS merge_conflito (
    id          INTEGER PRIMARY KEY,
    dominio     TEXT NOT NULL,        -- 'mes' | 'projeto' | 'pessoa'
    chave       TEXT NOT NULL,        -- json da chave natural
    rotulo      TEXT,                 -- legível p/ a tela
    base_val    TEXT, our_val TEXT, their_val TEXT,   -- json
    resolvido   INTEGER NOT NULL DEFAULT 0,
    valor_final TEXT
);
-- importação do BI em duas fases: monta + relatório (não commita) -> confirma
-- (ver bi_import.preparar_importacao / confirmar_importacao). Singleton.
CREATE TABLE IF NOT EXISTS bi_pendente (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    mensagem    TEXT NOT NULL,
    e_bi_json   TEXT NOT NULL,
    resumo_json TEXT NOT NULL,
    criado_em   TEXT NOT NULL
);
-- multiusuário (ver docs/COLABORACAO.md): identidade + rascunho por autor/branch
CREATE TABLE IF NOT EXISTS usuario (
    nome            TEXT PRIMARY KEY,
    sempre_revisar  INTEGER NOT NULL DEFAULT 0,   -- promove o nível 2 do "salvar" ao digest
    criado_em       TEXT
);
CREATE TABLE IF NOT EXISTS rascunho (
    rascunho_id    INTEGER PRIMARY KEY,
    autor          TEXT NOT NULL,
    ref_nome       TEXT NOT NULL,
    base_commit_id INTEGER NOT NULL,   -- topo da branch quando o rascunho nasceu
    nome           TEXT,
    criado_em      TEXT NOT NULL,
    atualizado_em  TEXT NOT NULL,
    edicoes        TEXT NOT NULL,      -- JSON: {mes, aloc_add, aloc_del, pessoa, projeto, janela}
    UNIQUE (autor, ref_nome)
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

    # mes_inicio / ano_inicio saíram: eram só o parâmetro da 1ª coluna da planilha,
    # não um atributo do projeto. A janela de meses vive em projeto_periodo.
    for tbl in ("projeto", "chg_projeto", "base_projeto"):
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({tbl})")}
        for c in ("mes_inicio", "ano_inicio"):
            if c in cols:
                conn.execute(f"ALTER TABLE {tbl} DROP COLUMN {c}")

    # projeto criado na ferramenta com matrícula GP mas sem nome do gestor:
    # resolve pela tabela pessoa (o filtro de GP na grade usa o nome).
    conn.execute(
        """UPDATE projeto SET gestor_projetos = (
               SELECT nome FROM pessoa WHERE pessoa.matricula = projeto.matricula_gp)
           WHERE gestor_projetos IS NULL AND matricula_gp IS NOT NULL
             AND EXISTS (SELECT 1 FROM pessoa WHERE pessoa.matricula = projeto.matricula_gp)"""
    )

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

    # v2: versionamento estilo Git. Cria o commit raiz + branch main + HEAD a
    # partir do estado atual (baseline_alocacao* + campos do working). As tabelas
    # baseline_pessoa/projeto/meta (snapshot por projeto) foram substituídas por
    # base_pessoa/base_projeto/base_projeto_periodo (globais) + o grafo de commits.
    if conn.execute("PRAGMA user_version").fetchone()[0] < 2:
        from . import versao
        versao.garantir_inicializado(conn)
        for t in ("baseline_meta", "baseline_pessoa", "baseline_projeto"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.execute("PRAGMA user_version = 2")
    conn.commit()
    # o branch `BI` (B-lite) é criado preguiçosamente na 1ª importação do BI
    # (versao.garantir_bi), apontando para o topo atual da `main`.


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

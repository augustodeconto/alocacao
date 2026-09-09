# Plano — Versionamento estilo Git (branches + merge)

> **Status:** Fase 1 **implementada** (`PRAGMA user_version = 2`, módulo `app/versao.py`).
> Fase 2 (merge) segue planejada. Resumo do que está no código abaixo; o resto do
> documento é a proposta original.

## Implementado (Fase 1)

Tabelas: `commit_`, `ref_`, `head_`, `chg_projeto|pessoa|alocacao|alocacao_mes|projeto_periodo`.
Cache do HEAD: `baseline_alocacao` / `baseline_alocacao_mes` (mantidas) + `base_projeto` /
`base_pessoa` / `base_projeto_periodo` (novas, globais). `baseline_meta` / `baseline_pessoa`
/ `baseline_projeto` foram **removidas** (eram snapshot por projeto — incoerentes para
commit global).

Módulo `app/versao.py`:
`commit(msg)` · `checkout(ref)` · `descartar(projeto_id=None)` · `branch(nome, a_partir, trocar)`
· `deletar_branch` · `log(ref, limite)` · `materializar(commit_id)` · `estado_repo` ·
`removidas` / `pessoas_alteradas` / `restaurar_alocacao` (usadas pelo export) ·
`commit_transicao_cache` (usada pelo rebuild do BI).

Endpoints: `GET /api/versao/estado`, `GET /api/versao/log`, `POST /api/versao/commit`,
`POST /api/versao/branch`, `POST /api/versao/checkout`, `DELETE /api/versao/branch/{nome}`.
`/api/estado` passou a incluir um bloco `versao`. `/api/descartar-tudo` = `reset --hard`;
`/api/projetos/{id}/descartar` = descarte parcial (alocação/janela do projeto).
`/api/projetos/{id}/marcar-baseline` **removido** (409 apontando para `/api/versao/commit`).

Desvios do plano original:
- **Import e export não commitam mais.** As mudanças ficam pendentes até um
  `POST /api/versao/commit` explícito (antes: import de projeto novo e export = commit).
- **`bi_import`**: o rebuild move o cache e registra **um** commit `origem='bi'` via
  `commit_transicao_cache` (o working não é tocado; edições pendentes passam a diferir do
  novo HEAD).
- **`projeto` / `pessoa` no `checkout`**: upsert (nunca apagam linha), e só no descarte
  global — evita brigar com FK/cascade. `alocacao` / `alocacao_mes` / `projeto_periodo`:
  substituição total no escopo.
- `pessoa.ativo` continua **fora** do versionamento (vem do BI).

## Objetivo

Ter o plano de alocação evoluindo como um repositório Git:

- **`main`** = o plano acordado.
- **commits** = snapshots do plano inteiro, com pai, autor, mensagem.
- **branches** = linhas paralelas de trabalho (cenários).
- **merge** = trazer um cenário de volta pro `main`, combinando o que mudou dos dois lados.
- **histórico** = navegar os commits de um ref.

O histórico importa, mas o alvo real é **branch + merge**.

---

## Decisões fechadas

| # | Decisão |
|---|---|
| 1 | **Commit é global** — um snapshot do plano inteiro (todos os projetos, pessoas, alocações). Não há mais "baseline por projeto". |
| 2 | **Armazenamento por delta** — cada commit grava só as linhas que diferem do 1º pai. Snapshot inteiro por commit seria ~50k linhas/commit; delta é ~dezenas–centenas. |
| 3 | **`pessoa` é versionada**, incluindo `valor_hora`. Custo/hora é atributo durável da pessoa; o BI só o **calcula**. Se a pessoa não tem `valor_hora`, o BI preenche. |
| 4 | **`bi_custo` / `bi_projeto` ficam fora do versionamento** — staging cru de import. Um import de BI vira edições nas tabelas working (inclusive `pessoa.valor_hora`) + um commit `origem='bi'`. |
| 5 | **`baseline_*` são mantidas**, reinterpretadas como **cache do estado materializado do HEAD** (caminho de menor esforço: tabelas e lógica de diff já existem). |
| 6 | **Entrega em duas fases.** Fase 1: `branch` + `commit` + `checkout` + `log` + `diff`. Fase 2: `merge` 3‑way com resolução de conflito na grade. O **esquema já nasce pronto pra fase 2** (2 pais no commit, `chg_*` por chave natural). |
| 7 | Ficam **fora do versionamento**: `anotacao` (comentários são sempre live), `catalogo`, `preferencias`, `bi_*`. |

---

## Princípio que sustenta a mudança

As 5 tabelas **working** (`projeto`, `pessoa`, `alocacao`, `alocacao_mes`, `projeto_periodo`)
continuam sendo **a materialização do checkout atual** — HEAD + edições não commitadas.

Todo o editor (grades, `mes-lote`, undo, criar/remover alocação, import de projeto, export)
**continua igual**. As tabelas de versão só são lidas em `commit`, `checkout`, `merge`, `log`
e `diff` — nunca no caminho de edição. É isso que torna a mudança incremental em vez de um
rewrite.

---

## Esquema novo

### Grafo de commits + refs

```sql
CREATE TABLE commit_ (
  commit_id       INTEGER PRIMARY KEY,
  parent_id       INTEGER REFERENCES commit_(commit_id),   -- 1º pai (NULL só na raiz)
  merge_parent_id INTEGER REFERENCES commit_(commit_id),   -- 2º pai, só em merge (fase 2)
  autor           TEXT,
  mensagem        TEXT,
  criado_em       TEXT NOT NULL,                           -- ISO, timespec=seconds
  origem          TEXT NOT NULL                            -- manual | import | export | bi | merge | snapshot
);

CREATE TABLE ref_ (
  nome        TEXT PRIMARY KEY,        -- 'main', 'cenario-corte-2027', ...
  commit_id   INTEGER NOT NULL REFERENCES commit_(commit_id),
  criado_em   TEXT,
  nota        TEXT
);

CREATE TABLE head_ (                    -- singleton: o que está check-outado
  id             INTEGER PRIMARY KEY CHECK (id = 1),
  ref_nome       TEXT NOT NULL REFERENCES ref_(nome),
  base_commit_id INTEGER NOT NULL REFERENCES commit_(commit_id)
);
```

`head_.base_commit_id` = o commit a partir do qual as tabelas working foram materializadas.
Normalmente é igual a `ref_(head_.ref_nome).commit_id`; passa a divergir só se o ref for
movido por fora (não previsto no v1).

### Conteúdo dos commits: tabelas `chg_*` (append-only, delta vs 1º pai)

Regra: um commit grava uma linha em `chg_X` **somente** para cada chave natural cujo valor
mudou em relação ao estado do 1º pai. `deleted = 1` é lápide (a linha deixou de existir).

```sql
CREATE TABLE chg_projeto (
  commit_id INTEGER NOT NULL REFERENCES commit_(commit_id),
  projeto_id INTEGER NOT NULL,                 -- chave NATURAL (estável entre branches)
  deleted   INTEGER NOT NULL DEFAULT 0,
  nome TEXT, empresa TEXT, status TEXT, id_status INTEGER, matricula_gp TEXT,
  id_filial INTEGER, mes_inicio INTEGER, ano_inicio INTEGER,
  cenario1 INTEGER, cenario2 INTEGER, cenario3 INTEGER, gestor_projetos TEXT,
  PRIMARY KEY (commit_id, projeto_id)
);

CREATE TABLE chg_pessoa (
  commit_id INTEGER NOT NULL REFERENCES commit_(commit_id),
  matricula TEXT NOT NULL,
  deleted   INTEGER NOT NULL DEFAULT 0,
  nome TEXT, situacao TEXT, equipe TEXT, area TEXT, tipo_contrato TEXT,
  inicio_contrato TEXT, fim_contrato TEXT, formacao TEXT, id_filial INTEGER,
  carga_diaria REAL, capacidade_mensal INTEGER, remuneracao REAL,
  inicio_vigencia TEXT, valor_hora REAL,
  PRIMARY KEY (commit_id, matricula)
);

CREATE TABLE chg_alocacao (
  commit_id INTEGER NOT NULL REFERENCES commit_(commit_id),
  projeto_id INTEGER NOT NULL, matricula TEXT NOT NULL, tipo_alocacao TEXT NOT NULL,
  deleted   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (commit_id, projeto_id, matricula, tipo_alocacao)
);

CREATE TABLE chg_alocacao_mes (
  commit_id INTEGER NOT NULL REFERENCES commit_(commit_id),
  projeto_id INTEGER NOT NULL, matricula TEXT NOT NULL, tipo_alocacao TEXT NOT NULL,
  periodo TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0,          -- deleted=1  ==  "voltou a 0" (invariante Opção B)
  horas INTEGER,
  PRIMARY KEY (commit_id, projeto_id, matricula, tipo_alocacao, periodo)
);

CREATE TABLE chg_projeto_periodo (
  commit_id INTEGER NOT NULL REFERENCES commit_(commit_id),
  projeto_id INTEGER NOT NULL, periodo TEXT NOT NULL,
  deleted INTEGER NOT NULL DEFAULT 0, ordem INTEGER,
  PRIMARY KEY (commit_id, projeto_id, periodo)
);
```

**Crítico:** `chg_alocacao*` usa a **chave natural** (`projeto_id + matricula + tipo_alocacao
[+ periodo]`), nunca `alocacao_id`. O `alocacao_id` é um surrogate local; ao materializar um
checkout os `alocacao_id` são **regerados**.

### `baseline_*` → cache do HEAD

As tabelas `baseline_meta / baseline_projeto / baseline_pessoa / baseline_alocacao /
baseline_alocacao_mes` continuam existindo com a mesma estrutura, mas passam a significar
**"estado inteiro já materializado de `head_.base_commit_id`"**. `commit` atualiza esse cache
in‑place; `checkout` reconstrói. Evita caminhar o grafo a cada `diff` de tela.

---

## Como cada operação funciona

### `commit(mensagem, origem='manual')`
1. Calcula o diff **working × cache‑base** (reaproveita a lógica de `baseline.capturar`,
   `baseline.removidas`, `baseline.pessoas_alteradas`):
   - `projeto` / `pessoa`: comparação campo a campo (`PROJETO_COLS` / `PESSOA_COLS`);
   - `alocacao`: linha no working e não no cache = adicionada; no cache e não no working =
     lápide;
   - `alocacao_mes`: por `(chave natural, periodo)`, `horas` working ≠ cache → linha em
     `chg` (`deleted=1` se ausente no working, senão `horas = working`);
   - `projeto_periodo`: mesmo padrão.
2. Grava as linhas do diff em `chg_*` com `commit_id` novo, `parent_id = head_.base_commit_id`.
3. Se o diff for vazio → erro "nada para commitar".
4. Avança `ref_(head_.ref_nome).commit_id` e `head_.base_commit_id` para o commit novo.
5. Atualiza o cache‑base (aplica o mesmo diff nas `baseline_*`).
6. **Working não muda.**

### `branch(nome, a_partir_de = ref atual)`
`INSERT INTO ref_` apontando pro commit indicado. Opcional: já fazer `checkout`.

### `checkout(ref)`
1. Exige working limpo (diff working × cache‑base vazio). Senão: erro pedindo `commit` ou
   `descartar` antes. (Stash fica pra depois.)
2. **Materializa** o estado do commit alvo:
   - caminha `commit_` do alvo → `parent_id` → … → raiz;
   - para cada `chg_*` encontrada, se a chave natural ainda **não foi vista**, registra
     (valor ou lápide) — *mais recente vence*;
   - ao fim: chaves com lápide ou não vistas = ausentes; demais = presentes com o valor.
3. Escreve o resultado nas tabelas **working** (regenerando `alocacao_id`) **e** nas
   `baseline_*` (cache).
4. `head_` ← `ref_nome = ref`, `base_commit_id = ref.commit_id`.
5. Frontend recarrega `/api/estado`; pilha de undo é limpa.

*(Commits de merge, fase 2: a `chg_*` do merge guarda o diff do resultado vs o 1º pai, então
a caminhada por 1º pai continua correta.)*

### `descartar()` — equivale a `git reset --hard`
Re‑materializa working de `head_.base_commit_id` (passo 2–3 do checkout, sem trocar de ref).
É o atual "↺ Descartar". O "descartar só este projeto" continua como conveniência (materializa
só as chaves daquele `projeto_id`).

### `log(ref, limite)`
Caminha `commit_` a partir de `ref_(ref).commit_id` por `parent_id`.

### `diff(commit_a, commit_b)`
Materializa os dois estados e compara. É o mesmo cálculo que hoje pinta os triângulos —
generalizar `aggregate` / `baseline` pra receber dois commits em vez de "working vs baseline".
Caso de tela: `diff(head_.base_commit_id, WORKING)`.

### `merge(branch_origem)` — **fase 2**
1. `merge_base` = ancestral comum mais próximo (LCA) dos dois tips no DAG (BFS dos dois lados).
2. Materializa BASE, OURS (branch atual), THEIRS (`branch_origem`).
3. Por chave natural, comparação 3‑way:
   - só um lado ≠ BASE → pega esse lado;
   - os dois lados iguais entre si → pega;
   - os dois lados ≠ BASE e ≠ entre si → **conflito**.
4. Aplica a união sem conflito no working; grava conflitos em `merge_conflito`.
5. UI resolve célula a célula (grade mostra BASE / OURS / THEIRS pro mês em conflito).
6. Resolvido tudo → `commit` com `parent_id = OURS`, `merge_parent_id = THEIRS`,
   `origem='merge'`.

```sql
-- fase 2
CREATE TABLE merge_conflito (
  id           INTEGER PRIMARY KEY,
  entidade     TEXT NOT NULL,     -- 'alocacao_mes' | 'pessoa' | 'projeto' | ...
  chave        TEXT NOT NULL,     -- json da chave natural
  campo        TEXT,              -- p/ pessoa/projeto: qual coluna
  base_val     TEXT, our_val TEXT, their_val TEXT,
  resolvido    INTEGER NOT NULL DEFAULT 0,
  valor_final  TEXT
);
```

### Otimização opcional (adiar até doer)
Commits‑**snapshot** (`origem='snapshot'`) a cada N commits guardam o estado inteiro, então a
caminhada de materialização para no snapshot mais próximo em vez de ir até a raiz.

---

## Migração (quando implementar)

1. Cria `commit_ #1` (raiz, `parent_id=NULL`), `origem` = valor predominante em
   `baseline_meta.origem`. Suas `chg_*` = **todo** o conteúdo atual das `baseline_*`
   (delta contra o nada = tudo "adicionado").
2. `ref_('main') → #1`. `head_` = `{ ref_nome:'main', base_commit_id:1 }`.
3. As edições working de hoje (diff vs baseline) permanecem como **mudanças não commitadas em
   `main`** — a primeira ação do usuário é `commit` ou `descartar`.
4. `baseline_*` já são a materialização de `#1` → viram o cache sem transformação.
5. `PRAGMA user_version = 2`.
6. Remapear no código:
   - `baseline.capturar()` → `versao.commit()`
   - `baseline.restaurar_working()` → `versao.descartar()` / `versao.checkout()`
   - `POST /api/descartar-tudo` → `POST /api/versao/descartar`
   - `POST /api/projetos/{id}/marcar-baseline` → **removido** (commit é global) ou vira
     atalho de "commit agora" — decidir na implementação.
   - `POST /api/projetos/{id}/descartar` → mantém (materializa só aquele projeto).

---

## Superfície de API (proposta)

### Fase 1
```
GET    /api/versao/estado           -> { branch, head_commit, sujo: bool, refs: [...] }
GET    /api/versao/log?ref=&limite=
GET    /api/versao/diff?de=&para=    (para=WORKING é o default de tela)
POST   /api/versao/commit           { mensagem }
POST   /api/versao/branch           { nome, a_partir_de? }
POST   /api/versao/checkout         { ref }
POST   /api/versao/descartar        (reset --hard no HEAD)
DELETE /api/versao/branch/{nome}
```

### Fase 2
```
POST   /api/versao/merge            { origem }           -> { conflitos: [...] } | { commit_id }
GET    /api/versao/conflitos
POST   /api/versao/conflito/resolver { id, valor_final }
POST   /api/versao/merge/concluir   { mensagem }
```

---

## Superfície de frontend (proposta)

- **Toolbar:** seletor de branch (troca = `checkout`), botão **"Commit…"** (desabilitado se
  working limpo) com campo de mensagem, indicador "N alterações não commitadas".
- **Painel "Histórico":** lista de commits do branch atual (autor, mensagem, data, origem);
  clicar num commit abre `diff` contra o HEAD em modo leitura.
- **"↺ Descartar"** continua, agora = `reset --hard` no HEAD.
- **Fase 2:** botão "Merge…" (escolhe branch origem) e, havendo conflito, a grade entra em
  modo resolução (3 colunas BASE / ATUAL / OUTuro por célula em conflito).

---

## Riscos / partes difíceis

1. **Tela de resolução de conflito (fase 2)** — maior esforço isolado. A grade ajuda mas é
   tela nova.
2. **LCA no grafo (fase 2)** — precisa estar correto pra merge não duplicar/perder mudança.
   Testável isoladamente com grafos pequenos montados à mão.
3. **Instabilidade do `alocacao_id`** — pilha de undo e `ALOC_INFO` no front referenciam
   `alocacao_id`; ambos são descartados/recarregados no `checkout`. `anotacao` já usa chave
   natural (`projeto_id + matricula + tipo_alocacao + periodo`), então sobrevive.
4. **Custo do `checkout`** — caminhada até a raiz é O(commits × mudanças). Trivial em centenas
   de commits; commits‑snapshot resolvem se crescer.
5. **`projeto_id` como chave natural** — hoje é `AUTOINCREMENT` local. Continua sendo a chave
   de versionamento (não some entre branches porque projetos não são recriados). Projeto
   criado numa branch e não mergeado no `main` simplesmente não existe no `main` — ok.

---

## Fora de escopo do v1 de versionamento

- Stash (checkout com working sujo).
- Rebase / cherry‑pick / squash.
- Merge de mais de duas branches de uma vez.
- Reescrita de histórico.
- Versionar `anotacao`, `catalogo`, `preferencias`, `bi_*`.
- Hash de conteúdo como `commit_id` (integer serve; hash fica como evolução futura).

---

## Ordem de implementação sugerida (fase 1)

1. Schema: `commit_`, `ref_`, `head_`, `chg_*`; migração `user_version = 2` + seed do `#1`.
2. `versao.materializar(commit_id)` + testes (grafo linear).
3. `versao.commit()` reusando o diff de `baseline` + testes (commit → log → checkout volta).
4. `versao.checkout()` / `versao.descartar()` + testes (working limpo obrigatório).
5. `versao.branch()` / `DELETE branch` + `versao.log()` + `versao.diff()`.
6. Endpoints `/api/versao/*` e remapeamento dos endpoints antigos de baseline.
7. Frontend: seletor de branch, botão Commit, painel Histórico.
8. Migrar o texto relevante deste arquivo para `ESPECIFICACAO.md`.

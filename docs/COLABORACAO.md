# Multiusuário (rascunhos + identidade)

> **Status:** **backend implementado**; frontend pendente (edição continua no modelo
> single-user de hoje até a migração). Decisões fechadas na conversa de 2026‑09‑09,
> continuação de `VERSIONAMENTO.md`.
>
> **Feito:** tabelas `usuario` / `rascunho`; `versao.commitar_rascunho` com os 4 níveis;
> endpoints `GET/POST /api/usuarios`, `GET/PUT/DELETE /api/rascunho`,
> `POST /api/rascunho/commitar` (aceita `autor` no corpo ou header `X-Autor`). Aditivo —
> os endpoints de edição de célula, `head_` e as tabelas `working` **continuam ativos**.
>
> **Pendente:** frontend (rascunho no cliente + autossave + seletor de branch/rascunho),
> aposentar os endpoints de edição de célula, remover `head_`, revisar export/cache
> (itens 5–7 da ordem de implementação).
>
> **2026-09-11 — identidade/papel de acesso decididos** (ver seção própria abaixo),
> **nada implementado ainda**: substitui a tabela `usuario` daqui por `pessoa` + campo
> `pessoa.papel` (fora do versionamento); matriz de 4 papéis; resolve o item 3 de "Em
> aberto" (Corrente protegida = sim).

## Problema

O versionamento (Fases 1 e 2) pressupõe **um usuário, uma superfície**:

- `head_` é **singleton** — existe um checkout para o servidor inteiro.
- As tabelas `working` são uma materialização só — quem abrir o app edita as **mesmas** linhas.
- `merge_estado` é singleton.
- `commit_.autor` nunca é preenchido de verdade.

Dois usuários hoje = mesmo rascunho, mesma branch, "last write wins", sem atribuição.

## Decisões fechadas

| # | Decisão |
|---|---|
| 1 | **Sem cópia local.** Um servidor, um banco. (D3 — clones distribuídos — está descartado.) |
| 2 | **Identidade é obrigatória**, mas sem login (ferramenta de rede interna). |
| 3 | **Larga o vocabulário de `checkout`.** O modelo é: você *trabalha a partir de um commit*, *salva como um commit*, e *referencia commits por número* ("olha o #57"). |
| 4 | **Rascunho autorado no cliente** (variante A): o navegador segura as edições pendentes; o servidor só faz operações atômicas de commit e guarda o rascunho. Sem `workspace_id` espalhado nos endpoints. |
| 5 | **O rascunho é persistido no servidor** (autossave). Não se perde rascunho ao limpar cache / travar / trocar de máquina. Comunicação/transferência entre pessoas é **sempre por commit**. |
| 6 | **Branches são compartilhadas e visíveis para todos.** `ref_` vira uma lista que qualquer um enxerga e cria. `main` é o tronco. |
| 7 | Um **rascunho por (autor, branch)**. O seletor de branch é o seletor de rascunho — sem tela separada. |
| 8 | Autossave: debounce de ~3 s após parar de digitar. Entre abas do mesmo autor/branch: last‑write‑wins. |
| 9 | Rascunho **vazio não existe** — a linha só é criada no 1º edit. |

## Modelo do rascunho

```sql
CREATE TABLE usuario (
  nome            TEXT PRIMARY KEY,   -- escolhido de uma lista; evita "augusto"/"Augusto"
  sempre_revisar  INTEGER NOT NULL DEFAULT 0,  -- ver "4 níveis" abaixo
  criado_em       TEXT
);

CREATE TABLE rascunho (
  rascunho_id    INTEGER PRIMARY KEY,
  autor          TEXT NOT NULL REFERENCES usuario(nome),
  ref_nome       TEXT NOT NULL REFERENCES ref_(nome),          -- em qual branch você está
  base_commit_id INTEGER NOT NULL REFERENCES commit_(commit_id),-- contra o que o diff é feito
  criado_em      TEXT NOT NULL,
  atualizado_em  TEXT NOT NULL,
  edicoes        TEXT NOT NULL,        -- JSON com o mesmo shape do delta de versao.py:
                                       --   { mes:{"pid|mat|tipo|per":horas|null},
                                       --     aloc_add:[...], aloc_del:[...],
                                       --     pessoa:{mat:{...}}, projeto:{pid:{...}},
                                       --     janela:{"pid|per":ordem|null} }
  UNIQUE (autor, ref_nome)
);
```

`base_commit_id` = topo de `ref_nome` no instante em que o rascunho nasceu. Pode ficar para
trás se outra pessoa commitar na mesma branch → merge no `salvar` (abaixo).

## Identidade

- Cliente pede o nome na 1ª vez, guarda em `localStorage`, manda em `X-Autor` toda request.
- Lista de nomes vem de `usuario` (um "admin" cadastra; sem senha).
- `X-Autor` é **afirmado pelo cliente** (spoofável) — aceitável num time confiável em rede
  interna. Não é autenticação.
- Vai para `rascunho.autor` e `commit_.autor`.

## Fluxos

### Editar
1. Cliente escolhe uma branch (default `main`). `GET` do estado materializado do topo dela
   (`materializar(commit)`), + `GET /api/rascunho?autor=&ref=` para recuperar o rascunho.
2. Renderiza a grade = estado materializado **+ overlay do rascunho**; triângulos/"novo"/
   riscado são calculados **no cliente** (rascunho vs. materializado).
3. Cada edição entra no edit‑set em memória + `localStorage`; debounce 3 s → `PUT /api/rascunho/{id}`
   (cria a linha no 1º edit).

### Salvar (rascunho → commit)
- `merge base` = `rascunho.base_commit_id` (por construção é ancestral do topo atual).
- OURS (sintético) = `materializar(base_commit_id)` + aplica `edicoes`.
- THEIRS = `materializar(topo atual de ref_nome)`.
- 3-way (reusa `_merge3` de `versao.py`).
- **Sempre commit de 1 pai** — o rascunho nunca foi um commit, então não há 2º pai. (Isso é
  diferente do `merge(origem)` da Fase 2, que junta duas branches reais e gera commit de 2 pais.)
- `commit_.autor = rascunho.autor`. A ref da branch avança. O rascunho é **consumido**
  (apagado); o autor fica num rascunho limpo sobre o commit novo.

#### Quanto o "salvar" incomoda — 4 níveis

Decididos por dados baratos: `tocado` = chaves que o meu rascunho editou; `entrou` =
chaves que mudaram no topo desde `base_commit_id`.

| Nível | Situação | Ao salvar |
|---|---|---|
| **1** | topo == base | commita, `parent = base`. Nada a dizer. |
| **2** | topo andou, `tocado ∩ entrou = ∅` | merge silencioso, `parent = topo`. Toast passivo: *"incorporei os commits #58–#60 de Maria e Pedro"*. |
| **3** | `tocado ∩ entrou ≠ ∅`, mas `_merge3` resolve (sem conflito real) | **bloqueia** com um *digest* do que veio do outro lado (*"Maria mudou a capacidade do João; 2 alocações novas em BRAVO 2"*) → **Salvar assim / Cancelar**. Não é revisão célula a célula. |
| **4** | conflito real (mesma célula/campo, valores diferentes) | para; resolução célula a célula (`merge_conflito`, máquina da Fase 2); depois commita, `parent = topo`. |

- **Preferência por usuário** (`usuario.sempre_revisar`, default `0`): quando ligada,
  promove o nível 2 a também mostrar o digest com **Salvar assim / Cancelar**. Nível 1
  nunca pede nada.

### Descartar
`DELETE /api/rascunho/{id}` → recarrega do topo da branch.

### Branch
`ref_` já existe. `POST /api/versao/branch` (existe). Nova branch nasce apontando para um
commit (topo de outra branch, ou um #N específico). Todos veem todas. `merge(origem)` da
Fase 2 junta branch↔branch.

## Os três cenários

1. **Mesmo commit, divergem, merge de volta.** A e B criam cada um sua branch a partir de
   #2 (`branch cenario-a` / `cenario-b`), editam no seu rascuho daquela branch, salvam
   (commits nas respectivas branches). Alguém dá `merge cenario-a` e `merge cenario-b` na
   `main`.
2. **Commits diferentes (#2 e #3).** Idêntico — a branch de cada um nasce no commit que a
   pessoa escolheu. "Partir do #2 ou do #3" é só onde a `ref` começa.
3. **Retomar trabalho.**
   - Usuário 1 **commitou** #57 → usuário 2 abre, escolhe trabalhar a partir do #57 (ou da
     branch que aponta pra lá) e segue.
   - Usuário 1 tem rascunho **não commitado** e some → o rascunho está autossalvo no servidor
     sob `(autor=1, ref=main)`. O 1 não perde nada ao voltar. Para **passar adiante**, o 1
     commita (pode ser um commit `wip` numa branch pessoal `augusto/wip`) e dá o número.
     Ninguém "pega" o rascunho do outro — ele é pessoal.

## Ripple (o que muda no que já existe)

- **Endpoints de edição de célula se aposentam** (`mes-lote`, `POST /api/alocacao`,
  `PUT /api/alocacao/{id}/mes`, `DELETE /api/alocacao/{id}`, `PUT /api/pessoa/{m}`…).
  Edição é no cliente; o servidor recebe o edit‑set inteiro no autossave e no `commitar`.
- **`head_` some** (ou vira vestigial). `garantir_inicializado` passa a garantir só
  commit raiz + `ref('main')`.
- **`working` deixa de ser editável.** A grade vem de `materializar(topo da branch)`.
  As tabelas `working` viram, no máximo, cache de leitura de um tip.
- **`baseline_alocacao*` / `base_*`** perdem o sentido de "o HEAD" (não há HEAD global).
  Ou viram cache por branch (`cache_<ref>`), ou materializa‑se sob demanda
  (~4 mil linhas / dezenas de commits → <100 ms). Decidir na implementação.
- **Export** passa a exportar **de um commit** (topo de branch ou #N), não de um working
  mutável. `versao.removidas` / `pessoas_alteradas` comparam esse commit com o 1º pai.
  (Revisar em detalhe na implementação.)
- **`/api/estado`** ganha `usuario` atual + lista de `ref_` + o rascunho do autor.

## Identidade e papel de acesso (2026-09-11) — substitui `usuario` deste doc

> Decisões fechadas numa sessão de arquitetura separada, ainda **não implementadas**
> (nenhuma linha de código escrita). Endereça um problema diferente do resto deste
> documento: **quem é a pessoa e o que ela pode fazer**, não a edição concorrente
> (rascunho/autosave/4-níveis, que continua como descrito acima e segue pendente).

A tabela `usuario(nome PK, ...)` proposta na seção anterior está **substituída**: não existe
lista de identidade separada. Todo `pessoa` cadastrado é um usuário em potencial. Quando o
`rascunho` for implementado, `rascunho.autor` e `commit_.autor` passam a referenciar
`pessoa.matricula` (não `usuario.nome`) — é a única mudança de fato no desenho de rascunho
acima; o resto (edit-set, 4 níveis, autosave) não muda.

### Identidade — como é gravada
- Cliente guarda só a **matrícula** em `localStorage` (chave tipo `alocacao.autor`).
  Escolhida uma vez, num seletor simples (lista de `pessoa`); só muda depois via um menu
  escondido nas configurações — não repergunta a cada carga.
- **Sem auto-detecção do usuário do sistema operacional no v1** — o navegador não expõe
  essa informação; Kerberos/`REMOTE_USER` fica como possibilidade futura, não agora.
- Cada requisição mutante manda a matrícula em `X-Autor` (mecanismo já existe). Grava em
  `commit_.autor` / `rascunho.autor` — sempre matrícula, nunca nome livre.

### Papel — `pessoa.papel`, fora do versionamento
`pessoa.papel TEXT NOT NULL DEFAULT 'leitura'`, valores `leitura | gp | coordenador |
admin`. **Decisão definitiva: fica fora do sistema de versionamento** — não entra em
`PESSOA_COLS` (`versao.py`), não gera linha em `chg_pessoa`, não passa por `base_pessoa`,
não é afetado por commit/checkout/merge/descartar. Grava direto na tabela, como
`preferencias`. Motivo: controle de acesso precisa ser imediato — promover alguém não pode
ficar pendente de commit nem virar conflito de merge entre branches.

### Matriz de permissão

| papel | edita alocações/projetos | custo totalizado (projeto/mês) | custo individual (valor_hora/remuneração) | incorporar → Corrente (merge main) | promove usuário |
|---|---|---|---|---|---|
| leitura | não | não | não | — | não |
| gp | sim | sim | não | não (só cenário↔cenário) | não |
| coordenador | sim | sim | sim (vê e edita) | sim (aprova) | não |
| admin | sim | sim | sim | sim | sim — inclusive promove outro admin |

`admin` é superset de `coordenador`. Isso também fecha o item 3 de "Em aberto" abaixo:
**Corrente (`main`) fica protegida** — incorporar exige `coordenador`/`admin`.

### Superfície de enforcement (nada implementado ainda)
1. Toda rota mutante (alocação, projeto, pessoa, commit/branch/merge/descartar) bloqueada
   pra `leitura`.
2. `_PESSOA_PUB` (`main.py`) — hoje esconde `valor_hora`/`remuneracao` de todo mundo em
   `/api/estado`; vira condicional por papel.
3. `cadastros.py` `editar_pessoa` — editar custo individual só `coordenador`/`admin`.
4. Endpoint de custo por projeto (`GET /api/custo/projeto`) bloqueado pra `leitura`.
5. `versao.merge`/commit pra `main` (Corrente) — gate por papel, além da proteção de branch
   que já existe.
6. Enforcement **precisa ser server-side** — `X-Autor` é auto-declarado/falsificável;
   esconder elemento na UI é só cosmético.

### Apelido — facet de usuário, não de cadastro

`pessoa.apelido TEXT` (opcional). Conceitualmente é um atributo do **usuário** (como
`papel`), não do cadastro da pessoa — por isso, mesma regra do `papel`: **fica fora do
versionamento** (fora de `PESSOA_COLS`/`chg_pessoa`/`base_pessoa`, grava direto). Existe
fisicamente na tabela `pessoa` só porque não há tabela `usuario` separada (ver acima); o
que importa é o tratamento (fora do diff/commit), não onde a coluna mora.

Resolve o problema de exibição: `pessoa.nome` vem sujo do BI (maiúsculas inconsistentes) e
**não vai ser corrigido agora** (a correção certa é higienizar o dado importado, fora de
escopo aqui). Função de exibição — usar em **todo lugar que hoje mostraria autor/pessoa**
(commit, badge de usuário conectado, git-graph, diálogos):

```
nome_exibicao(pessoa) = pessoa.apelido, se definido, senão primeiro token de pessoa.nome
```

Cada um edita o próprio apelido (self-service, na tela de Configurações — ver abaixo). Não
precisa de UI de admin editando apelido alheio nesta rodada.

### Autor "sistema"

Reservar uma **pessoa** com matrícula fixa `SISTEMA` (não numérica — não colide com
matrícula real vinda do BI), `nome='Sistema'`, `apelido='Sistema'`, `papel='admin'`.
Criada uma vez na migração, se não existir. Não é caso especial em lugar nenhum do código —
é só mais uma matrícula possível em `commit_.autor`.

**Uso imediato:** nenhum — hoje a importação (BI e planilha de projeto) é sempre disparada
manualmente por uma pessoa logada. O autor do commit gerado pela importação deve ser
**essa pessoa** (via `X-Autor` da requisição), não mais a string fixa `'BI'` que
`bi_import.py` grava hoje — esse é um bug a corrigir nesta implementação (ver plano
abaixo).

**Uso futuro:** quando a importação passar a ser automática (conectada direto no banco de
origem, sem alguém clicar em "importar"), o autor desses commits passa a ser `SISTEMA`.
Não implementar a automação agora — só deixar a pessoa `SISTEMA` pronta pra esse dia.

## Plano de implementação — identidade, papel, apelido

> Escopo desta rodada: fazer a identidade "chegar" em todo o sistema (backend resolve
> quem está por trás de cada requisição; frontend sabe e mostra quem é o usuário atual e
> seu papel). **Não** inclui aplicar o enforcement da matriz de permissão (bloquear
> edição/custo por papel) — isso é o próximo passo, depois que esta base estiver no ar.

> **Status (2026-09-11): implementado**, itens 1-6 abaixo. Detalhe de uma escolha de
> implementação não antecipada no plano: em vez de enfiar `X-Autor` como parâmetro em
> cada uma das ~30 rotas mutantes, o backend captura o header **uma vez por requisição**
> num middleware (`_AUTOR_ATUAL`, `contextvars.ContextVar` em `main.py`) — qualquer
> função no meio de uma requisição lê `_AUTOR_ATUAL.get()` sem precisar receber o header
> no parâmetro. `_estado()` (chamado de ~28 endpoints diferentes) usa isso pra sempre
> devolver `usuario_atual` correto, sem precisar tocar em cada um deles.

### 1. Schema (`backend/app/db.py`)
- `ALTER TABLE pessoa ADD COLUMN papel TEXT NOT NULL DEFAULT 'leitura'` (idempotente em
  `_migrate`).
- `ALTER TABLE pessoa ADD COLUMN apelido TEXT` (idempotente).
- **Nem `papel` nem `apelido` entram em `PROJETO_COLS`/`PESSOA_COLS`** (`versao.py`) —
  ficam de fora de `chg_pessoa`/`base_pessoa` de propósito.
- Migração idempotente: `INSERT OR IGNORE INTO pessoa (matricula, nome, apelido, papel)
  VALUES ('SISTEMA', 'Sistema', 'Sistema', 'admin')`.

### 2. Backend — resolução de identidade
- Helper `nome_exibicao(pessoa_row) -> str` (apelido ou primeiro token do nome) — um lugar
  só, reusado em toda serialização que hoje mostra autor.
- `/api/estado` ganha um bloco `usuario_atual`: `{matricula, nome_exibicao, papel}`,
  resolvido do header `X-Autor` da própria requisição (pessoa inexistente/`X-Autor`
  ausente → tratar como não identificado; decidir o fallback — sugestão: `papel='leitura'`
  até o picker rodar no cliente).
- Endpoint pra listar pessoas pro seletor (provavelmente já existe via `/api/estado` ou
  `cadastros.cadProjetos`-like; reusar, não duplicar).
- Endpoint pra o usuário setar o próprio apelido: `PUT /api/pessoa/{matricula}/apelido`
  body `{apelido}` (sem checagem de papel nesta rodada — cada um mexe no próprio; se
  quiser, restringir a `matricula == X-Autor` pra não editar apelido alheio por engano).
- **Corrigir a autoria da importação do BI**: `bi_import.py`/`versao.commitar_estado_bi`
  hoje gravam `autor='BI'` fixo — passar a receber e usar o `X-Autor` de quem disparou o
  upload (pessoa real). `SISTEMA` só entra quando existir de fato um caminho de importação
  automática (não existe ainda).

### 3. Frontend — seletor de primeira execução
- No boot, se não há matrícula em `localStorage` (`alocacao.autor`): modal bloqueante,
  lista de pessoas (busca por nome/matrícula), escolhe uma → grava no `localStorage`.
- `api.js`: toda chamada mutante manda `X-Autor: <matrícula>` (mecanismo estava desenhado
  em `docs/COLABORACAO.md` mas nunca chegou a ser ligado no cliente — é isso que fecha
  agora).
- Estado global (`S.usuario` ou equivalente) populado a partir do bloco `usuario_atual` de
  `/api/estado` — disponível em toda a árvore de render, mesmo sem uso de enforcement
  ainda (é o "chegar pra todo mundo" que você pediu).

### 4. Frontend — ícone de Configurações (substitui o ícone de tema)
- Trocar o ícone de tema na rail (canto inferior esquerdo) por uma **engrenagem**. Clique
  abre um painel/modal **Configurações** contendo:
  - **Tema** — o que já era o botão antigo (auto/claro/escuro), só que migrado pra dentro
    daqui.
  - **Usuário atual**: `nome_exibicao (papel)` + botão "Trocar usuário" → reabre o mesmo
    seletor do primeiro acesso.
  - **Meu apelido**: campo de texto, salva via `PUT /api/pessoa/{matricula}/apelido`.
- Um indicativo permanente e discreto de quem está conectado (ex.: badge pequeno perto da
  engrenagem, "· Augusto") — não precisa abrir Configurações pra saber quem está logado.

### 5. Autoria visível
- Em todo lugar que hoje mostra `autor`/nome de pessoa vindo de commit (histórico de
  versões, badges do grafo, diálogos de merge/incorporação) — trocar para `nome_exibicao`
  em vez do `pessoa.nome` cru.

### 6. Testes
- Migração: colunas novas existem, idempotente em 2ª chamada; pessoa `SISTEMA` criada uma
  vez só.
- `usuario_atual` em `/api/estado` reflete o `X-Autor` da requisição.
- `PUT` de apelido grava e não aparece em `chg_pessoa`/diff de versionamento.
- Importação do BI grava `commit_.autor` = quem disparou o upload, não mais `'BI'` fixo
  (ajustar `test_bi.py` de acordo).

### Fora de escopo nesta rodada (fica pro próximo passo)
- Aplicar a matriz de permissão de fato (bloquear rotas/esconder custo por papel — os 6
  pontos de enforcement listados acima).
- UI de admin promovendo o papel de outra pessoa (hoje ninguém tem UI pra mudar `papel` de
  ninguém — pode ser feito direto no banco enquanto isso).
- Importação automática conectada no banco de origem (o motivo de existir `SISTEMA`, mas
  não o caminho em si).

## Em aberto

1. ~~Salvar com a branch adiantada: transparente ou sempre revisar?~~ **Decidido:** 4
   níveis (ver "Quanto o salvar incomoda"). Transparente nos níveis 1–2; bloqueio com
   *digest* (não revisão célula a célula) no nível 3 — commitou por cima de algo que mexeu
   no seu território, então você olha antes; nível 4 (conflito real) sempre trava.
   Preferência `usuario.sempre_revisar` promove o nível 2 ao digest.
2. Rascunho pode ser renomeado / ter mais de um por (autor, branch)? — hoje: **não**, um só.
3. ~~`main` protegida contra `salvar` direto (forçar branch + merge)?~~ **Decidido
   (2026-09-11):** sim — incorporar em Corrente (`main`) exige papel `coordenador`/`admin`.
   Ver seção "Identidade e papel de acesso" acima.
4. Autossave: e se o `PUT` falhar (rede)? Cliente mantém em `localStorage` e re‑tenta;
   badge "não salvo" enquanto pendente.
5. Limpeza: rascunho abandonado há muito tempo — expira? notifica o autor?
6. Concorrência real no `commitar`: dois `commitar` na mesma branch ao mesmo tempo — o
   `_lock` serializa; o 2º recalcula o merge base contra o topo já avançado. Confirmar que
   o caminho aguenta.

## Ordem de implementação

1. ✅ `usuario` + `rascunho` (schema).
2. ✅ `GET/POST /api/usuarios`, `GET/PUT/DELETE /api/rascunho` (aceita `autor` no corpo ou
   header `X-Autor`).
3. ⬜ Grade a partir de `materializar(ref tip)` + overlay do rascunho no cliente (mover o
   cálculo de diff da tela para o cliente); autossave (debounce 3 s + `localStorage`);
   `X-Autor` no `req()` da API.
4. ✅ `POST /api/rascunho/commitar` — `versao.commitar_rascunho`: 3-way vs. tip, commit de
   1 pai, consome o rascunho, 4 níveis, `confirmar` p/ os níveis 2 (com `sempre_revisar`) e 3.
5. ⬜ Aposentar os endpoints de edição de célula; remover `head_`.
6. ⬜ Seletor de branch = seletor de rascunho na toolbar; lista de branches compartilhada.
7. ⬜ Revisar export (exporta de um commit) e o cache `baseline_*`/`base_*`.
8. ⬜ Migrar o texto relevante para `ESPECIFICACAO.md`.

### Contrato do edit-set (`rascunho.edicoes`, JSON)

```json
{
  "mes":      { "<pid>|<mat>|<tipo>|<YYYY-MM-01>": <horas> | null },
  "aloc_add": [ ["<pid>","<mat>","<tipo>"], ... ],
  "aloc_del": [ ["<pid>","<mat>","<tipo>"], ... ],
  "pessoa":   { "<mat>": { "<campo>": <valor>, ... } },
  "projeto":  { "<pid>": { "<campo>": <valor>, ... } },
  "janela":   { "<pid>|<YYYY-MM-01>": <ordem> | null }
}
```
`null` em `mes` = voltou a 0. `versao._aplicar_edicoes` aplica isso sobre
`materializar(base_commit_id)` para montar o OURS sintético.

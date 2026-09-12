# Especificação — Editor de Planejamento de Alocação

> **Documento vivo.** A especificação é mantida aqui e corrigida ao longo do
> desenvolvimento. O processo é iterativo: o usuário usa a plataforma, propõe
> melhorias, e a especificação é atualizada antes/junto com a implementação.
> Toda mudança relevante entra no **Histórico de mudanças** no fim do arquivo.

- **Início:** 2026-09-09
- **Status atual:** v1 em implementação
- **Amostra de referência:** `amostras/ed425bf6-20260518_Otimizeplan.xlsx`

---

## 1. Contexto e objetivo

O planejamento de alocação de pesquisadores hoje é feito em **um Excel por projeto**
(abas `dados_projeto`, `Alocacao`, `Novos_Pesquisadores`, `Planilha4`). Editar à mão,
projeto a projeto, é penoso e não dá visão consolidada por pessoa (quem está
super/subalocado somando todos os projetos).

**Arquitetura do v1:** a aplicação tem um **banco de dados interno que é a fonte da
verdade**. Ela **importa** os `.xlsx` para esse banco, permite editar em **duas grades
sincronizadas** (por projeto e por recurso) estilo Excel, e **exporta** de volta para
`.xlsx` no padrão do template. Os arquivos são formato de intercâmbio, não a base.

**Visão de futuro (orienta decisões, fora do v1):** versionamento estilo Git — linha
central de planejamento, branches de cenário, merge/commit no plano principal. O esquema
do banco já nasce compatível (períodos em formato longo; projeto/alocação/pessoa
normalizados).

## 1-A. Vocabulário

Termos que são usados como se fossem sinônimos, mas **não são** — cada um significa
exatamente uma coisa neste documento e no código. Onde um nome de entidade aparece em
código/API, é indicado entre parênteses.

- **Pessoa** (`pessoa`) — a entidade humana. É o conceito mais estável e neutro; tudo o
  mais abaixo é uma faceta dela, nunca outra entidade.
- **Colaborador** — uma pessoa com algum vínculo de trabalho com a organização (empregado,
  bolsista, terceiro etc., dependendo da linguagem da empresa). Não é o nome da tabela nem
  da entidade — é um jeito de descrever o vínculo de uma pessoa.
- **Recurso** — o papel da pessoa no planejamento de capacidade/alocação. É uma visão
  gerencial ("por recurso", "carga do recurso", "capacidade disponível"), não a identidade
  da pessoa. Usado nas telas de planejamento; a tela de cadastro fala em **pessoa**, não em
  recurso (cadastrar pessoa, dados da pessoa).
- **Membro de equipe / membro do projeto** — pessoa vinculada a uma equipe ou a um projeto
  (no banco: uma linha de `alocacao`, `(projeto_id, matricula, tipo_alocacao)`).
- **Usuário** — pessoa com acesso ao sistema. É outra faceta da pessoa, não uma entidade
  separada nem sinônimo de "todo mundo cadastrado" — nem toda pessoa cadastrada precisa ser
  usuária. Ver `docs/COLABORACAO.md` para o modelo de identidade/papel de acesso.
- **Nome** — atributo de uma pessoa (`pessoa.nome`), nunca usado como nome de entidade.
- **Equipe** (`pessoa.equipe`) — agrupamento organizacional da pessoa (vem do extrato
  "equipe" do BI, campo `Nome_equipe`). **Não é sinônimo de `tipo_alocacao`.**

**Atenção — colisão histórica já corrigida:** `tipo_alocacao` (o balde de custo/fonte de
um projeto — `Técnica`, `TecnicaANP`, `OffShore`...) foi chamado de "equipe" em vários
lugares deste documento e na UI até 2026-09-10. São dois eixos completamente diferentes:
`pessoa.equipe` é um atributo da pessoa; `tipo_alocacao` é uma propriedade da linha de
alocação num projeto. Daqui em diante, **`tipo_alocacao` nunca é chamado de "equipe"** —
só `pessoa.equipe` pode ser.

**Vocabulário da interface de versionamento** — diretiva completa em
[`docs/TERMINOLOGIA.md`](TERMINOLOGIA.md). O mecanismo interno continua Git-like (`main`,
`commit`, `branch`, `merge`, `checkout` seguem como nomes técnicos no banco/código), mas a
**UI nunca expõe esses termos como primário** — usa a tradução abaixo (Git entre
parênteses/tooltip, se útil):

| Interface | Técnico |
|---|---|
| Versão | commit |
| Consolidar alterações | ação de commit |
| Cenário | branch criado pelo usuário |
| Principal | `main` |
| Publicado | branch `BI` |
| Abrir cenário | checkout |
| Incorporar cenário | merge |
| Conflito de alterações / Resolver conflito | merge conflict |
| Histórico de versões | log |
| Alterações não salvas | working tree sujo / uncommitted |
| Descartar alterações (deste projeto / global) | reset/discard |
| Atualizar | refresh |

Tabela completa e regras de uso em `docs/TERMINOLOGIA.md` §19.

## 2. Formato do arquivo de projeto (import/export)

### Aba `dados_projeto` (1 linha de dados, linha 2)
`Id_projeto`, `NomeProjeto`, `Empresa`, `Status`, `idStatus`, `Matricula_GP`,
`ID_Filial`, `Mês Inicio da alocação`, `Ano Inicio da alocação`, `cenario 1/2/3`.
Linhas 3+ são fórmulas de template quebradas (`#REF!`) — preservadas intactas no
export, ignoradas na leitura.

### Aba `Alocacao` (tabela principal)

| Col | Campo | Natureza |
|-----|-------|----------|
| A | `Matricula` | número — identidade da pessoa |
| B | `Tipo Alocacao` | string de catálogo (`Técnica, Econômica, Prospecção, OffShore, TecnicaEPII, TecnicaANP`), dropdown `Planilha4!$P$2:$Q$25`. É o balde de custo/fonte do projeto; **não confundir com `pessoa.equipe`**, que é o time organizacional da pessoa (ver §1-A Vocabulário). |
| C | `id_tipo` | **fórmula** `=IF(ISBLANK(Bn),"",VLOOKUP(Bn,Planilha4!$P$2:$Q$25,2,FALSE))` — derivada |
| D | `Perfil` | string — nome da pessoa (literal) |
| E… | meses | nº de colunas **variável por projeto**; cabeçalho = data serial do dia 1 do mês, a partir de `Mês/Ano Início`. Valor = **horas inteiras**. `0` no arquivo = mês sem alocação — **não vira registro** no banco (ver §7). |

- **Chave de uma linha:** `(Matricula, Tipo Alocacao)` dentro do projeto. A mesma pessoa
  aparece em várias linhas com `Tipo Alocacao` diferente (pode ter horas em mais de um
  balde no mesmo mês).
- Linha inteiramente zerada no arquivo = pessoa "no rol", ainda não alocada — **não é
  importada** como registro (o banco não guarda alocação sem horas).

### Aba `Novos_Pesquisadores`
Campos: `Matricula, Nome, Ativo, Equipe (+ID_Equipe fórmula), Area (+ID_Area fórmula),
Contrato (+ID_Contrato fórmula), Iníciocontrato, fimcontrato, formacao, ID_Filial,
cargahoraria Diária (8/6/4), Remuneração, InicioVigencia`. `ID_*` são VLOOKUPs p/ `Planilha4`.
- **Import:** as linhas caem na tabela **única `pessoa`** (não existe mais `pessoa_nova`).
- **Export:** a aba é **gerada do diff** — pessoas alocadas no projeto que são **novas ou
  com algum campo alterado** vs. a baseline. Sem alteração → só o cabeçalho.

### Aba `Planilha4` (catálogos)
`Nome_equipe/idEquipe` (A/B), `Tipo_contrato/idContratos` (D/E), `Nome_Area/idarea`
(G/H), `Ativo` (K), `Ensino` (M), `Tipo_alocacao/id_tipo` (P/Q), `Status/idStatus`
(S/T). Import semeia os catálogos do banco; export preserva a aba intacta.

### Comentários do Excel
- **Import:** só os comentários da aba `Alocacao` viram **anotações** (os de
  `dados_projeto` etc. são instruções de preenchimento e são ignorados). Comentários
  encadeados (texto limpo) têm prioridade sobre o espelho legado. Deduplicados por
  `(matrícula, texto)`.
- **Export:** todos os parts de comentário são **removidos** do `.xlsx`.

## 3. Banco de dados interno (SQLite — fonte da verdade)

Tabelas: `projeto`, `projeto_periodo` (janela de meses, formato longo `YYYY-MM-01` +
`ordem`), `alocacao` (`UNIQUE(projeto_id, matricula, tipo_alocacao)`), `alocacao_mes`
(`PK(alocacao_id, periodo)`, `horas INT >= 0`), **`pessoa`** — tabela **única** de
pesquisadores (`matricula` PK; `nome, situacao, equipe, area, tipo_contrato,
inicio_contrato, fim_contrato, formacao, id_filial, carga_diaria, capacidade_mensal,
remuneracao, inicio_vigencia, ativo`), `catalogo` (`tipo`, `id`, `texto`), `anotacao`
(notas na grade — nunca voltam pro `.xlsx`), `preferencias`.

### Versionamento estilo Git (`user_version = 2`)

Grafo global de commits (um snapshot do plano inteiro por commit). Detalhe e desvios em
[`docs/VERSIONAMENTO.md`](VERSIONAMENTO.md); módulo `backend/app/versao.py`. A camada
multiusuário (rascunhos por autor/branch, identidade, edição no cliente) está planejada em
[`docs/COLABORACAO.md`](COLABORACAO.md) — ainda não implementada.

- **Grafo:** `commit_(commit_id, parent_id, merge_parent_id, autor, mensagem, criado_em,
  origem)`, `ref_(nome → commit_id)` (`main` + branches de cenário), `head_` (branch
  check-outada + commit-base).
- **Delta por commit:** `chg_projeto` / `chg_pessoa` / `chg_alocacao` / `chg_alocacao_mes` /
  `chg_projeto_periodo` — só as linhas que mudaram vs. o 1º pai; chave natural; `deleted=1`
  = lápide. Materializar um commit = caminhar `parent_id` até a raiz, mais-recente vence.
- **Cache do HEAD** (o que a tela e o `commit` comparam contra o working):
  `baseline_alocacao` / `baseline_alocacao_mes` + `base_projeto` / `base_pessoa` /
  `base_projeto_periodo`.
- **Operações:** `commit` (working → novo commit, avança a ref), `checkout` (materializa
  working+cache; exige working limpo), `descartar` (working := HEAD; parcial por projeto),
  `branch`, `log`, e **`merge`** — 3-way com merge base = LCA no DAG. Conflito por célula
  (`mes`) ou linha (`projeto`/`pessoa`) vai para `merge_conflito`; `merge_estado` guarda o
  merge em andamento; `resolver_conflito` (`ours`/`theirs`/valor) aplica no working;
  `concluir_merge` grava o commit de 2 pais (`origem='merge'`); `abortar_merge` desfaz.
- **Import de planilha de projeto e export não commitam** — deixam mudanças pendentes no
  working. **Import do BI** = recarga da baseline: monta o estado completo do BI e grava
  **um commit na `main`** (`origem='bi'`, `autor='BI'`), pegando os valores do BI como
  estão, sem 3-way. Não encosta no working de ninguém — as edições de **alocação/janela**
  pendentes são reancoradas por cima do BI novo (campos de pessoa/projeto pertencem ao BI:
  se o extrato traz, o BI ganha).
- O export continua saindo **do diff working × HEAD**: alocação sumida → linha zerada;
  nunca-no-HEAD e ausente → não sai; pessoa alterada → `Novos_Pesquisadores`.

Esquema completo em `backend/app/db.py`.

## 4. Importação (`.xlsx` → banco)

Dois botões, dois caminhos:

### 4a. Planilha de projeto → `POST /api/importar-projeto` (ou o `importar-upload` esperto)
1. Upload pelo navegador (multipart). Os bytes vão para `uploads/` e o caminho vira o
   `arquivo_origem` (base da exportação).
2. Parse das 4 abas → escreve o **working** daquele projeto (zera e recria a alocação com o
   conteúdo do arquivo, atualiza campos e janela de meses). Status `imported` (novo) /
   `updated`. **Não commita** — fica pendente até um commit explícito.
3. Comentários da `Alocacao` → `anotacao`; catálogos de `Planilha4` → `catalogo`;
   `cargahoraria Diária` de `Novos_Pesquisadores` → `pessoa.carga_diaria` +
   `capacidade_mensal = carga_diaria × 22`.

### 4b. Extratos do BI → duas fases (leitura + relatório → confirmação)  (branch `BI`, B-lite)

**Fase 1 — `POST /api/importar-bi`** (`bi_import.preparar_importacao`): lê os arquivos,
monta o **estado-alvo do BI** como dict (`materializar(topo do branch BI)` + alocação/janela
do `bi_custo`, substituindo a dos projetos que o BI cobre, + campos do BI — só
`_BI_PROJETO_FIELDS` = nome/empresa/status/gestor e `_BI_PESSOA_FIELDS` = nome/situacao/area/
contrato/fim_contrato/carga/valor_hora, **só nos IDs/matrículas dos extratos**) e devolve um
**relatório** (`resumo`: projetos/pessoas com campo alterado, alocações novas/removidas,
células e janelas alteradas, cobertura de horas do extrato, linhas sem matrícula).
**Não commita nada** — fica guardado em `bi_pendente` (singleton) e o working/cache voltam
exatamente como estavam (inclusive projeto/pessoa que a leitura precisou criar de verdade
pra montar o relatório — se não for confirmado, são apagados de novo).

**Fase 2** — só roda se o usuário concordar com o relatório:
- **`POST /api/importar-bi/confirmar`** (`confirmar_importacao`): grava **um commit no
  branch `BI`** (`origem='bi'`, `autor='BI'`, sem 3-way — o BI é a autoridade do que cobre).
- **`POST /api/importar-bi/descartar`** (`descartar_pendente`): cancela sem commitar; some
  também o projeto/pessoa que só existiam pro relatório pendente.

`importar_arquivos(paths)` continua existindo como atalho "prepara e já confirma na hora"
(usado pelo `/api/importar-upload` esperto e pelo uso via CLI).

- **`bi_projeto` / `bi_custo`** = staging, não versionados. `valor_hora` só muda se o
  `colabmescusto` está no lote.
- **O branch `BI` é criado preguiçosamente** (`versao.garantir_bi`) na 1ª importação,
  apontando para o topo atual da `main` → "main e BI equivalentes por hora".
- **Fast-forward:** se a `main` ainda está colada no `BI` (nenhum commit próprio à frente),
  a `main` anda junto (`commitar_estado_bi` move os dois refs). O checkout atual (se for
  `main`) reancora as edições de **alocação/janela** pendentes por cima; edições de campo de
  pessoa/projeto **não** são reancoradas (pertencem ao BI).
- **Divergiu:** no instante em que você faz um commit próprio, `main` ≠ `BI`. Daí em diante
  a importação **só avança o branch `BI`** — `main`/working não se mexem. Você traz a
  atualização com `merge BI` (3-way; conflito onde você editou a mesma célula que o BI).
- **Não arrasta trabalho seu para o commit do BI.** Projeto/pessoa que você criou ou editou
  fora do footprint do BI segue como **sua** mudança pendente (bug corrigido: o antigo
  `_sync_base_campos` copiava o working inteiro).

## 5. Exportação (banco → `.xlsx`)

Por projeto, **injeção cirúrgica** — tocar só no necessário, resto byte a byte:

- Base = `arquivo_origem`; se `criado_na_ferramenta` e sem origem, usar o **template**
  `templates/projeto_template.xlsx` (derivado do arquivo de amostra na 1ª execução:
  cópia com as linhas de dados de `Alocacao`/`Novos_Pesquisadores`/`dados_projeto`
  esvaziadas).
- Reescrever apenas:
  - `Alocacao`: cabeçalho de meses conforme `projeto_periodo`; uma linha por `alocacao`
    (`A`=matrícula, `B`=tipo *(inlineStr)*, `C`=fórmula `id_tipo` replicada com o nº da
    linha, `D`=`pessoa.nome`, `E…`=`horas` — `0` onde não houver).
  - `Novos_Pesquisadores`: uma linha por `pessoa_nova` do projeto, `ID_*` como fórmula
    (só reescrita se houver linhas a escrever).
  - `<dimension>` das abas mexidas.
  - Remover `xl/calcChain.xml` e os parts de comentário (`comments*.xml`,
    `threadedComments`, `vmlDrawing*`, `persons/`) + suas relações, `Override`s do
    `[Content_Types].xml`, `Default Extension="vml"` e `<legacyDrawing>` das sheets.
  - `calcPr fullCalcOnLoad="1"` no `workbook.xml` (Excel recalcula as fórmulas).
- Strings novas escritas como **inline strings** (`t="inlineStr"`) — evita mexer em
  `sharedStrings.xml`.
- Preservados intactos: `Planilha4`, `dados_projeto`, estilos, `dataValidation`
  (dropdowns), `printerSettings`, `customXml`, `docProps`.
- **Popup "Exportar…"** — lista de projetos com checkbox (os "não exportados" `●` já vêm
  marcados) + "marcar todos". `POST /api/exportar {projeto_ids}`: 1 projeto → baixa o
  `.xlsx`; vários → baixa um `.zip` (`AAAAMMDD alocacoes (N).zip`).
- **⭳** por projeto na grade → `GET /api/projetos/{id}/download` (um arquivo).
- Nome do arquivo: **`AAAAMMDD NomeProjeto.xlsx`** (com espaço, como os arquivos atuais —
  ex.: `20260615 Catarina A2.xlsx`). Remove só `\ / : * ? " < > |`.
- *(Também `POST /api/projetos/{id}/exportar` grava numa pasta do servidor, p/ localhost;
  sobrescrita → `*.xlsx.bak`.)*
- **Janela de meses do arquivo gerado:**
  - **início = mês da geração** (ex.: arquivo gerado em setembro começa em setembro; meses
    anteriores **não** vão para o arquivo).
  - **fim = último mês com horas lançadas** (≥ mês da geração).
  - sequência **mensal contígua** entre os dois.
  - `dados_projeto.Mês Inicio da alocação` / `Ano Inicio da alocação` são **reescritos** com
    o mês/ano dessa 1ª coluna — **o importador usa esses dois campos como referência**, não
    lê o cabeçalho das colunas da `Alocacao`. Têm que estar coerentes.
- **Remoção via diff de versão** (transparente para o usuário — ele não vê "base"/"xlsx"):
  - **Linha de base** (`baseline_*`) = último *commit* do projeto: capturada na **importação**
    e a cada **exportação** (export = commit, a base avança).
  - No export, compara base × estado atual:
    - alocação que **estava na base e sumiu do working** → sai no `.xlsx` **zerada** (sinal
      de remoção). Inclui o caso de o usuário ter zerado todos os meses da linha.
    - alocação **nunca vista na base** e ausente do working → **não sai**.
    - toda alocação presente no working sai (inclusive as novas).
  - Na interface: só **✕ remover** na linha da pessoa. Enquanto não exporta, a linha
    removida aparece **riscada** com **↩ restaurar** (recria com os valores da base).
  - O export sempre escreve **uma linha por alocação atual** (com `0` nos meses vazios) +
    as linhas zeradas das removidas.
  - Projetos importados antes do versionamento: o estado atual virou a base (backfill no
    start).
  - Futuro: várias bases nomeadas = commits/branches; abrir uma para análise de cenário;
    "comitar no principal".
- Implementação: `zipfile` + `lxml` (sem `openpyxl` — não dá a fidelidade).

## 6. Criar projeto novo

Operação de banco: insere `projeto` (`criado_na_ferramenta=1`) via formulário
(`Id_projeto, NomeProjeto, Empresa, Status→id_status, Matricula_GP, Mês/Ano Início,
cenário 1/2/3`) + **quantos meses** a janela terá (default 10) → gera `projeto_periodo`.
`ID_Filial` = atributo do projeto, **default 62** (campo editável, sem catálogo).
Vira arquivo só na exportação.

## 7. Regras de negócio

- **Capacidade mensal** = valor único por pessoa, constante nos meses. Fonte: `pessoa`
  no banco (tela de cadastro). Sem capacidade → sem `%` e sem cor para a pessoa.
- **Percentual nunca é persistido.** Exibe `% = round(horas / capacidade × 100)`.
  Entrada `50%` → `horas = round(pct/100 × capacidade)`.
- **Horas sempre inteiras** — decimal digitado arredonda ao salvar.
- **Cor de divergência**, por `(matrícula, mês)` somando **todos os projetos**:
  `total > capacidade` → **vermelho**; `0 < total < capacidade` → **amarelo**;
  `= capacidade`, `= 0` ou capacidade desconhecida → sem cor.
  A cor do mês vai em **toda célula daquela pessoa naquele mês**, nas duas grades.
- **Nunca travar edição** — só sinaliza. Sem diálogos de confirmação em célula.
- **O banco não guarda registro de alocação zerado.** `alocacao_mes` só tem linhas com
  `horas > 0`; ausência de linha para um mês **é** `0`. Consequências:
  - Limpar uma célula → `DELETE` da linha daquele mês (não grava `0`).
  - Zerar **todos** os meses de uma linha → a `alocacao` some do working. Se ela existia
    na baseline, aparece **riscada** na grade (via `baseline.removidas()`) e sai **zerada**
    no `.xlsx` (sinal de remoção); se nunca esteve na baseline, some sem deixar rastro.
  - Import de arquivo por projeto e rebuild pelo BI descartam meses `0` e linhas
    inteiramente zeradas.
  - O `.xlsx` exportado continua escrevendo `0` nos meses sem lançamento — o formato do
    arquivo não muda.
- Remover linha = ação explícita (ou zerar tudo, acima).
- **Edição livre além da janela do projeto.** Pode-se lançar horas em qualquer mês,
  mesmo fora de `projeto_periodo`. Esses meses aparecem **tingidos** (hachura) na grade e
  no cabeçalho, mas são editáveis. `alocacao_mes` aceita qualquer `YYYY-MM`; a união de
  meses visível (`periodos`) e as colunas do `.xlsx` no export passam a incluí-los.
  Botão `＋`/`－` no fim do cabeçalho estende a faixa de meses mostrada para permitir
  lançar além de tudo que já existe.

## 8. As duas grades

Duas grades independentes, empilhadas na vertical. Em ambas: **coluna 1 = árvore,
travada (`position: sticky; left:0`)**; **colunas seguintes = um mês cada** (`MM/AA`),
roláveis na horizontal; **cabeçalho travado no topo**. Larguras de coluna idênticas
entre as duas.

- **Divisória arrastável** entre as duas grades (`#divider`) redistribui o espaço
  vertical; a proporção fica em `localStorage` (`splitFrac`). Preparado para uma 3ª
  grade futura (custo/resumo por projeto).
- **Coluna da árvore redimensionável** — arrastar o *grip* no cabeçalho muda a variável
  CSS `--treecol-w` (aplica nas duas grades juntas); persistida em `localStorage`.
- **Scroll horizontal inicial no mês atual** e botão **"Hoje"**: `panel.scrollLeft =
  idx × 62` (largura fixa da coluna de mês) — cálculo exato, sem medir DOM. `.panel` tem
  `padding-right: 33vw`, que entra na área rolável (Chrome/FF), garantindo folga para o
  mês atual chegar à esquerda mesmo quando tudo caberia na tela — sem coluna extra.
- Botões **`+ mês` / `− mês`** na toolbar estendem/reduzem a faixa de meses mostrada
  (para lançar horas além de tudo que já existe).
- Cabeçalho da coluna-árvore com título ("Projeto · Pessoa" / "Pessoa · Projeto"),
  `z-index` 40 (canto) > 20 (cabeçalho de mês) > 10 (1ª coluna no corpo), fundo opaco e
  borda de 2px — sem sobreposição ao rolar. **Cabeçalho e linhas têm exatamente as mesmas
  colunas** (coluna-árvore + meses), senão o `table-layout: fixed` desalinha. Conjunto de meses = **união** das janelas dos projetos carregados; numa
linha de projeto, meses fora da janela daquele projeto ficam **desabilitados** (cinza).

**Ordenação alfabética** em ambas as grades: por projeto → projetos por nome, filhos
por nome da pessoa (depois `tipo_alocacao`); por recurso → pessoas por nome, filhos por
nome do projeto (depois `tipo_alocacao`). `tipo_alocacao` vem do catálogo fixo de
`Planilha4`, não editável no v1 (escolhido só ao adicionar a linha).

### Filtros da tela
- **Por GP:** dropdown na toolbar (distintos de `projeto.gestor_projetos`). Filtra as
  duas grades.
- **Filtro de período (substitui "só ativos" — corrigido em 2026-09-12, ver Histórico):**
  um seletor de **período inicial** (padrão = mês atual) e, opcionalmente, **período
  final** (padrão **aberto** — vai até o último mês com dado; nunca usar sentinela tipo
  `9999-12` pra simular "aberto", é `NULL`/ausente mesmo). Só as colunas de mês dentro de
  `[período inicial, período final]` aparecem na grade.
  - **Visibilidade de linha (projeto / pessoa / alocação) segue o mesmo critério das
    colunas**: aparece se tiver **qualquer hora dentro da janela visível**. Projeto criado
    na ferramenta aparece mesmo sem hora (pra poder começar a alocar). Isso substitui o
    antigo `tem_futuro`/`tem_horas_futuras` (que decidia "mostro em qualquer mês?" com um
    critério só sobre o futuro, escondendo a linha inteira mesmo em meses passados onde
    ela tinha dado real — ver bug 2026-09-12 no Histórico).
  - **Nunca esconder dado que explica um total já mostrado.** Como coluna e linha usam o
    mesmo critério de janela, uma alocação só pode ficar de fora se **nenhuma coluna
    visível** tem hora dela — logo nunca existe uma coluna visível cujo total inclua uma
    linha invisível.
  - `status = Encerrado` → item marcado "(encerrado)" em itálico (rótulo, não filtro).
  - **Filtro por "ativo"/situação da pessoa continua existindo, mas só em telas de
    busca/seleção** (ex.: autocomplete de "+ adicionar pessoa"/"+ adicionar alocação") —
    nunca pra esconder linha já presente na grade principal.
  - Botão **"Hoje"** continua existindo, mas agora é **só atalho de rolagem** (posiciona
    a coluna do mês atual à esquerda) — não é mais parte do critério de filtro.
  - **Controles na toolbar, ao lado do "Hoje"**: um botão/dropdown pra abrir os dois
    seletores (período inicial e período final) e **uma opção pra desabilitar o filtro**
    — desligado = mostra tudo, equivalente a período inicial aberto também (volta ao
    `periodos_union` completo, sem corte nenhum). É o mesmo lugar/gesto que hoje abre a
    troca entre "só ativos"/"todos", só que agora configura datas em vez de alternar um
    booleano.

### Grade "Por Projeto" — 3 níveis
- Nível 0: **projeto**. Total do mês (leitura, negrito).
- Nível 1: **tipo de alocação** (`TecnicaANP`, `TecnicaEPII`, `Técnica`…) — grupo
  com subtotal do mês. Um `●` no rótulo indica que o grupo tem alteração não exportada.
- Nível 2: **pessoa**. Célula = horas (**editável**), cor `(matrícula, mês)`.
- `+ adicionar pessoa` por grupo (tipo pré-preenchido); `+ adicionar tipo` por projeto.
- **Mover de tipo:** `<select>` na linha da pessoa (aparece no hover) — troca o
  `tipo_alocacao` da alocação. No diff isso vira "removido do tipo antigo" + "novo no tipo
  novo" (o export zera um e lança o outro).

### Marcas de diff vs. baseline (nas duas grades)
- Célula com valor **diferente do último commit** → **triângulo azul no canto** + tooltip
  "era: X" (não usa cor de fundo, para não colidir com vermelho/amarelo).
- **Também nos totais** (projeto, tipo, pessoa) — mudança indireta: se remover alguém ou
  editar uma célula, os subtotais e o total do projeto que mudaram ganham o triângulo.
- Linha **nova** (não existia na baseline) → faixa azul + "novo" no rótulo.
- Linha **removida** → riscada, com **↩ restaurar** (até exportar).

### Estado inicial / recolher-expandir
- Padrão: **tudo recolhido** — "Por Projeto" lista só os projetos; "Por Recurso" só as
  pessoas. (Persistido: `localStorage.open` = conjunto de nós expandidos; vazio = recolhido.)
- **⊟ / ⊞ na barrinha de cada grade** (não na toolbar) — agem só naquela grade.

## 9-A. Edição estilo Excel (`frontend/js/grid-excel.js`)

Camada de planilha sobre as duas grades. A **seleção cobre toda e qualquer célula** —
coluna 1 (árvore: projeto/tipo/pessoa), linhas de total (projeto/tipo/pessoa) e linhas
"+ adicionar..." (uma `<td>` vazia por mês, não um `colspan`) — não só as células de hora
editáveis; isso permite arrastar um bloco retangular que atravesse tudo (ex.: um projeto
inteiro, grupos e a linha "+ adicionar pessoa" no meio) e copiar de uma vez. Só o
**destino de edição** (digitar/F2/preenchimento/colar/Delete) continua restrito às
células de hora realmente editáveis — nas demais a seleção existe só para copiar.
- **Clique = seleciona** a célula (não edita). Bloco: clique+arraste ou Shift+clique.
- **Setas / Tab / Enter** navegam entre células (qualquer uma, não só as editáveis).
- **Digitar** um caractere, **F2** ou **duplo-clique** abre a edição (só em célula de hora
  editável); `Enter`/`Tab` confirmam e andam; `Esc` cancela.
- **Delete/Backspace** zera as células editáveis da seleção (ignora as demais).
- **Alça de preenchimento** (quadradinho azul no canto da célula ativa, só aparece numa
  célula editável): arrasta para copiar o valor pela linha/coluna/bloco.
- **Ctrl+C / Ctrl+V**: copia/cola como TSV (troca dados com o Excel), sem "h"/"%" nem
  formatação — só o número. O **destino do colar é a seleção**: se maior que o bloco
  copiado, o bloco é **ladrilhado** (1 célula → preenche tudo; bloco 3×1 → repete em cada
  coluna do bloco selecionado); só grava nas células editáveis do destino. Copiar a
  **coluna 1** junto (quando ela é a borda esquerda do bloco) leva o nome com um recuo de
  2 espaços por nível de hierarquia (projeto → tipo → pessoa), não um tab (que abriria
  coluna nova no Excel e desalinharia os meses).
- Escritas múltiplas (arrastar/colar) vão num só request: `PUT /api/alocacao/mes-lote`.
- **Undo/Redo** (`Ctrl+Z` / `Ctrl+Y`, botões `↶ ↷`): pilha no cliente que guarda o valor
  anterior das células de cada edição (editar/arrastar/colar/Delete) e reaplica via lote.
  Até 100 passos; ações estruturais (add/remover/mudar tipo) não entram.
- **Painel lateral** (resumo de custo/pessoa, `#sec-body`): os trechos tabelados (KPIs,
  legenda, barras de custo/mês, principais projetos, alocação mês a mês) também aceitam
  selecionar com o mouse e `Ctrl+C` — copia como TSV (mesma regra: números puros, sem
  unidade), reconstruído a partir de marcações `data-copy-row`/`data-copy-cell` no HTML,
  não da seleção de texto bruta.

### Grade "Por Recurso"
- Nível 0: **pessoa**. Célula do mês = **total da pessoa** (todos projetos/tipos),
  leitura, com a cor `(matrícula, mês)`. É o número comparado à capacidade.
- Nível 1: **`Projeto / Tipo`** (string no rótulo). Célula = horas da linha (**editável**).
- Linha final por pessoa: `(+ adicionar alocação)` → escolhe projeto e `Tipo Alocacao`.

*Sem linha de total geral por mês — a soma de todos os projetos não traz informação
relevante. Só existem os totais por projeto e por pessoa.*

## 9. Edição (comportamento Excel)

- Clicar seleciona; digitar substitui; `Enter` confirma e desce; `Tab` confirma e vai à
  direita; setas navegam; `Delete`/`Backspace` → `0`.
- Aceita `88` (horas) ou `50%`.
- Toggle na toolbar **"Exibir: Horas ▸ %"** — só troca o número grande da célula; o
  outro aparece pequeno/cinza.
- Cada edição grava no banco (API) e recalcula na hora: total do pai, cor da pessoa no
  mês nas duas grades. Marca o projeto com "alterações não exportadas".

## 10. Sincronização entre as grades

- **Rolagem horizontal:** rolar meses num painel rola o outro; 1ª coluna nunca rola.
- **Sincronização de seleção:** clicar numa **linha de alocação** (chave = `alocacao_id`)
  seleciona **só aquela linha**. No outro painel: **expande** o container de 1º nível (a
  pessoa no "Por Recurso"; o projeto + grupo no "Por Projeto"), rola até deixar **esse
  container de 1º nível no topo** da janela, e o **destaque** fica na linha da alocação
  equivalente. Clicar na linha da pessoa (nível 0) sincroniza pela matrícula.
- **Botão de modo** entre os quadros: `↑↓` (ambos), `✕↓` (só recurso→projeto),
  `↑✕` (só projeto→recurso), `✕✕` (desligado). `↑` = de cima p/ baixo; `↓` = de baixo p/ cima.

## 11. Stack

- **Backend:** Python + FastAPI + SQLite. Import/export com `zipfile` + `lxml`.
- **Frontend:** HTML estático + JS vanilla (ES modules), sem build. Duas `<table>` com
  `table-layout: fixed`, coluna 1 `sticky`, rolagem horizontal sincronizada por evento.
- App local: backend serve o frontend em `localhost`.
- **Escala:** alvo pequeno (render da grade inteira, sem virtualização). Estruturado
  para crescer: agregações no backend, camada de render isolada.

## 11-A. Dados do BI — MONTA o banco de trabalho

Os extratos do BI **são a fonte de tudo**: `projetos.xlsx` → tabela `projeto` (todos),
`colabs.xlsx` → `pessoa`, **`colabmescusto.xlsx` → reconstrói a BASELINE** (alocação por
projeto × pessoa × mês × tipo, **todos os meses** jan/2025→jun/2028) + `valor_hora`.

- **BI é autoridade da baseline.** O **working** (o que o usuário edita) **não é
  sobrescrito**; projeto sem alocação no working ganha cópia da baseline (aparece pronto).
- A diferença working × baseline vira o **diff da sessão** (triângulos, novo, riscado).
- **Descartar mudanças** (`↺` por projeto / `POST /api/descartar-tudo` global): working
  volta a ser **exatamente** a baseline (alocação + campos de pessoa/projeto).
- **⌾ definir baseline = estado atual**: commit local (baseline := working) sem passar
  pelo BI.
- `build_grade` ≈ 0,07 s / payload ≈ 1 MB com ~123 projetos e ~55 com alocação.

Detecção pelo cabeçalho; nomes normalizados **sem acento**. Formas de carregar:
- **Botão "Importar arquivos…"** — o mesmo botão aceita planilha de projeto **e** extrato
  do BI na mesma seleção; cada arquivo é classificado e roteado (`/api/importar-upload`).
- `POST /api/importar-bi` (só BI) ou `python -m app.bi_import <arquivos>`.

| Extrato | Cabeçalho-chave | Destino |
|---|---|---|
| **equipe** | `matricula, Colaborador, Area, Tipo de contrato, ativo, Carga Horária, Fim contrato` | enriquece **`pessoa`** (`area, tipo_contrato, situacao, fim_contrato`; `carga_diaria`; `capacidade_mensal = carga×22` **só se estava nula** — não sobrescreve edição manual) |
| **colabmescusto** | `idProjetos, nomeColaborador, Data, tipo_alocacao, Horas, custo` | **`bi_custo`** (fato: horas e custo por projeto/colaborador/mês/tipo; `matricula` anexada por nome). ~4093 linhas, 2025-01→2028-06. Full refresh (`DELETE`+reinsere). |
| **projetos** | `idProjetos, NomeProjeto, Empresa, Status, Gestor de Projetos` | **`bi_projeto`** (+ `empresa/status/gestor`) e sincroniza `projeto.gestor_projetos` / `empresa` / `status` por `id_projeto_externo` |
| **projetomescusto** | `idProjetos, NomeProjeto, Data, Horas, tipo_alocacao, custo` | `bi_projeto` (`id_projetos → nome_projeto`) |
| projetocolabalocacao | pivot BI multi-cabeçalho | ignorado (redundante) |
| colabprojetoalocacao | só descrição de filtros | ignorado (sem dados) |

- **Sobrescrita:** o import do BI é **autoridade** para `area, tipo_contrato, situacao,
  carga_diaria, fim_contrato, ativo` e **`valor_hora`** de `pessoa` (não faz mais só
  COALESCE). `capacidade_mensal` não é clobberada (só preenche se nula).
- **`pessoa.valor_hora`** = `custo / horas` do mês **mais recente** com dados no
  `colabmescusto`. (Um número por pessoa; o custo mês a mês continua no `bi_custo`.)
- Nome ambíguo no `colabmescusto` (mesma pessoa, matrículas diferentes) → resolve para a
  matrícula **Ativa** (senão Planejado, senão Desligado; empate → matrícula maior).
- `bi_custo.id_projetos` == `projeto.id_projeto_externo` (ex.: OTIMIZEPLAN 16045,
  Barragem 4.0 fase 2 = 20423) → liga aos projetos do planejamento.
- **`GET /api/custo/projeto?de=YYYY-MM&ate=YYYY-MM&por=tipo_alocacao|area`** — custo e
  horas por projeto por mês, opcionalmente subdividido. (Ainda **sem UI** — dado disponível
  para a fase de custo.)

## 12. Estado da implementação

> Atualizada em 2026-09-12 — revisão de consolidação, não é um corte de release formal.

| Módulo | Arquivo | Status |
|--------|---------|--------|
| Leitura/escrita cirúrgica de `.xlsx` | `xlsx_io.py`, `xlsx_export.py` | ✅ feito (roundtrip validado, LibreOffice) |
| Esquema do banco | `db.py` | ✅ feito (`user_version = 2`, migrações idempotentes) |
| Importação por projeto | `xlsx_import.py` | ✅ feito |
| Import do BI (2 fases: relatório → confirmação), branch `BI` | `bi_import.py` | ✅ feito |
| Agregações + cor + filtro de período | `aggregate.py` | ✅ feito |
| Template derivado da amostra | `templates.py` | ✅ feito |
| API FastAPI | `main.py` | ✅ feito |
| Cadastros (projeto/pessoa/catálogo) | `cadastros.py` + aba Cadastros | ✅ feito |
| Versionamento estilo Git (commit/branch/checkout/merge 3-way) | `versao.py` + tela Versões (grafo, diff) | ✅ feito (Fases 1–2 de `VERSIONAMENTO.md`) |
| Terminologia da UI (Versão/Cenário/Principal/Publicado/Incorporar) | `docs/TERMINOLOGIA.md`, aplicado em `index.html`/`main.js` | ✅ feito |
| Identidade (matrícula, `X-Autor`, seletor 1ª execução) + papel/apelido (`pessoa.papel`/`apelido`, fora do versionamento) | `docs/COLABORACAO.md`, `main.py`, `main.js` | ✅ identidade chega em todo o sistema; **enforcement da matriz de permissão NÃO implementado** (ver §14) |
| Frontend (duas grades, edição estilo Excel, undo/redo, 3-pane shell, painel de custo) | `frontend/` | ✅ feito |
| Custo por projeto (dado) | `bi_import.py`, `GET /api/custo/projeto` | ✅ dado no banco; sem UI dedicada de custo além do painel lateral |
| Rascunho/edição concorrente multiusuário (overlay no cliente) | `docs/COLABORACAO.md` | ⬜ não implementado — hoje é 1 `working` compartilhado (ver Ripple em `COLABORACAO.md`) |
| Testes | `backend/tests/` | ✅ 64 passando (só backend — sem infra de teste de frontend, ver §14) |

**Como rodar:** `./run.sh` (prod, porta 8731) ou `./run.sh dev` (porta 8732) — ver `CLAUDE.md`.

## 13. Decisões fechadas

- Reimport de arquivo de projeto **atualiza o working** (baseline intacta); projeto novo
  cria + vira a baseline.
- `tipo_alocacao` = catálogo fixo de `Planilha4`, não editável no v1. **Nunca chamado de
  "equipe"** — `pessoa.equipe` é um conceito diferente (ver §1-A).
- Nome canônico da pessoa vem de `pessoa` no banco; exibição de autor usa `nome_exibicao`
  (apelido, senão 1º nome) — ver `docs/COLABORACAO.md`.
- Linhas ordenadas alfabeticamente nas duas grades.
- Template derivado do arquivo de amostra na 1ª execução.
- `ID_Filial` = atributo do projeto, default 62, sem catálogo. `mes_inicio`/`ano_inicio`
  **removidos** de `projeto` (eram só parâmetro interno da planilha, não atributo do
  projeto — o exportador deriva isso da janela de dados).
- Strings no export vão como inline strings (não mexe em `sharedStrings.xml`).
- Terminologia da UI segue `docs/TERMINOLOGIA.md` (Versão/Cenário/Principal/Publicado/
  Incorporar); nomes técnicos internos (`main`, `commit`, `branch`) não mudam.
- `pessoa.papel` e `pessoa.apelido` ficam **fora do sistema de versionamento** — gravam
  direto, nunca entram em `chg_pessoa`/commit/merge (controle de acesso precisa ser
  imediato). Matriz de papéis e pessoa reservada `SISTEMA` em `docs/COLABORACAO.md`.
- **Filtro de período substitui "só ativos"** (§8): linha e coluna usam o mesmo critério
  de janela — uma linha nunca fica invisível se tiver hora numa coluna visível (corrige o
  bug de "total sem linha que explica", ver Histórico 2026-09-12).
- Incorporar (`merge`) para `main`/Principal exige papel `coordenador`/`admin` — decidido,
  **enforcement ainda não implementado** (ver §14).

## 14. Itens em aberto / a decidir com uso

- **Enforcement da matriz de permissão (leitura/gp/coordenador/admin) não implementado.**
  A identidade e o papel já chegam em todo o sistema (`usuario_atual`), mas nenhuma rota
  bloqueia edição/custo por papel ainda, e não há UI pra promover o papel de outra pessoa.
  Os 6 pontos de enforcement estão listados em `docs/COLABORACAO.md`.
- **Rascunho/edição concorrente multiusuário** (overlay no cliente, autosave, 4 níveis de
  fricção ao salvar) — desenhado em `docs/COLABORACAO.md`, schema parcial existe
  (`usuario`/`rascunho`), mas nunca foi ligado no frontend. Hoje dois GPs editando ao mesmo
  tempo compartilham o mesmo `working` ("last write wins").
- **Import automático do BI** (conectado direto no banco de origem, sem upload manual) —
  motivo de existir a pessoa reservada `SISTEMA`, mas o caminho em si não existe.
- **Rótulo do usuário "Sistema" no seletor de identidade** — hoje aparece misturado com
  pessoas de verdade, sem indicação visual de que é uma conta reservada (bootstrap /
  autor de commit automático). Considerar marcá-lo ou deixá-lo por último na lista.
- Descoberta de arquivos: só pasta, ou também seleção avulsa persistida?
- Edição/CRUD da aba `Novos_Pesquisadores` na interface (form dedicado) — escopo do v1?
- Exibir/editar anotações na grade (ícone de nota) — v1 ou depois?
- Comportamento ao remover um projeto do banco (confirmar, mover arquivo, etc.).
- **Sem infra de teste de frontend.** `frontend/js/*.js` (grade, grafo de versões, import
  do BI) não tem nenhuma cobertura automatizada — só `pytest` no backend. Lógica pura sem
  DOM (ex. `layoutGrafo` em `main.js`) já seria testável isoladamente, mas falta o runner:
  este ambiente de desenvolvimento não tem Node.js instalado (só `nodejs` 12 disponível via
  `apt`, não instalado). Ficou pendente decidir se vale montar essa infra (`node --test` ou
  equivalente) para um app single-user deste porte.
- **Painel lateral: toggle pra travar a troca automática.** Hoje o painel sempre segue o
  último clique nas grades (projeto → custo, pessoa → resumo). O usuário quer um botão
  discreto pra desligar esse acompanhamento automático — inclusive de forma assimétrica
  (ex.: segue projeto, mas trava em pessoa uma vez aberta). Não implementado ainda.

## Histórico de mudanças

- **2026-09-12** — **Ação de commit: "Salvar versão" → "Consolidar alterações".**
  `docs/TERMINOLOGIA.md` §8.1/§19/§20 atualizados — "salvar" ficava ambíguo (as horas já
  estão persistidas no banco antes do commit; "consolidar" comunica melhor que é um ponto
  formal do histórico). Aplicado no botão principal da toolbar de Versões, no botão
  alternativo que aparece quando a branch atual é protegida ("Salvar num cenário…" →
  "Consolidar num cenário…"), no botão de confirmação do relatório do BI, no prompt que
  pede a mensagem da versão, e nas mensagens de log logo após a ação ("versão salva" →
  "versão consolidada"). **Não mexido**: "Alterações não salvas" (o estado de working
  sujo) e "última versão salva" (nas telas de Descartar) continuam com "salvo/salva" — são
  um par de termos deliberadamente ajustado antes (ver entrada de 2026-09-11) e o usuário
  não pediu para reabrir essa escolha; fica registrado como tensão de vocabulário em aberto
  (ação = "consolidar", mas o estado ainda descreve em termos de "salvo") até uma decisão
  explícita.
- **2026-09-12** — **Cadastros: campos de texto livre viram seleção onde já existe
  catálogo/FK.** `matricula_gp` (Projeto) virou busca por nome (`<input list>` +
  `<datalist>` de pessoas), resolvendo pra matrícula no salvar; a coluna derivada
  "Gestor" ao lado atualiza sozinha, mesmo padrão do `id_status`→"Status". `area`,
  `tipo_contrato` e a nova `formacao` (Pessoa) viraram `<select>` alimentado pelos
  catálogos `area`/`contrato`/`ensino` — diferente do `id_status`, gravam o texto
  direto, não um id. `situacao` também virou `<select>`; como o catálogo só tem
  Desligado/Planejado, "Ativo" entra como opção explícita (era o padrão implícito).
  `formacao` (`pessoa.formacao`, já existia no banco) passou a aparecer/ser editável no
  Cadastros pela primeira vez. Sem mudança de schema.
- **2026-09-12** — **Renomeado "Corrente" → "Principal"** (nome de UI do branch `main`,
  docs/TERMINOLOGIA.md §9.1). Trocado por ser mais direto/claro. Atualizado em todo lugar
  que gerava esse texto: `versao._nome_cenario` (mensagem automática de merge),
  `main.js`'s `nomeCenario`/tooltip de branch protegida, o texto de ajuda do diálogo de
  Arquivos (`index.html`), e a tabela de vocabulário (§ acima) — `main` continua o nome
  técnico interno da ref, só o rótulo de UI mudou.
- **2026-09-12** — **Cópia/cola estilo Excel estendida pra toda célula da grade + painel
  lateral.** Pedido: dava pra selecionar/copiar (Ctrl+C) só as células de hora editáveis
  (`td.cell.editable`) — coluna 1 (árvore), linhas de total (projeto/tipo/pessoa) e a linha
  "+ adicionar..." (que nem tinha `<td>` por mês, era um `colspan` só) ficavam fora, então
  não dava pra arrastar uma seleção que atravessasse um projeto inteiro. `grid-excel.js`
  reescrito: o modelo de linhas passou a incluir TODA `<tr>` do `tbody` (não só as com
  `alocacao_id`) e toda célula de mês ganhou `data-per` (antes só as editáveis tinham);
  "+ adicionar..." ganhou uma `<td class="month addfill" data-per="...">` por mês em vez do
  `colspan`. Seleção/navegação/cópia agora cobrem qualquer célula; edição, Delete,
  preenchimento e o destino do colar continuam restritos às células de hora editáveis
  (as demais entram na seleção só pra copiar, em branco quando vazias). Copiar a coluna 1
  junto (quando é a borda esquerda do bloco) leva o nome com recuo de 2 espaços por nível
  de hierarquia (não tab, que abriria coluna nova no Excel). Corrigido também um bug em
  `cellText()` que lia só o primeiro filho do `<td>` como texto — numa célula com ícone de
  sub/superalocação o primeiro filho é o ícone (elemento, não texto), então a cópia saía
  vazia; agora procura o primeiro nó de texto direto, ignorando ícone e o "twin" (%/horas
  pequeno). Além disso, o **painel lateral** (resumo de custo/pessoa) passou a aceitar
  selecionar com o mouse + `Ctrl+C` nos trechos tabelados (KPIs, legenda, barras de
  custo/mês, principais projetos, alocação mês a mês), reconstruindo TSV a partir de
  marcações `data-copy-row`/`data-copy-cell`/`data-copy-value` no HTML (o valor copiado é
  sempre o número puro, sem `R$`/"h"/"%"/separador de milhar). Ver §9-A.
- **2026-09-12** — **Bug encontrado e critério de "só ativos" corrigido para filtro de
  período.** Achado: total de um mês passado na grade "Por Recurso" incluía horas de uma
  alocação (projeto "Férias Bolsa/estagio", 64h em 12/2025) que ficava **invisível** —
  `tem_horas_futuras`/"só ativos" escondia a linha inteira por não ter hora ≥ mês atual,
  mesmo tendo hora real no mês exibido. Não era dado corrompido nem falha de importação
  (confirmado consultando o banco); era o critério de filtro mal desenhado — 304 linhas /
  109 pessoas no banco atual têm esse mesmo padrão (hora só no passado). Correção de
  critério (ver §8 "Filtros da tela"): "só ativos" (cutoff fixo em hoje, escondia linha
  inteira) → **filtro de período** (período inicial, padrão hoje; período final opcional,
  padrão aberto — sem sentinela tipo 9999-12). Linha e coluna passam a usar o **mesmo**
  critério de janela — uma linha só fica de fora se nenhuma coluna visível tiver hora
  dela, então never mais existe total visível sem a linha que o explica. Filtro por
  situação/ativo da pessoa continua existindo, mas só em telas de busca/seleção, nunca pra
  esconder linha já presente na grade principal. Botão "Hoje" vira puro atalho de rolagem.
- **2026-09-12** — **Filtro de período implementado.** `aggregate.build_grade` ganhou
  `periodo_inicial`/`periodo_final`/`periodo_ativo`; `_visivel_no_periodo` substitui o
  antigo `_fut` e é usado no mesmo critério pra linha (`tem_horas_no_periodo` no leaf,
  `visivel_no_periodo` em projeto/pessoa) e pra coluna (`periodos_union` recortada pela
  janela). `periodo_ativo=False` é um terceiro estado à parte de "não configurado" —
  desliga o filtro inteiro (período inicial também vira `None` de verdade, não cai no
  padrão de hoje); trafega via header `X-Periodo-Ativo: 0` (junto de `X-Periodo-Inicial`/
  `X-Periodo-Final`), capturado em `main.py` por um contextvar por requisição (mesmo
  padrão do `X-Autor`) pra `_grade()` respeitar o filtro em toda resposta, não só em
  `GET /api/estado`. Na tela, o toggle "só ativos"/"todos" saiu do grupo do `#gp-filter`
  e virou o botão `#btn-periodo` ao lado do `#btn-hoje` (rótulo mostra a janela efetiva,
  ex. "Período: 09/26→aberto" ou "Período: todos"), que abre um popover
  (`#periodo-ctx`, mesmo padrão visual do `#aloc-ctx`) com período inicial, período final
  e o checkbox de desligar o filtro. `frontend/js/api.js` persiste as 3 preferências em
  `localStorage` e as manda em todo request. Testes novos em `test_aggregate.py`
  reproduzem o caso real do bug (hora só num mês que sai da janela) e o estado
  `periodo_ativo=False`; suíte completa (64 testes) verde.
- **2026-09-12** — **Menu de contexto na grade Por Projeto: ajuste rápido de
  alocação.** Botão direito numa célula de horas editável abre um menuzinho com
  "Normalizar alocação" (tooltip "Ajustar para alocação plena (100%)", com o quanto
  falta/sobra entre parênteses — ex. "+35h") e uma fileira de 5 botões (100/75/50/25/0%)
  — cada um ajusta a célula pra aquele percentual da **capacidade daquela pessoa**,
  arredondado pra hora inteira (nunca fração, ver invariante Option B). Funciona sobre
  seleção de várias células (`EG.cellsPara` em `grid-excel.js`, reaproveita o mesmo
  retângulo de seleção estilo Excel já existente): clicar dentro da seleção atual afeta
  ela inteira, clicar fora afeta só a célula clicada (e a seleção pula pra ela, como no
  Excel). Como o ajuste é por pessoa (capacidade varia), cada célula da seleção recebe
  seu próprio valor calculado; o rótulo dos 5 botões mostra a hora exata quando todas as
  células da seleção têm a mesma capacidade, senão cai pro percentual puro (não dá pra
  mostrar dois números de hora diferentes no mesmo botão); o delta do "Normalizar" segue
  a mesma lógica ("vários" se divergir). Rótulo/tooltip mostrado depende do botão
  "Exibir: Horas/%" já existente. Ícone é uma barrinha de nível simples (SVG, mesmo
  estilo mono-traço dos outros ícones do app — não copiado da mockup ASCII que o
  usuário mandou só como referência de layout). Escopo desta rodada: só a grade **Por
  Projeto**, a pedido explícito — dá pra estender pra Por Recurso depois, é o mesmo
  mecanismo (`ALOC_INFO`/`applyBatch` já são genéricos).
- **2026-09-11** — **Bug real no gráfico de área: linha de capacidade sempre no topo,
  escondia superalocação.** A linha tracejada (capacidade/100%) era desenhada em
  `yOf(maxY)` — mas `maxY` é, por definição, o próprio teto da escala (cresce pra
  caber o pico mais alto), então essa linha ficava **sempre** exatamente no topo do
  gráfico, nunca na altura real da capacidade. Com superalocação (ex.: capacidade
  176h, 221h alocadas em nov/dez), o pico e a "linha de 176h" ficavam colados no
  mesmo lugar, escondendo visualmente que ele ultrapassou a capacidade — o gráfico
  parecia dizer "o pico é exatamente a capacidade", quando na verdade era 45h a mais.
  Corrigido: a linha vai pra `yOf(cap)` (a altura de verdade dela dentro da escala,
  que agora só serve pra definir até onde o eixo vai); a área que ultrapassa aparece
  visivelmente acima da linha. Eixo Y também mudou de 3 rótulos fixos (topo/meio/0)
  pra posição absoluta de verdade: rótulo do topo = valor real do teto da escala
  (podia ser maior que a capacidade), e a capacidade ganha um rótulo próprio (em
  amarelo) na altura certa quando não coincide com o topo.
- **2026-09-11** — **Resumo de pessoa: nome de projeto truncando + legenda duplicada.**
  Dois ajustes em "Principais projetos": (1) a coluna do nome tinha largura fixa
  (90px), truncando nomes longos ("Barragem 4.0 - Fase 2" virava "Barragem 4.0 - f...")
  mesmo sobrando espaço na linha — nome agora ocupa o espaço livre (`1fr`), a barra de
  referência encolheu pra 56px fixos; (2) a legenda do gráfico de área (embaixo dele)
  repetia os mesmos nomes de projeto que já aparecem logo abaixo em "Principais
  projetos" — tirada; a cor de cada projeto agora aparece como um quadradinho ao lado
  do nome ali mesmo (mesma paleta `cores2`), sem repetir a lista duas vezes.
- **2026-09-11** — **"+ adicionar pessoa"/"+ adicionar tipo"/"+ adicionar projeto":
  dropdown inline, só ativos, e sem duplicar quem já está no grupo.** Antes era um
  único diálogo com `<select>`s mostrando **todo mundo** do banco (inclusive
  desligados) e **todo projeto** (inclusive encerrado), pessoa e tipo de alocação
  juntos na mesma tela. Passou por uma iteração com popup de busca (chegou a entrar
  no código) e foi revertida a pedido do usuário — o formato final é:
  - Clicar em "+ adicionar pessoa"/"+ adicionar projeto" transforma a própria célula
    num `<select>` **inline** (não abre diálogo nenhum) — `addRowEscolha` em `main.js`.
    Pessoa: só **ativas** (mesmo critério de `aggregate.cor_pessoa_mes` — `ativo`
    truthy, `situacao` fora de Desligado/Planejado) e **sem quem já está naquele
    grupo/tipo** (não dá pra duplicar `(projeto, matrícula, tipo)`); tem uma opção
    fixa "+ pessoa nova" que pede matrícula/nome por `prompt()`. Projeto: só **não
    encerrado** (`status` diferente de "Encerrado" — Prospecção/Aditivo/Em
    Contratação/Contratado/Em Encerramento continuam todos disponíveis).
  - "+ adicionar tipo" (nível do projeto) tinha um bug de UX: abria o dropdown de
    **pessoa** primeiro, como se fosse "+ adicionar pessoa", quando deveria abrir o
    de **tipo**. Corrigido (`addRowNovoTipo`): 1º dropdown lista os tipos que o
    projeto **ainda não tem** (filtra os já usados nos grupos existentes); ao
    escolher, a mesma célula troca pro dropdown de pessoa (sem exclusão — tipo é
    novo, ninguém pode já estar nele).
  - Segunda etapa (quando o tipo ainda não é conhecido) continua um popup minúsculo
    (`#dlg-tipo`, só um `<select>`) — pulado quando o tipo já vem fixo (ex.: "+
    adicionar pessoa" dentro de um grupo específico).
  Puramente frontend, nenhuma rota nova.
- **2026-09-11** — **`projeto.status` vira FK "lógica" pro catálogo — coluna de texto
  removida.** `projeto` tinha `status` (TEXT) e `id_status` (INTEGER) editáveis
  **independentemente**, sem nada garantindo que combinassem — e na prática só o texto
  era preenchido (no banco de produção, 116 de 122 projetos tinham `status` mas
  `id_status` NULL). A pedido do usuário: `id_status` vira a **única fonte de verdade**
  (aponta pra `catalogo` onde `tipo='status'`); `status` deixou de existir como coluna —
  texto é sempre derivado via `LEFT JOIN catalogo` (`aggregate.py`, `main.py._projetos`,
  `cadastros.py`, `xlsx_export.py`). Migração em duas etapas, em `projeto`/`chg_projeto`/
  `base_projeto`: (1) backfill — resolve `id_status` a partir do texto existente via
  `catalogo` (case-insensitive; catalogo manda, sobrescreve se os dois já estavam
  preenchidos e discordavam) — testado 100% sem perda nos dois bancos reais; (2)
  `ALTER TABLE ... DROP COLUMN status`. Novo helper `db.resolver_id_status(conn,
  status_texto, id_status)` reusado em todo write path que hoje só tem o texto (BI,
  import de planilha antiga, formulário): `bi_import.load_projetos` (as duas escritas —
  insert inicial e a sincronização por `id_projeto_externo`), `xlsx_import._insert_projeto`
  /`_update_projeto`, `cadastros.editar_projeto` (aceita `status` texto por
  compatibilidade, mas resolve pro id antes de gravar), `main.criar_projeto`. Frontend:
  "Novo projeto" e a célula de Cadastros › Projetos viram `<select>` de catálogo em vez
  de texto/número livre; a coluna "Status" na tabela de Cadastros passa a ser só leitura
  (derivada), quem edita é "idStatus". Teste de round-trip
  `test_status_vira_id_status_e_volta_igual_no_export` confirma que o `.xlsx` exportado
  continua com `Status`/`idStatus` idênticos ao original. Aplicado nos dois bancos reais
  (dev: 122/123 projetos com id_status resolvido, 1 já estava sem status desde antes;
  prod: 122/122).
- **2026-09-11** — **Catálogo (`Planilha4`) semeado direto na migração — não depende mais
  de import de planilha por projeto.** `catalogo` (área, contrato, equipe, status,
  tipo_alocacao, ensino, ativo) só era preenchido pelo import de planilha por projeto —
  o BI nunca escreve nele. Um banco alimentado só por BI (o caminho mais comum hoje)
  ficava com a aba "Catálogo" de Cadastros vazia (era o caso do `alocacao-dev.db`: 0
  linhas, contra 84 no `alocacao.db` de produção, que em algum momento teve uma planilha
  importada). Esses valores são, na prática, fixos — então `db._CATALOGO_PADRAO` (as 84
  linhas capturadas do banco de produção) agora é gravado direto em `_migrate()` via
  `INSERT OR IGNORE` (idempotente, PK é `tipo`+`texto`) — existe sempre, com ou sem
  import de planilha. Aplicado diretamente no dev; prod já tinha essas mesmas linhas
  (import de planilha nunca sobrescreve, só adiciona catálogos novos que ainda não
  existirem). Teste `test_catalogo_padrao_semeado_na_migracao`.
- **2026-09-11** — **Correções pós-feedback na tela de Configurações/seletor de usuário
  + linha fantasma na tabela `pessoa`.** (1) Botão "Fechar" de `#dlg-config` não fazia
  nada — faltava o `onclick` explícito (`button[value="cancel"]` dentro de um `<dialog>`
  sem `<form method="dialog">` não fecha sozinho; todo outro diálogo do app já tinha essa
  linha, esse foi só esquecido). (2) Lista de "Trocar usuário" mostrava o **apelido** —
  ruim pra reconhecer quem é quem numa lista de busca; trocado pro nome original
  (`pessoa.nome`, o que vem do BI). (3) A lista tinha `max-height` (encolhe conforme o
  filtro reduz os resultados) — virou `height` fixa. (4) **Bug real de import**: uma
  legenda de rodapé que o Power BI deixa na última linha exportada ("Filtros aplicados:
  ativo não é Desligado") estava na mesma coluna da matrícula em `colabs.xlsx`, e
  `bi_import.load_equipe` não validava o formato — virou uma "pessoa" fantasma (sem
  nenhuma alocação, mas aparecia na lista de usuários). Corrigido na origem (guarda:
  matrícula não pode ter quebra de linha nem passar de 20 caracteres) e limpo via
  migração idempotente em `db.py` (`DELETE FROM pessoa WHERE matricula LIKE 'Filtros
  aplicados:%'`) — roda sozinha no próximo restart de qualquer banco, dev ou prod, sem
  precisar editar o `.db` na mão.
- **2026-09-11** — **Identidade + papel + apelido chegam em todo o sistema**
  (`docs/COLABORACAO.md` "Identidade e papel de acesso" + "Plano de implementação").
  Escopo: identidade "chega" em tudo — **não** inclui aplicar a matriz de permissão de
  verdade (isso é o próximo passo). `pessoa.papel` (default `'leitura'`) e
  `pessoa.apelido` novos, de propósito **fora do versionamento** (fora de
  `PESSOA_COLS`/`chg_pessoa`/`base_pessoa` — gravam direto, como `preferencias`); pessoa
  `SISTEMA` (papel `admin`) criada uma vez na migração, reservada pro dia em que a
  importação for automática (não é hoje). Backend: `nome_exibicao(pessoa)` = apelido, senão
  1º token do nome — usado em todo autor de commit exibido (grafo, diff); `/api/estado`
  ganha `usuario_atual {matricula, nome_exibicao, papel}` resolvido do `X-Autor`; novo
  `PUT /api/pessoa/{matricula}/apelido` (self-service, 403 se tentar editar apelido
  alheio). **Corrigido um bug real**: `bi_import.py`/`versao.commitar_estado_bi` gravavam
  `autor='BI'` fixo (string, não uma pessoa) — agora usam quem disparou o upload de
  verdade (a importação continua sempre manual; `SISTEMA` só entra quando existir
  importação automática). `X-Autor` capturado **uma vez por requisição** via middleware +
  `contextvars.ContextVar` (`_AUTOR_ATUAL`) em vez de parâmetro em cada rota — evita tocar
  nas ~30 rotas que devolvem `_estado()`. Frontend: `api.js` manda `X-Autor` em toda
  chamada (isso nunca tinha sido ligado no cliente); seletor bloqueante de pessoa na 1ª
  execução (`localStorage` `alocacao.autor`); ícone de tema do rail virou uma
  **engrenagem** (Configurações: tema + usuário atual + "Trocar usuário" + campo de
  apelido), com um badge discreto (inicial do nome) sempre visível no rail. Testes novos
  em `backend/tests/test_identidade.py` (7): migração idempotente, papel/apelido fora do
  diff, `usuario_atual` reflete `X-Autor`, apelido alheio recusado, autoria do BI é quem
  importou.
- **2026-09-11** — **Ícones de sub/superalocação: maiores, no canto superior-esquerdo,
  velocímetro trocado por despertador.** Segunda rodada de feedback sobre o item anterior:
  o velocímetro não ficou legível pequeno e o par inteiro estava pequeno demais pra
  perceber de relance. `.sev-icon` foi de 8×12px inline (antes do número, empurrando o
  texto) pra 14×14px **posicionado absoluto** no quadrante superior-esquerdo da célula
  (`position:absolute; top:1px; left:2px`, `td.month` virou `position:relative` pra ser a
  referência) — não interfere mais no alinhamento do número. Ícone de superalocação
  redesenhado: era velocímetro, virou **despertador tocando** (sininhos no topo + ponteiros
  bem abertos), pra ficar na mesma família visual do relógio parado da subalocação (mesmo
  círculo+ponteiros) só que claramente "em alarme" — mais fácil de reconhecer o par de
  relance do que dois ícones de famílias diferentes.
- **2026-09-11** — **Sub/superalocação: gravidade invertida + ícone no lugar de pintar a
  célula.** Dois pedidos do usuário sobre a marcação de divergência da grade: (1) a
  severidade estava invertida pro objetivo de planejamento de capacidade — subalocação
  (recurso ocioso) é mais grave que superalocação (recurso sobrecarregado), então o
  vermelho (mais forte) passa a ser da subalocação, e o amarelo da superalocação. Corrigido
  na origem, `aggregate.cor_pessoa_mes` (backend/app/aggregate.py) — os 3 branches que
  decidiam "vermelho"/"amarelo" trocaram de rótulo; tudo que consome essas strings
  (`colorClassFor` em main.js, `CUSTO_COR_PESSOA` no resumo de pessoa) seguiu
  automaticamente, sem precisar mexer em mais nada além do ponto de origem. Teste
  `test_cor_sobre_e_subalocacao` ajustado. (2) A célula não pinta mais o fundo inteiro de
  vermelho/amarelo — um ícone pequeno (SVG à mão, ~8×12px, alongado verticalmente) aparece
  à esquerda do número grande: um relógio pra subalocação (vermelho — tempo ocioso) e um
  velocímetro com o ponteiro passando do limite pra superalocação (amarelo). Ícones
  escolhidos depois de descartar alternativas — barrinhas+linha de meta, pessoa carregando
  peso — por não caberem legíveis no espaço minúsculo disponível. `--sev-amarelo` novo token
  de cor (claro/escuro); vermelho reaproveita `--err`. `td.cell.over`/`.under` e
  `td.total.over`/`.under` perderam o `background` — ficam só como gancho semântico na
  célula, sem estilo próprio.
- **2026-09-11** — **Correções no gráfico de área do resumo de pessoa** (feedback direto
  em cima da versão anterior do mesmo dia): (1) o gráfico e a legenda listavam **todo**
  projeto em que a pessoa já teve alguma alocação (`rec.filhos`, histórico completo), mesmo
  sem nenhuma hora no período mostrado (mês atual em diante) — projetos encerrados/"Férias"
  apareciam na legenda sem nunca aparecer no gráfico; agora ambos usam a mesma lista, já
  filtrada por `soma > 0` no período visível; (2) como efeito colateral disso, duas cores se
  repetiam quando havia mais projetos do que cores na paleta (`CUSTO_CORES` tem 6) — some
  sozinho ao filtrar, já que sobra pouca coisa na prática; (3) adicionado eixo Y (`0` /
  metade / topo — topo = capacidade mensal em horas, ou 100% no modo percentual), com as
  marcas deslocadas pra fora do gráfico (`.sec-area-wrap`/`.sec-area-y`) em vez de só uma
  linha pontilhada solta sem rótulo.
- **2026-09-11** — **Resumo de pessoa: gráfico de área, prioridade pessoa > projeto,
  auto-expandir tipo, contrato vencido.** Iteração sobre o painel lateral do dia:
  (1) clicar na linha de topo de um projeto (Por Projeto) já expande os grupos de tipo de
  alocação dele (`S.open` ganha as chaves `g:<projeto>:<tipo>` na hora, sem precisar de um
  segundo clique no `▸`); (2) **pessoa manda mais que projeto** na hora de decidir o que o
  painel lateral mostra — clicar numa linha de alocação (pessoa dentro de um projeto, em
  qualquer uma das duas grades) abre o resumo da pessoa, não o custo do projeto; só a linha
  de topo do projeto (sem pessoa nenhuma) abre custo (antes era o contrário: qualquer linha
  com projeto resolvível ganhava do resumo de pessoa); (3) o resumo de pessoa trocou a lista
  de barras por um **gráfico de área empilhado** (uma camada por projeto, SVG à mão, mesma
  paleta `CUSTO_CORES` do painel de custo) com legenda, alternando horas/% conforme o botão
  "Exibir" da toolbar (`S.displayUnit`) — igual à grade principal; "Principais projetos"
  (sempre em horas) fica logo abaixo do gráfico+legenda; por último, "Alocação mês a mês"
  mostra as duas unidades juntas (horas grande, % pequeno do lado); (4) `pessoa.fim_contrato`
  aparece na ficha, e fica em vermelho quando já passou da data de hoje.
- **2026-09-11** — **Corrige filtro de GP vazio (dados vindos do BI) e reforça o
  scroll automático pro mês atual.** (1) O filtro de GP (`gp-filter`) era ancorado em
  `projeto.matricula_gp`, mas a importação do BI só preenche `gestor_projetos` (nome livre)
  — nunca a matrícula (`_BI_PROJETO_FIELDS` não inclui `matricula_gp`; só o import de
  planilha por projeto ou edição manual/cadastro grava isso). Resultado: pra qualquer banco
  cuja origem principal seja o BI (praticamente todos, hoje), o dropdown só mostrava "Todos
  os GPs" e "— sem GP —" — nenhum GP de verdade, mesmo com o nome já aparecendo nas linhas
  da grade. Corrigido em `frontend/js/main.js` (`gpDe`/`gpNome`/`refreshGpFilter`): a chave
  do filtro agora cai pro nome (`gestor_projetos`, prefixo interno `"nome:"` pra não colidir
  com uma matrícula) quando não há matrícula conhecida. Limitação aceita: se o mesmo GP
  tiver alguns projetos com matrícula e outros só com nome, aparecem como duas entradas
  separadas no filtro — não há hoje um cruzamento nome→matrícula confiável pra unificar.
  (2) `scrollToCurrentMonth()` (rola as duas grades pro mês atual ao carregar) reforçado com
  2 frames de `requestAnimationFrame` + um reforço via `setTimeout(80ms)`, porque em pelo
  menos um caso reportado pelo usuário o scroll não "colava" após 1 frame só (não foi
  possível confirmar a causa exata sem ferramenta de browser neste ambiente — é uma correção
  defensiva, não uma causa-raiz confirmada; **pedir pro usuário confirmar** que resolveu).

- **2026-09-11** — **Painel lateral ganha resumo de pessoa; custo mostra valor no
  centavo.** O painel à direita (antes só "Custo do projeto") agora responde ao que foi
  clicado por último em qualquer uma das duas grades: clicar num projeto mostra o custo
  (como antes); clicar numa pessoa (linha de topo da grade Por Recurso, ou qualquer linha
  de alocação — que também identifica a pessoa) mostra um **resumo da pessoa**: nome, ficha
  (vínculo/carga diária/capacidade mensal/fim de contrato, de `pessoa`), alocação mês a mês
  a partir do mês atual (cor vermelho/amarelo reaproveitada de `cor_pessoa_mes`) e os
  principais projetos em que está alocada. **Resumo de pessoa nunca mostra custo** — só
  alocação (`_PESSOA_PUB` já exclui `valor_hora`/`remuneracao` do `/api/estado`, então o
  dado nem chega ao cliente). Implementado em `frontend/js/main.js`
  (`renderResumoPessoa`, `openResumoPessoa`, `S.secModo`) + CSS novo em `styles.css`. Sem
  alteração de schema/API — usa dados que `/api/estado` já entrega
  (`grade.por_recurso`, `pessoas`). **Fora do escopo agora** (a pedido do usuário): botão
  pra desabilitar a troca automática de conteúdo do painel ao alternar seleção entre
  projeto/pessoa — fica pra depois.
  Também: a tabela "Custo por mês" (e o novo total ao lado do título) passou a mostrar o
  valor **completo, no centavo** (`_brlFull`, `Intl`/`toLocaleString` com `currency: "BRL"`)
  — só o KPI grande no topo continua abreviado (ex. "R$ 64 k"), com o valor exato no
  `title` (tooltip). O rótulo desse KPI também foi trocado de "Total (daqui pra frente)"
  pra **"Total (mês atual em diante)"** — mais formal.
- **2026-09-11** — **Ajuste fino da terminologia da tela Versões**, a pedido direto do
  usuário (revisão sobre a diretiva anterior deste mesmo dia): (1) "Alterações pendentes" →
  **"Alterações não salvas"** (`docs/TERMINOLOGIA.md` §15, §19, §22 atualizados) — "pendente"
  soava como algo esperando aprovação, não uma ação de salvar que falta fazer; (2) contagem
  vem primeiro e concorda em número/gênero, sem parênteses — **"1 alteração não salva"** em
  vez de "alterações não salvas (1)"; (3) os termos Git **não desaparecem da interface** —
  continuam nos tooltips dos botões da toolbar, entre parênteses, no fim da frase: "Salva uma
  versão. (commit)", "Cria um novo cenário e já passa a trabalhar nele. (branch + switch)",
  "Incorpora outro cenário ao atual. (merge)", "Descarta as alterações não salvas. Volta ao
  estado da última versão salva. (reset)" — exemplos canônicos em `docs/TERMINOLOGIA.md` §20;
  (4) tirado o "TODAS" em caixa alta do tooltip de descarte global (lido como grito, não
  como ênfase) e o botão perdeu o "pendentes" do rótulo (agora só "Descartar alterações" —
  o alvo fica no tooltip); (5) o prompt de "Novo cenário" perdeu a explicação parentética
  ("a partir daqui, leva as pendências junto") — ficou só "Nome do cenário novo:";
  (6) as **mensagens automáticas de commit** (`backend/app/versao.py` e `bi_import.py`, não
  só rótulo de UI) também passaram a usar Corrente/Publicado/incorporar em vez de
  main/BI/merge cru: import do BI grava mensagem "Importação do Publicado em AAAA-MM-DD"
  (era "Importação BI ..."); merge sem conflito grava "Incorporar <origem> em <destino>" já
  traduzidos (era "Merge da branch 'X'"). Novo helper `_nome_cenario()` em `versao.py`,
  espelho do `nomeCenario()` do frontend.
- **2026-09-11** — **Corrige desenho do grafo de Versões** (`layoutGrafo`/`paintVer` em
  `frontend/js/main.js`). Dois bugs na raia que só "passa de raspão" por uma linha sem ser
  o commit dela: (1) convergia visualmente com outra raia assim que as duas passavam a
  apontar pro mesmo commit-alvo (`botLanes.indexOf`), fundindo as linhas um commit **antes**
  da hora, em vez de na linha do commit-alvo de fato; (2) o trecho que chega no nó de
  convergência usava a cor da raia de **destino**, não da raia de **origem**, fazendo a
  linha "trocar de cor" bem no ponto de fusão. Sintoma relatado: um fast-forward que criou
  dois commits irmãos (`origem='bi'`) parecia, na tela, uma sequência linear/bugada em vez
  de um garfo curto que se fecha de novo. Sem teste automatizado — `layoutGrafo` é lógica
  pura (sem DOM), testável isoladamente, mas o projeto não tem nenhuma infra de teste de
  frontend (sem Node.js neste ambiente; ver item em "Itens em aberto"). Verificado por
  simulação manual do algoritmo (Python replicando a lógica) comparada ao grafo real do
  banco, não por suíte automatizada.
- **2026-09-11** — **Diretiva de terminologia da interface** ([`docs/TERMINOLOGIA.md`](TERMINOLOGIA.md)).
  Formaliza o vocabulário canônico (Pessoa/Recurso/Colaborador/Usuário/Equipe/Tipo de
  alocação, já alinhado com §1-A) **e** a tradução da camada de versionamento Git-like para
  linguagem de gerente de projeto: commit→**Versão** (ação: Salvar versão), branch do
  usuário→**Cenário**, `main`→**Corrente**, branch `BI`→**Publicado**, checkout→**Abrir
  cenário**, merge→**Incorporar cenário**, merge conflict→**Conflito de alterações**,
  log→**Histórico de versões**, working sujo→**Alterações não salvas**, reset→**Descartar
  alterações**, refresh→**Atualizar**. Termos técnicos (`main`, `commit`, `branch`, nomes
  internos/API) continuam como estão — só a UI muda; termo Git pode aparecer secundário em
  tooltip/parênteses. Aplicado em `index.html`/`main.js` (tela Versões, diálogo Arquivos,
  grade de projeto) nesta mesma data — sem teste automatizado (é troca de texto; o projeto
  não tem infra de teste de frontend, ver item em "Itens em aberto").
- **2026-09-10** — **Vocabulário canônico + fim da colisão `tipo_alocacao`/"equipe".**
  Nova seção §1-A define Pessoa, Colaborador, Recurso, Membro de equipe/projeto, Usuário,
  Nome e Equipe como facetas/conceitos distintos, não sinônimos. Corrigida a colisão real
  já existente: `tipo_alocacao` (balde de custo/fonte do projeto) vinha sendo chamado de
  "equipe" em vários pontos deste documento (e em `CLAUDE.md`), colidindo com
  `pessoa.equipe` (time organizacional da pessoa, vindo do extrato BI). Daqui em diante
  "equipe" só significa `pessoa.equipe`. Troca de labels equivalente na UI/backend
  (`index.html`, `main.js`, `main.py`) entra à parte.
- **2026-09-10** — **Import do BI em duas fases: relatório → confirmação.**
  `POST /api/importar-bi` lê os arquivos e devolve um relatório (`resumo`) sem commitar nada
  (`bi_import.preparar_importacao`, guardado em `bi_pendente`); só grava o commit no branch
  `BI` se o usuário chamar `POST /api/importar-bi/confirmar`
  (`confirmar_importacao`) — `POST /api/importar-bi/descartar` cancela. Projeto/pessoa que a
  leitura precisou criar pra montar o relatório são desfeitos se não for confirmado
  (`_podar_estrutural_novo`). `importar_arquivos` = atalho prepara+confirma (usado pelo
  `/api/importar-upload` esperto). Frontend: diálogo de relatório com "Confirmar e
  commitar" / "Descartar" antes de qualquer commit do BI.
- **2026-09-10** — **BI vira branch (`BI`), B-lite + correção do vazamento.** Bug: o import
  do BI arrastava projetos/pessoas criados na mão para dentro do commit `origem='bi'` (o
  `_sync_base_campos` copiava o working inteiro). Agora `_rebuild_baseline` monta o
  estado-alvo do BI como dict = `materializar(topo do BI)` + só o footprint dos extratos
  (`_BI_PROJETO_FIELDS` / `_BI_PESSOA_FIELDS`). O commit vai pro branch **`BI`** (criado
  preguiçosamente colado na `main`); a `main` faz **fast-forward** enquanto não tiver commit
  próprio à frente, e depois de divergir a importação só avança o `BI` — traz-se com
  `merge BI`. `versao.commitar_estado_bi(E_bi, msg)` / `versao.garantir_bi`.
- **2026-09-10** — **Import do BI = recarga da baseline, sempre igual.** Monta o estado
  completo do BI e grava **um commit na `main`** (`origem='bi'`, `autor='BI'`, sem 3-way).
  Não encosta no working de ninguém — `importar_arquivos` fotografa o working e reancora só
  as edições de alocação/janela pendentes (campos de pessoa/projeto pertencem ao BI). HEAD
  numa branch de cenário → o commit ainda vai pra `main` e a branch é restaurada.
  `versao.commitar_estado_bi` / `escrever_working` / `aplicar_delta`. Dois botões de import:
  **Importar do BI** (`/api/importar-bi`) e **Importar planilha de projeto**
  (`/api/importar-projeto`, recusa extrato do BI). `valor_hora` / `remuneracao` deixaram de
  vazar em `/api/estado` (whitelist `_PESSOA_PUB`); `valor_hora` só muda com `colabmescusto`
  no lote.
- **2026-09-09** — **Multiusuário: backend** ([`docs/COLABORACAO.md`](COLABORACAO.md)).
  Tabelas `usuario` (identidade sem login) e `rascunho` (um por `autor`×branch, edit-set
  JSON). `versao.commitar_rascunho`: promove um rascunho a commit de 1 pai via 3-way contra
  o topo da branch, com os **4 níveis** de fricção (1–2 transparentes, 3 pede confirmação
  com digest, 4 = conflito real). Endpoints `GET/POST /api/usuarios`,
  `GET/PUT/DELETE /api/rascunho`, `POST /api/rascunho/commitar` (`autor` no corpo ou header
  `X-Autor`). **Aditivo** — edição de célula, `head_` e `working` seguem ativos; a migração
  do frontend (rascunho no cliente, aposentar os endpoints antigos) fica pendente.
- **2026-09-09** — **Plano multiusuário** ([`docs/COLABORACAO.md`](COLABORACAO.md), agora
  parcialmente implementado). Sem cópia local. Identidade sem login (`X-Autor`). Larga o `checkout`:
  trabalha-se *a partir de um commit*, salva-se *como commit*, comunicação por número de
  commit. Rascunho autorado no cliente e **autossalvo** no servidor (`rascunho` por
  `(autor, branch)`, edit-set JSON, debounce 3 s). Branches compartilhadas e visíveis.
  Salvar = commit de 1 pai (3-way vs. o topo da branch). `head_` e `working` editável saem.
- **2026-09-09** — **Merge 3-way (Fase 2 do versionamento).** `versao.merge(origem)`:
  fast-forward quando possível, senão 3-way contra o LCA no DAG. Conflito por célula
  (`mes`) / linha (`projeto`/`pessoa`) → `merge_conflito`; `merge_estado` = `MERGE_HEAD`.
  `resolver_conflito` / `concluir_merge` (commit de 2 pais, `origem='merge'`) /
  `abortar_merge`. `commit` e `checkout` bloqueiam durante o merge. Endpoints
  `POST /api/versao/merge`, `GET /api/versao/conflitos`, `POST /api/versao/conflito/resolver`,
  `POST /api/versao/merge/{concluir,abortar}`.
- **2026-09-09** — **Versionamento estilo Git no banco (`user_version = 2`).** Grafo global
  de commits (`commit_` / `ref_` / `head_`) + delta por commit (`chg_*`, chave natural,
  lápide). Cache do HEAD = `baseline_alocacao*` + `base_projeto` / `base_pessoa` /
  `base_projeto_periodo`; `baseline_meta` / `baseline_pessoa` / `baseline_projeto` removidas.
  Módulo `app/versao.py`: `commit` / `checkout` / `descartar` / `branch` / `log` (Fase 1;
  merge = Fase 2). Import e export **deixaram de commitar** — mudanças ficam pendentes até
  `POST /api/versao/commit`; rebuild do BI registra um commit `origem='bi'`. Endpoints
  `/api/versao/*`; `/api/estado` ganhou o bloco `versao`; `marcar-baseline` removido.
  Pasta do projeto renomeada `apontamento` → `alocacao`. Ver `docs/VERSIONAMENTO.md`.
- **2026-09-09** — **Banco não guarda alocação zerada.** `alocacao_mes` /
  `baseline_alocacao_mes` só têm `horas > 0`; ausência de linha == `0`. Limpar célula
  → `DELETE` do mês; zerar a linha toda → `alocacao` some do working (e sai zerada no
  export se estava na baseline). Import por projeto e rebuild do BI descartam meses `0` e
  linhas vazias. Migração `user_version=1` poda o que já existia (215 meses, 14 alocações,
  84 meses de baseline, 9 alocações de baseline). `.xlsx` exportado inalterado (continua
  escrevendo `0` nos meses sem lançamento).
- **2026-09-09** — Sync: rola até o **container de 1º nível** (nome da pessoa / do projeto)
  no topo, não a sub-linha; expande e destaca a alocação equivalente. No sentido
  recurso→projeto, **recolhe os grupos irmãos** do projeto (abre só o do alvo) para o
  título do projeto não ser empurrado para fora.
- **2026-09-09** — Sincronização das grades: seleção agora é **só a linha clicada** (por
  `alocacao_id`, não mais todas as linhas da pessoa); sync nos dois sentidos com **expansão
  do ancestral** quando a linha-alvo está recolhida; rola o alvo para o topo.
- **2026-09-09** — **Undo/Redo** (Ctrl+Z / Ctrl+Y) para edições de célula — pilha no
  cliente, reaplica via `mes-lote`. Botões `↶ ↷` na toolbar.
- **2026-09-09** — Import de arquivo de projeto que já existe **não pula mais** — atualiza
  o **working** (BI = baseline, Excel por projeto = working). Projeto novo continua criando
  + virando baseline. Status `updated`.
- **2026-09-09** — Filtro **"só ativos"** (padrão): esconde projeto/pessoa sem alocação de
  hoje em diante; botão "todos" revela. Projetos `Encerrado` marcados. `id_projetos <= 0`
  do BI ignorados.
- **2026-09-09** — **BI monta o banco inteiro.** `projetos.xlsx` → tabela `projeto` (todos
  os ~123); `colabmescusto.xlsx` → **reconstrói a baseline** (alocação, todos os meses).
  Working preservado; projeto sem alocação ganha cópia da baseline. Botão **↺ Descartar**
  (por projeto e global — `POST /api/descartar-tudo`) volta o working à baseline.
- **2026-09-09** — BI v2: novo extrato `projetos.xlsx` → `projeto.gestor_projetos` (+
  empresa/status) e **filtro por GP** na toolbar (filtra as duas grades). `pessoa.valor_hora`
  derivado do `colabmescusto`. Import do BI passou a **sobrescrever** os campos que vêm dele.
  Botão **⌾ definir baseline = estado atual** por projeto (`POST
  /api/projetos/{id}/marcar-baseline`) para descartar o diff pendente.
- **2026-09-09** — **`pessoa_nova` eliminada** — tabela única `pessoa` com todos os campos
  (migração automática move os dados e dropa a tabela). A aba `Novos_Pesquisadores` do
  export passa a ser **diff-driven** (pessoa nova/alterada vs. `baseline_pessoa`).
  Versionamento estendido: `baseline_pessoa` + `baseline_projeto` por projeto, capturados
  junto com a alocação. Desenho do "Git do banco inteiro" (commits/branches) registrado na
  seção 3.
- **2026-09-09** — Correções da camada Excel: `mes-lote` recebia `alocacao_id` mas o front
  mandava `alocacaoId` (arrastar/colar/Delete não gravavam); seleção agora usa
  `background-image` (aparece sobre células amarelas/vermelhas); setas durante a edição
  fazem commit + mover; clicar em qualquer lugar da grade dá foco de teclado a ela.
- **2026-09-09** — Edição estilo Excel (`grid-excel.js`): seleção de célula/bloco,
  navegação por setas/Tab/Enter, digitar/F2/duplo-clique edita, Delete zera, **alça de
  preenchimento** (arrastar copia), **Ctrl+C/Ctrl+V** como TSV. Escritas em lote via
  `PUT /api/alocacao/mes-lote`. **Clique agora só seleciona** (edição = digitar/F2/duplo).
  Botões ⊟/⊞ movidos para a barra de cada grade (por tabela).
- **2026-09-09** — Marca de diff também nos **totais** (projeto/tipo/pessoa) para mudanças
  indiretas. Estado inicial das grades = **tudo recolhido**; botões **⊟ recolher** /
  **⊞ expandir** na toolbar (`S.open` = nós expandidos).
- **2026-09-09** — Grade "Por Projeto" ganhou o nível **tipo de alocação/equipe**
  (projeto → tipo → pessoa), com subtotais por grupo e `<select>` para mover a pessoa de
  tipo (`PUT /api/alocacao/{id}/tipo`). **Marcas de diff vs. baseline**: triângulo no canto
  da célula alterada (+ tooltip "era: X"), faixa "novo" em linha nova, riscado em removida.
- **2026-09-09** — Versionamento (subconjunto do "Git-like"): tabelas `baseline_*` =
  último commit por projeto, capturadas na importação e na exportação. Export virou
  **diff base × atual** — removidos que estavam na base saem zerados; o fluxo interno
  sumiu da interface (não há mais "zerar"/"excluir do banco", só **✕ remover**; a linha
  removida fica **riscada** com **↩ restaurar** até exportar). Backfill da base para
  projetos já importados. `POST /projetos/{id}/restaurar-alocacao`.
- **2026-09-09** — Export: janela do arquivo começa no **mês da geração** (não mais no
  início da janela configurada) e vai até o último mês com dados; `dados_projeto.Mês/Ano
  Inicio da alocação` reescritos coerentes com a 1ª coluna (referência do importador).
  Corrigido `id_projeto_externo` do HBESS: 9001 → 20496 (bate com o BI).
- **2026-09-09** — Correções na grade: coluna `spacer` (folga à direita) faz o botão "Hoje"
  e o scroll inicial funcionarem em telas largas (antes clampava porque o conteúdo cabia);
  título na 1ª coluna + z-index em camadas resolvem a sobreposição do cabeçalho ao rolar
  na horizontal.
- **2026-09-09** — Exportação: popup "Exportar…" (seleção múltipla, sujos pré-marcados,
  1→`.xlsx` / vários→`.zip`), nome de arquivo `AAAAMMDD NomeProjeto.xlsx` (espaço). Ações
  por linha **⊘ zerar** (`POST /api/alocacao/{id}/zerar` — mantém a linha, exporta com 0
  para sinalizar remoção ao sistema de horas) e **🗑 excluir** (hard delete). Botão unificado
  "Importar arquivos…" agora classifica e roteia planilha de projeto **ou** extrato do BI.
- **2026-09-09** — Import dos extratos do BI (`bi_import.py`, `POST /api/importar-bi`):
  cadastro `equipe` enriquece `pessoa` (área/contrato/situação/capacidade de ~140 pessoas),
  `colabmescusto` → `bi_custo` (custo e horas por projeto/colab/mês/tipo, 2025→2028),
  `projetomescusto` → `bi_projeto`. Endpoint `GET /api/custo/projeto`. `xlsx_io` corrigido
  para células sem atributo `r=` e para `Target` de relação absoluto. Base para a fase de
  cálculo de custo do projeto.
- **2026-09-09** — UX das grades: divisória arrastável entre "Por Projeto"/"Por Recurso",
  coluna da árvore redimensionável (nas duas juntas), scroll inicial no mês atual + botão
  "Hoje". **Edição livre além da janela do projeto** — células fora de `projeto_periodo`
  não bloqueiam mais, ficam tingidas; API `PUT /alocacao/{id}/mes` aceita qualquer mês;
  `periodos_union` e o export passam a incluir meses com horas fora da janela; botão
  `＋/－` no cabeçalho estende a faixa visível. Correção do bug `el()`/`dataset` que
  abortava a renderização.


- **2026-09-09** — Especificação inicial aprovada e migrada para documento vivo no
  projeto. Arquitetura definida: banco interno como fonte da verdade + import/export
  `.xlsx`. Removida a linha de total geral por mês. Comentários do Excel não são
  reescritos no `.xlsx` (viram anotações no banco na importação).
- **2026-09-09** — Importação passou a ser por **upload de arquivo** (não mais caminho de
  pasta do servidor) e exportação por **download**, para funcionar quando o navegador está
  noutra máquina da rede. Endpoints de pasta/servidor mantidos para localhost. `run.sh`
  passa a escutar em `0.0.0.0` por padrão.
- **2026-09-09** — Implementação do v1 concluída: leitura/escrita cirúrgica de `.xlsx`,
  importação validada contra a amostra, agregações + regra de cor, API FastAPI,
  frontend vanilla com as duas grades, criar/exportar projeto, 11 testes passando.
  Ajustes durante a implementação:
  - Projeto recém-importado **não** entra como "não exportado" (`alterado_em` só muda
    em edição real).
  - Export passa a reescrever a linha 2 de `dados_projeto` a partir do banco (necessário
    para projeto criado na ferramenta; inócuo para importado).
  - Strings novas no export vão como `inlineStr` — `sharedStrings.xml` fica intacto.
  - Conexão SQLite compartilhada com `check_same_thread=False` + lock (FastAPI usa
    threadpool para endpoints síncronos).

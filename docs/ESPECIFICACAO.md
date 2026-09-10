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
| B | `Tipo Alocacao` | string de catálogo (`Técnica, Econômica, Prospecção, OffShore, TecnicaEPII, TecnicaANP`), dropdown `Planilha4!$P$2:$Q$25`. **É a "equipe do projeto"** (balde de custo/fonte). |
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

### 4b. Extratos do BI → `POST /api/importar-bi`
Recarga da baseline, **sempre igual**: monta o estado completo do BI (`bi_custo` →
alocação/janela; `colabs` → campos de `pessoa`; `projetos` → campos de `projeto`;
`colabmescusto` → `valor_hora`) e grava **um commit na `main`** (`origem='bi'`,
`autor='BI'`), pegando os valores do BI **como estão** — não há 3-way, não há tela de
conflito. Para o que o BI cobre, o BI ganha.
- `bi_projeto` / `bi_custo` são staging (não versionados).
- **Não encosta no working de ninguém.** `importar_arquivos` fotografa o working antes e,
  depois do commit, reaplica só as edições de **alocação/janela** pendentes por cima do
  estado do BI — elas seguem visíveis como diff. Edições de campo de pessoa/projeto **não**
  são reancoradas (pertencem ao BI).
- `valor_hora` só é atualizado se o `colabmescusto` está no lote; senão fica intocado.
- Recarregar o BID no decorrer do projeto = repetir isto: cada refresh = um commit na `main`.
- Se o HEAD (legado) está numa **branch de cenário** na hora do import do BI: o commit vai
  pra `main` mesmo assim, e o cache/working da branch são **restaurados** ao estado de antes
  (a branch não mexe). Projetos/pessoas novos que o BI criou passam a existir e aparecem
  como pendentes ("novo") na branch até um `merge main` — é o sinal de que há novidade do BI
  a incorporar.

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
- **"só ativos" (padrão):** só mostra projeto/pessoa com **hora alocada em mês ≥ hoje**
  (`tem_futuro`; projeto criado na ferramenta também conta). Botão alterna p/ "todos"
  (revela encerrados + histórico). `status = Encerrado` → item marcado "(encerrado)" em
  itálico. Ambos combinam.

### Grade "Por Projeto" — 3 níveis
- Nível 0: **projeto**. Total do mês (leitura, negrito).
- Nível 1: **tipo de alocação / equipe** (`TecnicaANP`, `TecnicaEPII`, `Técnica`…) — grupo
  com subtotal do mês. Um `●` no rótulo indica que o grupo tem alteração não exportada.
- Nível 2: **pessoa**. Célula = horas (**editável**), cor `(matrícula, mês)`.
- `+ adicionar pessoa` por grupo (tipo pré-preenchido); `+ adicionar tipo/equipe` por projeto.
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

Camada de planilha sobre as células editáveis das duas grades:
- **Clique = seleciona** a célula (não edita). Bloco: clique+arraste ou Shift+clique.
- **Setas / Tab / Enter** navegam entre células (pulam meses fora da janela).
- **Digitar** um caractere, **F2** ou **duplo-clique** abre a edição; `Enter`/`Tab` confirmam
  e andam; `Esc` cancela.
- **Delete/Backspace** zera a seleção.
- **Alça de preenchimento** (quadradinho azul no canto da célula ativa): arrasta para
  copiar o valor pela linha/coluna/bloco.
- **Ctrl+C / Ctrl+V**: copia/cola como TSV (troca dados com o Excel). O **destino é a
  seleção**: se maior que o bloco copiado, o bloco é **ladrilhado** (1 célula → preenche
  tudo; bloco 3×1 → repete em cada coluna do bloco selecionado).
- Escritas múltiplas (arrastar/colar) vão num só request: `PUT /api/alocacao/mes-lote`.
- **Undo/Redo** (`Ctrl+Z` / `Ctrl+Y`, botões `↶ ↷`): pilha no cliente que guarda o valor
  anterior das células de cada edição (editar/arrastar/colar/Delete) e reaplica via lote.
  Até 100 passos; ações estruturais (add/remover/mudar tipo) não entram.

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

| Módulo | Arquivo | Status |
|--------|---------|--------|
| Leitura de `.xlsx` | `backend/app/xlsx_io.py` | ✅ feito |
| Esquema do banco | `backend/app/db.py` | ✅ feito |
| Importação | `backend/app/xlsx_import.py` | ✅ feito (validado contra a amostra) |
| Agregações + cor | `backend/app/aggregate.py` | ✅ feito |
| Exportação cirúrgica | `backend/app/xlsx_export.py` | ✅ feito (valida no LibreOffice; roundtrip nos testes) |
| Template derivado da amostra | `backend/app/templates.py` | ✅ feito |
| API FastAPI | `backend/app/main.py` | ✅ feito |
| Frontend (grades) | `frontend/` | ✅ feito (v1) |
| Criar projeto | `POST /api/projetos` + `#dlg-novo` | ✅ feito |
| Import do BI + custo | `backend/app/bi_import.py`, `GET /api/custo/projeto` | ✅ dados no banco (sem UI de custo ainda) |
| Testes | `backend/tests/` | ✅ 14 passando |

**Como rodar:** `uvicorn --app-dir backend app.main:app --port 8731 --reload` → http://localhost:8731

## 13. Decisões fechadas

- Reimport de arquivo de projeto **atualiza o working** (baseline intacta); projeto novo
  cria + vira a baseline.
- `tipo_alocacao` = catálogo fixo de `Planilha4`, não editável no v1.
- Nome canônico da pessoa vem de `pessoa` no banco.
- Linhas ordenadas alfabeticamente nas duas grades.
- Template derivado do arquivo de amostra na 1ª execução.
- `ID_Filial` = atributo do projeto, default 62, sem catálogo.
- Strings no export vão como inline strings (não mexe em `sharedStrings.xml`).

## 14. Itens em aberto / a decidir com uso

- Descoberta de arquivos: só pasta, ou também seleção avulsa persistida?
- Edição/CRUD da aba `Novos_Pesquisadores` na interface (form dedicado) — escopo do v1?
- Exibir/editar anotações na grade (ícone de nota) — v1 ou depois?
- Comportamento ao remover um projeto do banco (confirmar, mover arquivo, etc.).

## Histórico de mudanças

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

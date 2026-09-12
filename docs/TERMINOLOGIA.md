# Terminologia — vocabulário do domínio e da interface

> **Diretiva registrada em 2026-09-11.** Define o vocabulário canônico do sistema e como
> ele se traduz para a linguagem da interface. Vale para toda documentação, código novo e
> UI daqui em diante; migração de nomes legados é gradual (ver §21).

## Objetivo

Padronizar a terminologia do sistema de alocação para eliminar ambiguidades entre
conceitos como pessoa, colaborador, recurso, usuário, equipe, branch, main, BI, commit e
merge.

Regra geral:
- O **modelo interno** usa termos semanticamente corretos e estáveis (podem continuar
  sendo os nomes técnicos do Git — `main`, `commit`, `branch` — como identificadores
  internos, ver §21).
- A **interface** usa linguagem natural para gerente de projetos, preservando os termos
  técnicos do Git apenas como referência secundária (parênteses/tooltip), quando útil.

## 1. Pessoa, recurso, colaborador e usuário

### 1.1 Entidade mestre: Pessoa

A entidade que representa um ser humano é **Pessoa** — termo canônico no modelo de
domínio, banco, documentação técnica e APIs novas (`pessoa`, `pessoa_id`, `nome`,
`matricula`). Intencionalmente não pressupõe tipo de vínculo: empregado, bolsista,
consultor, terceiro, pesquisador parceiro ou outro — todos são **Pessoa**.

### 1.2 Recurso

**Recurso** é usado quando a pessoa é vista sob a perspectiva de capacidade e alocação em
projetos: "Por Recurso", "Recursos do projeto", "Capacidade do recurso", "Carga do
recurso", "Alocação de recursos".

> Pessoa = entidade cadastral. Recurso = papel da pessoa no planejamento.

Não criar uma entidade independente `recurso` só para duplicar `pessoa`, salvo se o
sistema um dia suportar recursos não humanos.

### 1.3 Colaborador

Não é termo estrutural do sistema — não usar como nome de tabela/campo/variável
(`colaborador`, `colaborador_id`, `lista_colaboradores`) quando o conceito real é uma
pessoa cadastrada. Pode existir como classificação organizacional ou texto vindo de
sistemas externos (ex.: cabeçalhos do BI), mas nunca como sinônimo estrutural de Pessoa.
Código legado migra progressivamente.

### 1.4 Usuário

**Usuário** significa exclusivamente: pessoa ou identidade com acesso à aplicação. Nunca
sinônimo de "pessoa alocada". É possível existir pessoa sem usuário, e usuário associado a
uma pessoa — conceitos distintos. (Ver `docs/COLABORACAO.md` para o modelo de identidade e
papel de acesso.)

## 2. Nome

**Nome** é atributo de Pessoa (`pessoa.nome`) — nunca substituto conceitual da entidade.
Evitar `nome_id`, `lista_de_nomes` quando o que se representa são pessoas.

## 3. Equipe

**Equipe** representa um agrupamento organizacional de pessoas (`pessoa.equipe` ou
equivalente). Não é sinônimo de tipo de alocação, centro de custo ou categoria de
lançamento.

## 4. Tipo de alocação

O campo hoje associado a valores como `Técnica`, `Econômica`, `Prospecção`, `OffShore`,
`TecnicaEPII`, `TecnicaANP` continua sendo **Tipo de alocação** (ou nome funcional
equivalente, se a regra de negócio for refinada). **Nunca chamado de Equipe.**

- **Equipe** = vínculo/agrupamento organizacional da pessoa.
- **Tipo de alocação** = classificação das horas de uma pessoa dentro de um projeto.

(Esta distinção já foi corrigida no código/UI em 2026-09-10 — ver Histórico de mudanças em
`docs/ESPECIFICACAO.md` e §1-A lá.)

## 5. Estrutura semântica principal

```
Pessoa
  ↓ participa do planejamento como
Recurso
  ↓ recebe
Alocação
  ↓ dentro de
Projeto
```

Uma **Alocação** relaciona: Projeto, Pessoa, Tipo de alocação, Período, Horas.

## 6. Terminologia da interface de planejamento

Manter **Por Projeto** / **Por Recurso**.

- Visão por projeto: `Projeto → Tipo de alocação → Pessoa`.
- Visão por recurso: `Pessoa → Projeto → Tipo de alocação`.

Embora a raiz técnica seja sempre Pessoa, o título **Por Recurso** se mantém porque essa
visão representa capacidade e utilização.

## 7. Semântica do versionamento

O sistema implementa um mecanismo inspirado em Git (branches, commits, checkout, merge,
working state, history). A arquitetura técnica continua usando esses conceitos
internamente. **A interface não deve exigir conhecimento de Git** — a terminologia Git
aparece só entre parênteses, em tooltip, em ajuda técnica ou em documentação avançada.

## 8. Conceitos principais de versionamento

### 8.1 Versão
Um **commit** é apresentado como **Versão**. A ação de commit é **Consolidar alterações**
(revisado em 2026-09-12 — era "Salvar versão"; "salvar" é ambíguo porque as alterações já
estão persistidas no banco antes do commit, "consolidar" comunica melhor que é um ponto
formal do histórico, não uma gravação de dado). Nunca voltar a usar "Salvar" sozinho pra
essa ação. Pode aparecer como "Consolidar alterações (commit)" ou tooltip "Cria uma versão
(commit) no cenário atual." O estado resultante é uma **versão consolidada** — evitar
"versão salva" daqui em diante, é o mesmo problema de vocabulário.

### 8.2 Cenário
Um **branch criado pelo usuário** é apresentado como **Cenário**. Ação: **Criar cenário**
(técnico: `new branch`). Exemplos: "Cenário: Contratação de 2 pesquisadores", "Cenário:
Atraso SatSDR", "Cenário: Redução de orçamento 2027".

Um cenário representa uma linha alternativa de evolução do planejamento e pode conter
várias versões:
```
Cenário
  ├─ Versão 1
  ├─ Versão 2
  └─ Versão 3
```
(técnico: `branch → commit, commit, commit`.) **Cenário ≠ Versão.**

## 9. Estados especiais: Principal e Publicado

Os branches técnicos hoje chamados `main` e `BI` recebem nomes semânticos próprios — não
são apresentados como cenários comuns.

### 9.1 Principal
O antigo `main` é **Principal** (nome completo: **Planejamento Principal**; revisado em
2026-09-12 — era "Corrente", trocado por ser mais direto/claro) — estado vigente do
planejamento dentro da ferramenta, com as alterações já incorporadas ao plano de trabalho.
Não chamar de "Main", "Planejamento Oficial" ou "Corrente" na UI. `main` pode continuar
como nome interno da ref (ver §21).

### 9.2 Publicado
O branch hoje chamado `BI` é **Publicado** (nome completo: **Planejamento Publicado**) —
último estado conhecido como publicado/refletido no sistema corporativo externo. Não usar
"BI" como conceito semântico — BI é só a tecnologia/origem atual dos dados; o conceito
continua válido mesmo se o sistema externo deixar de ser Power BI.

## 10. Relação entre Principal, Publicado e Cenários

```
PLANEJAMENTO
  Principal
  Publicado

CENÁRIOS
  Contratação de pesquisadores
  Adiamento Projeto X
  Redução 2027
```

Não apresentar "branch main", "branch BI", "branch scenario-x" para usuários comuns.

## 11. Fluxo conceitual

```
Publicado
    ↓ atualização proveniente do sistema externo
Principal
    ↓ edições e consolidações
Principal atualizado
    ↓ exportação/publicação
Publicado
```

Cenários derivam do Principal (`Principal → Cenário A/B/C`); depois um cenário pode ser
incorporado ao Principal.

## 12. Merge → Incorporar cenário

A operação técnica `merge` é **Incorporar cenário** (técnico: `merge`). Não usar
"Mesclar" como primeira opção de linguagem — "Incorporar" comunica melhor o efeito
gerencial. Tooltip opcional: "Incorpora as alterações deste cenário ao Planejamento
Principal (merge)."

## 13. Checkout → Abrir cenário

`checkout` é **Abrir cenário** (ou "Mudar cenário", quando aplicável). Preferir "Abrir
cenário" quando o usuário seleciona explicitamente outro cenário para trabalhar. Evitar
expor "checkout" na interface principal.

## 14. Histórico

`git log`/equivalente é **Histórico de versões**. Cada entrada corresponde a uma versão
registrada, ex.:
```
Histórico de versões
10/09/2026 23:01 — Ajuste das alocações do SatSDR
10/09/2026 21:32 — Inclusão de novos pesquisadores
```

## 15. Alterações não versionadas

O estado equivalente a "working tree modificado" é **Alterações não salvas** (revisado em
2026-09-11 — era "Alterações pendentes"; "não salvas" comunica melhor que existe uma ação
pendente de salvar, não uma aprovação pendente) — alterações realizadas desde a última
versão salva. Evitar "working tree dirty"/"uncommitted changes" na interface comum.

Quando o texto tem uma contagem, o número vem primeiro e concorda em número/gênero, sem
parênteses: **"1 alteração não salva"**, **"3 alterações não salvas"** — nunca "alterações
não salvas (3)".

## 16. Descartar

`reset`/`discard` é **Descartar alterações**. Texto simples, sem "TODAS" em caixa alta —
maiúscula-gritando não é ênfase, é ruído. Parcial: "Descartar alterações deste projeto".
Global: "Descartar alterações" (o alvo — "todas as pendentes" — fica na explicação/tooltip,
não no rótulo do botão: ex. "Descarta as alterações não salvas. Volta ao estado da última
versão salva."). A interface pede confirmação quando há perda de dados de trabalho.

## 17. Conflitos

`merge conflict` é **Conflito de alterações**. Ação: **Resolver conflito**. Não é preciso
esconder "merge" da documentação técnica, mas o usuário comum trabalha com a ideia de
conflito entre alterações de dois planejamentos.

## 18. Refresh → Atualizar

`Refresh` é **Atualizar** (evitar "Refresh" em português). Quando a operação for mais
específica, preferir verbo explícito: "Atualizar dados", "Atualizar do sistema externo",
"Recarregar" — conforme o comportamento real.

## 19. Mapeamento oficial de termos

| Termo técnico/interno | Termo da interface |
|---|---|
| `pessoa` | Pessoa |
| pessoa em contexto de planejamento | Recurso |
| `colaborador` | evitar como termo estrutural |
| `usuario` | Usuário da aplicação |
| `equipe` | Equipe organizacional |
| `tipo_alocacao` | Tipo de alocação |
| `branch` (do usuário) | Cenário |
| `main` | Principal |
| branch `BI` | Publicado |
| `commit` | Versão |
| ação de commit | Consolidar alterações |
| `checkout` | Abrir cenário |
| `merge` | Incorporar cenário |
| merge conflict | Conflito de alterações |
| `log` | Histórico de versões |
| dirty working state | Alterações não salvas |
| discard/reset | Descartar alterações |
| refresh | Atualizar |

## 20. Uso dos termos Git

Não remover a terminologia Git do sistema técnico — continua em código, nomes internos,
documentação técnica, logs, API interna. **E também não some da interface**: o termo Git
some do rótulo do botão (texto grande, sempre visível), mas continua presente e visível no
tooltip (o texto que aparece ao passar o mouse), entre parênteses, no final da frase — quem
já usa Git reconhece de cara; quem não usa, ignora e segue com a explicação em português.

Exemplos canônicos (toolbar da tela Versões — `frontend/index.html`):

| Botão | Tooltip |
|---|---|
| Consolidar alterações… | Cria uma versão (commit) no cenário atual. |
| Novo cenário… | Cria um novo cenário e já passa a trabalhar nele. (branch + switch) |
| Incorporar… | Incorpora outro cenário ao atual. (merge) |
| Descartar alterações | Descarta as alterações não salvas. Volta ao estado da última versão salva. (reset) |

Regra de redação do tooltip: frase(s) curta(s) em português comum, sem CAIXA ALTA pra dar
ênfase (isso lê como grito, não como destaque), terminando com o comando Git equivalente
entre parênteses.

## 21. Regra de implementação

Antes de alterar nomes no código, separar: nome conceitual do domínio, nome apresentado na
interface, nome técnico legado/interno. **Não renomear banco ou API só por estética** se
houver risco elevado de regressão — a migração pode ser gradual. Prioridade:
1. padronizar interface;
2. padronizar documentação;
3. padronizar novos endpoints e código;
4. migrar legado quando conveniente.

## 22. Regra de documentação

A especificação viva (`docs/ESPECIFICACAO.md`, seção Vocabulário) registra explicitamente
pelo menos: Pessoa, Recurso, Usuário, Equipe, Tipo de alocação, Alocação, Principal,
Publicado, Cenário, Versão, Alterações não salvas, Incorporar cenário. Não introduzir novos
termos concorrentes sem necessidade.

## 23. Princípio final

A interface permite que um gerente de projetos use o sistema sem conhecer Git. Ao mesmo
tempo, um usuário técnico familiarizado com Git reconhece imediatamente a correspondência
da tabela do §19.

```
Planejamento → Principal / Publicado → Cenários → Versões → Incorporar
```

O modelo Git permanece como mecanismo técnico; a linguagem do usuário é a de cima.

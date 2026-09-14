# Performance — resposta incremental de edição

> **Registrado em 2026-09-12; implementado em 2026-09-12.** Motivado por medição real:
> editar uma célula na grade levava ~300-330ms de resposta, sentido como travamento. Causa
> raiz identificada e desenho da correção documentados aqui antes da implementação.
>
> **Escopo reduzido na implementação** (decisão tomada durante o trabalho, não junto do
> desenho original — ver "Fora de escopo" abaixo): `impacto_edicao` só cobre edição de
> **valor de célula numa linha que já existia e continua existindo**. Se a edição criar
> uma `alocacao` nova (undo/redo/colar recriando uma linha zerada) ou remover uma por
> completo (zerar a última hora dela), o endpoint cai pro `{"estado": _estado()}` de
> sempre — a árvore incremental não tenta corrigir mudança estrutural no cliente.
> Medido: ~1085 bytes / ~40-70ms pro caso comum (era ~1,1MB / ~300-330ms). 5 testes
> novos em `test_impacto_edicao.py`, incluindo comparação direta contra `build_grade()`
> completo pra garantir que dá o mesmo número, não só mais rápido.

## O problema, com números medidos (prod, 2026-09-12)

Toda rota mutante do backend termina devolvendo o estado **inteiro** do aplicativo —
`return {"estado": _estado()}` (ou equivalente) aparece em **~30 endpoints** em
`backend/app/main.py`, da edição de uma célula até operações de versionamento. `_estado()`
monta as duas grades completas (`aggregate.build_grade`, todos os projetos/pessoas),
o status do versionamento (`versao.estado_repo`), catálogos e pessoas — do zero, a cada
chamada.

| Etapa (medido com ~122 projetos / ~269 pessoas) | Custo |
|---|---|
| `build_grade()` (as duas grades inteiras) | ~60-100ms |
| `versao.estado_repo()` | ~36ms |
| Serializar em JSON | ~20ms |
| **Tamanho do payload** | **~1,1 MB** |
| **Tempo total até o navegador receber a resposta** | **~310-330ms** |

Editar **uma hora em uma célula** custa o mesmo que recarregar a tela inteira — porque é
exatamente isso que acontece por baixo. Isso vale pra qualquer uma das ~30 rotas, mas dói
de verdade só na que é chamada com mais frequência: edição de célula (`mes-lote`, `/mes`),
disparada a cada digitação, arrasto, colagem, undo/redo.

## Princípio da correção

Não é "devolver tudo" (o problema atual) nem "devolver só o valor bruto que mudou" (o
cliente não tem — e não deveria ter — a lógica de negócio pra saber o que isso afeta). É:
**o backend calcula a árvore de tudo que depende do que mudou, e devolve só essa árvore.**
O frontend só aplica/pinta o que chega — nunca recalcula regra de negócio (cor, total,
diff) por conta própria.

## Escopo desta fase

Só o caminho quente: **`PUT /api/alocacao/mes-lote`** e **`PUT /api/alocacao/{id}/mes`** —
as rotas por trás de digitar, arrastar, colar e undo/redo na grade. As outras ~28 rotas
mutantes (commit, incorporar, checkout, resetar, importar, criar projeto, etc.) **continuam
devolvendo `_estado()` completo** — são ações deliberadas, pouco frequentes, onde 300ms não
incomoda. Não é escopo agora; a mesma função de base pode ser reaproveitada lá depois, se
algum dia doer.

`PUT /api/alocacao/{id}/tipo` (mover pessoa de tipo_alocacao) também fica de fora desta
fase — mexe em dois grupos ao mesmo tempo (sai de um, entra no outro), é um caso mais
complexo pra tratar depois com a mesma base.

## Árvore de impacto de uma edição de célula

Editar `(alocacao_id, periodo, horas)` — ou várias de uma vez, no caso de `mes-lote` — afeta,
em cascata:

1. **A própria célula**: valor, marcador de diff vs. baseline (`baseline_alocacao_mes`).
2. **Dentro do mesmo projeto** (`por_projeto`):
   - subtotal do grupo (`tipo_alocacao`) naquele mês;
   - total do projeto naquele mês;
   - `sujo` (`alterado_em`) — já tocado hoje via `_touch_projeto`, barato.
3. **Fora do projeto — a parte que não é só "subir a árvore"**:
   - **total da pessoa naquele mês, somando TODOS os projetos dela** (`por_recurso`,
     nível 0) — muda porque uma alocação dela mudou, mesmo que em só um projeto.
   - **cor da pessoa naquele mês** (`aggregate.cor_pessoa_mes`) — depende do total acima
     vs. `capacidade_mensal`. A cor pinta **toda célula daquela pessoa naquele mês, em
     qualquer projeto** (regra já existente, ver `aggregate.py`) — então editar uma célula
     num projeto pode mudar a cor de células **em outros projetos** onde a mesma pessoa
     está alocada no mesmo mês, sem que o valor delas tenha mudado.

O item 3 é o motivo de não ser uma árvore estritamente hierárquica projeto→grupo→pessoa:
é preciso, pra cada `(matricula, periodo)` tocado, descobrir **todas as outras linhas**
daquela pessoa (`SELECT DISTINCT projeto_id, tipo_alocacao FROM alocacao WHERE
matricula=?`) pra saber quem mais precisa repintar cor/total, mesmo sem diff de valor.

**Diff agregado também precisa recalcular**, não só os totais: `totais_alterado` do grupo/
projeto/pessoa (`aggregate.tot_diff`, compara com `base_projeto`/`baseline_alocacao*`)
muda porque a soma que ele compara mudou — não é só "o total novo", é "o total novo ainda
bate ou não com a baseline".

`versao.estado_repo()` (sujo/pendente) **continua sendo recalculado por inteiro** nesta
fase — já é relativamente barato (~36ms) comparado ao resto; não vale o risco de mexer
nisso agora. Fica registrado como possível otimização futura, não como parte desta.

## Função nova: `aggregate.impacto_edicao(conn, tocados)`

`tocados` = lista de `(alocacao_id, periodo)` realmente escritos nesta requisição (uma
célula só, ou o lote inteiro de um arrasto/colagem/undo).

1. Resolve cada `alocacao_id` → `(projeto_id, matricula, tipo_alocacao)`.
2. Agrupa por `(projeto_id, tipo_alocacao)` → recalcula subtotal do grupo pros períodos
   tocados + diff vs. baseline.
3. Agrupa por `projeto_id` → recalcula total do projeto pros períodos tocados + diff +
   `sujo`.
4. Agrupa por `matricula` → pros períodos tocados, recalcula total da pessoa (soma de
   **todas** as alocações dela, não só as tocadas) + `cor_pessoa_mes` pra
   `(matricula, periodo)`.
5. Pra cada `matricula` tocada, busca todas as `(projeto_id, tipo_alocacao)` onde ela tem
   alocação — vira `linhas_para_repintar` (podem não ter diff de valor, só de cor/total).
6. Reaproveita as funções que já existem em `aggregate.py` (`cor_pessoa_mes`, `tot_diff`,
   a lógica de diff por célula) — **não duplicar regra de negócio**; extrair o necessário
   pra funções auxiliares reutilizáveis por `build_grade` (caminho completo) e por
   `impacto_edicao` (caminho incremental).

### Formato da resposta (mantém os mesmos nomes de campo de `build_grade`, pra o frontend reaproveitar renderização)

```json
{
  "alocacoes": [ { "alocacao_id": 17253, "periodo": "2026-09-01", "horas": 88, "...diff...": {} } ],
  "grupos": [ { "projeto_id": 108, "tipo_alocacao": "Técnica", "totais": {"2026-09-01": 264}, "totais_alterado": {} } ],
  "projetos": [ { "projeto_id": 108, "totais": {...}, "totais_alterado": {}, "sujo": true } ],
  "pessoas": [
    {
      "matricula": "75993",
      "totais": {"2026-09-01": 264},
      "cores": {"2026-09-01": "amarelo"},
      "linhas_para_repintar": [ {"projeto_id": 108, "tipo_alocacao": "Técnica"}, {"projeto_id": 205, "tipo_alocacao": "TecnicaEPII"} ]
    }
  ],
  "versao": { "...estado_repo() completo, como hoje..." }
}
```

## Frontend: aplicar (patch), nunca substituir

Hoje: `S.estado = (await api.editarMesLote(edits)).estado` — substitui tudo.

Depois: `aplicarImpacto((await api.editarMesLote(edits)).impacto)` — uma função nova que:
- localiza cada projeto/grupo/pessoa/alocação tocado dentro de `S.estado.grade` (por id/
  matrícula) e atualiza só esses nós in-place;
- pra cada entrada de `linhas_para_repintar`, localiza a linha correspondente (mesma
  pessoa, outro projeto/tipo) e atualiza só cor/total dela, sem mexer no valor da célula;
- depois de aplicar o patch, chama o `render()` normal — a função de renderização em si
  não muda, só para de receber um `S.estado` inteiro novo e passa a receber o mesmo
  `S.estado` com uns nós atualizados.

Undo/redo (`undo()`/`redo()` em `main.js`) passam pelo mesmo endpoint (`editarMesLote`),
então ganham o mesmo tratamento de graça.

## Fora de escopo desta fase

- As ~28 outras rotas mutantes continuam com `_estado()` completo.
- `PUT /api/alocacao/{id}/tipo` (mover de tipo) fica de fora — mais complexo, depois.
- Otimizar `versao.estado_repo()` — já é barato o suficiente por ora.
- **Criação/remoção de linha por esta edição** (linha nasce ou some por completo) — cai
  pro `_estado()` completo em vez de tentar corrigir a árvore incremental (ver nota no
  topo do documento). É o caso mais raro (zerar a última hora de uma linha, ou digitar
  na primeira célula de uma linha nova/recriada por undo) — o caminho quente de verdade
  (digitar/arrastar/colar num valor já existente) não passa por aqui.
- **`tem_horas_no_periodo`/`visivel_no_periodo`** (visibilidade de linha/projeto sob o
  filtro de período, §8 do ESPECIFICACAO.md) não são recalculados no patch incremental —
  ficam como estavam até a próxima ação que traga `_estado()` completo. Só importa no
  caso raro de editar exatamente a célula que decide se uma linha entra/sai da janela
  visível; não afeta valor, total, cor ou diff, só a visibilidade da linha em si.

## Verificação

- Medir de novo com `curl -w "%{time_total} / %{size_download}"` (mesmo método usado pra
  diagnosticar) — confirmar que o payload de uma edição cai de ~1,1MB pra poucos KB e o
  tempo de resposta cai proporcionalmente.
- Teste novo comparando, pra uma edição dada, o resultado de `impacto_edicao` contra o
  subconjunto equivalente extraído de um `build_grade()` completo antes/depois da mesma
  edição — garante que não é só mais rápido, é **igual** ao que o caminho completo daria.
- Rodar a suíte inteira — nenhuma rota fora do escopo desta fase deve mudar de
  comportamento.

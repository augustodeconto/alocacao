# Plano — Multiusuário (rascunhos + identidade)

> **Status:** planejado, não implementado. Decisões fechadas na conversa de 2026‑09‑09,
> continuação de `VERSIONAMENTO.md`. Quando implementar, o essencial migra para
> `ESPECIFICACAO.md`.

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

## Em aberto

1. ~~Salvar com a branch adiantada: transparente ou sempre revisar?~~ **Decidido:** 4
   níveis (ver "Quanto o salvar incomoda"). Transparente nos níveis 1–2; bloqueio com
   *digest* (não revisão célula a célula) no nível 3 — commitou por cima de algo que mexeu
   no seu território, então você olha antes; nível 4 (conflito real) sempre trava.
   Preferência `usuario.sempre_revisar` promove o nível 2 ao digest.
2. Rascunho pode ser renomeado / ter mais de um por (autor, branch)? — hoje: **não**, um só.
3. `main` protegida contra `salvar` direto (forçar branch + merge)? — provável que **não**
   no v1, mas fácil de ligar depois (`ref_.protegida`).
4. Autossave: e se o `PUT` falhar (rede)? Cliente mantém em `localStorage` e re‑tenta;
   badge "não salvo" enquanto pendente.
5. Limpeza: rascunho abandonado há muito tempo — expira? notifica o autor?
6. Concorrência real no `commitar`: dois `commitar` na mesma branch ao mesmo tempo — o
   `_lock` serializa; o 2º recalcula o merge base contra o topo já avançado. Confirmar que
   o caminho aguenta.

## Ordem de implementação sugerida

1. `usuario` + `rascunho` (schema) + `X-Autor` no cliente e no `req()` da API.
2. `GET/PUT/DELETE /api/rascunho`, autossave no cliente (debounce 3 s + `localStorage`).
3. Grade a partir de `materializar(ref tip)` + overlay do rascunho no cliente (mover o
   cálculo de diff da tela para o cliente).
4. `POST /api/rascunho/{id}/commitar` (3-way vs. tip, commit de 1 pai, consome o rascunho).
5. Aposentar os endpoints de edição de célula; remover `head_`.
6. Seletor de branch = seletor de rascunho na toolbar; lista de branches compartilhada.
7. Revisar export (exporta de um commit) e o cache `baseline_*`/`base_*`.
8. Migrar o texto relevante para `ESPECIFICACAO.md`.

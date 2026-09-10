# Planejamento de Alocação

Editor multi-projeto do planejamento mensal de alocação de pesquisadores.
Importa os `.xlsx` de projeto para um banco interno (SQLite), edita em duas grades
sincronizadas (por projeto / por recurso) e exporta de volta para `.xlsx`
preservando o template.

Especificação viva: [`docs/ESPECIFICACAO.md`](docs/ESPECIFICACAO.md).

## Rodar

```bash
./run.sh            # perfil "prod": backend/alocacao.db,     porta 8731
./run.sh dev        # perfil "dev":  backend/alocacao-dev.db, porta 8732  (p/ brincar/testar)
./run.sh dev --seed # copia prod -> dev antes de subir (dados realistas p/ mexer à vontade)
```

Os dois perfis são a **mesma aplicação**, bancos e diretórios (`uploads/`, `exports/` vs
`uploads-dev/`, `exports-dev/`) separados — dá para rodar os dois ao mesmo tempo, em abas
diferentes do navegador. `--reload` recarrega o código nas duas instâncias.

Abrir <http://localhost:8731> (prod) ou <http://localhost:8732> (dev).

Overrides: `HOST=127.0.0.1`, `PORT=9000`, `ALOCACAO_DB=/caminho/x.db` antes do `./run.sh`.
Apague o `.db` do perfil para começar do zero. O template de projeto novo é derivado de
`amostras/ed425bf6-20260518_Otimizeplan.xlsx` na 1ª exportação (`templates/projeto_template.xlsx`).

## Uso

1. **Importar arquivos…** — seleciona um ou mais `.xlsx` do seu computador; são enviados
   ao servidor e carregados. Reimportar o mesmo projeto é ignorado.
2. Editar as células estilo Excel (aceita `88` ou `50%`). Toggle **Horas/%** na toolbar.
3. **Capacidade**: clique em `[cap: ?]` ao lado do nome na grade "Por Recurso".
   Vermelho = sobrealocado no mês; amarelo = subalocado.
4. **Novo projeto…** — cria no banco; vira `.xlsx` só ao exportar.
5. **⭳** na linha do projeto — baixa o `.xlsx` gerado.
6. Botão de **sincronia** (`↑↓ / ✕↓ / ↑✕ / ✕✕`) controla o sentido em que selecionar
   uma pessoa numa grade rola a outra.

## Testes

```bash
. .venv/bin/activate
cd backend && python -m pytest -q
```

## Estrutura

```
backend/app/
  xlsx_io.py       leitura de .xlsx (zip + xml)
  db.py            esquema SQLite
  xlsx_import.py   .xlsx  -> banco
  aggregate.py     grades + regra de cor
  xlsx_export.py   banco  -> .xlsx (injeção cirúrgica)
  templates.py     deriva o template da amostra
  main.py          API FastAPI + serve o frontend
frontend/          HTML + JS vanilla (sem build)
docs/ESPECIFICACAO.md   documento vivo
```

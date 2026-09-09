# Planejamento de Alocação

Editor multi-projeto do planejamento mensal de alocação de pesquisadores.
Importa os `.xlsx` de projeto para um banco interno (SQLite), edita em duas grades
sincronizadas (por projeto / por recurso) e exporta de volta para `.xlsx`
preservando o template.

Especificação viva: [`docs/ESPECIFICACAO.md`](docs/ESPECIFICACAO.md).

## Rodar

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt

# sobe API + frontend em http://localhost:8731
uvicorn --app-dir backend app.main:app --port 8731 --reload
```

Abrir <http://localhost:8731> no navegador.

O banco fica em `backend/alocacao.db` (apague para começar do zero).
O template de projeto novo é derivado de `amostras/ed425bf6-20260518_Otimizeplan.xlsx`
na primeira exportação e salvo em `templates/projeto_template.xlsx`.

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

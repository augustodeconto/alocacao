#!/usr/bin/env bash
# Sobe API + frontend. Padrão: acessível na rede local (0.0.0.0:8731).
#   ./run.sh              -> escuta em todas as interfaces (rede local)
#   HOST=127.0.0.1 ./run.sh  -> só nesta máquina
set -e
cd "$(dirname "$0")"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8731}"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r backend/requirements.txt
fi

if [ "$HOST" = "0.0.0.0" ]; then
  ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  echo "Acesse desta máquina:  http://localhost:$PORT"
  [ -n "$ip" ] && echo "Acesse da rede local:  http://$ip:$PORT"
fi

# python -m (em vez de .venv/bin/uvicorn) sobrevive a renomear a pasta do projeto,
# já que o shebang dos console-scripts do venv aponta pro caminho absoluto antigo.
exec .venv/bin/python -m uvicorn --app-dir backend app.main:app --host "$HOST" --port "$PORT" \
  --reload --reload-dir backend --reload-dir frontend

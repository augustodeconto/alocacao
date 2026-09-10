#!/usr/bin/env bash
# Sobe a API + frontend. Dois perfis = dois bancos, portas diferentes, mesma app.
#   ./run.sh              -> perfil "prod"  (backend/alocacao.db,     porta 8731)
#   ./run.sh dev          -> perfil "dev"   (backend/alocacao-dev.db, porta 8732)  p/ brincar
#   ./run.sh dev --seed   -> copia prod -> dev antes de subir (dados realistas p/ mexer)
# Rodar os dois ao mesmo tempo: `./run.sh` num terminal e `./run.sh dev` noutro.
# Overrides: HOST=127.0.0.1  PORT=9000  ALOCACAO_DB=/caminho/x.db  ./run.sh
set -e
cd "$(dirname "$0")"

PERFIL="${1:-prod}"
case "$PERFIL" in
  prod)      DB_DEF="$PWD/backend/alocacao.db";     PORT_DEF=8731; SFX="" ;;
  dev|test)  DB_DEF="$PWD/backend/alocacao-dev.db"; PORT_DEF=8732; SFX="-dev" ;;
  *) echo "perfil desconhecido: '$PERFIL' (use 'prod' ou 'dev')"; exit 1 ;;
esac

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-$PORT_DEF}"
export ALOCACAO_DB="${ALOCACAO_DB:-$DB_DEF}"
export ALOCACAO_ENV="${ALOCACAO_ENV:-$PERFIL}"
export ALOCACAO_UPLOADS="${ALOCACAO_UPLOADS:-$PWD/uploads$SFX}"
export ALOCACAO_EXPORTS="${ALOCACAO_EXPORTS:-$PWD/exports$SFX}"

if [ "$2" = "--seed" ]; then
  [ -f "$PWD/backend/alocacao.db" ] || { echo "sem backend/alocacao.db para semear"; exit 1; }
  cp -v "$PWD/backend/alocacao.db" "$ALOCACAO_DB"
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -q -r backend/requirements.txt
fi

echo "perfil $PERFIL  ·  banco $ALOCACAO_DB  ·  porta $PORT"
if [ "$HOST" = "0.0.0.0" ]; then
  ip=$(hostname -I 2>/dev/null | awk '{print $1}')
  echo "Acesse desta máquina:  http://localhost:$PORT"
  [ -n "$ip" ] && echo "Acesse da rede local:  http://$ip:$PORT"
fi

# python -m (em vez de .venv/bin/uvicorn) sobrevive a renomear a pasta do projeto,
# já que o shebang dos console-scripts do venv aponta pro caminho absoluto antigo.
exec .venv/bin/python -m uvicorn --app-dir backend app.main:app --host "$HOST" --port "$PORT" \
  --reload --reload-dir backend --reload-dir frontend

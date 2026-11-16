#!/usr/bin/env bash
#
# Lo ejecuta el comando forzado del VPS cuando GitHub Actions hace
#   ssh -p 2222 ubuntu@<host> "mcp-server"
# que resuelve a /home/ubuntu/apps/lab-ai/mcp-server/deploy.sh
#
# Todo el cuerpo va dentro de { } a propósito: bash lee el bloque entero antes
# de ejecutarlo, así el "git reset --hard" puede reescribir este mismo archivo
# a mitad de ejecución sin romper nada.

{
  set -euo pipefail

  cd "$(dirname "$(readlink -f "$0")")"
  echo "==> $(pwd)"

  if [ ! -f secrets.env ]; then
    echo "ERROR: falta secrets.env. Cópialo de secrets.env.example y pon MC_RCON_PASSWORD." >&2
    exit 1
  fi

  echo "==> Trayendo main desde origin"
  git fetch --prune origin
  git reset --hard origin/main          # nunca 'git clean': borraría secrets.env
  git --no-pager log --oneline -1

  echo "==> docker compose up -d --build"
  docker compose up -d --build
  docker compose ps

  echo "==> Logs del arranque"
  sleep 4
  docker compose logs --tail 25 mcp-lab-server

  exit 0
}

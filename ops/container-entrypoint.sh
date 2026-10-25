#!/bin/sh
set -eu
node /app/server/agy-cli.mjs --dnd-bootstrap
exec "$@"

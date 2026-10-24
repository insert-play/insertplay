#!/usr/bin/env bash
# Gera InsertPlay.zip no formato que o Decky aceita em "Install Plugin from file/URL":
# exatamente uma pasta com plugin.json na raiz do zip.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
OUT="$ROOT/out"
rm -rf "$OUT" && mkdir -p "$OUT/InsertPlay/dist"

npx -y pnpm@9 build 2>&1 | tee "$OUT/build.log"
if grep -q "plugin typescript" "$OUT/build.log"; then
	echo "Build com erros de TypeScript." >&2
	exit 1
fi

cp plugin.json package.json main.py NOTICE "$OUT/InsertPlay/"
cp -r py_modules "$OUT/InsertPlay/"
cp dist/index.js "$OUT/InsertPlay/dist/"
find "$OUT/InsertPlay" -name __pycache__ -type d -prune -exec rm -rf {} +
(cd "$OUT" && zip -qr InsertPlay.zip InsertPlay && unzip -l InsertPlay.zip)

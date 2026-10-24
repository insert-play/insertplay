#!/usr/bin/env bash
# Gera out/InsertPlay.zip no formato que o Decky aceita em "Install Plugin from ZIP File":
# exatamente uma pasta InsertPlay/ com plugin.json na raiz do zip. É o mesmo arquivo que o
# build-sd-card.sh grava no cartão (insertplay/decky/InsertPlay.zip). O zip sai do
# build_zip_from_dir do próprio plugin, que valida o pacote como o plugin fará no cartão.
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
PYTHONPATH="$ROOT/py_modules" python3 - "$OUT/InsertPlay" "$OUT/InsertPlay.zip" <<'PY'
import sys
from insertplay_decky.plugin_package import build_zip_from_dir
info = build_zip_from_dir(sys.argv[1], sys.argv[2])
print(f"{sys.argv[2]}: v{info['version']} sha256={info['sha256']} {info['size']} bytes")
PY

#!/usr/bin/env bash
# Builds out/InsertPlay.zip in the format Decky accepts in "Install Plugin from ZIP File":
# exactly one InsertPlay/ folder with plugin.json at the root of the zip. It is the file
# attached to GitHub releases and copied to cards (insertplay/decky/InsertPlay.zip). The zip
# comes from the plugin's own build_zip_from_dir, which validates the package the same way
# the plugin does when it reads it from a card.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
OUT="$ROOT/out"
mkdir -p "$OUT"
rm -f "$OUT/InsertPlay.zip"

npx -y pnpm@9 build 2>&1 | tee "$OUT/build.log"
if grep -q "plugin typescript" "$OUT/build.log"; then
	echo "Build com erros de TypeScript." >&2
	exit 1
fi

# build_zip_from_dir escolhe os arquivos (PACKAGE_ENTRIES) direto da pasta do projeto
PYTHONPATH="$ROOT/py_modules" python3 - "$ROOT" "$OUT/InsertPlay.zip" <<'PY'
import sys
from insertplay_decky.plugin_package import build_zip_from_dir
info = build_zip_from_dir(sys.argv[1], sys.argv[2])
print(f"{sys.argv[2]}: v{info['version']} sha256={info['sha256']} {info['size']} bytes")
PY

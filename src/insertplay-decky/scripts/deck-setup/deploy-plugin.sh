#!/bin/bash
# Instalado no Deck como /etc/insertplay-dev/deploy-plugin.sh (root:root 755) e liberado
# para o usuário deck SEM SENHA via /etc/sudoers.d/zz-insertplay-dev.
#
# É a única coisa que o acesso automatizado (IA/scripts) pode fazer como root:
#   deploy   copia /tmp/insertplay-decky → ~/homebrew/plugins/InsertPlay e reinicia o Decky
#   restart  reinicia o plugin_loader
#   remove   apaga ~/homebrew/plugins/InsertPlay e reinicia o Decky
#
# Por segurança recusa plugin.json com a flag "root": sem ela o Decky roda o backend do
# plugin como usuário comum, então esta liberação não vira um sudo geral.
set -euo pipefail

STAGE=/tmp/insertplay-decky
DEST=/home/deck/homebrew/plugins/InsertPlay

case "${1-}" in
	deploy)
		[[ -d "$STAGE" && ! -L "$STAGE" ]] || { echo "stage $STAGE ausente ou é symlink" >&2; exit 1; }
		[[ -f "$STAGE/plugin.json" && -f "$STAGE/main.py" && -f "$STAGE/dist/index.js" ]] \
			|| { echo "stage incompleto (plugin.json, main.py, dist/index.js)" >&2; exit 1; }
		python3 - "$STAGE/plugin.json" <<'PY' || { echo "recusado: plugin.json inválido ou pede a flag root" >&2; exit 1; }
import json, sys
data = json.load(open(sys.argv[1]))
sys.exit(1 if data.get("name") != "InsertPlay" or "root" in data.get("flags", []) else 0)
PY
		mkdir -p "$DEST"
		rsync -a --delete --no-links "$STAGE/" "$DEST/"
		chown -R root:root "$DEST"
		systemctl restart plugin_loader
		echo "InsertPlay instalado em $DEST"
		;;
	restart)
		systemctl restart plugin_loader
		;;
	remove)
		rm -rf "$DEST"
		systemctl restart plugin_loader
		echo "InsertPlay removido"
		;;
	*)
		echo "uso: $0 deploy|restart|remove" >&2
		exit 2
		;;
esac

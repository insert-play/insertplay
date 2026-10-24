#!/bin/bash
# Instalado no Deck como /etc/insertplay-dev/deploy-plugin.sh (root:root 755) e liberado
# para o usuário deck SEM SENHA via /etc/sudoers.d/zz-insertplay-dev.
#
# É a única coisa que o acesso automatizado (IA/scripts) pode fazer como root:
#   deploy   instala o conteúdo de /tmp/insertplay-decky em ~/homebrew/plugins/InsertPlay
#            e reinicia o Decky
#   restart  reinicia o plugin_loader
#   remove   apaga ~/homebrew/plugins/InsertPlay e reinicia o Decky
#
# Por segurança recusa plugin.json com a flag "root": sem ela o Decky roda o backend do
# plugin como usuário comum, então esta liberação não vira um sudo geral.
#
# O stage (/tmp/insertplay-decky) é do usuário deck e pode mudar a qualquer momento.
# Por isso nada é validado nem instalado A PARTIR dele (review do PR #2):
#   1. copiamos o stage para um snapshot privado do root (mktemp -d, 700), sem symlinks
#      e com permissões normalizadas (sem escrita para grupo/outros);
#   2. validamos o SNAPSHOT;
#   3. instalamos os bytes do snapshot.
#
# Risco residual conhecido e aceito: ~/homebrew é do usuário deck por design do Decky,
# então o deck pode trocar a pasta `plugins` inteira entre a checagem e a cópia. Isso
# não é novo: com essa mesma posse, o deck já controla o que o Decky (root) carrega no
# próximo boot. Checamos o que dá para checar (plugins é diretório real do root, o
# destino não é symlink) e não prometemos mais que isso.
set -euo pipefail

STAGE=/tmp/insertplay-decky
PLUGINS=/home/deck/homebrew/plugins
DEST="$PLUGINS/InsertPlay"

die() {
	echo "$*" >&2
	exit 1
}

check_destination() {
	[[ -d "$PLUGINS" && ! -L "$PLUGINS" ]] || die "$PLUGINS ausente ou é symlink"
	[[ "$(realpath -e "$PLUGINS")" == "$PLUGINS" ]] || die "$PLUGINS não resolve para si mesmo"
	[[ "$(stat -c %u "$PLUGINS")" == 0 ]] || die "$PLUGINS não pertence ao root"
	# PLUGINS é do root (755): o deck não consegue criar nada aqui dentro, inclusive
	# um symlink no lugar do destino; checamos mesmo assim
	[[ ! -e "$DEST" && ! -L "$DEST" ]] || [[ -d "$DEST" && ! -L "$DEST" ]] || die "$DEST não é um diretório comum"
}

case "${1-}" in
	deploy)
		[[ -d "$STAGE" && ! -L "$STAGE" ]] || die "stage $STAGE ausente ou é symlink"
		SNAP="$(mktemp -d /tmp/insertplay-deploy.XXXXXX)"
		trap 'rm -rf "$SNAP"' EXIT
		rsync -r --no-links --no-specials --no-devices --chmod=D755,F644 "$STAGE/" "$SNAP/"

		[[ -f "$SNAP/plugin.json" && -f "$SNAP/main.py" && -f "$SNAP/dist/index.js" ]] \
			|| die "stage incompleto (plugin.json, main.py, dist/index.js)"
		python3 - "$SNAP/plugin.json" <<'PY' || die "recusado: plugin.json inválido ou pede a flag root"
import json, sys
data = json.load(open(sys.argv[1]))
flags = data.get("flags", [])
sys.exit(1 if data.get("name") != "InsertPlay" or not isinstance(flags, list) or "root" in flags else 0)
PY

		check_destination
		mkdir -p "$DEST"
		rsync -r --delete --no-links --chmod=D755,F644 "$SNAP/" "$DEST/"
		chown -R root:root "$DEST"
		systemctl restart plugin_loader
		echo "InsertPlay instalado em $DEST"
		;;
	restart)
		systemctl restart plugin_loader
		;;
	remove)
		check_destination
		rm -rf "$DEST"
		systemctl restart plugin_loader
		echo "InsertPlay removido"
		;;
	*)
		echo "uso: $0 deploy|restart|remove" >&2
		exit 2
		;;
esac

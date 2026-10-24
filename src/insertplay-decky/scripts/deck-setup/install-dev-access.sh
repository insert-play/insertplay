#!/bin/bash
# Roda UMA vez, no Deck, como root (pede a senha do deck):
#   ssh -t deck@<ip> "sudo bash /tmp/insertplay-deck-setup/install-dev-access.sh"
# Instala o deploy-plugin.sh e a regra de sudo que o libera sem senha.
# Para desfazer:  sudo rm -rf /etc/insertplay-dev /etc/sudoers.d/zz-insertplay-dev
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "rode com sudo" >&2; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

install -d -o root -g root -m 755 /etc/insertplay-dev
install -o root -g root -m 755 "$HERE/deploy-plugin.sh" /etc/insertplay-dev/deploy-plugin.sh

# O sudo aplica a ÚLTIMA regra que combina, e /etc/sudoers.d é lido em ordem
# alfabética. O SteamOS tem /etc/sudoers.d/wheel ("%wheel ALL=(ALL) ALL", com senha);
# uma regra com nome anterior a "wheel" é anulada por ela (medido no Deck). Daí o "zz-".
RULE=$(mktemp)
echo 'deck ALL=(root) NOPASSWD: /etc/insertplay-dev/deploy-plugin.sh' > "$RULE"
visudo -cf "$RULE"
install -o root -g root -m 440 "$RULE" /etc/sudoers.d/zz-insertplay-dev
rm -f "$RULE" /etc/sudoers.d/insertplay-dev
visudo -c >/dev/null

echo "OK: acesso de desenvolvimento instalado."
echo "Teste: sudo -n /etc/insertplay-dev/deploy-plugin.sh   (deve mostrar o 'uso:' sem pedir senha)"

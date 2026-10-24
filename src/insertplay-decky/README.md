# InsertPlay: plugin Decky (POC)

Insira um cartão InsertPlay: o atalho do jogo aparece na biblioteca com as artes do próprio cartão e o jogo inicia. Remova o cartão: o atalho é ocultado (opção "Ocultar ao remover o cartão" no painel). Jogos no cartão inserido levam um badge de cartão SD na capa: os atalhos do InsertPlay e os jogos Steam instalados numa biblioteca do cartão. Tudo isso sem reiniciar o Steam.

O plugin **substitui o InsertPlay legado** (CLI em C#, `insertplay.service` e os atalhos criados no `shortcuts.vdf`). Se os dois rodarem juntos, o jogo é iniciado duas vezes. Num aparelho que tinha o legado, remova-o **antes** de instalar o plugin: veja [Remover o InsertPlay legado](#remover-o-insertplay-legado).

## Como funciona
- `main.py` + `py_modules/insertplay_decky/` (backend Python):
  - verifica `/run/media/*` e `/run/media/*/*` a cada 1 s;
  - lê `steam-shortcut.json` e `shortcut-identity.json` sem escrever no cartão;
  - avisa o frontend com `card_inserted` e `card_removed`.
- `src/` (frontend, roda dentro do Steam):
  - cria ou atualiza o atalho com `SteamClient.Apps.*`;
  - aplica as artes, oculta e mostra o atalho e inicia o jogo;
  - desenha o badge de cartão SD nas capas da biblioteca (`src/steam-badges.tsx`, depende do DOM do Steam).
- Registro `guid → appid` em `~/homebrew/settings/InsertPlay/registry.json`.
- Log de eventos em `~/homebrew/data/InsertPlay/events.jsonl`, também visível no painel.

Requisito do cartão: ter `shortcut-identity.json`, que o `build-sd-card.sh` gera. Sem ele o cartão é recusado com mensagem no painel.

## Desenvolvimento no Windows
```powershell
npx -y pnpm@9 install
npx -y pnpm@9 typecheck
npx -y pnpm@9 build     # leia a saída: erro de TypeScript aparece como aviso e o build termina com código 0
uvx pytest tests -q     # backend; o teste de symlink só roda em Linux/WSL
node scripts/deck.mjs check     # diagnóstico da conexão com o Deck
node scripts/deck.mjs deploy    # build + instala no Deck + reinicia o Decky
```
Todos os comandos do `deck.mjs` estão no topo de [scripts/deck.mjs](scripts/deck.mjs) e em [CLAUDE.md](CLAUDE.md). O zip para distribuição sai de `scripts/package.sh` (WSL), em `out/InsertPlay.zip`. Para testar o fluxo do cartão sem refazer o cartão: `node scripts/deck.mjs stage-card <rótulo> [--version X]`.

## Instalar e atualizar pelo cartão SD

O cartão leva o próprio plugin em `insertplay/decky/InsertPlay.zip`. Não precisa de rede nem do Modo Desktop. Só o Decky Loader precisa estar instalado.

### Gerar o pacote e colocar no cartão (no PC, WSL)
```bash
bash src/insertplay-decky/scripts/package.sh   # gera src/insertplay-decky/out/InsertPlay.zip
bash src/build-sd-card.sh                      # copia o zip para insertplay/decky/ no cartão
```
- **A cada release, suba a `version` em `package.json`.** É por ela que o plugin decide se o cartão é mais novo.
- Sem um zip novo, o `build-sd-card.sh` mantém o que já estava no cartão.

### Primeira instalação (no Deck, Modo de Jogo)
1. Insira o cartão.
2. Botão `...` → ícone do Decky → engrenagem → **Geral**: ligue **Developer mode** (só uma vez).
3. Ainda nas configurações do Decky → **Desenvolvedor** → "Instalar Plugin a partir de um arquivo ZIP" → **Navegar**. Aceite o aviso de plugin de terceiros.
4. No seletor, use o botão **↑** até chegar em `/` e entre em `run` → `media` → `deck` → `<nome do cartão>` → `insertplay` → `decky`.
5. Toque em `InsertPlay.zip` e confirme **Instalar**.

### Atualizar (no Deck, painel do InsertPlay)
A seção **Plugin** mostra a versão instalada e, para cada cartão inserido, a versão que ele traz:

| O cartão tem | Botão | O que acontece |
|---|---|---|
| versão mais nova | Atualizar plugin para vX | O Decky pede confirmação e troca o plugin pela versão do cartão. |
| a mesma versão | Reinstalar plugin do cartão | Igual ao anterior, com a mesma versão. |
| versão mais antiga, zip inválido | Atualizar cartão para vX | Pede confirmação e grava a versão instalada no cartão. |
| nenhum pacote | Gravar plugin vX no cartão | Grava a versão instalada no cartão. |

Ao inserir um cartão com versão mais nova, aparece um aviso. O plugin nunca se atualiza sozinho.

O vínculo dos cartões com os atalhos (`~/homebrew/settings/InsertPlay`) é mantido na atualização. Se você tinha ocultado ou reordenado o InsertPlay na lista do Decky, isso volta ao padrão, como numa atualização pela loja.

## Remover o InsertPlay legado

O legado (CLI + serviço) deixava no aparelho:
- o serviço `/etc/systemd/system/insertplay.service` e, se o "controller sleep" foi ligado, a regra `/etc/polkit-1/rules.d/49-insertplay-controller-sleep.rules`;
- o CLI e os scripts em `~/.local/share/insertplay` e o lançador `~/.local/bin/insertplay`;
- a configuração em `~/.config/insertplay` e os logs em `~/.local/state/insertplay`;
- atalhos não-Steam gravados direto no `shortcuts.vdf` de cada conta Steam (ex.: "SD CARD", que lança `~/.local/share/insertplay/insertplay-start-bootstrap.sh`). Sem os arquivos acima, eles deixam de funcionar.

Os cartões não dependem de nada disso: o plugin lança o `insertplay-start.sh` do próprio cartão.

### Passo 1: no Windows (PowerShell, pasta `insertplay-decky`), ver o que existe
```
node scripts/deck.mjs check [--device machine]
```
As linhas **InsertPlay legado ausente** e **atalhos legados ausentes** mostram FALHOU com o que ainda existe (arquivos; `<userdata>:<qtd>` por conta Steam).

### Passo 2: no Windows, enviar o desinstalador e rodá-lo com sudo
```
node scripts/deck.mjs legacy-uninstall [--device machine --confirm-prod]
```
Ele copia `src/insertplay-cli/assets/installer/uninstall.sh` para `/tmp` no aparelho e mostra o comando a rodar, que pede a senha do `deck`:
```
ssh -t deck@<ip> "sudo bash /tmp/insertplay-legacy-uninstall.sh --purge"
```
Esperado: uma linha `removed:` ou `already absent:` por item e, no fim, `Done: N removed, M already absent.`. Pode rodar de novo sem problema: na segunda vez tudo aparece como `already absent`. Sem `--purge`, a configuração e os logs ficam.

### Passo 3: atalhos legados, pelo próprio Steam
O script não mexe em atalhos. Com o Steam aberto **na conta que tem os atalhos** (no Modo de Jogo, troque de usuário se o aparelho tiver mais de uma conta), remova-os com `SteamClient.Apps.RemoveShortcut(<appid>)` via `deck.mjs eval`, ou pela própria biblioteca (Gerenciar → Remover atalho do jogo). Nunca edite o `shortcuts.vdf` à mão nem com o Steam aberto.

Legado é o atalho que lança algo de `~/.local/share/insertplay` ou que menciona InsertPlay/`--guid` com o executável **sem aspas** (o CLI gravava o vdf direto). Atalhos do plugin têm o executável entre aspas (`"/bin/bash"`) e opções terminando em `--guid <uuid>`. Não remova esses.

### Passo 4: no Windows, conferir
```
node scripts/deck.mjs check [--device machine]
```
As duas linhas devem dar OK. Faça isso em todas as contas Steam do aparelho: o `check` lê o `shortcuts.vdf` de todas.

## Preparar o Steam Deck (uma vez)

Tudo passa pela rede (Wi-Fi). O Deck e o PC precisam estar na mesma rede. Nenhum passo usa pendrive.

### Passo 1: no Deck (Modo Desktop → Konsole)
```bash
passwd                              # define uma senha para o usuário deck (se ainda não tiver)
sudo systemctl enable --now sshd    # liga o servidor SSH
ip -4 addr show | grep inet         # mostra o IP; use o que começa com 192.168... ou 10...
```
Anote o IP, por exemplo `192.168.0.42`. Nos comandos abaixo, troque `<ip>` por ele.

### Passo 2: no Windows (PowerShell), testar a conexão
```powershell
ssh deck@<ip>
```
- Na primeira vez ele pergunta `Are you sure you want to continue connecting?`. Digite `yes`.
- Depois pede a senha definida no passo 1.
- Se aparecer o prompt `(deck@steamdeck ~)$`, funcionou. Digite `exit` para voltar ao Windows.

### Passo 3: no Windows, parar de digitar a senha a cada deploy (opcional)
Sem este passo tudo funciona, mas o deploy pede a senha do Deck umas 3 vezes. A ideia é gerar uma chave no Windows e mandá-la para o Deck pela própria conexão SSH:
```powershell
# gera a chave (aperte Enter em todas as perguntas); pule se já existir ~\.ssh\id_ed25519.pub
ssh-keygen -t ed25519

# envia a chave pública para o Deck (pede a senha uma última vez)
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh deck@<ip> "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys"
```
Teste com `ssh deck@<ip>`: agora deve entrar sem pedir senha. O `sudo` do deploy continua pedindo a senha, e isso é normal.

### Passo 4: no Deck, instalar o Decky
- Se o Deck tinha o InsertPlay legado, remova-o antes: [Remover o InsertPlay legado](#remover-o-insertplay-legado).
- Instale o Decky Loader (instalador oficial, pelo navegador do Modo Desktop).
- Volte ao Modo de Jogo. No menu `...` (Quick Access) → ícone do Decky → engrenagem: ligue **Developer mode** e depois, em Developer, **Allow Remote CEF Debugging**.

### Passo 5: no Windows, abrir o console do Steam do Deck
No Chrome ou Edge: `chrome://inspect` → **Configure…** → adicione `<ip>:8081` → Done. Em alguns segundos aparece a lista de abas do Deck; clique em **inspect** na **SharedJSContext**.

A janela que abre é o DevTools **do Steam do Deck**: tudo o que roda ali roda dentro do Steam, como se fosse o plugin. Durante os testes do plugin, deixe-o aberto: os erros do frontend aparecem em vermelho na aba **Console**. Se o Steam do Deck reiniciar, volte ao `chrome://inspect` e clique em **inspect** de novo.

### Passo 5b: no Windows, configurar o IP e conferir tudo
Copie `deck.config.example.json` para `deck.config.json` e coloque os IPs: `deck` é o Steam Deck de desenvolvimento (padrão) e `machine` é a Steam Machine usada como produção. Esse arquivo não vai para o git. Para usar outro aparelho, acrescente `--device machine` a qualquer comando. Depois:
```powershell
node scripts/deck.mjs check
```
Aparece uma tabela OK/FALHOU: SSH, Decky, Steam via CEF, etc. A linha "deploy sem senha" só fica OK depois do passo seguinte.

### Passo 5c: acesso sem senha para a IA (uma vez)
Para a IA (ou você) instalar e testar o plugin sem digitar a senha a cada vez, instale no Deck um script fixo que o `sudo` libera **só para instalar o InsertPlay e reiniciar o Decky**. Não é um sudo geral: o script recusa qualquer plugin que peça root. No Windows:
```powershell
node scripts/deck.mjs setup-access
ssh -t deck@<ip> "sudo bash /tmp/insertplay-deck-setup/install-dev-access.sh"
```
O primeiro comando envia o instalador. O segundo pede a senha do deck uma vez e instala. Rode `node scripts/deck.mjs check` de novo: "deploy sem senha" deve ficar OK.

Para desfazer, no Deck: `sudo rm -rf /etc/insertplay-dev /etc/sudoers.d/zz-insertplay-dev`.

O script valida e instala uma cópia privada do root, nunca os arquivos que o usuário `deck` ainda pode alterar. Limite conhecido: `~/homebrew` pertence ao `deck` por design do Decky, e com essa posse o `deck` já controla o que o Decky (root) carrega no boot. O script não cria esse risco, mas também não o elimina. Use essa liberação só no aparelho de desenvolvimento.

Rode `setup-access` e o comando `ssh -t … install-dev-access.sh` de novo quando:
- **o SteamOS atualizar**: a atualização apaga `/etc/insertplay-dev` e `/etc/sudoers.d/zz-insertplay-dev` (medido na 3.8.28 → 3.9.5). O `check` mostra "deploy sem senha: FALHOU";
- `scripts/deck-setup/` mudar: o Deck usa a cópia instalada em `/etc/insertplay-dev`.

As funções do Steam que o plugin usa já foram conferidas no seu Deck com `node scripts/spike.mjs` (resultado em [docs/steam-api-notes.md](docs/steam-api-notes.md)). Rode de novo depois de atualizações do Steam.

### Passo 6: instalar o plugin
```powershell
node scripts/deck.mjs deploy
```
O plugin aparece no menu do Decky (Quick Access) como **InsertPlay**. Logs do plugin: `node scripts/deck.mjs logs`.

## Fora da POC
Ícone do atalho (`SetShortcutIcon` existe e é candidato), ocultar/mostrar manualmente, i18n, criar a identidade do cartão.

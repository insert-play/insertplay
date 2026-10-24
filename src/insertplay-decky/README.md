# InsertPlay: plugin Decky (POC)

Insira um cartão InsertPlay: o atalho do jogo aparece na biblioteca com as artes do próprio cartão e o jogo inicia. Remova o cartão: o atalho é ocultado. Tudo isso sem reiniciar o Steam.

O plugin **substitui o `insertplay-service`**. Se os dois rodarem juntos, o jogo é iniciado duas vezes. Antes de testar, rode no Deck:
```
sudo systemctl disable --now insertplay.service
```

## Como funciona
- `main.py` + `py_modules/insertplay_decky/` (backend Python):
  - verifica `/run/media/*` e `/run/media/*/*` a cada 1 s;
  - lê `steam-shortcut.json` e `shortcut-identity.json` sem escrever no cartão;
  - avisa o frontend com `card_inserted` e `card_removed`.
- `src/` (frontend, roda dentro do Steam):
  - cria ou atualiza o atalho com `SteamClient.Apps.*`;
  - aplica as artes, oculta e mostra o atalho e inicia o jogo.
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
Todos os comandos do `deck.mjs` estão no topo de [scripts/deck.mjs](scripts/deck.mjs) e em [CLAUDE.md](CLAUDE.md). O zip para distribuição sai de `scripts/package.sh` (WSL), em `out/InsertPlay.zip`.

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

### Passo 4: no Deck, instalar o Decky e desligar o serviço antigo
- Instale o Decky Loader (instalador oficial, pelo navegador do Modo Desktop).
- No Konsole: `sudo systemctl disable --now insertplay.service`. Pode dar erro se o serviço não estiver instalado; nesse caso, ignore.
- Volte ao Modo de Jogo. No menu `...` (Quick Access) → ícone do Decky → engrenagem: ligue **Developer mode** e depois, em Developer, **Allow Remote CEF Debugging**.

### Passo 5: no Windows, abrir o console do Steam do Deck
No Chrome ou Edge: `chrome://inspect` → **Configure…** → adicione `<ip>:8081` → Done. Em alguns segundos aparece a lista de abas do Deck; clique em **inspect** na **SharedJSContext**.

A janela que abre é o DevTools **do Steam do Deck**: tudo o que roda ali roda dentro do Steam, como se fosse o plugin. Durante os testes do plugin, deixe-o aberto: os erros do frontend aparecem em vermelho na aba **Console**. Se o Steam do Deck reiniciar, volte ao `chrome://inspect` e clique em **inspect** de novo.

### Passo 5b: no Windows, configurar o IP e conferir tudo
Copie `deck.config.example.json` para `deck.config.json` e coloque o IP do Deck. Esse arquivo não vai para o git. Depois:
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

As funções do Steam que o plugin usa já foram conferidas no seu Deck com `node scripts/spike.mjs` (resultado em [docs/steam-api-notes.md](docs/steam-api-notes.md)). Rode de novo depois de atualizações do Steam.

### Passo 6: instalar o plugin
```powershell
node scripts/deck.mjs deploy
```
O plugin aparece no menu do Decky (Quick Access) como **InsertPlay**. Logs do plugin: `node scripts/deck.mjs logs`.

## Fora da POC
Ícone do atalho (`SetShortcutIcon` existe e é candidato), ocultar/mostrar manualmente, i18n, criar a identidade do cartão, selos visuais na biblioteca.

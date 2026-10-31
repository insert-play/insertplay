# insertplay-decky: guia para agentes de IA

Plugin Decky Loader (POC) do InsertPlay. Visão geral, layout e preparação manual do Deck estão em [README.md](README.md). Este arquivo diz **quais ferramentas usar para trabalhar com os aparelhos reais** e as regras para usá-las.

## Aparelhos

| Nome (`--device`) | Aparelho | Papel |
|---|---|---|
| `deck` (padrão) | Steam Deck (Jupiter), na bancada | **Dev.** Ambiente principal: deploy, restart, testes destrutivos com atalhos `IP Teste…`. O usuário mexe nele fisicamente quando preciso. |
| `machine` | Steam Machine (SteamOS, GPU Navi33), na sala gamer | **Prod.** Simula o uso real. Fica mais longe: peça intervenção física só quando necessário. |

Regras da Steam Machine (prod):
- Por padrão, só leitura: `check`, `eval` de consulta, `logs`, `screenshot`, `panel`, `rpc` de leitura.
- O fluxo real de instalação (zip do cartão / "Install Plugin from ZIP File" do Decky) já foi validado nela. Para testar uma versão nova, o `deploy --device machine --confirm-prod` por SSH também vale.
- **Confira o estado antes de qualquer instalação ou teste:** ela fica em outro ambiente e alguém pode estar jogando nela. Antes de `deploy`, `restart`, `remove` ou de pedir para abrir um jogo, rode `check` e `eval "SteamUIStore.RunningApps.map(a => a.display_name)"`. Com jogo aberto, não faça nada e pergunte ao usuário: o deploy reinicia o Decky no meio da partida de alguém.
- `deploy`, `remove`, `restart`, `stage-card`, `setup-access` e `legacy-uninstall` exigem `--confirm-prod`. Só use depois de combinar com o usuário **naquela sessão**.
- Não rode o `spike.mjs` nela: ele cria atalhos de teste.
- Ela tem plugins que o Deck não tem (SteamGridDB, PlayCount). Algo que funciona no Deck pode se comportar diferente lá. Non-Steam Badges e MicroSDeck foram desinstalados em 2026-10-24: o badge de cartão do próprio InsertPlay os substitui.
- Não altere atalhos que o plugin não criou. O InsertPlay legado (CLI + `insertplay.service`) foi removido dos dois aparelhos em 2026-10-24; se o `check` voltar a acusar legado, siga "Remover o InsertPlay legado" no README.

Os aparelhos ficam em `deck.config.json` (fora do git; modelo em `deck.config.example.json`): `{"default": "deck", "devices": {"deck": {"host", "user", "role"}, "machine": {...}}}`. O formato antigo, `{"host"}` na raiz, ainda vale como o aparelho `deck`. Todo comando aceita `--device <nome>`, em qualquer posição, ou a variável `INSERTPLAY_DEVICE`, e mostra no stderr qual aparelho está usando. Os IPs vêm de DHCP e podem mudar: se o `check` falhar na conexão, peça ao usuário o IP atual e atualize o arquivo.

## Acesso aos aparelhos

Pré-requisitos, feitos uma vez e à mão pelo usuário (README, passos 1 a 5 e "Acesso sem senha para a IA"):
- SSH com chave para `deck@<ip>`;
- Decky com "Allow Remote CEF Debugging";
- a regra de sudo restrita instalada por `setup-access`.

Todos os comandos rodam da pasta `insertplay-decky`, no Windows ou no WSL. Use `node scripts/deck.mjs <comando> [--device machine]`:

| Comando | Para quê |
|---|---|
| `check` | **Comece por aqui.** Diagnóstico: SSH, Decky ativo, deploy sem senha, plugin instalado, InsertPlay legado (arquivos e atalhos em todas as contas Steam), Steam via CEF. Também lista "atalhos do plugin fora do registro" (issue #8). |
| `deploy [--skip-build]` | Build (falha se houver erro de TypeScript), envio para `/tmp/insertplay-decky` e instalação via `sudo -n /etc/insertplay-dev/deploy-plugin.sh deploy`, que reinicia o Decky. |
| `logs [n]` | Últimas linhas do `journalctl -u plugin_loader` filtradas por "insertplay". |
| `eval "<js>"` | Executa JS dentro do Steam (aba SharedJSContext, onde roda o frontend do plugin). Promises são aguardadas; o valor volta em JSON. |
| `screenshot [nome]` | Salva a tela atual do Steam em `out/screens/<nome>.png`. Leia a imagem para conferir a interface. |
| `panel [nome]` | Abre o Quick Access no painel do InsertPlay e captura. O Quick Access é outra janela do CEF (`QuickAccess_uid2`); o `screenshot` comum não o pega. Feche depois com `eval "(SteamUIStore.m_WindowStore ?? SteamUIStore.WindowStore).SteamUIWindows[0].MenuStore.CloseSideMenus()"`. |
| `rpc <método> [args JSON…]` | Chama um método do `main.py` pelo Decky, como o frontend faz. Ex.: `rpc log_tail 5`, `rpc current_cards`. Internamente usa `DeckyBackend.call("loader/call_plugin_method", "InsertPlay", método, ...args)`: os argumentos vão **espalhados**; uma lista vira um argumento a mais. |
| `ssh "<cmd>"` | Comando no Deck como usuário `deck`, sem TTY e sem senha. |
| `stage-card <rótulo> [--version X]` | Build e gravação do zip do plugin no cartão montado (`insertplay/decky/InsertPlay.zip`), validado pelo mesmo `build_zip_from_dir` do plugin. `--version` muda a versão só no zip, para testar "cartão mais novo/antigo". |
| `restart` / `remove` | Reiniciam o Decky / desinstalam o InsertPlay. |
| `legacy-uninstall` | Envia o desinstalador do InsertPlay legado para `/tmp` e mostra o `ssh -t … sudo …` que **o usuário** roda (não há sudo para a IA fora do deploy). Atalhos legados saem pelo Steam (`RemoveShortcut`), nunca editando o `shortcuts.vdf`. |

Outros:
- `node scripts/spike.mjs`: confirma as chamadas da API do Steam (ver [docs/steam-api-notes.md](docs/steam-api-notes.md)). Rode depois de atualização do Steam ou ao usar uma chamada nova.
- `node scripts/cdp.mjs <ip> "<js>" [aba]`: CDP cru para qualquer aba (`QuickAccess_uid2`, `MainMenu_uid2`, `Steam — Modo Big Picture`).

Úteis no `eval`:
- `SteamUIStore.Navigate("/library/app/<appid>")` e `SteamUIStore.Navigate("/library/home")` para levar a tela até o que você quer capturar;
- `appStore.GetAppOverviewByAppID(id)`;
- `collectionStore.BIsHidden(id)`.

## Ciclo de desenvolvimento

1. Lógica no backend vai em `py_modules/insertplay_decky/` (sem `import decky`), com teste em `tests/`: `uvx pytest tests -q`.
2. `npx -y pnpm@9 typecheck` e depois `node scripts/deck.mjs deploy`.
3. Verifique no aparelho com `logs`, `eval` no estado (`appStore`, `collectionStore`) e `screenshot`. Não declare "funciona" sem uma dessas evidências.
4. Chamada nova do Steam: entra em `src/steam.ts`, é medida primeiro com `eval` no Deck e registrada em `docs/steam-api-notes.md`.

## Regras

- **O Deck é o aparelho pessoal do usuário.** A biblioteca dele tem centenas de jogos reais.
  - Nunca remova, oculte ou altere atalhos ou jogos que não foram criados no teste em andamento.
  - Atalho de teste leva nome claramente de teste (`IP Teste …`) e é removido no fim, também em caso de falha.
- **O Deck tem duas contas Steam** (`wjsantos123`, 108436075, a principal; `dalilaviana`, 385089570). O registro do plugin é por aparelho (issue #8): com a outra conta ativa e um cartão inserido, o plugin cria atalho nela e sobrescreve o registro. Troque de conta só sem cartão, ou corrija depois com `rpc registry_put`.
- Não mexa em outros plugins do Decky (`~/homebrew/plugins/*` além de `InsertPlay`).
- Não inicie jogos reais do usuário. `RunGame` só em atalho de teste ou num cartão InsertPlay que o usuário pediu para testar.
- Não tente obter root fora do `deploy-plugin.sh`, não peça a senha do usuário e não altere a regra de sudo. Se faltar permissão, explique e deixe o passo para o usuário.
- **Atualização do SteamOS apaga a liberação de deploy** (`/etc/insertplay-dev`, `/etc/sudoers.d/zz-insertplay-dev`). Se o `check` mostrar "deploy sem senha: FALHOU", rode `setup-access` e peça ao usuário o comando `ssh -t … install-dev-access.sh`. Depois de uma atualização, rode também o `spike.mjs`: a API do Steam pode ter mudado.
- O `deploy-plugin.sh` recusa `plugin.json` com a flag `root`. Não contorne isso; o backend precisa rodar como usuário comum.
- Inserir e remover cartão SD é físico: peça ao usuário e confira com `logs` ou `eval`.
- **Reiniciar o Steam** (para testar a inicialização): `eval "(SteamClient.User.StartRestart(false), true)"`.
  - Confira antes que nenhum jogo está rodando (`SteamUIStore.RunningApps`).
  - Espere uns 25 s entre reinícios. Reinícios em sequência rápida fizeram o **Decky se desligar** (`updater.do_shutdown`, serviço inativo, todos os plugins do usuário sem carregar).
  - Depois de cada reinício confira `ssh "systemctl is-active plugin_loader"`; se estiver inativo, `deck.mjs restart`.
- "Funcionou" exige evidência positiva: um atalho a menos ou a mais pode vir de um erro que interrompeu o fluxo. Leia `rpc log_tail` (a checagem inicial registra `reconcile`) além de contar o estado.
- `pnpm build` termina com 0 mesmo com erro de TypeScript. O `deploy` já barra isso; num build manual, procure `plugin typescript` na saída.

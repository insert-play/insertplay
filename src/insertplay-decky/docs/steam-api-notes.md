# Steam API: notas medidas no Deck

O plugin usa funções internas do Steam (`SteamClient.Apps.*`, `appStore`, `collectionStore`) que não têm documentação. Todo o código que depende delas fica em [src/steam.ts](../src/steam.ts). Este arquivo registra o que foi **medido** no aparelho.

## Como medir

```powershell
node scripts/spike.mjs
```
O script se conecta ao Steam do Deck pela porta 8081 (Chrome DevTools Protocol) e:
- cria um atalho "IP Teste" e mede cada chamada;
- tira screenshots da página do jogo e da tela inicial (`out/screens/spike-*.png`);
- inicia o jogo e espera o Steam avisar que ele abriu e fechou;
- apaga o atalho no fim, mesmo se algo falhar.

Não faz perguntas e não precisa de ninguém olhando. O resultado fica em `out/spike-result.json`.

Rode de novo **depois de toda atualização do Steam/SteamOS**.

## Revalidação: 2026-10-24, SteamOS 3.9.5 + Decky v3.3.0

`spike.mjs`: 15/15 OK, mesmo comportamento da tabela abaixo, com artes confirmadas por screenshot. Nada mudou nas chamadas usadas pelo plugin.

## Resultado: 2026-10-24, Steam Deck (Jupiter), SteamOS 3.8.28, Steam Deck Stable

| Chamada | Resultado |
|---|---|
| `await SteamClient.Apps.AddShortcut(nome, exe, startDir, opts)` | Devolve o appid (número). **Ignora o nome**: o tile nasce como `bash` (nome do executável). |
| `SteamClient.Apps.SetShortcutName(appid, nome)` | Funciona, efeito imediato no `appStore`. |
| `SteamClient.Apps.SetShortcutLaunchOptions(appid, opts)` | Funciona: com `-c 'sleep 15'` o jogo ficou 17 s em execução. Não há leitura confiável da string (`GetLaunchOptionsForApp` devolve `[]` para atalhos). |
| `await SteamClient.Apps.SetCustomArtworkForApp(appid, base64, "png", tipo)` | Funciona. Confirmado por screenshot: **0 = capa vertical**, **1 = hero**, **2 = logo**. O tipo 3 (horizontal) aceita a chamada; a confirmação visual ficou pendente. |
| `collectionStore.SetAppsAsHidden([appid], bool)` + `BIsHidden(appid)` | Funciona nos dois sentidos. |
| `appStore.GetAppOverviewByAppID(appid).m_gameid` | Igual a `(appid << 32) \| 0x02000000`. |
| `SteamClient.Apps.RunGame(gameid, "", -1, 100)` | Inicia o atalho. `GameSessions.RegisterForAppLifetimeNotifications` avisa a abertura e o encerramento. |
| `SteamClient.Apps.SetShortcutExe(appid, '"/bin/bash"')` | Não lança erro. A forma gravada (aspas) não foi conferida visualmente. |
| `SteamClient.Apps.RemoveShortcut(appid)` | Remove; o `appStore` deixa de ter o appid em ~1 s. |
| `SteamUIStore.Navigate(path)` | Navega (`/library/app/<appid>`, `/library/home`). Útil em testes. |

## Inicialização do Steam (medido 2026-10-24)

Medido com `SteamClient.User.StartRestart(false)` e o cartão inserido. Os tempos contam a partir do pedido de reinício:

| ms | Evento |
|---|---|
| ~7 000 | novo SharedJSContext |
| ~7 900 | `appStore` definido, mas ainda vazio |
| ~11 900 | **Decky carrega o plugin**, que já roda a checagem inicial |
| ~12 450 | `appStore.m_bIsInitialized = true`; atalhos aparecem em `allApps` e `GetAppOverviewByAppID` no mesmo instante |
| depois | coleções: `collectionStore.SetAppsAsHidden` → `GetCollection` lança "reading 'get'" até elas carregarem |

Consequências no código:
- `waitForLibrary()` em `src/steam.ts` espera `m_bIsInitialized` **e** `GetCollection("hidden")` sem erro antes de qualquer decisão.
- Antes disso, o plugin achava que o atalho não existia e **criava um duplicado** (bug real, reproduzido e corrigido).

Atalhos não-Steam têm `app_type === 1073741824` e `BIsShortcut() === true`, e aparecem em `appStore.allApps`.

**Existem e ainda não foram usadas:**
- `SteamClient.Apps.SetShortcutIcon`: candidata para o ícone do atalho, fora da POC por enquanto;
- `GetShortcutDataForPath`;
- `LaunchNonSteamApp`;
- `SetShortcutSortAs`.

## Bibliotecas Steam no cartão (medido 2026-10-24, SteamOS 3.9.5)

Usado pelo badge de cartão (`src/steam-badges.tsx`) para marcar também jogos Steam instalados no cartão, não só atalhos do InsertPlay, e pela notificação e início automático desses jogos (issue #10, `checkSteamLibraries` em `src/flow.ts`).

| Chamada | Resultado |
|---|---|
| `await SteamClient.InstallFolder.GetInstallFolders()` | Uma entrada por biblioteca que o Steam já viu, montada ou não: `strFolderPath`, `bIsDefaultFolder`, `bIsMounted`, `bIsFixed`, `vecApps[].nAppID`. O cartão no leitor (`/run/media/deck/Expedition 33`) veio com `bIsMounted: true`, `bIsFixed: false` e o appid do jogo. Cartões ausentes continuam na lista com `bIsMounted: false` **e os appids antigos**, então é preciso filtrar por `bIsMounted`. |
| `SteamClient.InstallFolder.RegisterForInstallFolderChanges(cb)` | Devolve `{ unregister }`. Chama `cb({ folderIndex })` uma vez logo após o registro (`folderIndex: 0`). Retirada do cartão: aviso ~0,5 s depois do unmount do kernel. Inserção: dois avisos em menos de 1 s depois do mount. Não precisamos de polling. |
| Sequência dos avisos (sonda com polling de 100 ms, 2026-10-24) | Retirada: um aviso, já com `bIsMounted: false` e os appids mantidos. Inserção: dois avisos iguais, já com `bIsMounted: true` e os appids. Nenhum estado intermediário (montado sem apps). O aviso da inserção chegou ~1 s depois do mount do kernel. |
| Mover jogo do SSD para o cartão (Configurações → Armazenamento) | O início do move não gera aviso. No fim, `vecApps` troca de pasta **em silêncio** (sonda: 18:24:22) e o aviso só chega **~12 s depois** (18:24:34). Tirar o cartão nessa janela fazia a remoção sair sem toast, porque o retrato ainda tinha a biblioteca vazia. Por isso a remoção usa os appids que o Steam mantém na pasta desmontada, não os do último aviso. Formatar pelo Steam remonta o cartão com o rótulo novo (`/run/media/deck/<rótulo>`), e a pasta nova entra vazia. |
| Instalar jogo num cartão já inserido | O appid entra em `vecApps` **com aviso** assim que o download começa, antes de terminar. Desinstalar tira o appid da lista. Ou seja, o move é o caso sem aviso; a instalação, não. |
| Lista ao reiniciar o Steam | Começa **vazia** (`[]`, sem nem a pasta padrão). ~3 s depois aparece inteira de uma vez, **sem aviso**; depois vêm ~30 avisos seguidos com a lista igual. Nas medições ela encheu antes de `appStore.m_bIsInitialized` (3,0 s contra 5,6 s), mas um retrato tirado com a lista vazia transformou o cartão já inserido em "ausente" e a remoção seguinte se perdeu. Por isso `installFolders()` espera a pasta padrão aparecer antes de usar a lista. |
| `bIsFixed` na Steam Machine (2026-10-24) | O leitor de cartão da Machine é USB (`/dev/sda1`, `lsblk`: `RM 1`, `HOTPLUG 1`, `TRAN usb`), e o Steam marca a biblioteca do cartão como **`bIsFixed: true`**, com `strDriveName` igual ao ponto de montagem. No Deck (leitor `mmcblk0`) ela vem `false`. Por isso o cartão é "não fixo para o Steam **ou** disco removível para o kernel": o backend (`disk_is_removable`) lê `removable` do disco em `/sys/dev/block/<maj>:<min>/..`. Na Machine, `/sys/block/sda/removable` = 1 e o NVMe = 0. |
| `strUserLabel` | Rótulo que o usuário deu à biblioteca no Steam. Nem sempre é o rótulo do cartão (`/run/media/deck/The Witcher 3` veio como "Assassins Creed Unity"). O toast usa o nome da pasta de montagem, como o fluxo dos cartões InsertPlay. |
| `strFolderPath` | Pode ser a raiz do cartão (`/run/media/deck/Expedition 33`) ou subpasta (`/run/media/deck/Games/SteamLibrary`). O backend (`library_card`) sobe até o ponto de montagem e olha o manifesto InsertPlay no disco. O cartão do NFS Underground tem biblioteca Steam vazia além do manifesto. |
| `SteamClient.Apps.RunGame("1903340", "", -1, 100)` | Abre o jogo Steam (Clair Obscur, appid 1903340): `SteamUIStore.RunningApps` passou a ter o appid. Jogo Steam não tem `m_gameid` no overview (`undefined`); o gameid é o próprio appid como string. |
| `appStore.GetAppOverviewByAppID(1903340).display_name` | "Clair Obscur: Expedition 33". Usado no toast. |

DOM da biblioteca (janela "Steam — Modo Big Picture", 854×534 px de CSS no Deck): na tela inicial todo card `div[role="listitem"]` tem `data-id` com o appid, inclusive capas da loja cuja URL usa hash em vez do appid.

## Instalação do plugin pelo Decky (medido 2026-10-24, Decky v3.3.0)

Não é API do Steam, mas é o outro global não documentado que o plugin usa (`src/selfupdate.ts`).

- `DeckyBackend.call("utilities/install_plugin", artifact, name, version, hash, installType)`:
  - é a mesma rota da loja e do "Install Plugin from ZIP File";
  - `artifact` pode ser `file:///caminho/absoluto.zip`;
  - não instala nada sozinha: abre o **modal nativo do Decky** ("Tem certeza que você deseja atualizar InsertPlay 0.2.1?"), e só "Atualizar"/"Instalar" instala;
  - `installType` (0 INSTALL, 1 REINSTALL, 2 UPDATE, 3 DOWNGRADE, 4 OVERWRITE) só muda o texto do modal;
  - o Decky sempre remove a pasta da versão instalada e extrai o zip como está em `~/homebrew/plugins`. A pasta raiz do zip precisa ser `InsertPlay/`, senão vira outro plugin.
- `hash`: SHA-256 do zip. Se não bater, o Decky recusa a instalação; vazio pula a checagem. Passamos o hash que o backend leu, então um zip trocado depois da leitura é recusado.
- Com `version` diferente de `"dev"`, o nome vem do argumento `name` e precisa ser igual ao `name` do `plugin.json`.
- O que sobrevive à atualização: `~/homebrew/settings/InsertPlay` (registro guid → appid) e `~/homebrew/data/InsertPlay`. O Decky limpa só as entradas do plugin em `pluginOrder`, `hiddenPlugins` e `disabled_plugins` do loader.
- Depois de instalar, o Decky recarregou o plugin duas vezes seguidas (watcher de arquivos). Isso rodou duas reconciliações e não duplicou atalho.
- Instalado pelo Decky, os arquivos ficam `deck:deck 755`. Pelo `deploy-plugin.sh` eles ficam `root:root`.
- O modal do Decky aparece na janela "Steam — Modo Big Picture". O `Page.captureScreenshot` dessa janela sai preto enquanto o modal está aberto, então confira o texto com `document.body.innerText` (via `cdp.mjs`).
- **Seletor de arquivos** do "Install Plugin from ZIP File": abre em `/home/deck`, mas o botão ↑ sobe até `/`. Dá para chegar em `/run/media/deck/<rótulo>/insertplay/decky/InsertPlay.zip`. O backend do seletor (`utilities/filepicker_ls`) roda como root e não restringe caminho. Tocar no zip já abre o modal "instalar InsertPlay dev?", e o Decky lê o nome do `plugin.json`.

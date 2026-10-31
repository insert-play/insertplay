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
- `GetShortcutDataForPath`;
- `LaunchNonSteamApp`;
- `SetShortcutSortAs`.

## Ícone dos atalhos (issue #17, medido 2026-10-27, SteamOS 3.9.5)

Usado por `ensureIcon` (`src/flow.ts`) e pela RPC `shortcut_icon` (`main.py`, lógica em `py_modules/insertplay_decky/icons.py`). Atalhos de teste `IP Teste…` e o cartão do NFS Underground.

| Chamada / situação | Resultado |
|---|---|
| `SteamClient.Apps.SetShortcutIcon(appid, caminho)` | O Steam guarda **só o caminho** (campo `icon` do `shortcuts.vdf`) e lê o arquivo sob demanda. O overview recebe `icon_data` (PNG em base64), `icon_data_format` e `icon_hash` em menos de 2,5 s, sem reiniciar. Caminho `""` limpa o campo no `shortcuts.vdf`, mas o `icon_data` antigo fica no overview até o reinício. |
| Gravação no `shortcuts.vdf` | ~0,16 s depois da chamada (mtime do arquivo). Por isso o backend lê o arquivo para saber o ícone atual. |
| Caminho do ícone no frontend | Não existe: nem o overview nem `appDetailsStore.GetAppDetails` trazem o caminho. Só o `shortcuts.vdf` (`userdata/<conta>/config/`). A conta vem de `App.m_CurrentUser.strSteamID` (steamid64 − 76561197960265728). |
| Ícone num caminho do cartão, Steam reiniciado com o cartão fora | Atalho sem ícone. Com o cartão de volta, nem `RequestIconDataForApp` nem um novo `SetShortcutIcon` **com o mesmo caminho** recarregam até reiniciar. Por isso o ícone é copiado para `~/homebrew/data/InsertPlay/icons/<guid>-<sha256 curto>-<geração>.<ext>`. |
| Ícone no SSD, Steam reiniciado com o cartão fora | O ícone continua (`RequestIconDataForApp` → `icon_data`). |
| Caminho já carregado na sessão | Voltar para um caminho que o Steam já leu nesta sessão não troca o `icon_data`. Por isso todo conteúdo novo ganha um caminho inédito (hash + geração), mesmo voltando a um ícone já usado (A → B → A); o mesmo conteúdo mantém o caminho. |
| Inserção com o ícone do cartão trocado | Caminho novo aplicado e `icon_data` trocado em segundos, sem reiniciar. A cópia antiga só sai numa chamada seguinte, quando nenhum `shortcuts.vdf` (de nenhuma conta) a usa mais. |
| Ícone fora da pasta do plugin no atalho (como o do SteamGridDB) | O plugin não aplica o dele. O SteamGridDB não usa `SetShortcutIcon`: ele edita o `shortcuts.vdf` direto e guarda o ícone em `userdata/<conta>/config/grid/<appid>_icon.<ext>`. |
| Reconciliação no início do Steam, cartão dentro, atalho sem ícone | Aplica e carrega. |

**Quando o ícone só aparece depois do próximo reinício** (o caminho já fica certo no `shortcuts.vdf`, e o reinício seguinte carrega do SSD):
- Na sessão aberta por um `deploy` (o webhelper é relançado), nenhum `icon_data` chegou ao frontend para atalhos que já existiam, nem com `RequestIconDataForApp`. Um atalho criado nessa sessão recebeu.
- Atalho do NFS **sem ícone** e com o **cartão fora** quando o Steam iniciou: nessa sessão ele recusou qualquer caminho, mesmo inédito e mesmo depois de o cartão voltar. A causa não foi identificada. Testado e descartado: atalho oculto, `RunGame` logo depois, as chamadas de `updateShortcut` logo antes, e pasta de início inexistente (um atalho de teste sem ícone e com pasta de início inexistente aceitou). Só um dos dois fatores não basta: com ícone no início e cartão fora, ou sem ícone e cartão dentro, aplicou. Afeta atalhos criados antes do ícone existir; depois do primeiro ícone, o caso não se repete.

## Bibliotecas Steam no cartão (medido 2026-10-24, SteamOS 3.9.5)

Usado pelo badge de cartão (`src/steam-badges.tsx`) para marcar também jogos Steam instalados no cartão, não só atalhos do InsertPlay, e pela notificação e início automático desses jogos (issue #10, `checkSteamLibraries` em `src/flow.ts`).

| Chamada | Resultado |
|---|---|
| `await SteamClient.InstallFolder.GetInstallFolders()` | Uma entrada por biblioteca que o Steam já viu, montada ou não: `strFolderPath`, `bIsDefaultFolder`, `bIsMounted`, `bIsFixed`, `vecApps[].nAppID`. O cartão no leitor (`/run/media/deck/Expedition 33`) veio com `bIsMounted: true`, `bIsFixed: false` e o appid do jogo. Cartões ausentes continuam na lista com `bIsMounted: false` **e os appids antigos**, então é preciso filtrar por `bIsMounted`. |
| `SteamClient.InstallFolder.RegisterForInstallFolderChanges(cb)` | Devolve `{ unregister }`. Chama `cb({ folderIndex })` uma vez logo após o registro (`folderIndex: 0`). Retirada do cartão: aviso ~0,5 s depois do unmount do kernel. Inserção: dois avisos em menos de 1 s depois do mount. Não precisamos de polling. |
| Sequência dos avisos (sonda com polling de 100 ms, 2026-10-24) | Retirada: um aviso, já com `bIsMounted: false` e os appids mantidos. Inserção: dois avisos iguais, já com `bIsMounted: true` e os appids. Nenhum estado intermediário (montado sem apps). O aviso da inserção chegou ~1 s depois do mount do kernel. |
| Mover jogo do SSD para o cartão (Configurações → Armazenamento) | O início do move não gera aviso. No fim, `vecApps` troca de pasta **em silêncio** (sonda: 18:24:22) e o aviso só chega **~12 s depois** (18:24:34). Tirar o cartão nessa janela fazia a remoção sair sem toast, porque o retrato ainda tinha a biblioteca vazia. Por isso a remoção usa os appids que o Steam mantém na pasta desmontada, não os do último aviso. Formatar pelo Steam remonta o cartão com o rótulo novo (`/run/media/deck/<rótulo>`), e a pasta nova entra vazia. |
| Instalar jogo num cartão já inserido | O appid entra em `vecApps` **com aviso** assim que o download começa, antes de terminar. Desinstalar tira o appid da lista. Ou seja, o move é o caso sem aviso; a instalação, não. |
| Lista ao reiniciar o Steam | Começa **vazia** (`[]`, sem nem a pasta padrão). ~3 s depois aparece inteira de uma vez, **sem aviso**; depois vêm ~30 avisos seguidos com a lista igual. Nas medições ela encheu antes de `appStore.m_bIsInitialized` (3,0 s contra 5,6 s), mas um retrato tirado com a lista vazia transformou o cartão já inserido em "ausente" e a remoção seguinte se perdeu. Por isso `installFolders()` espera a biblioteca interna (índice 0) aparecer antes de usar a lista. |
| `bIsDefaultFolder` x biblioteca interna (2026-10-25) | `bIsDefaultFolder` é o **local padrão de instalação**, não o SSD: ao instalar a trilogia Prince of Persia escolhendo o cartão `PoP Sands of Tim` como padrão, a marca foi para o cartão (`nFolderIndex: 33`). A biblioteca interna (`/home/deck/.local/share/Steam`) é sempre `nFolderIndex: 0` (Deck e Machine). O plugin descartava a "pasta padrão" e, com isso, o cartão inteiro: sem badge, sem retrato e sem save portátil. |
| `bIsFixed` na Steam Machine (2026-10-24) | O leitor de cartão da Machine é USB (`/dev/sda1`, `lsblk`: `RM 1`, `HOTPLUG 1`, `TRAN usb`), e o Steam marca a biblioteca do cartão como **`bIsFixed: true`**, com `strDriveName` igual ao ponto de montagem. No Deck (leitor `mmcblk0`) ela vem `false`. Por isso o cartão é "não fixo para o Steam **ou** disco removível para o kernel": o backend (`disk_is_removable`) lê `removable` do disco em `/sys/dev/block/<maj>:<min>/..`. Na Machine, `/sys/block/sda/removable` = 1 e o NVMe = 0. Com o cartão do Expedition 33 preparado no Deck, a Machine listava a pasta desmontada com 0 apps e, ao inserir, passou a listar o 1903340 (toast e badge ok). |
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

## Prefixo do Proton no cartão via symlink (issue #13, medido 2026-10-24, SteamOS 3.9.5)

Call of Juarez Gunslinger (204450), cartão ext4 `Call of Juarez G`, Modo Jogo. Prefixo copiado para `<cartão>/.insertplay/prefixes/204450` e `~/.local/share/Steam/steamapps/compatdata/204450` trocado por um symlink para ele.

| Situação | Resultado |
|---|---|
| Abrir o jogo | O Proton (11.0-100) e o pressure-vessel seguem o link até `/run/media`. `pfx/*.reg`, `version`, `tracked_files` e os `Settings` do jogo foram gravados no cartão; nada novo no SSD. O link continuou link. |
| `config_info` | Guarda caminhos absolutos (`/home/deck/.local/share/Steam/steamapps/common/Proton 11.0/files/...`) e a versão do Proton. O Steam não o reescreveu em nenhum dos testes. |
| Passo "Running Script" (antes de abrir e depois de cada move) | Grava através do link (`tracked_files`, `version`, `pfx`). Não troca o link por pasta. |
| Verificar integridade dos arquivos | Link mantido; "erro na validação de 0 arquivo(s)" = nenhum erro. O shader cache baixado vai para `shadercache/204450` no SSD, separado do prefixo. |
| Mover cartão → SSD (com o `steamapps/compatdata/204450` do cartão fora do caminho) | Passou. Link e prefixo do cartão intactos. O Steam **não** recriou pasta no SSD: o caminho já existe (o link). O jogo fica no SSD com o prefixo no cartão. |
| Mover SSD → cartão | Passou. O Steam **segue o link e copia** o prefixo inteiro para `steamapps/compatdata/204450` do cartão (493 MB) e **não apaga a origem**: link e `.insertplay/prefixes/204450` ficaram iguais (2.327 itens). Essa cópia é a que, segundo a issue, bloqueia o move de volta. |
| Tirar o cartão com o link ativo | O link fica quebrado e o Steam não mexe nele. Abrir outro jogo Proton (Duke Nukem 3D, 434050) não muda nada em `compatdata`. O jogo do cartão aparece só como "Instalar". Reinserir o cartão faz o link voltar a resolver. |
| Desinstalar com o link ativo (jogo no cartão) | "finished uninstall (No Error)". Apagou `shadercache/204450` do SSD; **não tocou** no link, no `.insertplay` (2.329 itens, sem diferença) nem no `steamapps/compatdata/204450` do cartão. O link sobra no SSD. |

Onde o save mora não é regra: o CoJ grava o save em `userdata/<conta>/204450/remote` (Steam Cloud) e, no prefixo, só as configurações. Para testar o save portátil é preciso um jogo que salve dentro do prefixo (a conferir no Bully e no Metro Redux).

### Prefixo inteiro num cartão lento x só `steamuser` (2026-10-25, Bully 12200)

Cartão `Bully`: SDHC 8 GB antigo (`SA08G`), ext4, ~0,4 MB/s de escrita com o journal do ext4 (`do_get_write_access`).

- **Prefixo inteiro no cartão:** cada abertura levou minutos, com o Proton (`python3 proton`) no estado `D`.
  - Toda execução regrava **~115 MB** no prefixo: DLLs do Steam (`steamclient*.dll`, `GameOverlayRenderer*.dll`), `vrclient`, `openxr`, `Fonts` e parte de `system32`/`syswow64`. Isso acontece mesmo com o `config_info` igual: o `try_copy` copia sem comparar.
  - Na primeira abertura depois de mudar o prefixo, a regravação acontece **duas vezes**: no script de instalação (`iscriptevaluator`, `Install=1`) e na abertura do jogo.
  - O save foi gravado no cartão (`Documents/Bully Scholarship Edition/BullyFile1`).
- **Só `pfx/drive_c/users/steamuser` no cartão** (200 KB), com link no SSD:
  - a abertura ficou rápida: a cópia de DLLs foi para o SSD e levou menos de 1 s;
  - o jogo grava pelo link (`AppData/Local/Temp`, `BullyFile2`);
  - o Wine segue o link, e o Proton só mexe nos links de dentro (`My Documents` → `Documents`).
- **Aparelho sem prefixo** (simulado tirando `compatdata/12200` do caminho): com só `compatdata/12200/pfx/drive_c/users/steamuser` (o link) no lugar, o Proton montou o prefixo inteiro em volta (441 MB), manteve o link e o jogo abriu com os dois saves já na primeira vez.
- Desligar pelo plugin (cópia cartão → SSD de 441 MB) levou 3,9 s. Ler do cartão é rápido; escrever, não.
- **`STEAM_COMPAT_DATA_PATH` nas opções de inicialização funciona no SteamOS** (Steam Machine, 2026-10-25).
  - Com `STEAM_COMPAT_DATA_PATH="/run/media/deck/Bully/compatdata" %command%`, sobra de um teste antigo, o Proton usou o prefixo da raiz do cartão (o `environ` do `proton waitforexitandrun` mostra a variável) e ignorou o link em `compatdata/12200`. O jogo abriu sem os saves e gravou ~115 MB de DLLs no cartão, por isso a abertura demorou ~2 min.
  - As opções de inicialização são guardadas **por aparelho**: no Deck o campo estava vazio. Elas se leem em `appDetailsStore.GetAppDetails(appid).strLaunchOptions`, mas esse cache só existe depois que alguém abre a página do jogo. Sem ele, `SteamClient.Apps.RegisterForAppDetails(appid, cb)` carrega os detalhes: o callback chegou em ~40 ms (Deck e Machine).
- **Bully do Deck na Steam Machine, modo "Saves"** (2026-10-25, opção de inicialização removida):
  - ao inserir o cartão, o plugin trocou o `steamuser` vazio da Machine (de 19/09) por um link (`linked`) e guardou o original como backup;
  - o jogo abriu rápido e carregou os saves feitos no Deck;
  - a resolução continuou a de cada aparelho (800p no Deck, 4K na Machine): o Bully guarda a configuração fora de `steamuser`.


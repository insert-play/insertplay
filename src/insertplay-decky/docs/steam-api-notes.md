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

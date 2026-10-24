// Executa JavaScript dentro do Steam do Deck pelo Chrome DevTools Protocol, a mesma
// porta (8081) que o chrome://inspect usa. Requer "Allow Remote CEF Debugging" no Decky.
//
// Como módulo:  const steam = await connect("192.168.0.42"); await steam.evaluate("1+1");
// Pela linha de comando:  node scripts/cdp.mjs <ip> "appStore.allApps.length" [aba]
import { pathToFileURL } from "node:url";

export const CEF_PORT = 8081;

/** `title` é o nome exato da aba ou uma função (target) => boolean. */
export async function connect(host, title = "SharedJSContext") {
  let targets;
  try {
    targets = await (await fetch(`http://${host}:${CEF_PORT}/json`)).json();
  } catch (err) {
    throw new Error(
      `não consegui falar com ${host}:${CEF_PORT} (${err.cause?.code ?? err.message}). ` +
        `Confira o IP, a rede e "Allow Remote CEF Debugging" no Decky.`,
    );
  }
  const matches = typeof title === "function" ? title : (t) => t.title === title;
  const target = targets.find(matches);
  if (!target) {
    throw new Error(`aba "${title}" não encontrada. Abas disponíveis: ${targets.map((t) => t.title).join(", ")}`);
  }
  // O CEF às vezes anuncia o WebSocket como localhost; trocamos pelo IP do Deck.
  const wsUrl = target.webSocketDebuggerUrl.replace(/^ws:\/\/[^/]+/, `ws://${host}:${CEF_PORT}`);
  const ws = new WebSocket(wsUrl);
  await new Promise((ok, fail) => {
    ws.onopen = ok;
    ws.onerror = () => fail(new Error(`falha ao abrir ${wsUrl}`));
  });

  let nextId = 0;
  const pending = new Map();
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    const resolve = pending.get(msg.id);
    if (resolve) {
      pending.delete(msg.id);
      resolve(msg);
    }
  };
  // Steam reiniciando derruba o WebSocket; sem isto as chamadas pendentes nunca
  // terminam e o processo morre por "unsettled top-level await" (medido).
  let closed = false;
  ws.onclose = () => {
    closed = true;
    for (const resolve of pending.values()) resolve({ error: { message: "conexão com o Steam fechada" } });
    pending.clear();
  };
  const send = (method, params) =>
    new Promise((resolve) => {
      if (closed) return resolve({ error: { message: "conexão com o Steam fechada" } });
      const id = ++nextId;
      pending.set(id, resolve);
      ws.send(JSON.stringify({ id, method, params }));
    });

  return {
    /** Comando CDP cru, ex.: send("Page.captureScreenshot", { format: "png" }). */
    async send(method, params = {}) {
      const msg = await send(method, params);
      if (msg.error) throw new Error(msg.error.message);
      return msg.result;
    },
    /** Avalia `expression` no Steam e devolve o valor (Promises são aguardadas). */
    async evaluate(expression) {
      const msg = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
      if (msg.error) throw new Error(msg.error.message);
      const { result, exceptionDetails } = msg.result;
      if (exceptionDetails) {
        throw new Error(exceptionDetails.exception?.description ?? exceptionDetails.text);
      }
      return result.value;
    },
    close: () => ws.close(),
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [host, expression, title] = process.argv.slice(2);
  if (!host || !expression) {
    console.error('uso: node scripts/cdp.mjs <ip-do-deck> "<expressão JS>" [aba, padrão SharedJSContext]');
    process.exit(1);
  }
  let steam;
  try {
    steam = await connect(host, title);
    console.log(JSON.stringify(await steam.evaluate(expression), null, 2));
  } catch (err) {
    console.error(`Erro: ${err.message}`);
    process.exitCode = 1;
  } finally {
    steam?.close();
  }
}

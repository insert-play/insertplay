// Runs JavaScript inside the Deck's Steam over the Chrome DevTools Protocol, the same
// port (8081) that chrome://inspect uses. Requires "Allow Remote CEF Debugging" in Decky.
//
// As a module:  const steam = await connect("192.168.0.42"); await steam.evaluate("1+1");
// From the command line:  node scripts/cdp.mjs <ip> "appStore.allApps.length" [tab]
import { pathToFileURL } from "node:url";

export const CEF_PORT = 8081;

/** `title` is the exact tab name or a function (target) => boolean. */
export async function connect(host, title = "SharedJSContext") {
  let targets;
  try {
    targets = await (await fetch(`http://${host}:${CEF_PORT}/json`)).json();
  } catch (err) {
    throw new Error(
      `could not reach ${host}:${CEF_PORT} (${err.cause?.code ?? err.message}). ` +
        `Check the IP, the network and "Allow Remote CEF Debugging" in Decky.`,
    );
  }
  const matches = typeof title === "function" ? title : (t) => t.title === title;
  const target = targets.find(matches);
  if (!target) {
    throw new Error(`tab "${title}" not found. Available tabs: ${targets.map((t) => t.title).join(", ")}`);
  }
  // CEF sometimes announces the WebSocket as localhost; we swap it for the Deck's IP.
  const wsUrl = target.webSocketDebuggerUrl.replace(/^ws:\/\/[^/]+/, `ws://${host}:${CEF_PORT}`);
  const ws = new WebSocket(wsUrl);
  await new Promise((ok, fail) => {
    ws.onopen = ok;
    ws.onerror = () => fail(new Error(`failed to open ${wsUrl}`));
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
  // Steam restarting drops the WebSocket; without this the pending calls never
  // finish and the process dies with "unsettled top-level await" (measured).
  let closed = false;
  ws.onclose = () => {
    closed = true;
    for (const resolve of pending.values()) resolve({ error: { message: "connection to Steam closed" } });
    pending.clear();
  };
  const send = (method, params) =>
    new Promise((resolve) => {
      if (closed) return resolve({ error: { message: "connection to Steam closed" } });
      const id = ++nextId;
      pending.set(id, resolve);
      ws.send(JSON.stringify({ id, method, params }));
    });

  return {
    /** Raw CDP command, e.g.: send("Page.captureScreenshot", { format: "png" }). */
    async send(method, params = {}) {
      const msg = await send(method, params);
      if (msg.error) throw new Error(msg.error.message);
      return msg.result;
    },
    /** Evaluates `expression` in Steam and returns the value (Promises are awaited). */
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
    console.error('usage: node scripts/cdp.mjs <deck-ip> "<JS expression>" [tab, default SharedJSContext]');
    process.exit(1);
  }
  let steam;
  try {
    steam = await connect(host, title);
    console.log(JSON.stringify(await steam.evaluate(expression), null, 2));
  } catch (err) {
    console.error(`Error: ${err.message}`);
    process.exitCode = 1;
  } finally {
    steam?.close();
  }
}

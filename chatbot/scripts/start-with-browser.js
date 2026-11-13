const net = require("net");
const path = require("path");
const openBrowser = require("react-dev-utils/openBrowser");

function isPortReachable(host, port, timeoutMs = 600) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    let settled = false;

    const finish = (result) => {
      if (settled) {
        return;
      }

      settled = true;
      socket.destroy();
      resolve(result);
    };

    socket.setTimeout(timeoutMs);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false));
    socket.once("error", () => finish(false));
    socket.connect(port, host);
  });
}

async function waitForPort(host, port, maxWaitMs = 45000) {
  const startedAt = Date.now();

  while (Date.now() - startedAt < maxWaitMs) {
    const reachable = await isPortReachable(host, port);
    if (reachable) {
      return true;
    }

    await new Promise((resolve) => setTimeout(resolve, 300));
  }

  return false;
}

const protocol = process.env.HTTPS === "true" ? "https" : "http";
const hostFromEnv = process.env.HOST || "localhost";
const browserHost = hostFromEnv === "0.0.0.0" ? "localhost" : hostFromEnv;
const reachabilityHost = hostFromEnv === "0.0.0.0" ? "127.0.0.1" : hostFromEnv;
const port = Number.parseInt(process.env.PORT || "3000", 10);
const localUrl = `${protocol}://${browserHost}:${port}`;

let opened = false;

waitForPort(reachabilityHost, port).then((ready) => {
  if (ready && !opened) {
    opened = true;
    openBrowser(localUrl);
  }
});

// Disable CRA's internal auto-open; we control browser opening explicitly above.
process.env.BROWSER = "none";

require(path.join("react-scripts", "scripts", "start"));

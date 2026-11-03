import { spawn } from "node:child_process";
import { readFile, writeFile, mkdir, unlink } from "node:fs/promises";
import path from "node:path";
import { setTimeout as delay } from "node:timers/promises";
const root = path.resolve("..");
const browserIntegration = process.env.LEDGERPILOT_BROWSER_INTEGRATION === "1";
const directory = path.join(
  root,
  "tmp",
  browserIntegration ? "phase8" : "phase5",
);
await mkdir(directory, { recursive: true });
const fixture = path.join(directory, "live.json");
await unlink(fixture).catch(() => {});
const python =
  process.env.LEDGERPILOT_TEST_PYTHON ||
  path.join(root, "backend", ".venv", "Scripts", "python.exe");
try {
  await fetch("http://127.0.0.1:8000/health/ready", {
    signal: AbortSignal.timeout(1000),
  });
  throw new Error(
    "Port 8000 is already serving an API. Stop it before isolated acceptance testing.",
  );
} catch (error) {
  if (error.message.startsWith("Port 8000")) throw error;
}
const server = spawn(
  python,
  ["backend/tests/frontend_acceptance_server.py", fixture],
  { cwd: root, stdio: ["ignore", "ignore", "pipe"], windowsHide: true },
);
let serverErrors = "";
server.stderr.on("data", (value) => {
  serverErrors = (serverErrors + value).slice(-12000);
});
let status = 1;
try {
  let ready = false;
  for (let attempt = 0; attempt < 180; attempt++) {
    if (server.exitCode !== null)
      throw new Error("Isolated API failed: " + serverErrors);
    try {
      await readFile(fixture);
      const response = await fetch("http://127.0.0.1:8000/health/ready");
      if (response.ok) {
        ready = true;
        break;
      }
    } catch {}
    await delay(500);
  }
  if (!ready)
    throw new Error("Isolated API did not become ready: " + serverErrors);
  const child = spawn(
    process.execPath,
    browserIntegration
      ? [
          "node_modules/@playwright/test/cli.js",
          "test",
          "--config",
          "playwright.integration.config.ts",
        ]
      : [
          "node_modules/vitest/vitest.mjs",
          "run",
          "--config",
          "vitest.live.config.ts",
          "--reporter=default",
          "--reporter=json",
          "--outputFile=../tmp/phase5/live-results.json",
          ...(process.env.LEDGERPILOT_LIVE_FILTER
            ? ["--testNamePattern=" + process.env.LEDGERPILOT_LIVE_FILTER]
            : []),
        ],
    {
      stdio: "inherit",
      windowsHide: true,
      env: {
        ...process.env,
        DEBUG_PRINT_LIMIT: "12000",
        LEDGERPILOT_LIVE_FIXTURE: fixture,
      },
    },
  );
  status = await new Promise((resolve) =>
    child.on("exit", (code) => resolve(code ?? 1)),
  );
} catch (error) {
  console.error(error.message);
} finally {
  await writeFile(fixture.replace(/\.json$/, ".stop"), "stop");
  await Promise.race([
    new Promise((resolve) => server.on("exit", resolve)),
    delay(15000),
  ]);
  if (server.exitCode === null) {
    server.kill();
    console.error(
      "API cleanup did not finish; inspect isolated test database.",
    );
    status = 1;
  }
}
process.exitCode = status;

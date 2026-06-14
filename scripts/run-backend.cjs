const { spawn, spawnSync } = require("node:child_process");

function resolveRuntime() {
  if (process.platform === "win32") {
    const candidates = ["python", "py -3"];

    for (const command of candidates) {
      const probe = spawnSync(`${command} --version`, {
        stdio: "ignore",
        shell: true,
      });
      if (!probe.error && probe.status === 0) {
        return { shell: true, command: `${command} backend/main.py` };
      }
    }

    return null;
  }

  const candidates = [
    { command: "python3", args: [] },
    { command: "python", args: [] },
  ];

  for (const candidate of candidates) {
    const probe = spawnSync(candidate.command, [...candidate.args, "--version"], {
      stdio: "ignore",
    });
    if (!probe.error) {
      return { shell: false, command: candidate.command, args: [...candidate.args, "backend/main.py"] };
    }
  }

  return null;
}

const runtime = resolveRuntime();

if (!runtime) {
  const hint = process.platform === "win32" ? "python or py" : "python3 or python";
  console.error(`Unable to start backend: ${hint} is not available in PATH.`);
  process.exit(1);
}

const child = spawn(runtime.command, runtime.args ?? [], {
  stdio: "inherit",
  env: process.env,
  shell: runtime.shell,
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 1);
});

child.on("error", (error) => {
  console.error(`Unable to start backend: ${error.message}`);
  process.exit(1);
});
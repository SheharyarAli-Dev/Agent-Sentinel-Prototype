# Agent Sentinel Demo Launcher (Windows)

One-click setup, start, stop, and verification for the Agent Sentinel prototype.
No need to remember Python, npm, MiniLM, backend, or frontend commands by hand.

## Prerequisites

- Windows 10/11
- Python 3.10–3.13 installed and on `PATH`. **3.12 is the verified project
  version** (the project virtual environment is Python 3.12.10). When a new
  `backend\.venv` must be created, the launcher prefers `py -3.12`, then a real
  `python`, then `py -3` fallback. Python 3.13 is accepted only with a clear
  "not the verified version" warning and must still pass venv creation, pip
  install, torch / sentence-transformers import, and the backend test suite.
- Node.js 18+ installed and on `PATH` (`node --version` and `npm --version`).
  npm 9+ is required to install the v3 lockfile used by `frontend`.
- Git is **optional**: it is not needed to run an already-downloaded copy; it
  is only required to pull future updates. Setup warns (never fails) when Git
  is missing.
- An internet connection on the **first** setup only (to download dependencies
  and the MiniLM model)
- No API key, no cloud account, and no paid service are required.

## First-time setup

1. Clone the repository and open the `demo` folder.
2. Double-click **`setup_demo.bat`**.
   This:
   - checks prerequisites (Git optional, Node 18+ / npm 9+ required) and prints
     the detected Node and npm versions
   - prints the exact selected Python interpreter and version; verifies it is a
     real interpreter (rejects the Microsoft Store alias stub)
   - creates `backend\.venv` only if it does not exist, preferring `py -3.12`
     (never deletes an existing venv, so its interpreter is reused)
   - upgrades pip inside the venv
   - installs `backend/requirements.txt`
   - verifies `torch` and `sentence-transformers` actually import in the venv
   - runs `npm.cmd ci` in `frontend` (fails before installing if Node/npm are
     incompatible)
   - downloads and verifies `sentence-transformers/all-MiniLM-L6-v2`
     (first run downloads ~90 MB and can take a few minutes; internet is
     required only for that first download)
   - runs the backend test suite (`pytest`)
   - runs the frontend production build (`npm run build`)
   - writes a timestamped log under `demo\logs\` and prints it on success or
     failure
   - prints a final **SETUP PASSED / SETUP FAILED** summary
3. When you see **SETUP PASSED**, the machine is ready.

## Normal startup

Double-click **`start_demo.bat`**.

This:
- verifies `backend\.venv` and `frontend\node_modules` exist
- starts the backend in its own window:
  `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000` (no `--reload`)
- starts the frontend in its own window:
  `npm.cmd run dev -- --host 127.0.0.1`
- enables **offline model mode** (`HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`)
  **only when the MiniLM cache is actually complete** (no `*.incomplete`
  markers and a snapshot containing `config.json`); otherwise it explains that
  internet is needed for the first model download
- asks the backend to **prewarm the MiniLM semantic model at startup**
  (`AGENT_SENTINEL_PREWARM_SEMANTIC_MODEL=1`) so the first evaluation is fast
- waits for `http://127.0.0.1:8000/health` (bounded, 90 seconds)
- waits for `http://127.0.0.1:5173/` (bounded, 90 seconds) and fails clearly if
  Vite exits early or never becomes ready
- opens the dashboard browser **only after both the backend and frontend
  respond**
- writes a timestamped log under `demo\logs\` and prints it on success or
  failure

> **First startup takes 20–30 seconds.** The backend loads the MiniLM model
> before it reports healthy, and the browser opens only once Vite is answering.
> Do not close the backend or frontend windows while it is starting.
> Subsequent starts are faster because the model is cached.

If a service is already running on port 8000 or 5173, the launcher skips it and
does not start a duplicate.

## Stopping services

Double-click **`stop_demo.bat`**.

It stops **only** the backend/frontend processes that `start_demo.bat` launched,
using PID files under `demo\state\`. It checks the command line before killing so
it will not touch unrelated Python or Node processes, and it safely removes stale
PID files. If a launcher-owned process cannot be stopped, it prints
**STOP INCOMPLETE** and exits with a nonzero code.

> Note: services you started manually (not via `start_demo.bat`) are not stopped
> by this script.

## Diagnostics and logs

Every launcher script writes a timestamped log under **`demo\logs\`** (for
example `demo\logs\start_20260812_153000.log`) and prints that path after it
finishes. These logs keep genuine pip, npm, Hugging Face, backend, and frontend
errors. They never log secrets, tokens, credentials, or full command
environments. Both `demo\logs\` and `demo\state\` (PID files) are Git-ignored
and are never committed.

## Environment verification

Double-click **`verify_demo.bat`** to report:

- current Git branch and latest commit (warning-only when Git is missing)
- the selected Python command and version plus its compatibility verdict
- virtual environment status and its Python version
- Node and npm versions and their compatibility verdicts
- `torch` import (readiness-blocking — a missing torch import means NOT READY)
- `sentence-transformers` version
- MiniLM **cache completeness** and a real **offline load** from the cache
  (directory presence alone is never trusted)
- backend test result
- frontend build result
- whether ports 8000 and 5173 are available or occupied, with the owning
  process name and PID
- a final **READY / NOT READY** result

It does not modify any repository state.

## Live demonstration order

With the dashboard open at `http://127.0.0.1:5173/`, walk through the LiveOps
panel in this order:

1. **Restore Demo State** — resets the simulated cloud to its initial state.
2. **Stop development VM → ALLOW** — shows a low-risk action executing
   automatically.
3. **Stop production VM → WARN** — shows a high-risk action paused for a human,
   then **Approve** (it executes) or **Reject** (it is not executed).
4. **Delete protected snapshot → BLOCK** — shows a forbidden action refused with
   the resource left unchanged.

The **first MiniLM load** (first `WARN`-causing evaluation, or the first time the
backend needs the model) may be slow while the model loads. Subsequent calls use
the cache and are fast.

## Common errors

| Symptom | Cause / Fix |
|---|---|
| `port already in use` | Something already listens on 8000/5173. Use `stop_demo.bat` or close the other program, then `start_demo.bat` again. |
| `backend not reachable` / health timeout | Check the backend window for errors; confirm port 8000 is free; run `verify_demo.bat`. |
| `MiniLM not cached` | Run `setup_demo.bat` once with internet access so the model downloads. |
| `Vite exited / frontend not ready` | Check the Agent Sentinel Frontend window for the server error, then re-run `start_demo.bat`; confirm nothing else occupies port 5173. |
| `npm not found` or npm too old | Install Node.js 18+ (bundles npm 9+) and add it to `PATH`, then re-run `setup_demo.bat`. |
| `python not found` / interpreter rejected | Install Python 3.12 (verified) or another 3.10–3.13 build from python.org and add it to `PATH`, then re-run `setup_demo.bat`. |
| `torch ... does not import` | The venv has an incompatible wheel set. Re-run `setup_demo.bat` on Python 3.12 (verified). |
| dirty Git working tree | Uncommitted changes exist; Git is optional to run the demo and only warns here. See "How to update safely" below. |

## How to update safely

Do not use `git reset --hard` — it discards local changes.

```
git status          # see what is changed / untracked
git switch main     # leave the current feature branch
git pull origin main
```

Then re-run `setup_demo.bat` if dependencies changed, and `start_demo.bat` to
launch the updated prototype.

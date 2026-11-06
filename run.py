#!/usr/bin/env python3
"""Launch the shipped Museable app — no Docker, two modes.

  uv run demo   Replay-only Street Feast: deterministic, no model download.
                Starts Dolt + backend (LLM_MODE=demo) + frontend, open /demo.
  uv run app    Live app: downloads/serves Muse Glimmer (the only model) via
                llama-server, backend in LLM_MODE=local, open /.

Both modes start:
  dolt      native `dolt sql-server`, data in `.dolt-data/` (kept, pre-seeded)
  backend   FastAPI via `uv run --project backend python -m uvicorn ...`
  frontend  Next.js via `npm run dev`

Prereqs: uv, Node.js + npm, dolt (`brew install dolt`).
`app` additionally needs llama-server (`brew install llama.cpp`); first run
downloads the ~17 GB Muse Glimmer GGUF from Hugging Face.

Env (from `.env` if present, else process env, else defaults):
  DOLT_ROOT_PASSWORD (dolt-demo-password), DOLT_PORT (3306),
  BACKEND_PORT (8000), FRONTEND_PORT (3000), LLAMA_PORT (8080),
  LLAMA_CTX (32768), LOCAL_API_KEY, DOLTHUB_REMOTE.

Data persists in `.dolt-data/` across restarts. Ctrl-C stops everything.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"
DATA_DIR = ROOT / ".dolt-data"

DEFAULTS = {
    "DOLT_ROOT_PASSWORD": "dolt-demo-password",
    "DOLT_PORT": "3306",
    "BACKEND_PORT": "8000",
    "FRONTEND_PORT": "3000",
}

GLIMMER_HF = "meta-models/Muse-Glimmer-30B-GGUF:Q4_K_M"


def load_env() -> dict[str, str]:
    file_vars: dict[str, str] = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            file_vars[key.strip()] = value.strip()
    return {**DEFAULTS, **file_vars, **os.environ}


def stream_output(name: str, proc: subprocess.Popen) -> None:
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(f"[{name}] {line}")
        sys.stdout.flush()


class Stack:
    def __init__(self) -> None:
        self.procs: dict[str, subprocess.Popen] = {}

    def start(self, name: str, cmd: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen:
        print(f"[{name}] $ {' '.join(cmd)}  (cwd={cwd})", flush=True)
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        self.procs[name] = proc
        threading.Thread(target=stream_output, args=(name, proc), daemon=True).start()
        return proc

    def stop_all(self) -> None:
        # Block further signals so a second Ctrl-C / stray SIGTERM can't
        # interrupt teardown halfway through. Stop frontend & backend FIRST and
        # wait for them to exit before stopping dolt so in-flight SQL queries
        # never hit Connection Refused / Lost Connection on :3306.
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM})
        app_procs = [(n, p) for n, p in reversed(list(self.procs.items())) if n != "dolt"]
        for name, proc in app_procs:
            if proc.poll() is None:
                print(f"[{name}] stopping…", flush=True)
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        app_deadline = time.time() + 4
        for _, proc in app_procs:
            try:
                proc.wait(timeout=max(0.0, app_deadline - time.time()))
            except subprocess.TimeoutExpired:
                pass
        dolt_proc = self.procs.get("dolt")
        if dolt_proc and dolt_proc.poll() is None:
            print("[dolt] stopping…", flush=True)
            try:
                os.killpg(dolt_proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.time() + 5
        for proc in self.procs.values():
            remaining = max(0.0, deadline - time.time())
            try:
                proc.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                pass
        for proc in self.procs.values():
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def port_in_use(port: int) -> bool:
    import socket

    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) == 0


def backend_healthy(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1.5) as resp:
            return resp.status == 200 and b'"ok":true' in resp.read().replace(b" ", b"")
    except Exception:
        return False


def init_dolt_repo(env: dict[str, str]) -> bool:
    """Create `.dolt-data/<db>/` as a dolt repo on first run. Returns True if freshly created."""
    cfg_file = DATA_DIR / "config.yaml"
    if cfg_file.exists():
        cfg_text = cfg_file.read_text()
        updated = "\n".join(
            f"data_dir: {DATA_DIR}" if line.startswith("data_dir:") else line
            for line in cfg_text.splitlines()
        )
        if updated != cfg_text:
            cfg_file.write_text(updated + "\n")
    db_dir = DATA_DIR / env["DOLT_DATABASE"]
    if (db_dir / ".dolt").exists():
        return False
    db_dir.mkdir(parents=True, exist_ok=True)
    print(f"[dolt] initializing new database repo in {db_dir}")
    subprocess.run(
        ["dolt", "init", "--name", "Malleable Demo", "--email", "system@malleable.demo"],
        cwd=db_dir,
        check=True,
    )
    return True


def wait_for_port(port: int, name: str, timeout: float = 60.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_in_use(port):
            return
        time.sleep(0.25)
    raise SystemExit(f"{name} did not start listening on port {port} within {timeout:.0f}s")


def ensure_dolt_password(env: dict[str, str], fresh: bool) -> None:
    """Native dolt creates a passwordless root@localhost; set the configured
    password on first boot so the backend connects with the same credentials
    it always used. No-op if that already happened."""
    import pymysql

    password = env["DOLT_ROOT_PASSWORD"]
    if not password:
        return
    try:
        conn = pymysql.connect(host="127.0.0.1", port=int(env["DOLT_PORT"]), user="root", password=password, connect_timeout=3)
        conn.close()
        return  # password already set (e.g. by a previous run)
    except Exception:
        pass
    try:
        conn = pymysql.connect(host="127.0.0.1", port=int(env["DOLT_PORT"]), user="root", password="", connect_timeout=3)
    except Exception as exc:
        if fresh:
            raise SystemExit(f"Could not bootstrap Dolt root password: {exc}") from exc
        return
    with conn.cursor() as cursor:
        cursor.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (password,))
    conn.close()
    print("[dolt] root password set from DOLT_ROOT_PASSWORD")


def _run(mode: str) -> int:
    """mode: 'demo' (LLM_MODE=demo, no llama) or 'app' (LLM_MODE=local + llama-server)."""
    is_app = mode == "app"
    llm_mode = "local" if is_app else "demo"

    env = load_env()
    env.setdefault("DOLT_DATABASE", "malleable")
    env["LLM_MODE"] = llm_mode
    llama_port = int(env.get("LLAMA_PORT", "8080"))
    if is_app:
        env.setdefault("LOCAL_BASE_URL", f"http://127.0.0.1:{llama_port}/v1")
        env.setdefault("LOCAL_MODEL", "muse-glimmer-30b")
    dolt_port = int(env["DOLT_PORT"])
    backend_port = int(env["BACKEND_PORT"])
    frontend_port = int(env["FRONTEND_PORT"])

    missing: list[str] = []
    for tool in ("uv", "npm", "dolt"):
        if not shutil.which(tool):
            missing.append(tool)
    if is_app and not shutil.which("llama-server"):
        missing.append("llama-server")
    if missing:
        hints = {
            "uv": "https://docs.astral.sh/uv",
            "npm": "install Node.js from https://nodejs.org",
            "dolt": "brew install dolt  (or https://docs.dolthub.com/introduction/installation)",
            "llama-server": "brew install llama.cpp",
        }
        for tool in missing:
            print(f"error: '{tool}' not found on PATH. {hints.get(tool, '')}".rstrip(), file=sys.stderr)
        return 1

    stack = Stack()
    shutting_down = threading.Event()

    def handle_signal(signum, frame):  # noqa: ARG001
        if shutting_down.is_set():
            return  # ignore repeat signals (e.g. pkill also matching child names)
        shutting_down.set()
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        if is_app:
            if port_in_use(llama_port):
                print(f"[llama] port {llama_port} already in use — reusing the running server")
            else:
                llama_cmd = [
                    "llama-server",
                    "-hf", GLIMMER_HF,
                    "--host", "127.0.0.1", "--port", str(llama_port),
                    "-ngl", "999", "-c", env.get("LLAMA_CTX", "32768"),
                ]
                if env.get("LOCAL_API_KEY"):
                    llama_cmd += ["--api-key", env["LOCAL_API_KEY"]]
                stack.start("llama", llama_cmd, ROOT, env)
                print("[llama] waiting for Muse Glimmer (first run downloads ~17 GB)…")
                wait_for_port(llama_port, "llama-server", timeout=900.0)
            print(f"[llama] ready at 127.0.0.1:{llama_port} (Muse Glimmer, llama.cpp)")

        if port_in_use(dolt_port):
            print(f"[dolt] port {dolt_port} already in use — reusing the running server")
        else:
            fresh = init_dolt_repo(env)
            stack.start(
                "dolt",
                ["dolt", "sql-server", "--host", "127.0.0.1", "--port", str(dolt_port), "--data-dir", str(DATA_DIR)],
                ROOT,
                env,
            )
            wait_for_port(dolt_port, "dolt")
            ensure_dolt_password(env, fresh=fresh)
        print(f"[dolt] ready at 127.0.0.1:{dolt_port} (data: {DATA_DIR})")

        if backend_healthy(backend_port):
            print(f"[backend] port {backend_port} already running Malleable API — reusing the running server")
        else:
            backend_env = {
                **env,
                "APP_ENV": "development",
                # Both spellings: browsers treat localhost and 127.0.0.1 as
                # different origins, and CORS_ORIGINS is an exact-match list.
                "CORS_ORIGINS": f"http://localhost:{frontend_port},http://127.0.0.1:{frontend_port}",
                "DOLT_HOST": "127.0.0.1",
                "DOLT_PORT": str(dolt_port),
                "DOLT_USER": "root",
                "DOLT_PASSWORD": env["DOLT_ROOT_PASSWORD"],
                "DOLT_DATABASE": env["DOLT_DATABASE"],
                "LLM_MODE": llm_mode,
                "LOCAL_BASE_URL": env.get("LOCAL_BASE_URL", f"http://127.0.0.1:{llama_port}/v1"),
                "LOCAL_MODEL": env.get("LOCAL_MODEL", "muse-glimmer-30b"),
                "LOCAL_API_KEY": env.get("LOCAL_API_KEY", ""),
                "DOLTHUB_REMOTE": env.get("DOLTHUB_REMOTE", ""),
            }
            stack.start(
                "backend",
                ["uv", "run", "--project", "backend", "python", "-m", "uvicorn", "app.main:app",
                 "--host", "127.0.0.1", "--port", str(backend_port)],
                ROOT,
                backend_env,
            )

        if not (FRONTEND_DIR / "node_modules").exists():
            print("[frontend] installing dependencies (first run)…")
            subprocess.run(["npm", "install"], cwd=FRONTEND_DIR, env=env, check=True)
        frontend_env = {**env, "NEXT_PUBLIC_API_URL": f"http://127.0.0.1:{backend_port}"}
        stack.start(
            "frontend",
            ["npm", "run", "dev", "--", "--hostname", "127.0.0.1", "--port", str(frontend_port)],
            FRONTEND_DIR,
            frontend_env,
        )

        print()
        if is_app:
            print(f"  App:          http://localhost:{frontend_port}/")
            print(f"  Model:        Muse Glimmer (local, llama.cpp) at 127.0.0.1:{llama_port}")
        else:
            print(f"  Demo:         http://localhost:{frontend_port}/demo")
            print("  Model:        deterministic replay (no model download)")
        print(f"  Debug:        http://localhost:{frontend_port}/debug")
        print(f"  Backend docs: http://localhost:{backend_port}/docs")
        print(f"  Health:       http://localhost:{backend_port}/api/health")
        print()
        print("Press Ctrl-C to stop everything.")

        while True:
            for name, proc in stack.procs.items():
                code = proc.poll()
                if code is not None:
                    print(f"[{name}] exited with code {code} — shutting down")
                    return code or 1
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nCtrl-C received — shutting down…")
        return 0
    finally:
        stack.stop_all()


def demo() -> None:
    """`uv run demo` — replay-only Street Feast, deterministic, no model download."""
    sys.exit(_run("demo"))


def app() -> None:
    """`uv run app` — live app on local Muse Glimmer via llama-server."""
    sys.exit(_run("app"))


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] in {"demo", "app"}:
        return _run(args[0])
    print(__doc__)
    print("usage: uv run demo | uv run app   (or: python run.py demo|app)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

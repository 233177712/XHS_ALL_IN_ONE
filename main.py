from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional, Sequence


ROOT = Path(__file__).resolve().parent

frontend_process: Optional[subprocess.Popen] = None


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the Spider_XHS product platform.")
    parser.add_argument("--host", default="127.0.0.1", help="Backend host.")
    parser.add_argument("--port", type=int, default=8000, help="Backend port.")
    parser.add_argument("--reload", action="store_true", help="Enable Uvicorn reload.")
    parser.add_argument("--with-frontend", action="store_true", help="Also start the frontend Vite dev server.")
    parser.add_argument("--frontend-port", type=int, default=5173, help="Frontend dev server port.")
    return parser.parse_args(argv)


def resolve_npm_executable() -> str:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        raise FileNotFoundError("npm was not found on PATH; install Node.js or start the frontend manually.")
    return npm


def build_frontend_command(port: int, npm_executable: Optional[str] = None) -> list[str]:
    npm = npm_executable or resolve_npm_executable()
    return [npm, "run", "dev", "--", "--host", "127.0.0.1", "--port", str(port)]


def start_frontend(port: int) -> Optional[subprocess.Popen]:
    frontend_dir = ROOT / "frontend"
    package_json = frontend_dir / "package.json"
    if not package_json.exists():
        print("frontend/package.json not found; skipping frontend startup.")
        return None

    kwargs = {"cwd": str(frontend_dir)}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    command = build_frontend_command(port)
    print(f"Starting frontend at http://127.0.0.1:{port}")
    return subprocess.Popen(command, **kwargs)


def kill_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def kill_frontend() -> None:
    global frontend_process
    if frontend_process:
        kill_process_tree(frontend_process)
        frontend_process = None


def run_backend(host: str, port: int, reload: bool) -> None:
    try:
        import uvicorn

        if reload:
            uvicorn.run("backend.app.main:app", host=host, port=port, reload=True)
            return

        from uvicorn import Config, Server

        class _SignalSafeServer(Server):
            def install_signal_handlers(self) -> None:
                pass

        config = Config("backend.app.main:app", host=host, port=port)
        server = _SignalSafeServer(config=config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            while thread.is_alive():
                thread.join(1)
        except KeyboardInterrupt:
            pass
        finally:
            server.should_exit = True
            thread.join(5)
    finally:
        kill_frontend()


def main(argv: Optional[Sequence[str]] = None) -> int:
    global frontend_process
    args = parse_args(argv)

    # Resolve host/port: CLI args take precedence, then YAML/env config defaults
    host = args.host
    port = args.port
    try:
        from backend.app.core.config import get_settings
        settings = get_settings()
        if host == "127.0.0.1" and settings.server_host:
            host = settings.server_host
        if port == 8000 and settings.server_port:
            port = settings.server_port
    except Exception:
        pass

    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, lambda s, f: (kill_frontend(), os._exit(1)))

    frontend_process = start_frontend(args.frontend_port) if args.with_frontend else None
    print(f"Starting backend at http://{host}:{port}")
    run_backend(host, port, args.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

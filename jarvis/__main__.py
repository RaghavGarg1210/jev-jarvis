"""Run with python -m jarvis, or install the jev-jarvis command."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import platform
import sys
import threading
import webbrowser

from .actions import ActionExecutor, load_config
from .engine import Engine
from .planner import Planner
from .providers import Decider
from .server import JarvisServer
from .speech import Speech


def main():
    parser = argparse.ArgumentParser(description="Jev-Jarvis · rehearse, approve, act")
    parser.add_argument("--live", action="store_true", help="Enable real Mac actions after confirmation")
    parser.add_argument("--port", type=int, default=8765, help="Loopback port (default: 8765)")
    parser.add_argument("--state-dir", type=Path, default=Path.home() / ".jev-jarvis")
    parser.add_argument("--config", type=Path, help="Config JSON (default: STATE_DIR/config.json if present)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser on startup")
    args = parser.parse_args()
    if args.live and platform.system() != "Darwin":
        parser.error("Live actions require macOS. Rehearsal mode works on other platforms.")
    if not 0 <= args.port <= 65535:
        parser.error("Choose a valid port.")
    state_dir = args.state_dir.expanduser().resolve()
    config_path = args.config or state_dir / "config.json"
    try:
        config = load_config(config_path if config_path.exists() else args.config)
        planner = Planner(config, Decider(os.getenv("JARVIS_DECIDER", "rules")),
                          os.getenv("JARVIS_PLANNER", "rules"))
        executor = ActionExecutor(state_dir, config, args.live)
        server = JarvisServer(args.port, Engine(planner, executor, state_dir), Speech())
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    url = f"http://127.0.0.1:{server.port}"
    print(f"\n  JEV / JARVIS  ·  {'LIVE ACTIONS' if args.live else 'REHEARSAL'}\n  {url}\n"
          f"  Decisions: {planner.decider.name}  ·  Planner: {planner.name}\n"
          "  Every action needs a preview and confirmation. Ctrl+C to quit.\n", flush=True)
    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  See you next mission.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

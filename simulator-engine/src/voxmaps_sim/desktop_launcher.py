"""One-click local launcher for the VoxMaps desktop-style application."""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import webbrowser
from pathlib import Path


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def choose_port(host: str = DEFAULT_HOST, preferred: int = DEFAULT_PORT) -> int:
    """Choose the preferred local port or the next available one."""

    if not 1 <= preferred <= 65535:
        raise ValueError("port must be between 1 and 65535")
    for port in range(preferred, min(preferred + 20, 65536)):
        if _port_available(host, port):
            return port
    raise RuntimeError(
        f"No available local port was found between {preferred} and "
        f"{min(preferred + 19, 65535)}. Close another local application and try again."
    )


def _frontend_index() -> Path:
    return Path(__file__).resolve().parents[2] / "desktop" / "dist" / "index.html"


def _open_when_ready(url: str, host: str, port: int) -> None:
    """Wait briefly for the server socket, then open the user's browser."""

    import time

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.3):
                webbrowser.open(url, new=2)
                return
        except OSError:
            time.sleep(0.2)
    print(
        f"The application did not become ready automatically. Open {url} in your browser.",
        file=sys.stderr,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Start the local VoxMaps Pollution Source Simulator application."
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="start the server without opening a browser (useful for testing)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        import uvicorn
        from voxmaps_sim.desktop_api import app  # noqa: F401
    except ImportError as exc:
        print(
            "VoxMaps could not start because an application dependency is missing.\n"
            "Close this window, reconnect to the internet, and launch again so the "
            "safe setup step can finish.\n"
            f"Technical detail: {exc}",
            file=sys.stderr,
        )
        return 2

    index = _frontend_index()
    if not index.is_file():
        print(
            "VoxMaps could not find its graphical interface files.\n"
            f"Expected: {index}\n"
            "Reinstall or rebuild this project, then launch it again.",
            file=sys.stderr,
        )
        return 3

    try:
        port = choose_port(args.host, args.port)
    except (ValueError, RuntimeError) as exc:
        print(f"VoxMaps could not start: {exc}", file=sys.stderr)
        return 4

    url = f"http://{args.host}:{port}/"
    print("VoxMaps Pollution Source Simulator")
    print(f"Application address: {url}")
    print("Keep this window open while using VoxMaps. Press Ctrl+C here to stop it.")
    if not args.no_browser:
        threading.Thread(
            target=_open_when_ready,
            args=(url, args.host, port),
            daemon=True,
            name="voxmaps-browser-opener",
        ).start()
    try:
        uvicorn.run(
            "voxmaps_sim.desktop_api:app",
            host=args.host,
            port=port,
            log_level="info",
            access_log=False,
        )
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # pragma: no cover - terminal safety net
        print(
            "VoxMaps stopped unexpectedly. Close this window and launch it again.\n"
            f"Technical detail: {exc}",
            file=sys.stderr,
        )
        return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


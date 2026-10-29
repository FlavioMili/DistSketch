"""One entry point for the editor, native generation and project tools."""

import argparse
import shutil
import subprocess
import sys
import threading
import unittest
import webbrowser
from pathlib import Path


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "generate":
        from .generator import main as generate

        generate(argv[1:])
        return
    if argv and argv[0] == "benchmark":
        from .benchmark import main as benchmark

        benchmark(argv[1:])
        return
    parser = argparse.ArgumentParser(
        description="Draw a distribution and generate its dataset locally."
    )
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="Open the local editor (default)")
    serve.add_argument(
        "--port", type=int, default=8765, help="Local port; use 0 to choose a free port"
    )
    serve.add_argument(
        "--no-open", action="store_true", help="Print the address without opening a browser"
    )
    serve.add_argument("--output-dir", type=Path, default=Path.cwd() / "outputs")
    serve.add_argument("--cache-dir", type=Path, default=Path.cwd() / ".cache")
    build = commands.add_parser(
        "build-site", help="Build a static editor for GitHub Pages or offline use"
    )
    build.add_argument("--output", type=Path, default=Path.cwd() / "dist")
    commands.add_parser("generate", help="Generate from a saved drawing; use generate --help")
    commands.add_parser("benchmark", help="Benchmark generation; use benchmark --help")
    commands.add_parser("test", help="Run the integration tests in a source checkout")
    if not argv or (argv[0].startswith("-") and argv[0] not in ("--help", "-h")):
        argv.insert(0, "serve")
    args = parser.parse_args(argv)
    if args.command == "build-site":
        from .bundle import build_site

        print(build_site(args.output))
        return
    if args.command == "test":
        tests = Path(__file__).resolve().parents[2] / "tests"
        if not tests.is_dir():
            parser.error("Run tests from a source checkout.")
        suite = unittest.defaultTestLoader.discover(str(tests))
        if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
            raise SystemExit(1)
        node = shutil.which("node")
        frontend = tests / "frontend.test.cjs"
        if node and frontend.exists():
            subprocess.run([node, str(frontend)], check=True)
        elif frontend.exists():
            print("Frontend controller tests skipped: install Node.js to run them.")
        return
    from .server import make_server

    if not 0 <= args.port <= 65535:
        parser.error("Choose a port between 0 and 65535.")
    try:
        server = make_server(args.port, args.output_dir, args.cache_dir)
    except OSError as error:
        parser.error(str(error) + "\nTry --port 0 to choose an available port.")
    address = f"http://127.0.0.1:{server.server_address[1]}"
    print(
        f"DistSketch · {address}\nDatasets: {server.jobs.output_dir}\nPress Ctrl+C to stop.",
        flush=True,
    )
    if not args.no_open:
        threading.Timer(0.25, lambda: webbrowser.open(address)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.jobs.close()
        server.server_close()

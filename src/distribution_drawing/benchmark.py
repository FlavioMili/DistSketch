#!/usr/bin/env python3
"""Benchmark the native writer on a saved drawing, including its final disk flush."""

import argparse
import json
import os
import platform
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from .generator import compile_writer, read_config, read_native_source


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drawing", help="Saved generator .py or drawing JSON")
    parser.add_argument("--count", type=int, default=10000000)
    parser.add_argument("--threads", type=int, default=min(os.cpu_count() or 1, 16))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path.cwd(),
        help="Filesystem to benchmark; temporary outputs are removed",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.count <= 2000000000 or not 1 <= args.threads <= 256 or args.repeats < 1:
        parser.error("Invalid count, thread count or repetitions")
    config = read_config(args.drawing)
    original = sum(config["counts"])
    counts = [c * args.count // original for c in config["counts"]]
    for i in sorted(
        range(len(counts)), key=lambda i: (-(config["counts"][i] * args.count % original), i)
    )[: args.count - sum(counts)]:
        counts[i] += 1
    compiler = shutil.which("g++") or shutil.which("clang++")
    if not compiler:
        parser.error("This benchmark harness requires GCC or Clang")
    arch = "-mcpu=native" if platform.machine().lower() in ("arm64", "aarch64") else "-march=native"
    with tempfile.TemporaryDirectory(
        prefix="distribution-benchmark-", dir=args.directory
    ) as temporary:
        root = Path(temporary)
        executable, backend = compile_writer(
            read_native_source(Path(__file__).parent / "native/generator.cpp"),
            compiler,
            ["-O3", "-DNDEBUG", arch, "-std=c++17"],
            root / "build",
            "openmp",
            False,
        )
        saved = root / "config.txt"
        saved.write_text(
            f"{config['min']} {config['max']} {len(counts)}\n" + " ".join(map(str, counts))
        )
        output = root / "dataset.txt"
        results = []
        for _ in range(args.repeats):
            start = time.perf_counter()
            subprocess.run(
                [
                    str(executable),
                    str(saved),
                    str(output),
                    "txt",
                    config["order"],
                    str(args.threads),
                    "12345",
                    "1",
                    "0",
                    "262144",
                ],
                check=True,
            )
            results.append({"seconds": time.perf_counter() - start, "bytes": output.stat().st_size})
            output.unlink()
    print(
        json.dumps(
            {
                "count": args.count,
                "threads": args.threads,
                "backend": backend,
                "format": "text",
                "flush": True,
                "compilation_included": False,
                "runs": results,
            },
            indent=2,
        )
    )

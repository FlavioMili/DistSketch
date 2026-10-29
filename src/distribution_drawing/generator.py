#!/usr/bin/env python3
"""Compile and run the parallel memory-mapped writer (Linux, macOS, Windows)."""

import argparse
import ast
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CONFIG = None
NATIVE_SOURCE = None


def read_config(path):
    """Read old downloaded generators without executing their Python code."""
    source = Path(path).read_text()
    if str(path).endswith(".json"):
        return json.loads(source)
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "CONFIG" for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise ValueError("No literal CONFIG assignment found in " + str(path))


def read_native_source(path, seen=None):
    """Inline our local headers so every downloaded generator is self-contained."""
    seen = set() if seen is None else seen
    path = Path(path).resolve()
    if path in seen:
        return ""
    seen.add(path)

    def include(match):
        return read_native_source(path.parent / match.group(1), seen)

    source = path.read_text().replace("#pragma once", "")
    return re.sub(r'^#include "([^"\n]+)"\s*$', include, source, flags=re.MULTILINE)


def compile_writer(source, compiler, flags, cache, backend, verbose):
    msvc = Path(compiler).name.lower() in ("cl", "cl.exe")
    variants = [("openmp", flags + (["/openmp"] if msvc else ["-fopenmp"]))]
    if backend == "serial":
        variants = [("serial", flags)]
    elif backend == "auto":
        variants.append(("serial", flags))
    cache.mkdir(parents=True, exist_ok=True)
    last_error = None
    for name, options in variants:
        digest = hashlib.sha256((source + compiler + repr(options)).encode()).hexdigest()[:20]
        executable = cache.resolve() / ("writer-" + digest + (".exe" if os.name == "nt" else ""))
        if executable.exists():
            return executable, name
        if verbose:
            print("Compiling " + name + " writer (cached)...", file=sys.stderr)
        with tempfile.TemporaryDirectory(dir=cache) as temporary:
            cpp = Path(temporary).resolve() / "writer.cpp"
            binary = cpp.with_suffix(".exe" if os.name == "nt" else ".out")
            cpp.write_text(source)
            command = (
                [
                    compiler,
                    *options,
                    str(cpp),
                    "/Fe:" + str(binary),
                    "/Fo:" + str(cpp.with_suffix(".obj")),
                ]
                if msvc
                else [compiler, *options, str(cpp), "-o", str(binary)]
            )
            build = subprocess.run(command, capture_output=True, text=True)
            if not build.returncode:
                binary.replace(executable)
                return executable, name
            last_error = build.stdout + build.stderr
    raise ValueError(
        "Compilation failed. Install an OpenMP-capable compiler/runtime or use --backend serial.\n"
        + last_error
    )


def validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Expected a distribution object.")
    counts, low, high = config.get("counts"), config.get("min"), config.get("max")
    if (
        not isinstance(counts, list)
        or not 1 <= len(counts) <= 4096
        or any(type(c) is not int or c < 0 for c in counts)
        or not 1 <= sum(counts) <= 2_000_000_000
        or type(low) is not int
        or type(high) is not int
        or low > high
        or low < -(2**53 - 1)
        or high > 2**53 - 1
        or not len(counts) <= high - low + 1 <= 2**53 - 1
    ):
        raise ValueError("Use safe integer keys, up to 4,096 bins, and 1–2 billion elements.")
    fmt, order = config.get("format", "txt"), config.get("order", "shuffled")
    if fmt == "binary":
        fmt = "u32" if 0 <= low <= high <= 2**32 - 1 else "i64"
    if fmt not in ("txt", "csv", "u32", "i64") or order not in ("sorted", "shuffled"):
        raise ValueError("Invalid output format or order.")
    if fmt == "u32" and not 0 <= low <= high <= 2**32 - 1:
        raise ValueError("The keys do not fit uint32. Choose int64 instead.")
    return {"counts": list(counts), "min": low, "max": high, "format": fmt, "order": order}


def output_size(config):
    config = validate_config(config)
    counts, fmt = config["counts"], config["format"]
    total = sum(counts)
    if fmt in ("u32", "i64"):
        return total * (4 if fmt == "u32" else 8)
    width, bins = config["max"] - config["min"] + 1, len(counts)
    size = total * 2
    for i, count in enumerate(counts):
        if not count:
            continue
        low = config["min"] + i * width // bins
        span = (i + 1) * width // bins - i * width // bins

        def below(key):
            return min(count, max(0, ((key - low) * count + span - 1) // span))

        size += below(0)
        power = 10
        while power <= 10**15:
            size += below(1 - power) + count - below(power)
            power *= 10
    return size


def prepare_writer(cache, backend="auto", verbose=False):
    compiler = (
        (shutil.which("cl") if os.name == "nt" else None)
        or shutil.which("g++")
        or shutil.which("clang++")
    )
    if not compiler:
        raise ValueError(
            "Install a C++17 compiler: GCC, Clang, or MSVC Build Tools (x64 Native Tools prompt)."
        )
    msvc = Path(compiler).name.lower() in ("cl", "cl.exe")
    arch = "-mcpu=native" if platform.machine().lower() in ("arm64", "aarch64") else "-march=native"
    flags = (
        ["/nologo", "/O2", "/DNDEBUG", "/EHsc", "/std:c++17"]
        if msvc
        else ["-O3", "-DNDEBUG", arch, "-std=c++17", "-pthread"]
    )
    source = NATIVE_SOURCE or read_native_source(Path(__file__).parent / "native/generator.cpp")
    return compile_writer(source, compiler, flags, Path(cache), backend, verbose)


def generate(
    config,
    output,
    *,
    cache,
    threads=16,
    seed=None,
    backend="auto",
    progress=None,
    on_process=None,
    on_stage=None,
    cancelled=lambda: False,
):
    """Run a native job; hooks let the local web app report progress and cancel."""
    config = validate_config(config)
    if type(threads) is not int or not 1 <= threads <= 256:
        raise ValueError("Threads must be between 1 and 256.")
    seed = secrets.randbits(64) if seed is None else seed
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError("Seed must fit an unsigned 64-bit integer.")
    if on_stage:
        on_stage("building")
    writer, engine = prepare_writer(cache, backend)
    if cancelled():
        raise RuntimeError("Generation cancelled.")
    with tempfile.TemporaryDirectory() as temporary:
        saved = Path(temporary) / "config.txt"
        saved.write_text(
            f"{config['min']} {config['max']} {len(config['counts'])}\n"
            + " ".join(map(str, config["counts"]))
        )
        command = [
            str(writer),
            str(saved),
            str(output),
            config["format"],
            config["order"],
            str(threads if engine == "openmp" else 1),
            str(seed),
            "1",
            "0",
            "262144",
        ]
        if progress is not None:
            command.append(str(progress))
        if on_stage:
            on_stage("generating")
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if on_process:
            on_process(process)
        if cancelled():
            process.terminate()
        _, error = process.communicate()
        if cancelled():
            raise RuntimeError("Generation cancelled.")
        if process.returncode:
            raise RuntimeError(
                error.decode(errors="replace").strip() or "The native writer failed."
            )
    return {
        "seed": seed,
        "backend": engine,
        "format": config["format"],
        "count": sum(config["counts"]),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", nargs="?", help="Output filename; existing files require --force")
    parser.add_argument(
        "--format",
        choices=["txt", "csv", "binary", "u32", "i64"],
        help="binary selects little-endian uint32 or int64 automatically (fastest)",
    )
    parser.add_argument("--order", choices=["sorted", "shuffled"])
    parser.add_argument(
        "--backend",
        choices=["auto", "openmp", "serial"],
        default="auto",
        help="Use OpenMP when available; auto falls back to serial",
    )
    parser.add_argument("--threads", type=int, default=min(os.cpu_count() or 1, 16))
    parser.add_argument("--seed", type=int, help="Reproducible shuffled order (unsigned 64-bit)")
    parser.add_argument("--count", type=int, help="Rescale the saved shape to this many elements")
    parser.add_argument(
        "--from-script", help="Read distribution from an older downloaded .py or JSON file"
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip final disk flush; timing then includes OS buffering",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print build, size and timing information (normal runs are silent)",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite an existing output file")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        / "distribution-drawing",
    )
    args = parser.parse_args(argv)
    config = read_config(args.from_script) if args.from_script else CONFIG
    if config is None:
        parser.error("Use --from-script saved_generator.py to read a saved drawing")
    counts = config["counts"]
    minimum, maximum = config["min"], config["max"]
    if (
        not isinstance(counts, list)
        or not 1 <= len(counts) <= 4096
        or any(type(c) is not int or c < 0 for c in counts)
        or not 1 <= sum(counts) <= 2_000_000_000
        or type(minimum) is not int
        or type(maximum) is not int
        or minimum < -(2**53 - 1)
        or maximum > 2**53 - 1
        or minimum > maximum
        or maximum - minimum + 1 > 2**53 - 1
    ):
        parser.error(
            "Invalid saved distribution: expected safe integer keys and 1–2 billion total elements"
        )
    if args.count is not None:
        if not 1 <= args.count <= 2_000_000_000:
            parser.error("--count must be between 1 and 2 billion")
        total = sum(counts)
        remainders = [(c * args.count % total, i) for i, c in enumerate(counts)]
        counts = [c * args.count // total for c in counts]
        for _, i in sorted(remainders, key=lambda x: (-x[0], x[1]))[: args.count - sum(counts)]:
            counts[i] += 1
    if not 1 <= args.threads <= 256:
        parser.error("--threads must be between 1 and 256")
    seed = secrets.randbits(64) if args.seed is None else args.seed
    if not 0 <= seed < 2**64:
        parser.error("--seed must fit an unsigned 64-bit integer")
    fmt = args.format or config["format"]
    if fmt == "binary":
        fmt = "u32" if 0 <= minimum <= maximum <= 2**32 - 1 else "i64"
    if fmt == "u32" and not 0 <= minimum <= maximum <= 2**32 - 1:
        parser.error("Key range does not fit uint32; use --format i64")
    if fmt not in ("txt", "csv", "u32", "i64"):
        parser.error("Unknown saved format")
    order = args.order or config["order"]
    if order not in ("sorted", "shuffled"):
        parser.error("Invalid saved order")
    output = args.output or ("distribution.bin" if fmt in ("u32", "i64") else "distribution." + fmt)
    if Path(output).exists() and not args.force:
        parser.error("Output already exists; choose another filename or use --force")
    compiler = (
        (shutil.which("cl") if os.name == "nt" else None)
        or shutil.which("g++")
        or shutil.which("clang++")
    )
    if not compiler:
        parser.error(
            "Install GCC/Clang, or MSVC Build Tools on Windows and run in its x64 Native Tools prompt. No NumPy dependency is needed."
        )
    msvc = Path(compiler).name.lower() in ("cl", "cl.exe")
    architecture_flag = (
        "-mcpu=native" if platform.machine().lower() in ("arm64", "aarch64") else "-march=native"
    )
    flags = (
        ["/nologo", "/O2", "/DNDEBUG", "/EHsc", "/std:c++17"]
        if msvc
        else ["-O3", "-DNDEBUG", architecture_flag, "-std=c++17", "-pthread"]
    )
    native_source = NATIVE_SOURCE or read_native_source(
        Path(__file__).parent / "native/generator.cpp"
    )
    executable, backend = compile_writer(
        native_source, compiler, flags, args.cache_dir, args.backend, args.verbose
    )
    if backend == "serial":
        args.threads = 1
    total = sum(counts)
    if args.verbose:
        print(
            f"Writing {total:,} {order} values as {fmt}; seed={seed}, threads={args.threads}, backend={backend}",
            file=sys.stderr,
            flush=True,
        )
    if args.verbose and fmt in ("u32", "i64"):
        print(
            f"Output size: {total * (4 if fmt == 'u32' else 8) / 1e9:.3f} GB, raw little-endian, no header",
            file=sys.stderr,
            flush=True,
        )
    start = time.perf_counter()
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "config.txt"
        path.write_text(f"{minimum} {maximum} {len(counts)}\n" + " ".join(map(str, counts)) + "\n")
        subprocess.run(
            [
                str(executable),
                str(path),
                str(output),
                fmt,
                order,
                str(args.threads),
                str(seed),
                "0" if args.no_sync else "1",
                "1" if args.force else "0",
                "262144",
            ],
            check=True,
        )
    if args.verbose:
        print(
            f"Finished in {time.perf_counter() - start:.3f} s (generation and final flush; compilation excluded).",
            file=sys.stderr,
        )


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

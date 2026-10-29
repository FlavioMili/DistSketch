"""Integration checks for exact counts, native text/binary output, and parallel order."""

import array
import os
import random
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src/distribution_drawing"


class NativeGeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temporary.name)
        launcher = runpy.run_path(str(ROOT / "generator.py"))
        compiler = (
            (shutil.which("cl") if os.name == "nt" else None)
            or shutil.which("g++")
            or shutil.which("clang++")
        )
        if not compiler:
            raise unittest.SkipTest("A C++17 compiler is required")
        msvc = Path(compiler).name.lower() in ("cl", "cl.exe")
        flags = (
            ["/nologo", "/O2", "/EHsc", "/std:c++17"] if msvc else ["-O2", "-std=c++17", "-pthread"]
        )
        flags += shlex.split(os.environ.get("GENERATOR_TEST_CXXFLAGS", ""))
        cls.executable, cls.backend = launcher["compile_writer"](
            launcher["read_native_source"](ROOT / "native/generator.cpp"),
            compiler,
            flags,
            cls.directory / "cache",
            "auto",
            False,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def generate(self, counts, low, high, order, fmt, threads=3, seed=0):
        config = self.directory / "config.txt"
        output = self.directory / "output"
        config.write_text(f"{low} {high} {len(counts)}\n" + " ".join(map(str, counts)))
        subprocess.run(
            [
                str(self.executable),
                str(config),
                str(output),
                fmt,
                order,
                str(threads),
                str(seed),
                "0",
                "1",
                "127",
            ],
            check=True,
            capture_output=True,
        )
        if fmt in ("txt", "csv"):
            return list(map(int, output.read_text().splitlines()))
        data = array.array("I" if fmt == "u32" else "q")
        data.frombytes(output.read_bytes())
        if sys.byteorder != "little":
            data.byteswap()
        return list(data)

    @staticmethod
    def reference(counts, low, high):
        result = []
        for i, count in enumerate(counts):
            lo = low + i * (high - low + 1) // len(counts)
            span = (i + 1) * (high - low + 1) // len(counts) - i * (high - low + 1) // len(counts)
            result.extend(lo + j * span // count for j in range(count))
        return result

    def test_exact_multisets_and_sorted_order(self):
        rng = random.Random(42)
        cases = [
            ([1], 0, 0),
            ([0, 7, 0, 3, 1], -10, 10),
            ([1, 0, 2], -(2**52), 2**52 - 2),
            ([19, 3], 2**53 - 101, 2**53 - 1),
        ]
        cases += [([rng.randrange(0, 70) for _ in range(31)], -1000, 1000) for _ in range(8)]
        for counts, low, high in cases:
            expected = self.reference(counts, low, high)
            with self.subTest(counts=counts, low=low):
                self.assertEqual(self.generate(counts, low, high, "sorted", "i64"), expected)
                for seed in [0, 1, 12345, 2**64 - 1]:
                    self.assertEqual(
                        sorted(self.generate(counts, low, high, "shuffled", "i64", seed=seed)),
                        expected,
                    )

    def test_parallel_text_and_binary_match(self):
        counts = [10, 0, 30, 900, 3]
        expected = self.generate(counts, 0, 1234567891, "shuffled", "u32", threads=1, seed=12345)
        self.assertNotEqual(expected, sorted(expected))
        for fmt in ["u32", "i64", "txt", "csv"]:
            self.assertEqual(
                self.generate(counts, 0, 1234567891, "shuffled", fmt, threads=4, seed=12345),
                expected,
            )

    def test_serial_matches_parallel(self):
        launcher = runpy.run_path(str(ROOT / "generator.py"))
        compiler = shutil.which("g++") or shutil.which("clang++")
        if not compiler:
            self.skipTest("GCC/Clang serial comparison")
        serial, _ = launcher["compile_writer"](
            launcher["read_native_source"](ROOT / "native/generator.cpp"),
            compiler,
            ["-O2", "-std=c++17"],
            self.directory / "serial-cache",
            "serial",
            False,
        )
        parallel = self.executable
        counts = [30, 4, 0, 71]
        expected = self.generate(counts, -1000, 1000, "shuffled", "txt", seed=42)
        try:
            self.executable = serial
            self.assertEqual(
                self.generate(counts, -1000, 1000, "shuffled", "txt", seed=42), expected
            )
        finally:
            self.executable = parallel

    def test_launcher_and_browser_bundle(self):
        from distribution_drawing.bundle import generator_template

        template = generator_template()
        source = self.directory / "download.py"
        source.write_text(
            template.replace(
                "__CONFIG__",
                repr(
                    {"counts": [3, 0, 9], "min": 0, "max": 255, "order": "sorted", "format": "txt"}
                ),
            )
        )
        output = self.directory / "launcher.bin"
        command = [
            sys.executable,
            str(source),
            str(output),
            "--count",
            "1003",
            "--format",
            "binary",
            "--cache-dir",
            str(self.directory / "cache"),
        ]
        result = subprocess.run(command, check=True, capture_output=True)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(output.stat().st_size, 1003 * 4)
        # Existing files must not be silently truncated.
        self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertEqual(output.stat().st_size, 1003 * 4)


if __name__ == "__main__":
    unittest.main()

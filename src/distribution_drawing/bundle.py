"""Build the optional standalone/static editor and portable Python export."""

import json
import shutil
from pathlib import Path

from .generator import read_native_source

PACKAGE = Path(__file__).resolve().parent


def generator_template():
    source = (PACKAGE / "generator.py").read_text()
    native = read_native_source(PACKAGE / "native/generator.cpp")
    return source.replace("NATIVE_SOURCE = None", "NATIVE_SOURCE = " + repr(native)).replace(
        "CONFIG = None", "CONFIG = __CONFIG__"
    )


def browser_bundle():
    return "const GENERATOR_TEMPLATE = " + json.dumps(generator_template()) + ";\n"


def build_site(output):
    output = Path(output).resolve()
    if output == (PACKAGE / "web").resolve():
        raise ValueError("Choose a separate output directory for the static site.")
    output.mkdir(parents=True, exist_ok=True)
    for path in (PACKAGE / "web").iterdir():
        if path.is_file():
            shutil.copy2(path, output / path.name)
    (output / "generator.js").write_text(browser_bundle())
    return output

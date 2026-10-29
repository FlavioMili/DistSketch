#!/usr/bin/env python3
"""Start DistSketch without installing anything."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from distribution_drawing.cli import main

if __name__ == "__main__":
    main()

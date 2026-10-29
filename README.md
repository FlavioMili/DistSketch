# DistSketch

Draw a shape. Pour some sand. Make a dataset.

A local web app for creating integer distributions by hand and generating sorted or shuffled datasets of up to **two billion values**. Everything stays on your computer.

```sh
python distribution.py
```

The launcher opens the editor in your browser. Draw a profile or pour sand, choose the element count and output format, then click **Generate dataset**. The file is written to `outputs/`, with progress, cancellation, and an optional browser download. There is no separate generation script to run.

Requires **Python 3.9+** and a **C++17 compiler** for native generation. No Python packages, Node.js, or web build tools are required. The native engine compiles on first use and is cached for subsequent jobs. Windows users can use `py distribution.py` from a compiler-enabled terminal.

- **Draw** relative frequencies, or **pour sand** with adjustable scatter.
- Generate **sorted** keys or a **shuffled permutation** with the same exact counts.
- Export **text**, **CSV**, or raw **little-endian binary**.
- Save/open a drawing as JSON, or export a portable generator to use elsewhere.
- Generate large files through memory mapping and OpenMP, with SIMD permutation batches.

Files are saved under `outputs/<job-id>/`. Completed datasets stay there when you stop the app; cancelled partial files are removed. The interface shows the final path, so you can use the existing file without downloading another copy.

The project is an independent Git repository. `.git` stores its history; `.github` contains optional automation for GitHub. The generated datasets, caches, build output and local environments are ignored.

```text
DistSketch/
├── distribution.py            # One entry point
├── pyproject.toml             # Optional package installation
├── src/distribution_drawing/
│   ├── cli.py                 # Launcher and commands
│   ├── server.py              # Local generation API
│   ├── generator.py           # Native build/run support
│   ├── web/                   # Browser editor
│   └── native/                # C++ writer and headers
├── tests/                     # Native and HTTP integration tests
├── examples/                  # Saved distributions
└── docs/                      # Usage, architecture and benchmarks
```

All commands use the same entry point:

```sh
# Choose where files go, or run without opening a browser.
python distribution.py --output-dir /path/to/datasets
python distribution.py --no-open --port 8765

# Generate from a saved drawing in the terminal.
python distribution.py generate demo.txt --from-script examples/bimodal.json --count 1000000

# Build a static editor for GitHub Pages or offline sharing.
python distribution.py build-site

# Check the project and reproduce performance measurements.
python distribution.py test
python distribution.py benchmark examples/bimodal.json --count 10000000
```

The **local app** uses the native engine. The **static editor** in `dist/` can generate and save datasets directly in supported browsers, using bounded-memory streaming. Other browsers download small datasets directly and export a self-contained Python generator for larger ones. Browser generation runs on the visitor’s computer; performance depends on the machine and has not yet been benchmarked.

Linux/GCC is verified locally. macOS and Windows build paths and a CI matrix are included, but have not been run in this environment. OpenMP falls back to serial when the compiler lacks its runtime. See [setup and usage](docs/guide.md), [architecture](docs/architecture.md), and [honest benchmark results](docs/benchmarks.md). One billion values can mean around **10 GB of text**; performance depends on the distribution, machine and storage.

The interface uses [Catppuccin Macchiato](https://catppuccin.com/palette/), with mauve curves and yellow sand. The plot footer shows output values, independently of the editor’s internal resolution. The recommended repository name is `DistSketch`; the source launcher remains `distribution.py`.

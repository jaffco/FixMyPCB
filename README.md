# FixMyPCB

Generates a Markdown model-tree report from a FreeCAD document (`.FCStd`).

## Requirements

- [FreeCAD](https://www.freecad.org/) >= 1.0 installed at `/Applications/FreeCAD.app`  
  (override with the `FREECAD_PYTHON` env var if installed elsewhere)
- No additional PyPI packages required — FreeCAD's bundled Python 3.11 is used directly

## Setup

Run once after cloning:

```bash
./init.sh
source venv/bin/activate
```

This sets up a `venv/` using FreeCAD's bundled Python with `--system-site-packages` so
the `FreeCAD` module is available without any pip installs.

To override the FreeCAD location:

```bash
FREECAD_PYTHON=/path/to/FreeCAD.app/Contents/Resources/bin/python ./init.sh
```

## Usage

```bash
./run.sh <path/to/file.FCStd>
```

Outputs `report.md` in the project root. To specify a different output file:

```bash
./run.sh model/Example.FCStd -o my_report.md
```

You can also invoke `main.py` directly after activating the venv:

```bash
source venv/bin/activate
python main.py model/Example.FCStd
```

## Report Contents

- Document metadata (name, path, FreeCAD version, timestamp)
- Object type summary (counts per `TypeId`)
- Full indented model tree (auxiliary geometry — axes, planes, origins — hidden for clarity)

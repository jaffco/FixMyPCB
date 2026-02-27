# EasyFSF: EasyEDA->FreeCAD STEP file Fixer!

When importing EasyEDA-exported `.step` files into FreeCAD, the model tree is not organized very well. This project will fix the model tree of your `.FCStd` so that each populated PCB is a single, movable `App::Part`. 

Handles panelized designs by automatically detecting and splitting sub-boards from connecting rails/tabs based on component placement.

## Workflow
1. Import `.step` from EasyEDA to FreeCAD
2. Save `<nameItWhateverLOL>.FCStd`
3. In a Bash shell, do `./init.sh`, `source venv/bin/activate`
4. In a Bash shell, do `./run.sh <path/to/your/FCStd>`
5. New `.FCStd` will be good.

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

Outputs `<input>_regrouped.FCStd` alongside the source file. The original is never modified. To specify a custom output path:

```bash
./run.sh model/Example.FCStd -o model/Example_fixed.FCStd
```

You can also invoke `main.py` directly after activating the venv:

```bash
source venv/bin/activate
python main.py model/Example.FCStd
```

## What it does

EasyEDA exports FreeCAD assemblies as a single flat `App::Part` where every component and the board are siblings at the same level. This makes it impossible to move a populated PCB as one unit.

`main.py` restructures the tree into:

```
EasyEDA PCB Model  (App::Part)
└── Board (Populated)  (App::Part)   ← move this to move everything
    ├── Sub-Board 0  (App::Part)     ← panelized sub-board A + its components
    │   ├── Board geometry
    │   └── [component wrappers…]
    ├── Sub-Board 1  (App::Part)     ← panelized sub-board B + its components
    │   └── …
    └── Panel Rails / Tabs  (App::Part)  ← connective geometry with no components
```

**Panel detection:** each `Part::Feature` inside the board container is tested for component coverage using XY bounding-box containment. Features with components become individual sub-board `App::Part`s; features with no components are grouped into `Panel Rails / Tabs`. Single-board (non-panelized) files are handled correctly — no split is performed.

**Visibility:** the tool preserves all existing Gui-layer visibility by copying `GuiDocument.xml` from the source into the output archive and injecting `ViewProvider` entries for newly created container objects.

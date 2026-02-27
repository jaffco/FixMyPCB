#!/usr/bin/env python
"""main.py – FreeCAD document model-tree reporter.

Usage
-----
    python main.py <path/to/file.FCStd> [-o output.md]

Requires FreeCAD's Python interpreter or FREECAD_LIB pointing to
the FreeCAD lib directory (containing FreeCAD.so).

Default FREECAD_LIB: /Applications/FreeCAD.app/Contents/Resources/lib
Override with the FREECAD_LIB environment variable.
"""

import argparse
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# FreeCAD path setup
# ---------------------------------------------------------------------------
FREECAD_LIB = os.environ.get(
    "FREECAD_LIB",
    "/Applications/FreeCAD.app/Contents/Resources/lib",
)
if FREECAD_LIB not in sys.path:
    sys.path.insert(0, FREECAD_LIB)

try:
    import FreeCAD  # type: ignore
except ImportError as exc:
    sys.exit(
        f"Cannot import FreeCAD.\n"
        f"Set FREECAD_LIB to the directory containing FreeCAD.so.\n"
        f"Error: {exc}"
    )

# ---------------------------------------------------------------------------
# Types that are internal/auxiliary and clutter the tree
# ---------------------------------------------------------------------------
_AUX_TYPES = frozenset({"App::Origin", "App::Line", "App::Plane"})


# ---------------------------------------------------------------------------
# Tree helpers
# ---------------------------------------------------------------------------

def _build_hierarchy(doc):
    """Return (roots, children_map) excluding auxiliary objects.

    roots         – list of Names with no non-aux parents
    children_map  – dict Name -> [child Name, …]
    """
    all_names = {obj.Name for obj in doc.Objects if obj.TypeId not in _AUX_TYPES}
    children: dict[str, list[str]] = {n: [] for n in all_names}

    for obj in doc.Objects:
        if obj.TypeId in _AUX_TYPES or obj.Name not in all_names:
            continue
        real_parents = [p.Name for p in obj.InList if p.Name in all_names]
        for parent_name in real_parents:
            if obj.Name not in children[parent_name]:
                children[parent_name].append(obj.Name)

    roots = [
        n for n in all_names
        if not any(n in kids for kids in children.values())
    ]
    # Stable order: preserve document order
    doc_order = [obj.Name for obj in doc.Objects if obj.Name in all_names]
    roots = [n for n in doc_order if n in roots]

    return roots, children


def _render_tree_lines(name_map, nodes, children, depth: int = 0) -> list[str]:
    lines = []
    indent = "  " * depth
    for name in nodes:
        obj = name_map[name]
        lines.append(f"{indent}- `{obj.Label}` &nbsp;*({obj.TypeId})*")
        kids = children.get(name, [])
        if kids:
            lines.extend(_render_tree_lines(name_map, kids, children, depth + 1))
    return lines


# ---------------------------------------------------------------------------
# Report generator
# ---------------------------------------------------------------------------

def generate_report(doc_path: Path) -> str:
    doc = FreeCAD.openDocument(str(doc_path))

    name_map = {obj.Name: obj for obj in doc.Objects}
    visible_objs = [obj for obj in doc.Objects if obj.TypeId not in _AUX_TYPES]

    type_counts: Counter = Counter(obj.TypeId for obj in visible_objs)
    total_all = len(doc.Objects)
    total_visible = len(visible_objs)

    roots, children = _build_hierarchy(doc)
    tree_lines = _render_tree_lines(name_map, roots, children)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sections: list[str] = []

    # ---- Header ----
    sections += [
        f"# FreeCAD Model Report",
        f"",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| **File** | `{doc_path.name}` |",
        f"| **Full Path** | `{doc_path}` |",
        f"| **Document Label** | `{doc.Label}` |",
        f"| **FreeCAD Version** | {'.'.join(FreeCAD.Version()[:3])} |",
        f"| **Generated** | {now} |",
        f"| **Total Objects (raw)** | {total_all} |",
        f"| **Visible Objects** | {total_visible} |",
        f"",
    ]

    # ---- Type Summary ----
    sections += [
        f"## Object Type Summary",
        f"",
        f"| Type | Count |",
        f"|------|------:|",
    ]
    for type_id, count in sorted(type_counts.items(), key=lambda x: (-x[1], x[0])):
        sections.append(f"| `{type_id}` | {count} |")
    sections.append("")

    # ---- Model Tree ----
    sections += [
        f"## Model Tree",
        f"",
        f"> Auxiliary geometry (axes, planes, origin markers) is hidden for clarity.",
        f"",
    ]
    if tree_lines:
        sections += tree_lines
    else:
        sections.append("*No objects found.*")
    sections.append("")

    FreeCAD.closeDocument(doc.Name)
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate a Markdown model-tree report from a FreeCAD document.",
    )
    parser.add_argument(
        "fcstd",
        metavar="FILE.FCStd",
        help="Path to the FreeCAD document (.FCStd)",
    )
    parser.add_argument(
        "-o", "--output",
        default="report.md",
        metavar="OUT",
        help="Output Markdown file (default: report.md)",
    )
    args = parser.parse_args()

    doc_path = Path(args.fcstd).resolve()
    if not doc_path.exists():
        sys.exit(f"Error: file not found: {doc_path}")
    if doc_path.suffix.lower() != ".fcstd":
        print(f"Warning: expected a .FCStd file, got '{doc_path.suffix}'", file=sys.stderr)

    print(f"Opening {doc_path} …")
    report = generate_report(doc_path)

    out_path = Path(args.output)
    out_path.write_text(report, encoding="utf-8")
    print(f"Report written → {out_path.resolve()}")


if __name__ == "__main__":
    main()

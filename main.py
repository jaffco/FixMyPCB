#!/usr/bin/env python
"""main.py – Regrouper for EasyEDA-exported FreeCAD PCB assemblies.

EasyEDA exports a flat tree:  root App::Part
                                  ├── Board~xxx  (App::Part)
                                  ├── R1~…       (App::Part)   ← component wrapper
                                  ├── U2~…       (App::Part)
                                  └── …  (all siblings)

This tool detects which components belong to which board by checking
whether each component wrapper's global XY placement falls inside the
board's XY bounding box, then restructures the tree into:

    root App::Part
      └── <BoardLabel> (Populated)  (App::Part)
            ├── Board~xxx           (App::Part)  ← board geometry
            ├── R1~…                (App::Part)  ← component
            └── …

Moving the "Populated" part then moves everything with it.

Usage
-----
    python main.py <input.FCStd> [-o output.FCStd]

If -o is omitted the output is written to <input_stem>_regrouped.FCStd
in the same directory as the input.

FREECAD_LIB env var overrides the default FreeCAD lib path.
"""

import argparse
import os
import shutil
import sys
import zipfile
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
# Constants
# ---------------------------------------------------------------------------
_AUX_TYPES = frozenset({"App::Origin", "App::Line", "App::Plane"})
_BOARD_MARGIN_MM = 2.0  # XY containment tolerance


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _non_aux(doc):
    """All document objects excluding auxiliary geometry."""
    return [o for o in doc.Objects if o.TypeId not in _AUX_TYPES]


def _real_parents(obj, valid_names):
    return [p for p in obj.InList if p.Name in valid_names]


def _real_children(parent, objs, valid_names):
    return [o for o in objs if parent in _real_parents(o, valid_names)]


def _find_root(objs, valid_names):
    """Single top-level App::Part with no non-aux parents."""
    roots = [o for o in objs if not _real_parents(o, valid_names)]
    if len(roots) != 1:
        raise ValueError(
            f"Expected exactly 1 root object, found {len(roots)}: "
            + ", ".join(o.Label for o in roots)
        )
    return roots[0]


def _is_board_container(obj, objs, valid_names):
    """
    An App::Part whose direct Part::Feature children all have 'Board' in
    their label (i.e. the physical PCB geometry container).
    """
    if obj.TypeId != "App::Part":
        return False
    kids = _real_children(obj, objs, valid_names)
    feature_kids = [k for k in kids if k.TypeId == "Part::Feature"]
    if not feature_kids:
        return False
    return all("Board" in k.Label or "board" in k.Label for k in feature_kids)


def _board_global_bbox(bc, objs, valid_names):
    """
    Compute the global XY (and Z) bounding box of a board container,
    applying the container's own Placement translation.
    Assumes the board lies flat (no rotation), which is always true for
    EasyEDA exports.
    """
    kids = _real_children(bc, objs, valid_names)
    features = [k for k in kids if k.TypeId == "Part::Feature"]
    if not features:
        raise ValueError(f"Board container {bc.Label!r} has no Part::Feature children")

    tx, ty, tz = bc.Placement.Base
    bboxes = [f.Shape.BoundBox for f in features]
    return {
        "xmin": min(b.XMin for b in bboxes) + tx,
        "xmax": max(b.XMax for b in bboxes) + tx,
        "ymin": min(b.YMin for b in bboxes) + ty,
        "ymax": max(b.YMax for b in bboxes) + ty,
        "zmin": min(b.ZMin for b in bboxes) + tz,
        "zmax": max(b.ZMax for b in bboxes) + tz,
    }


def _assign_to_board(comp_wrapper, board_containers, board_bboxes, margin):
    """
    Return (board_container, was_fallback) for a component wrapper.

    Strategy:
      1. XY containment (with margin) in each board's bounding box.
      2. Multiple matches  → nearest centroid.
      3. No match          → nearest centroid fallback (was_fallback=True).
    """
    p = comp_wrapper.Placement.Base

    candidates = [
        bc for bc in board_containers
        if (
            board_bboxes[bc.Name]["xmin"] - margin <= p.x <= board_bboxes[bc.Name]["xmax"] + margin
            and board_bboxes[bc.Name]["ymin"] - margin <= p.y <= board_bboxes[bc.Name]["ymax"] + margin
        )
    ]

    def centroid_dist_sq(bc):
        bb = board_bboxes[bc.Name]
        cx = (bb["xmin"] + bb["xmax"]) / 2
        cy = (bb["ymin"] + bb["ymax"]) / 2
        return (p.x - cx) ** 2 + (p.y - cy) ** 2

    if len(candidates) == 1:
        return candidates[0], False
    elif len(candidates) > 1:
        return min(candidates, key=centroid_dist_sq), False
    elif board_containers:
        return min(board_containers, key=centroid_dist_sq), True  # fallback
    return None, True


# ---------------------------------------------------------------------------
# Panel splitting
# ---------------------------------------------------------------------------

def _split_panel_boards(doc, pcb_part, board_container, comp_wrappers,
                        objs, valid_names, margin):
    """
    Detect whether board_container holds a panelized PCB and, if so, split it
    into one App::Part per real sub-board plus a Panel_Rails part for connective
    features (tabs, rails) that carry no components.

    Detection: for each Part::Feature inside the board container, count how many
    component wrappers' XY placements land within that feature's XY bounding box.
    Features with components = real sub-boards; features without = connective tissue.

    Returns a list of new object Names created (for GuiDocument.xml patching).
    An empty list means no split was performed (single board, no panel).
    """
    board_features = [
        k for k in _real_children(board_container, objs, valid_names)
        if k.TypeId == "Part::Feature"
    ]

    # Build per-feature info
    tx, ty, tz = board_container.Placement.Base
    feature_info = []
    for bf in board_features:
        bb = bf.Shape.BoundBox
        xmin, xmax = bb.XMin + tx, bb.XMax + tx
        ymin, ymax = bb.YMin + ty, bb.YMax + ty

        # Find component wrappers whose placement falls on this feature
        candidates = []
        for cw in comp_wrappers:
            p = cw.Placement.Base
            if (xmin - margin <= p.x <= xmax + margin
                    and ymin - margin <= p.y <= ymax + margin):
                candidates.append(cw)

        feature_info.append({
            "feature":    bf,
            "xmin": xmin, "xmax": xmax,
            "ymin": ymin, "ymax": ymax,
            "components": candidates,
            "is_subboard": len(candidates) > 0,
        })

    sub_boards = [fi for fi in feature_info if fi["is_subboard"]]
    rail_feats  = [fi for fi in feature_info if not fi["is_subboard"]]

    # Nothing to split if there's only one populated feature and no rails
    if len(sub_boards) <= 1 and not rail_feats:
        return []

    print(
        f"  Panel detected: {len(sub_boards)} sub-board(s), "
        f"{len(rail_feats)} connective feature(s)"
    )

    # Resolve components that matched multiple sub-board bboxes → nearest centroid
    assigned: dict[str, list] = {fi["feature"].Name: [] for fi in sub_boards}
    for cw in comp_wrappers:
        p = cw.Placement.Base
        matches = [fi for fi in sub_boards if cw in fi["components"]]
        if len(matches) == 1:
            assigned[matches[0]["feature"].Name].append(cw)
        elif len(matches) > 1:
            def centroid_dist(fi):
                cx = (fi["xmin"] + fi["xmax"]) / 2
                cy = (fi["ymin"] + fi["ymax"]) / 2
                return (p.x - cx) ** 2 + (p.y - cy) ** 2
            best = min(matches, key=centroid_dist)
            assigned[best["feature"].Name].append(cw)
        # matches == 0: no sub-board bbox matched at all → stays unassigned here
        # (shouldn't happen since these are already board-level assigned components)

    # ---- Re-wire the tree ----
    # Vacate pcb_part to release bc and all comp_wrappers from GeoFeatureGroups
    pcb_part.Group = []
    # Vacate board_container to free its Part::Features
    board_container.Group = []

    new_names = []
    sub_parts = []

    for i, fi in enumerate(sub_boards):
        name  = f"SubBoard_{i}"
        label = f"Sub-Board {i}  ({len(assigned[fi['feature'].Name])} components)"
        part  = doc.addObject("App::Part", name)
        part.Label = label
        part.Group = [fi["feature"]] + assigned[fi["feature"].Name]
        sub_parts.append(part)
        new_names.append(name)
        print(f"    {label}")

    if rail_feats:
        rails_part = doc.addObject("App::Part", "Panel_Rails")
        rails_part.Label = "Panel Rails / Tabs"
        rails_part.Group = [fi["feature"] for fi in rail_feats]
        sub_parts.append(rails_part)
        new_names.append("Panel_Rails")
        print(f"    Panel Rails / Tabs ({len(rail_feats)} feature(s))")

    pcb_part.Group = sub_parts

    # Remove the now-empty board container
    doc.removeObject(board_container.Name)

    return new_names


# ---------------------------------------------------------------------------
# Core regroup
# ---------------------------------------------------------------------------

def regroup(doc):
    """
    Restructure the document in-place.

    Returns a stats dict.
    """
    objs = _non_aux(doc)
    valid_names = {o.Name for o in objs}

    root = _find_root(objs, valid_names)
    root_children = _real_children(root, objs, valid_names)

    # ---- Identify boards and component wrappers ----
    board_containers = [o for o in root_children if _is_board_container(o, objs, valid_names)]
    comp_wrappers    = [o for o in root_children if o not in board_containers]

    if not board_containers:
        raise ValueError("No board containers found. Is this an EasyEDA-exported FCStd?")

    print(f"  Boards found:       {len(board_containers)}")
    print(f"  Component wrappers: {len(comp_wrappers)}")

    # ---- Compute board bounding boxes ----
    board_bboxes = {}
    for bc in board_containers:
        bb = _board_global_bbox(bc, objs, valid_names)
        board_bboxes[bc.Name] = bb
        print(
            f"  Board {bc.Label!r:40s}  "
            f"XY ({bb['xmin']:.1f},{bb['ymin']:.1f})-({bb['xmax']:.1f},{bb['ymax']:.1f})"
        )

    # ---- Assign components to boards ----
    assignments   = {bc.Name: [] for bc in board_containers}
    unassigned    = []
    fallback_count = 0

    for cw in comp_wrappers:
        board, was_fallback = _assign_to_board(cw, board_containers, board_bboxes, _BOARD_MARGIN_MM)
        if board is None:
            unassigned.append(cw)
        else:
            if was_fallback:
                fallback_count += 1
            assignments[board.Name].append(cw)

    for bc in board_containers:
        print(f"  → {bc.Label!r}: {len(assignments[bc.Name])} components assigned")
    if unassigned:
        print(f"  → Unassigned: {len(unassigned)}")
    if fallback_count:
        print(f"  ⚠  {fallback_count} component(s) placed by nearest-centroid fallback")

    # Snapshot labels before bc objects may be deleted during panel splitting
    board_labels = {bc.Name: bc.Label for bc in board_containers}

    # ---- Build new tree ----
    # Must clear root first — FreeCAD enforces an object can only belong to
    # one GeoFeatureGroup at a time.
    root.Group = []

    new_root_group = []
    all_new_names  = []

    for i, bc in enumerate(board_containers):
        suffix    = f"_{i}" if len(board_containers) > 1 else ""
        pcb_name  = f"PopulatedPCB{suffix}"
        pcb_label = f"Board{suffix} (Populated)"

        pcb_part = doc.addObject("App::Part", pcb_name)
        pcb_part.Label = pcb_label
        pcb_part.Group = [bc] + assignments[bc.Name]
        all_new_names.append(pcb_name)

        # ---- Panel splitting ----
        print(f"  Checking {bc.Label!r} for panel sub-boards…")
        extra = _split_panel_boards(
            doc, pcb_part, bc, assignments[bc.Name],
            objs, valid_names, _BOARD_MARGIN_MM,
        )
        if extra:
            all_new_names.extend(extra)
        else:
            print(f"  Single board — no split")

        new_root_group.append(pcb_part)

    if unassigned:
        u_part = doc.addObject("App::Part", "Unassigned")
        u_part.Label = "Unassigned Components"
        u_part.Group = unassigned
        new_root_group.append(u_part)
        all_new_names.append("Unassigned")

    root.Group = new_root_group
    doc.recompute()

    # Make all pre-existing objects visible (FreeCAD may have hidden them
    # when we cleared root.Group; new_object_names get visibility via GuiDocument patch)
    for obj in doc.Objects:
        obj.Visibility = True

    return {
        "boards":      len(board_containers),
        "assignments": {label: len(assignments[name]) for name, label in board_labels.items()},
        "unassigned":  len(unassigned),
        "fallbacks":   fallback_count,
        "new_names":   all_new_names,
    }


# ---------------------------------------------------------------------------
# GUI visibility patcher
# ---------------------------------------------------------------------------

# Minimal ViewProvider XML for a new App::Part (Group/Part container)
_VIEWPROVIDER_TEMPLATE = """\
        <ViewProvider name="{name}" expanded="0" treeRank="0" Extensions="True">
            <Extensions Count="1">
                <Extension type="Gui::ViewProviderOriginGroupExtension" name="ViewProviderOriginGroupExtension">
                </Extension>
            </Extensions>
            <Properties Count="5" TransientCount="0">
                <Property name="DisplayMode" type="App::PropertyEnumeration" status="1">
                    <Integer value="0"/>
                </Property>
                <Property name="OnTopWhenSelected" type="App::PropertyEnumeration" status="1">
                    <Integer value="0"/>
                </Property>
                <Property name="SelectionStyle" type="App::PropertyEnumeration" status="1">
                    <Integer value="0"/>
                </Property>
                <Property name="ShowInTree" type="App::PropertyBool" status="1">
                    <Bool value="true"/>
                </Property>
                <Property name="Visibility" type="App::PropertyBool" status="1">
                    <Bool value="true"/>
                </Property>
            </Properties>
        </ViewProvider>"""


def _patch_gui_document(input_fcstd: Path, output_fcstd: Path, new_object_names: list[str]) -> None:
    """
    Copy GuiDocument.xml from input_fcstd into output_fcstd (which was saved
    headlessly and therefore lacks it), injecting ViewProvider entries for any
    new objects that don't exist in the original.
    """
    with zipfile.ZipFile(input_fcstd, "r") as zin:
        if "GuiDocument.xml" not in zin.namelist():
            print("  ⚠  Source has no GuiDocument.xml — skipping Gui patch")
            return
        gui_xml = zin.read("GuiDocument.xml").decode("utf-8")

    # Build entries for new objects not already present
    injections = []
    for name in new_object_names:
        if f'name="{name}"' not in gui_xml:
            injections.append(_VIEWPROVIDER_TEMPLATE.format(name=name))

    if injections:
        # Bump the Count attribute on ViewProviderData
        import re
        def bump_count(m):
            old = int(m.group(1))
            return f'Count="{old + len(injections)}"'
        gui_xml = re.sub(r'ViewProviderData Count="(\d+)"',
                         lambda m: f'ViewProviderData Count="{int(m.group(1)) + len(injections)}"',
                         gui_xml, count=1)
        # Insert before the closing tag
        insert_block = "\n".join(injections) + "\n    "
        gui_xml = gui_xml.replace("</ViewProviderData>", insert_block + "</ViewProviderData>", 1)
        print(f"  Injected {len(injections)} new ViewProvider entries into GuiDocument.xml")

    # Rewrite the output zip with the patched GuiDocument.xml appended
    tmp_path = output_fcstd.with_suffix(".tmp.FCStd")
    with zipfile.ZipFile(output_fcstd, "r") as zin, \
         zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        zout.writestr("GuiDocument.xml", gui_xml.encode("utf-8"))

    tmp_path.replace(output_fcstd)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Regroup an EasyEDA-exported FreeCAD assembly so each populated PCB "
            "is a single movable App::Part."
        )
    )
    parser.add_argument(
        "fcstd",
        metavar="INPUT.FCStd",
        help="Path to the source FreeCAD document",
    )
    parser.add_argument(
        "-o", "--output",
        metavar="OUTPUT.FCStd",
        default=None,
        help="Output path (default: <input>_regrouped.FCStd alongside input)",
    )
    args = parser.parse_args()

    input_path = Path(args.fcstd).resolve()
    if not input_path.exists():
        sys.exit(f"Error: file not found: {input_path}")

    if args.output:
        output_path = Path(args.output).resolve()
    else:
        output_path = input_path.with_stem(input_path.stem + "_regrouped")

    print(f"Input:  {input_path}")
    print(f"Output: {output_path}")

    # Work on a copy — never touch the original
    shutil.copy2(input_path, output_path)
    print("Copied to output path, opening…")

    doc = FreeCAD.openDocument(str(output_path))

    print("Analysing and regrouping…")
    stats = regroup(doc)

    doc.save()
    FreeCAD.closeDocument(doc.Name)

    print("Patching GuiDocument.xml for visibility…")
    _patch_gui_document(input_path, output_path, stats["new_names"])

    print()
    print("Done.")
    print(f"  Boards regrouped: {stats['boards']}")
    for board_label, count in stats["assignments"].items():
        print(f"    {board_label}: {count} components")
    if stats["unassigned"]:
        print(f"  Unassigned components: {stats['unassigned']}")
    if stats["fallbacks"]:
        print(f"  Fallback assignments:  {stats['fallbacks']}")
    print(f"  Saved → {output_path}")


if __name__ == "__main__":
    main()

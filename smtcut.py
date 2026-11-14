#!/usr/bin/env python3

# Cut fine-pitch SMT stencils from a gerber/PDF/SVG/DXF file using
# a Graphtec craft/vinyl cutter. (e.g. Silhouette Cameo or Portrait)
#
# Licensed under the terms of the GPL2, see LICENSE file.
# Copyright (c) 2012 Peter Monta <pmonta@gmail.com>
# Copyright (c) 2015 Philipp Klaus <philipp.klaus@gmail.com>
# Copyright (c) 2022 Julian Heinzel <jeinzi@gmx.de>
# Copyright (c) 2026 Andrew McConnell

import os
import sys
import shutil
import argparse

import pic
import paste
import config
import optimize


INKSCAPE_EXTENSIONS = ('svg', 'dxf')


def require(program):
    if shutil.which(program) is None:
        print(f"ERROR: Could not find '{program}' in your PATH. Exiting.", file=sys.stderr)
        sys.exit(1)


def legacy_strokes(input_filename, extension):
    """The original path: convert to PDF, then to PIC via Ghostscript and
       pstoedit, and read the line strokes. Used for PDF, SVG and DXF input,
       and for Gerber input with --legacy_gerbv."""
    temp_pdf = "_tmp_gerber.pdf"
    temp_ps  = "_tmp_gerber.ps"
    temp_pic = "_tmp_gerber.pic"
    pdf_filename = input_filename

    require("pstoedit")
    require("gs")
    if extension in INKSCAPE_EXTENSIONS:
        require("inkscape")
    elif extension != "pdf":
        require("gerbv")

    # If file is not a PDF, try to convert it to one.
    if extension != "pdf":
      if extension in INKSCAPE_EXTENSIONS:
        os.system(f"inkscape --export-filename={temp_pdf} '{input_filename}'")
      else:
        os.system(f"gerbv --export=pdf --output='{temp_pdf}' --border=0 '{input_filename}'")
      pdf_filename = temp_pdf

    # Convert PDF to PIC format.
    os.system(f"gs -q -dNOPAUSE -dBATCH -sDEVICE=ps2write -sOutputFile='{temp_ps}' '{pdf_filename}'")
    os.system(f"pstoedit -f pic '{temp_ps}' '{temp_pic}' 2>/dev/null")
    return pic.read_pic(temp_pic)


def build_cutter(args):
    if args.cutter == 'raw':
        import graphtec
        return graphtec.graphtec()
    import cameo
    return cameo.cameo(dry_run=args.dry_run,
                       transcript=args.transcript,
                       hardware=args.hardware,
                       toolholder=args.toolholder,
                       depth=args.depth,
                       blade_diameter=args.blade_diameter,
                       mat=args.mat,
                       overcut_mm=args.overcut,
                       bluetooth_name=args.bluetooth_name)


# Settings a saved material can hold, with their built-in defaults (None
# means "not set"). Values from config.py's defaults are filled in by
# resolve_settings().
MATERIAL_SETTINGS = {
    'speed': None, 'force': None, 'media_size': None, 'depth': None,
    'toolholder': 1, 'blade_diameter': 0.9, 'mat': 'cameo_12x12', 'overcut': 0.025,
    'thickness': None, 'shrink': 0.0, 'windowpane': None, 'pane_coverage': 0.65,
}


def resolve_settings(args, cnf):
    """Explicit options win, then the saved material, then the defaults.
       Optionally save the result as a material."""
    defaults = dict(MATERIAL_SETTINGS, speed=cnf["speed"], force=cnf["force"],
                    media_size=cnf["mediaSize"])
    preset = {}
    if args.material:
        materials = config.getMaterials()
        if args.material not in materials:
            known = ", ".join(sorted(materials)) or "none saved yet"
            print(f"ERROR: no saved material '{args.material}' in {config.configFilePath} "
                  f"(known: {known})", file=sys.stderr)
            sys.exit(1)
        preset = materials[args.material]
        print(f"material {args.material}: " + ", ".join(f"{k}={v}" for k, v in sorted(preset.items())),
              file=sys.stderr)
    for key, default in defaults.items():
        if getattr(args, key) is None:
            setattr(args, key, preset.get(key, default))
    if args.save_material:
        settings = {k: getattr(args, k) for k in MATERIAL_SETTINGS if getattr(args, k) is not None}
        config.saveMaterial(args.save_material, settings)
        print(f"saved material {args.save_material} in {config.configFilePath}", file=sys.stderr)


def design_openings(geom, args):
    """Window-pane, check paste release, shrink; return SMTCut strokes."""
    import gerber_reader
    if args.windowpane:
        geom, split, warnings = paste.windowpane(geom, args.windowpane, args.pane_coverage)
        for w in warnings:
            print(f"warning: window-paning: {w}", file=sys.stderr)
        print(f"window-paned {split} openings to {args.pane_coverage:.0%} coverage", file=sys.stderr)
    # the check looks at the openings as designed; --shrink only compensates
    # for the knife, so the cut openings should end up close to these
    if args.thickness:
        lines, _, _ = paste.release_report(geom, args.thickness)
        for line in lines:
            print(line, file=sys.stderr)
    if args.shrink:
        geom, lost = paste.shrink(geom, args.shrink)
        print(f"shrank openings by {args.shrink} mm", file=sys.stderr)
        if lost:
            print(f"warning: {lost} openings are narrower than twice the shrink and "
                  f"were dropped", file=sys.stderr)
    strokes = []
    for ring in gerber_reader.rings_mm(geom):
        strokes.append([(x / 25.4, y / 25.4) for (x, y) in ring])
    return strokes


def main():
    # Parse arguments.
    cnf = config.getDefaultConfig()
    floats = lambda s: list(map(float, s.split(',')))
    formatList = lambda a: str(a).strip("[]").replace(" ", "")

    # Format default values.
    dOffset = formatList(cnf["offset"])
    dBorder = formatList(cnf["border"])
    dMatrix = formatList(cnf["matrix"])
    dSpeed  = formatList(cnf["speed"])
    dMediaSize = formatList(cnf["mediaSize"]).replace(",", "×")

    parser = argparse.ArgumentParser(
        description='Cut SMT stencils from a paste layer. By default the job is sent '
                    'straight to a Silhouette Cameo 3/4/5 or Cameo 5 Alpha over USB; '
                    'use --cutter raw for the original stdout output.')
    parser.add_argument('--offset', type=floats, default=cnf["offset"],
        help=f'translate to device coordinates x,y (inches) (default: {dOffset})')
    parser.add_argument('--border', type=floats, default=cnf["border"],
        help=f'cut a border around the bounding box of the gerber file; 0,0 to disable (default: {dBorder})')
    parser.add_argument('--matrix', type=floats, default=cnf["matrix"],
        help=f'transform coordinates by [a b;c d] (default: identity transform {dMatrix})')
    parser.add_argument('--speed', type=floats, default=None,
        help=f'use speed s in device units; s2,s3 for multiple passes (default: {dSpeed})')
    parser.add_argument('--force', type=floats, default=None,
        help=f'use force f in device units; f2,f3 for multiple passes (default: first pass {cnf["force"][0]}, second pass {cnf["force"][1]})')
    parser.add_argument('--cut_mode', type=int, choices=[0,1], default=cnf["cutMode"],
        help=f'0 for highest accuracy (fine pitch), 1 for highest speed (default: {cnf["cutMode"]})')
    parser.add_argument('--media_size', type=floats, default=None,
        help=f'size of media: width across the carriage, length along the feed (default: {dMediaSize} inches)')
    parser.add_argument('--rotate', type=float, dest='theta', default=0.,
        help=f'rotate counterclockwise by theta degrees (default: no rotation)')
    parser.add_argument('--merge', type=int, choices=[0,1], default=cnf["merge"],
        help=f'merge close smaller structures into one big cutout (default: {cnf["merge"]})')
    parser.add_argument('--legacy_gerbv', action='store_true',
        help='read Gerber files through gerbv/Ghostscript/pstoedit like older versions '
             '(default: the built-in reader, which merges overlapping apertures)')

    cut = parser.add_argument_group('cutter (default backend: cameo)')
    cut.add_argument('--cutter', choices=['cameo', 'raw'], default='cameo',
        help='cameo: send over USB with the inkscape-silhouette driver (Cameo 3/4/5, '
             'Cameo 5 Alpha, Portrait 3/4); raw: write the original Cameo 1 command '
             'stream to stdout for file2graphtec.py or /dev/usb/lp0')
    cut.add_argument('--dry_run', action='store_true',
        help='build and check the job but do not move the cutter (no cutter needed '
             'when --hardware is given)')
    cut.add_argument('--transcript', metavar='FILE',
        help='write every command byte sent to the cutter to FILE (useful with --dry_run '
             'and tools/preview.py)')
    cut.add_argument('--hardware', metavar='NAME',
        help='force the cutter model, e.g. Silhouette_Cameo5_Alpha')
    cut.add_argument('--toolholder', type=int, choices=[1, 2], default=None,
        help='tool holder with the blade (default: 1, the AutoBlade side)')
    cut.add_argument('--depth', type=int, choices=range(0, 11), metavar='0-10', default=None,
        help='AutoBlade depth; omit to leave the blade where it is')
    cut.add_argument('--blade_diameter', type=float, default=None,
        help='drag-knife offset in mm for corner compensation (default: 0.9)')
    cut.add_argument('--mat', default=None,
        choices=['cameo_12x12', 'cameo_12x24', 'portrait_8x12', 'portrait_8_5x12',
                 'cameo_plus_15x15', 'cameo_pro_24x24', 'none'],
        help='cutting mat (default: cameo_12x12); none means media fed without a mat, '
             'which needs --media_size')
    cut.add_argument('--overcut', type=float, default=None,
        help='extend each straight cut at both ends, mm (default: 0.025, as the original 0.001 inch)')
    cut.add_argument('--bluetooth_name', metavar='NAME',
        help='connect over Bluetooth LE to the cutter advertising NAME (needs bleak)')

    design = parser.add_argument_group('stencil design (built-in Gerber reader only)')
    design.add_argument('--thickness', type=paste.parse_thickness, default=None, metavar='T',
        help='film thickness, e.g. 0.127, 0.127mm or 5mil; reports openings that fail the '
             'IPC-7525 paste-release guidelines (area ratio 0.66, aspect ratio 1.5)')
    design.add_argument('--shrink', type=float, default=None, metavar='MM',
        help='offset every opening inward by MM to compensate for the knife spreading '
             'the cut (the original advice was about 2 mil, 0.05 mm); negative grows them')
    design.add_argument('--windowpane', type=float, default=None, metavar='MM',
        help='split openings whose narrow side is at least MM into a grid of panes '
             '(e.g. QFN centre pads); off by default')
    design.add_argument('--pane_coverage', type=float, default=None, metavar='FRACTION',
        help='paste coverage of window-paned openings (default: 0.65)')

    mat = parser.add_argument_group('saved materials')
    mat.add_argument('--material', metavar='NAME',
        help='load saved settings for NAME from config.json; explicit options still win')
    mat.add_argument('--save_material', metavar='NAME',
        help='save the speed, force, depth, thickness, shrink and other cutting settings '
             'of this run as NAME in config.json')
    mat.add_argument('--list_materials', action='store_true',
        help='list saved materials and exit')

    parser.add_argument('input_filename', nargs='?')
    args = parser.parse_args()

    if args.list_materials:
        materials = config.getMaterials()
        if not materials:
            print(f"no saved materials in {config.configFilePath}")
        for name, settings in sorted(materials.items()):
            print(name + ": " + ", ".join(f"{k}={v}" for k, v in sorted(settings.items())))
        return

    if not args.input_filename:
        parser.error('the input file is required')

    # Check if file exists.
    if not os.path.isfile(args.input_filename):
        print(f"ERROR: File '{args.input_filename}' can't be found. Exiting.", file=sys.stderr)
        sys.exit(1)

    resolve_settings(args, cnf)

    extension = os.path.splitext(args.input_filename)[1][1:].lower()
    if extension in ('pdf',) + INKSCAPE_EXTENSIONS or args.legacy_gerbv:
        if args.thickness or args.shrink or args.windowpane:
            print("warning: thickness, shrink and window-pane settings only apply to Gerber "
                  "files read by the built-in reader; ignored for this input.", file=sys.stderr)
        strokes = legacy_strokes(args.input_filename, extension)
    else:
        import gerber_reader
        try:
            strokes, geom, reader = gerber_reader.read_strokes(args.input_filename)
        except gerber_reader.GerberError as e:
            print(f"ERROR: {args.input_filename}: {e}", file=sys.stderr)
            sys.exit(1)
        for w in reader.warnings:
            print(f"warning: {w}", file=sys.stderr)
        print(f"read {len(strokes)} outlines from {reader.stats['flashes']} flashes, "
              f"{reader.stats['draws']} draws and {reader.stats['regions']} regions",
              file=sys.stderr)
        strokes = design_openings(geom, args)

    if not strokes:
        print("ERROR: no geometry found in the input file. Exiting.", file=sys.stderr)
        sys.exit(1)

    # Convert to Graphtec.
    g = build_cutter(args)
    g.set(media_size=args.media_size)
    offset, border = args.offset, args.border
    g.set(offset=(offset[0]+border[0]+0.5,offset[1]+border[1]+0.5))
    g.set(matrix=args.matrix)
    g.start()

    # Optionally merge multiple smaller pads into bigger pads spanning the same area.
    if not args.merge == 0:
      import mergepads
      strokes = mergepads.fix_small_geometry(strokes, cnf["mergeThreshold"][0], cnf["mergeThreshold"][1])

    strokes = optimize.rotate(strokes, args.theta)
    strokes = optimize.justify(strokes)
    max_x,max_y = optimize.max_extent(strokes)

    border_path = [
      (-border[0], -border[1]),
      (max_x+border[0], -border[1]),
      (max_x+border[0], max_y+border[1]),
      (-border[0], max_y+border[1])
    ]

    for (s,f) in zip(args.speed, args.force):
      g.set(speed=s, force=f)
      if args.cut_mode==0:
        lines = optimize.optimize(strokes, border)
        for x in lines:
          g.line(*x)
      else:
        for stroke in strokes:
          g.closed_path(stroke)
      if border != [0, 0]:
        g.closed_path(border_path)

    if args.cutter == 'raw':
      g.end()
      return

    import cameo
    try:
      g.end()
    except cameo.OutOfBounds as e:
      print(f"ERROR: {e}", file=sys.stderr)
      sys.exit(1)




if __name__ == "__main__":
    main()

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
    parser.add_argument('--speed', type=floats, default=cnf["speed"],
        help=f'use speed s in device units; s2,s3 for multiple passes (default: {dSpeed})')
    parser.add_argument('--force', type=floats, default=cnf["force"],
        help=f'use force f in device units; f2,f3 for multiple passes (default: first pass {cnf["force"][0]}, second pass {cnf["force"][1]})')
    parser.add_argument('--cut_mode', type=int, choices=[0,1], default=cnf["cutMode"],
        help=f'0 for highest accuracy (fine pitch), 1 for highest speed (default: {cnf["cutMode"]})')
    parser.add_argument('--media_size', type=floats, default=cnf["mediaSize"],
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
    cut.add_argument('--toolholder', type=int, choices=[1, 2], default=1,
        help='tool holder with the blade (default: 1, the AutoBlade side)')
    cut.add_argument('--depth', type=int, choices=range(0, 11), metavar='0-10', default=None,
        help='AutoBlade depth; omit to leave the blade where it is')
    cut.add_argument('--blade_diameter', type=float, default=0.9,
        help='drag-knife offset in mm for corner compensation (default: 0.9)')
    cut.add_argument('--mat', default='cameo_12x12',
        choices=['cameo_12x12', 'cameo_12x24', 'portrait_8x12', 'portrait_8_5x12',
                 'cameo_plus_15x15', 'cameo_pro_24x24', 'none'],
        help='cutting mat (default: cameo_12x12); none means media fed without a mat, '
             'which needs --media_size')
    cut.add_argument('--overcut', type=float, default=0.025,
        help='extend each straight cut at both ends, mm (default: 0.025, as the original 0.001 inch)')
    cut.add_argument('--bluetooth_name', metavar='NAME',
        help='connect over Bluetooth LE to the cutter advertising NAME (needs bleak)')

    parser.add_argument('input_filename')
    args = parser.parse_args()

    # Check if file exists.
    if not os.path.isfile(args.input_filename):
        print(f"ERROR: File '{args.input_filename}' can't be found. Exiting.", file=sys.stderr)
        sys.exit(1)

    extension = os.path.splitext(args.input_filename)[1][1:].lower()
    if extension in ('pdf',) + INKSCAPE_EXTENSIONS or args.legacy_gerbv:
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

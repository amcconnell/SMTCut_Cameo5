#!/usr/bin/env python3

# Pick the cutter backend for the calibration scripts in this folder.
#
# Without options the scripts behave as before: the raw Cameo 1 command
# stream goes to stdout. With --cameo they drive a Cameo 3/4/5 or Cameo 5
# Alpha directly over USB through cameo.py:
#
#   python3 tests/test_forces.py --cameo --depth 2
#   python3 tests/test_forces.py --cameo --dry_run --hardware Silhouette_Cameo5_Alpha \
#       --transcript forces.gpgl && python3 tools/preview.py forces.gpgl
#
# Licensed under the terms of the GPL2, see LICENSE file.
# Copyright (c) 2026 Andrew McConnell

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def make():
  parser = argparse.ArgumentParser(description='SMTCut calibration pattern')
  parser.add_argument('--cameo', action='store_true',
                      help='drive a Cameo 3/4/5 or Cameo 5 Alpha over USB')
  parser.add_argument('--dry_run', action='store_true')
  parser.add_argument('--hardware', default=None)
  parser.add_argument('--transcript', default=None)
  parser.add_argument('--depth', type=int, default=None)
  parser.add_argument('--toolholder', type=int, default=1)
  parser.add_argument('--mat', default='cameo_12x12')
  args = parser.parse_args()
  if not args.cameo:
    import graphtec
    return graphtec.graphtec()
  import cameo
  return cameo.cameo(dry_run=args.dry_run, transcript=args.transcript,
                     hardware=args.hardware, depth=args.depth,
                     toolholder=args.toolholder, mat=args.mat)

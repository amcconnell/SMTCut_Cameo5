#!/usr/bin/env python3

# Render a cutter command stream (a --transcript file from the cameo backend,
# or the raw backend's stdout) to a PNG, as seen from above the mat: the
# media feeds from the top of the image toward the bottom, and the left edge
# of the image is the left edge of the media.
#
# Usage: python3 tools/preview.py job.gpgl [preview.png] [--moves]
#
# Each pass (each new force setting) gets its own colour. --moves also draws
# the knife-up travel moves.
#
# Licensed under the terms of the GPL2, see LICENSE file.
# Copyright (c) 2026 Andrew McConnell

import re
import sys

SU_PER_MM = 20.0

_TOKEN = re.compile(rb'(FX)(\d+)|(?<![A-Z])([MD])(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)')


def parse(data):
  """Return a list of passes; each pass is a dict with 'force' and a list of
     ('M'|'D', u_mm, v_mm) operations (u along the feed, v across)."""
  passes = []
  current = None
  for m in _TOKEN.finditer(data):
    if m.group(1):
      force = int(m.group(2))
      # a pressure command starts a new pass once the current one has cut
      # something (the cameo backend repeats the pressure within one setup)
      if current is None or any(o[0] == 'D' for o in current['ops']):
        current = dict(force=force, ops=[])
        passes.append(current)
      else:
        current['force'] = force
      continue
    op = m.group(3).decode()
    u = float(m.group(4)) / SU_PER_MM
    v = float(m.group(5)) / SU_PER_MM
    if current is None:
      current = dict(force=None, ops=[])
      passes.append(current)
    current['ops'].append((op, u, v))
  return [p for p in passes if any(o[0] == 'D' for o in p['ops'])]


def summary(passes):
  lines = []
  for i, p in enumerate(passes):
    cuts = sum(1 for o in p['ops'] if o[0] == 'D')
    # extent of the cuts only (not the final travel move off the job)
    pts = []
    for prev, op in zip([None] + p['ops'][:-1], p['ops']):
      if op[0] == 'D':
        pts.append(op[1:])
        if prev is not None:
          pts.append(prev[1:])
    us = [q[0] for q in pts]
    vs = [q[1] for q in pts]
    lines.append('pass %d: force %s, %d cuts, %.1f..%.1f mm along the feed, %.1f..%.1f mm across'
                 % (i + 1, p['force'], cuts, min(us), max(us), min(vs), max(vs)))
  return lines


def render(passes, out_png, moves=False):
  import matplotlib
  matplotlib.use('Agg')
  import matplotlib.pyplot as plt
  from matplotlib.collections import LineCollection

  colors = ['#c0392b', '#2c7fb8', '#2ca25f', '#8856a7', '#e6550d']
  fig, ax = plt.subplots(figsize=(10, 10), dpi=150)
  for i, p in enumerate(passes):
    cuts, travel = [], []
    pos = None
    for op, u, v in p['ops']:
      if op == 'D' and pos is not None:
        cuts.append([(pos[1], pos[0]), (v, u)])
      elif op == 'M' and pos is not None:
        travel.append([(pos[1], pos[0]), (v, u)])
      pos = (u, v)
    # later passes are drawn thinner so earlier ones stay visible underneath
    ax.add_collection(LineCollection(cuts, colors=colors[i % len(colors)],
                                     linewidths=max(0.3, 1.2 - 0.4 * i),
                                     label='pass %d (force %s)' % (i + 1, p['force'])))
    if moves:
      ax.add_collection(LineCollection(travel, colors='#bbbbbb', linewidths=0.2))
  ax.autoscale()
  ax.set_aspect('equal')
  ax.invert_yaxis()                  # feed direction points down
  ax.set_xlabel('across the carriage (mm)')
  ax.set_ylabel('along the feed (mm)')
  ax.grid(True, linewidth=0.2)
  ax.legend(loc='lower right', fontsize=8)
  fig.tight_layout()
  fig.savefig(out_png)


def main(argv):
  args = [a for a in argv[1:] if not a.startswith('--')]
  if not args:
    print(__doc__ or 'usage: preview.py job.gpgl [preview.png] [--moves]')
    return 1
  with open(args[0], 'rb') as f:
    data = f.read()
  passes = parse(data)
  for line in summary(passes):
    print(line)
  out = args[1] if len(args) > 1 else args[0] + '.png'
  render(passes, out, moves='--moves' in argv)
  print('wrote', out)
  return 0


if __name__ == '__main__':
  sys.exit(main(sys.argv))

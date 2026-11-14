#!/usr/bin/env python3

# cameo.py
#
# Drive current Silhouette cutters (Cameo 3/4/5, Cameo 5 Alpha, Portrait 3/4)
# through the inkscape-silhouette driver in silhouette/Graphtec.py.
#
# The class below is a drop-in replacement for graphtec.graphtec: it has the
# same set()/start()/line()/closed_path()/path()/end() methods and takes the
# same coordinates (inches, SMTCut frame: x runs along the media feed, y runs
# across the carriage). Instead of writing raw commands to stdout it buffers
# the whole job, checks that every point lies on the media, and then sends it
# over USB (or Bluetooth) with the per-model setup the modern machines need:
# tool holder selection (J1/J2), pressure and speed per tool holder,
# AutoBlade depth, cutting mat selection and the new USB vendor ID of the
# Cameo 5 Alpha.
#
# Licensed under the terms of the GPL2, see LICENSE file.
# Copyright (c) 2026 Andrew McConnell
#

import math
import os
import sys

MM_PER_INCH = 25.4

# mat name -> (length along the feed, width across) in inches; mirrors
# silhouette.Graphtec.CAMEO_MATS without importing the USB stack
MATS = {
  'none': None,
  'cameo_12x12': (12.0, 12.0),
  'cameo_12x24': (24.0, 12.0),
  'portrait_8x12': (12.0, 8.0),
  'portrait_8_5x12': (12.0, 8.5),
  'cameo_plus_15x15': (15.0, 15.0),
  'cameo_pro_24x24': (24.0, 24.0),
}


class OutOfBounds(Exception):
  pass


class cameo:
  def __init__(self, dry_run=False, transcript=None, hardware=None,
               toolholder=1, depth=None, blade_diameter=0.9, mat='cameo_12x12',
               overcut_mm=0.025, bluetooth_name=None, log=sys.stderr):
    """
    dry_run         generate the job without moving the cutter (no device needed)
    transcript      path of a file that receives every command byte sent
    hardware        force a model, e.g. 'Silhouette_Cameo5_Alpha' (needed for
                    a dry run without the cutter attached)
    toolholder      1 (the AutoBlade side) or 2
    depth           AutoBlade depth 0..10; None leaves the blade as it is
    blade_diameter  drag-knife offset in mm used for corner compensation
    mat             cutting mat, a key of MATS
    overcut_mm      extend every straight cut by this much at both ends
    bluetooth_name  connect over Bluetooth LE to a cutter advertising this name
    """
    if mat not in MATS:
      raise ValueError('unknown mat %r; choose one of %s' % (mat, ', '.join(sorted(MATS))))
    self.dry_run = dry_run
    self.transcript = transcript
    self.hardware = hardware
    self.toolholder = toolholder
    self.depth = depth
    self.blade_diameter = blade_diameter
    self.mat = mat
    self.overcut_in = overcut_mm / MM_PER_INCH
    self.bluetooth_name = bluetooth_name
    self.log = log

    self.offset = (0.5, 0.5)           # inches
    self.matrix = (1, 0, 0, 1)
    self.media_size = None             # (width across, length along feed), inches
    self.groups = [dict(speed=None, force=None, paths=[])]
    self.started = False
    self.ended = False
    self.result = None

  # -- graphtec.graphtec interface ---------------------------------------

  def start(self):
    self.started = True

  def set(self, **kwargs):
    for k, v in kwargs.items():
      if k in ('speed', 'force'):
        g = self.groups[-1]
        if g['paths']:
          g = dict(speed=g['speed'], force=g['force'], paths=[])
          self.groups.append(g)
        g[k] = v
      elif k == 'offset':
        self.offset = tuple(v)
      elif k == 'matrix':
        self.matrix = tuple(v)
      elif k == 'media_size':
        self.media_size = tuple(v)
      else:
        raise TypeError('unknown setting %r' % k)

  def transform(self, x, y):
    """SMTCut inches -> device millimetres (u along the feed, v across)."""
    m = self.matrix
    tx = m[0] * x + m[1] * y + self.offset[0]
    ty = m[2] * x + m[3] * y + self.offset[1]
    return tx * MM_PER_INCH, ty * MM_PER_INCH

  def _add(self, points):
    if len(points) < 2:
      return
    self.groups[-1]['paths'].append([self.transform(x, y) for (x, y) in points])

  def path(self, s):
    self._add(list(s))

  def closed_path(self, s):
    if len(s) < 3:
      return
    self._add(list(s) + [s[0]])

  def line(self, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length == 0:
      return
    ex, ey = dx / length * self.overcut_in, dy / length * self.overcut_in
    self._add([(x1 - ex, y1 - ey), (x2 + ex, y2 + ey)])

  def end(self):
    self.ended = True
    self.result = self.run()
    return self.result

  # -- job checks ----------------------------------------------------------

  def limits_mm(self):
    """(length along feed, width across) usable for this job, in mm."""
    lengths, widths = [], []
    mat = MATS[self.mat]
    if mat:
      lengths.append(mat[0] * MM_PER_INCH)
      widths.append(mat[1] * MM_PER_INCH)
    if self.media_size:
      widths.append(self.media_size[0] * MM_PER_INCH)
      lengths.append(self.media_size[1] * MM_PER_INCH)
    if not lengths:
      raise OutOfBounds('no mat and no media size given; pass --media_size so '
                        'the job can be checked before cutting')
    return min(lengths), min(widths)

  def extent_mm(self):
    us = [p[0] for g in self.groups for path in g['paths'] for p in path]
    vs = [p[1] for g in self.groups for path in g['paths'] for p in path]
    if not us:
      return None
    return min(us), min(vs), max(us), max(vs)

  def validate(self):
    ext = self.extent_mm()
    if ext is None:
      raise OutOfBounds('nothing to cut')
    umin, vmin, umax, vmax = ext
    length, width = self.limits_mm()
    problems = []
    if umin < 0:
      problems.append('%.2f mm before the top of the media' % -umin)
    if vmin < 0:
      problems.append('%.2f mm left of the media' % -vmin)
    if umax > length:
      problems.append('%.2f mm past the media length (%.1f mm)' % (umax - length, length))
    if vmax > width:
      problems.append('%.2f mm past the media width (%.1f mm)' % (vmax - width, width))
    if problems:
      raise OutOfBounds('job does not fit: ' + '; '.join(problems) +
                        '. Adjust --offset, --border or --media_size.')
    for g in self.groups:
      if g['paths'] and (g['speed'] is None or g['force'] is None):
        raise ValueError('every pass needs both a speed and a force')
    return ext

  # -- sending -------------------------------------------------------------

  def run(self):
    ext = self.validate()
    length_mm, width_mm = self.limits_mm()
    groups = [g for g in self.groups if g['paths']]

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from silhouette.Graphtec import SilhouetteCameo, PRODUCT_LINE_CAMEO3_ON

    cmdfile = open(self.transcript, 'wb') if self.transcript else None
    kwargs = dict(log=self.log, cmdfile=cmdfile, dry_run=self.dry_run,
                  force_hardware=self.hardware)
    if self.bluetooth_name:
      kwargs.update(bluetooth_le=True, bluetooth_name=self.bluetooth_name)
    dev = SilhouetteCameo(**kwargs)
    name = dev.hardware.get('name', 'unknown')
    print('cutter: %s' % name, file=self.log)
    if dev.product_id() not in PRODUCT_LINE_CAMEO3_ON:
      print('warning: %s is not a Cameo 3 or newer; for older machines the raw '
            'backend (the default) is the tested path' % name, file=self.log)

    if not self.dry_run:
      self.wait_for_media(dev)

    print('job extent: %.1f..%.1f mm along the feed, %.1f..%.1f mm across '
          '(limit %.1f x %.1f mm)' % (ext[0], ext[2], ext[1], ext[3], length_mm, width_mm),
          file=self.log)

    mat = None if self.mat == 'none' else self.mat
    clipped = 0
    for i, g in enumerate(groups):
      first = (i == 0)
      last = (i == len(groups) - 1)
      print('pass %d/%d: speed %s, force %s, %d strokes'
            % (i + 1, len(groups), g['speed'], g['force'], len(g['paths'])), file=self.log)
      dev.setup(media=None,
                speed=int(g['speed']),
                pressure=int(g['force']),
                toolholder=self.toolholder,
                cuttingmat=mat,
                sharpencorners=False,
                autoblade=self.depth is not None,
                depth=self.depth,
                bladediameter=self.blade_diameter,
                trackenhancing=False,
                mediawidth=width_mm,
                mediaheight=length_mm,
                skip_init=not first)
      # plot() takes (x, y) with x across and y along the feed
      pathlist = [[(v, u) for (u, v) in path] for path in g['paths']]
      res = dev.plot(pathlist=pathlist,
                     mediawidth=width_mm,
                     mediaheight=length_mm,
                     margintop=0,
                     marginleft=0,
                     offset=(0, 0),
                     endposition='below',
                     skip_init=not first,
                     skip_reset=not last)
      clipped += res['bbox'].get('clip', {}).get('count', 0)

    if clipped:
      print('warning: the driver clipped %d points at the media edge' % clipped, file=self.log)
    if not self.dry_run:
      dev.wait_for_ready(timeout=600, poll_interval=1.0)
    if cmdfile:
      cmdfile.flush()
    return dict(extent_mm=ext, passes=len(groups), clipped=clipped, hardware=name)

  def wait_for_media(self, dev):
    state = dev.status()
    if state == 'unloaded':
      print('load the mat with the stencil film, then press the load button...', file=self.log)
    if state != 'ready':
      state = dev.wait_for_ready(timeout=180, poll_interval=1.0)
    if state != 'ready':
      raise RuntimeError('cutter is not ready (status %r)' % (state,))

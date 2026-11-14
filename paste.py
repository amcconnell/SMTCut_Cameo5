#!/usr/bin/env python3

# Stencil aperture design: paste-release check, window-paning of large
# openings, and shrinking to compensate for the knife.
#
# All geometry is Shapely in millimetres, as returned by gerber_reader.
#
# The release check uses the two IPC-7525 rules of thumb for stencil
# apertures:
#
#   area ratio   = opening area / wall area = A / (perimeter * thickness)
#                  should be above 0.66, or paste tends to stay in the
#                  opening instead of transferring to the pad
#   aspect ratio = narrowest width / thickness, should be above 1.5
#
# Licensed under the terms of the GPL2, see LICENSE file.
# Copyright (c) 2026 Andrew McConnell

import math

from shapely import affinity
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

import gerber_reader

MIN_AREA_RATIO = 0.66
MIN_ASPECT_RATIO = 1.5
MM_PER_MIL = 0.0254


def parse_thickness(text):
  """'0.127', '0.127mm' or '5mil' -> millimetres."""
  t = str(text).strip().lower()
  if t.endswith('mil'):
    return float(t[:-3]) * MM_PER_MIL
  if t.endswith('mm'):
    return float(t[:-2])
  return float(t)


def min_width(poly):
  """Narrowest width of an opening: the short side of its minimum rotated
     rectangle (exact for rectangles and circles, a good estimate otherwise)."""
  rect = poly.minimum_rotated_rectangle
  pts = list(rect.exterior.coords)
  if len(pts) < 4:
    b = poly.bounds
    return min(b[2] - b[0], b[3] - b[1])
  a = math.dist(pts[0], pts[1])
  b = math.dist(pts[1], pts[2])
  return min(a, b)


def wall_length(poly):
  return poly.exterior.length + sum(r.length for r in poly.interiors)


class Opening:
  def __init__(self, poly, thickness):
    self.poly = poly
    self.area = poly.area
    self.perimeter = wall_length(poly)
    self.width = min_width(poly)
    self.area_ratio = self.area / (self.perimeter * thickness)
    self.aspect_ratio = self.width / thickness
    b = poly.bounds
    self.size = (b[2] - b[0], b[3] - b[1])
    c = poly.centroid
    self.center = (c.x, c.y)

  @property
  def ok(self):
    return self.area_ratio >= MIN_AREA_RATIO and self.aspect_ratio >= MIN_ASPECT_RATIO

  def max_thickness(self):
    """Thickest film for which this opening passes both rules."""
    return min(self.area / (MIN_AREA_RATIO * self.perimeter), self.width / MIN_ASPECT_RATIO)


def release_check(geom, thickness):
  openings = [Opening(p, thickness) for p in gerber_reader.polygons(geom) if p.area > 0]
  failing = sorted([o for o in openings if not o.ok], key=lambda o: o.area_ratio)
  return openings, failing


def release_report(geom, thickness, limit=10):
  """Human-readable lines for the paste-release check."""
  openings, failing = release_check(geom, thickness)
  lines = []
  mils = thickness / MM_PER_MIL
  if not failing:
    lines.append('paste release: all %d openings pass at %.3f mm (%.1f mil) film '
                 '(area ratio >= %.2f, aspect ratio >= %.1f)'
                 % (len(openings), thickness, mils, MIN_AREA_RATIO, MIN_ASPECT_RATIO))
    return lines, openings, failing
  worst_ok = min(o.max_thickness() for o in failing)
  lines.append('paste release: %d of %d openings are below the IPC-7525 guidelines '
               'at %.3f mm (%.1f mil) film; paste may stay in them'
               % (len(failing), len(openings), thickness, mils))
  lines.append('  all openings would pass with film up to %.3f mm (%.1f mil)'
               % (worst_ok, worst_ok / MM_PER_MIL))
  lines.append('  worst (Gerber coordinates, mm):')
  for o in failing[:limit]:
    lines.append('    at (%7.2f, %7.2f)  %.2f x %.2f mm  area ratio %.2f  aspect ratio %.2f'
                 % (o.center[0], o.center[1], o.size[0], o.size[1], o.area_ratio, o.aspect_ratio))
  if len(failing) > limit:
    lines.append('    ... and %d more' % (len(failing) - limit))
  return lines, openings, failing


# ---------------------------------------------------------------------------
# window-paning
# ---------------------------------------------------------------------------

def _web_for_coverage(w, h, nx, ny, coverage):
  """Web width giving the requested paste coverage for an nx x ny grid in a
     w x h rectangle: (w - (nx-1) web) (h - (ny-1) web) = coverage w h."""
  a = (nx - 1) * (ny - 1)
  b = -((nx - 1) * h + (ny - 1) * w)
  c = w * h * (1 - coverage)
  if a == 0:
    return -c / b
  disc = b * b - 4 * a * c
  # the smaller root is the physical one (webs narrower than the panes)
  return (-b - math.sqrt(max(0.0, disc))) / (2 * a)


def windowpane(geom, min_side, coverage=0.65, min_web=0.2):
  """Split openings whose narrow side is at least min_side (mm) into a grid
     of panes with the given paste coverage. Returns (geometry, count of
     openings split, list of warnings)."""
  out = []
  split = 0
  warnings = []
  for poly in gerber_reader.polygons(geom):
    rect = poly.minimum_rotated_rectangle
    pts = list(rect.exterior.coords)
    if len(pts) < 5:
      out.append(poly)
      continue
    e1 = (pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
    e2 = (pts[2][0] - pts[1][0], pts[2][1] - pts[1][1])
    l1, l2 = math.hypot(*e1), math.hypot(*e2)
    if min(l1, l2) < min_side:
      out.append(poly)
      continue
    # rotate the opening so its bounding rectangle is axis aligned
    angle = math.degrees(math.atan2(e1[1], e1[0]))
    c = rect.centroid
    local = affinity.rotate(poly, -angle, origin=c)
    minx, miny, maxx, maxy = local.bounds
    w, h = maxx - minx, maxy - miny
    nx = max(2, int(math.ceil(w / min_side)))
    ny = max(2, int(math.ceil(h / min_side)))
    web = _web_for_coverage(w, h, nx, ny, coverage)
    if web < min_web:
      warnings.append('opening %.2f x %.2f mm: webs would be %.2f mm for %.0f%% coverage; '
                      'using %.2f mm' % (w, h, web, coverage * 100, min_web))
      web = min_web
    pw = (w - (nx - 1) * web) / nx
    ph = (h - (ny - 1) * web) / ny
    if pw <= 0 or ph <= 0:
      warnings.append('opening %.2f x %.2f mm is too small to window-pane; left whole' % (w, h))
      out.append(poly)
      continue
    panes = []
    for i in range(nx):
      for j in range(ny):
        x0 = minx + i * (pw + web)
        y0 = miny + j * (ph + web)
        piece = local.intersection(box(x0, y0, x0 + pw, y0 + ph))
        # drop slivers left where a non-rectangular opening clips a pane
        for p in gerber_reader.polygons(piece):
          if p.area > 0.05 * pw * ph:
            panes.append(p)
    out.append(affinity.rotate(unary_union(panes), angle, origin=c))
    split += 1
  return unary_union(out), split, warnings


# ---------------------------------------------------------------------------
# shrinking
# ---------------------------------------------------------------------------

def shrink(geom, distance):
  """Offset every opening inward by distance mm (negative grows them), keeping
     square corners square. Returns (geometry, number of openings lost)."""
  if not distance:
    return geom, 0
  out = []
  lost = 0
  for poly in gerber_reader.polygons(geom):
    g = poly.buffer(-distance, join_style='mitre', mitre_limit=10.0)
    if g.is_empty:
      lost += 1
    else:
      out.append(g)
  return (unary_union(out) if out else Polygon()), lost

#!/usr/bin/env python3

# gerber_reader.py
#
# Read an RS-274X (Gerber X2/X3 compatible) paste layer and return the
# outlines of the stencil apertures.
#
# This replaces the gerbv -> PDF -> Ghostscript -> pstoedit -> PIC chain for
# Gerber input. That chain outlines every aperture primitive separately: a
# KiCad rounded-rectangle pad (an aperture macro) comes out as a body
# rectangle plus four corner circles, five overlapping outlines the knife
# would cut through the middle of the pad. Here all dark geometry is merged
# first, so every stencil opening is exactly one outline. It also needs no
# external programs: everything is plain Python plus Shapely.
#
# The reader implements the parts of the Gerber Layer Format Specification
# that paste layers use in practice:
#
#   - %FS (leading/trailing zero omission, absolute/incremental), %MO, G70/G71
#   - standard apertures C, R, O, P (with optional hole)
#   - aperture macros: primitives 1, 2/20, 21, 22, 4, 5, 7, variables,
#     arithmetic expressions and exposure off
#   - D01 linear and circular interpolation (G01/G02/G03, G74/G75)
#     with circle apertures, D01 linear draws with rectangle apertures
#     (swept rectangle, as gEDA pcb and Eagle emit), D02 moves, D03 flashes
#   - regions (G36/G37) with linear and arc segments
#   - polarity (%LPD/%LPC), aperture transformations (%LM, %LR, %LS)
#   - step and repeat (%SR)
#
# Attributes (%TF, %TA, %TO, %TD), comments and deprecated no-op commands are
# ignored. Anything else that would change the image raises GerberError, so a
# file the reader does not understand fails loudly instead of cutting a wrong
# stencil.
#
# Licensed under the terms of the GPL2, see LICENSE file.
# Copyright (c) 2026 Andrew McConnell
#

import math
import re

from shapely import affinity
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box
from shapely.geometry.polygon import orient
from shapely.ops import unary_union
from shapely.validation import make_valid

MM_PER_INCH = 25.4


class GerberError(Exception):
  pass


# ---------------------------------------------------------------------------
# geometry helpers (all in millimetres)
# ---------------------------------------------------------------------------

def _segments_for_arc(radius, sweep, tol):
  """Number of chords needed to keep the chord error below tol."""
  if radius <= tol:
    return max(1, int(math.ceil(abs(sweep) / (math.pi / 2))))
  step = 2 * math.acos(max(-1.0, 1 - tol / radius))
  return max(1, int(math.ceil(abs(sweep) / step)))


def _quad_segs(radius, tol):
  """Shapely quad_segs for a circle of the given radius."""
  return max(2, _segments_for_arc(radius, math.pi / 2, tol))


def circle(cx, cy, d, tol):
  r = d / 2.0
  if r <= 0:
    return Polygon()
  return Point(cx, cy).buffer(r, quad_segs=_quad_segs(r, tol))


def regular_polygon(cx, cy, d, n, rotation_deg):
  r = d / 2.0
  pts = []
  for i in range(int(n)):
    a = math.radians(rotation_deg) + 2 * math.pi * i / n
    pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
  return Polygon(pts)


def stadium(w, h, tol):
  """Obround centred on the origin."""
  if abs(w - h) < 1e-12:
    return circle(0, 0, w, tol)
  if w > h:
    r = h / 2.0
    seg = LineString([(-(w - h) / 2.0, 0), ((w - h) / 2.0, 0)])
  else:
    r = w / 2.0
    seg = LineString([(0, -(h - w) / 2.0), (0, (h - w) / 2.0)])
  return seg.buffer(r, quad_segs=_quad_segs(r, tol))


def arc_points(start, end, center, clockwise, tol, full_circle_if_closed=True):
  """Points along an arc from start to end around center, including both."""
  sx, sy = start
  ex, ey = end
  cx, cy = center
  r_start = math.hypot(sx - cx, sy - cy)
  r_end = math.hypot(ex - cx, ey - cy)
  r = (r_start + r_end) / 2.0
  a0 = math.atan2(sy - cy, sx - cx)
  a1 = math.atan2(ey - cy, ex - cx)
  if clockwise:
    sweep = a1 - a0
    while sweep >= 0:
      sweep -= 2 * math.pi
    if abs(sweep + 2 * math.pi) < 1e-12 and not full_circle_if_closed:
      sweep = 0.0
  else:
    sweep = a1 - a0
    while sweep <= 0:
      sweep += 2 * math.pi
    if abs(sweep - 2 * math.pi) < 1e-12 and not full_circle_if_closed:
      sweep = 0.0
  closed = math.hypot(ex - sx, ey - sy) < 1e-9
  if not closed and abs(abs(sweep) - 2 * math.pi) < 1e-12:
    sweep = 0.0
  n = _segments_for_arc(r, sweep, tol)
  pts = [start]
  for i in range(1, n):
    a = a0 + sweep * i / n
    # interpolate the radius too, in case the file's start and end radii
    # differ slightly (allowed by the spec within a small tolerance)
    ri = r_start + (r_end - r_start) * i / n
    pts.append((cx + ri * math.cos(a), cy + ri * math.sin(a)))
  pts.append(end)
  return pts


def _rotate(geom, deg):
  if not deg:
    return geom
  return affinity.rotate(geom, deg, origin=(0, 0))


def _fix(geom):
  if geom.is_empty:
    return geom
  if not geom.is_valid:
    geom = make_valid(geom)
  return geom


# ---------------------------------------------------------------------------
# aperture macro expressions
# ---------------------------------------------------------------------------

_EXPR_TOKEN = re.compile(r'\s*(?:(\d+\.?\d*|\.\d+)|(\$\d+)|([-+xX/()]))')


def eval_expression(text, variables):
  """Evaluate a macro arithmetic expression ($n, + - x / and parentheses)."""
  tokens = []
  pos = 0
  text = text.strip()
  while pos < len(text):
    m = _EXPR_TOKEN.match(text, pos)
    if not m or m.end() == pos:
      raise GerberError('bad macro expression: %r' % text)
    tokens.append(m.group(1) or m.group(2) or m.group(3))
    pos = m.end()
  tokens.append(None)
  idx = [0]

  def peek():
    return tokens[idx[0]]

  def take():
    t = tokens[idx[0]]
    idx[0] += 1
    return t

  def factor():
    t = take()
    if t == '+':
      return factor()
    if t == '-':
      return -factor()
    if t == '(':
      v = expr()
      if take() != ')':
        raise GerberError('unbalanced parentheses in %r' % text)
      return v
    if t is None:
      raise GerberError('truncated macro expression: %r' % text)
    if t.startswith('$'):
      return variables.get(int(t[1:]), 0.0)
    try:
      return float(t)
    except ValueError:
      raise GerberError('bad token %r in macro expression %r' % (t, text))

  def term():
    v = factor()
    while peek() in ('x', 'X', '/'):
      op = take()
      rhs = factor()
      v = v * rhs if op in ('x', 'X') else v / rhs
    return v

  def expr():
    v = term()
    while peek() in ('+', '-'):
      op = take()
      rhs = term()
      v = v + rhs if op == '+' else v - rhs
    return v

  value = expr()
  if peek() is not None:
    raise GerberError('trailing tokens in macro expression %r' % text)
  return value


class ApertureMacro:
  def __init__(self, name, statements):
    self.name = name
    self.statements = statements   # list of raw statement strings

  def render(self, params, tol, scale_to_mm):
    """Return the macro geometry in mm for the given parameters (file units)."""
    variables = {i + 1: v for i, v in enumerate(params)}
    geom = Polygon()
    for st in self.statements:
      st = st.strip()
      if not st:
        continue
      if st.startswith('0'):
        # comment primitive: "0" followed by a space or end
        if len(st) == 1 or not st[1].isdigit():
          continue
      if st.startswith('$'):
        m = re.match(r'\$(\d+)\s*=\s*(.*)$', st)
        if not m:
          raise GerberError('bad macro variable assignment %r' % st)
        variables[int(m.group(1))] = eval_expression(m.group(2), variables)
        continue
      fields = st.split(',')
      try:
        code = int(fields[0])
      except ValueError:
        raise GerberError('bad macro primitive %r in macro %s' % (st, self.name))
      v = [eval_expression(f, variables) for f in fields[1:]]
      prim, exposure = self._primitive(code, v, tol, scale_to_mm, st)
      if prim is None or prim.is_empty:
        continue
      prim = _fix(prim)
      if exposure:
        geom = geom.union(prim)
      else:
        geom = geom.difference(prim)
    return geom

  def _primitive(self, code, v, tol, s, st):
    def need(n):
      if len(v) < n:
        raise GerberError('macro %s primitive %r needs %d parameters'
                          % (self.name, st, n))

    if code == 1:                       # circle
      need(4)
      exposure, d, x, y = v[0], v[1] * s, v[2] * s, v[3] * s
      rot = v[4] if len(v) > 4 else 0.0
      return _rotate(circle(x, y, d, tol), rot), exposure >= 0.5
    if code in (2, 20):                 # vector line
      need(7)
      exposure, w = v[0], v[1] * s
      x1, y1, x2, y2, rot = v[2] * s, v[3] * s, v[4] * s, v[5] * s, v[6]
      length = math.hypot(x2 - x1, y2 - y1)
      if length == 0 or w == 0:
        return None, True
      ux, uy = (x2 - x1) / length, (y2 - y1) / length
      nx, ny = -uy * w / 2.0, ux * w / 2.0
      poly = Polygon([(x1 + nx, y1 + ny), (x2 + nx, y2 + ny),
                      (x2 - nx, y2 - ny), (x1 - nx, y1 - ny)])
      return _rotate(poly, rot), exposure >= 0.5
    if code == 21:                      # center line (rectangle)
      need(6)
      exposure, w, h, x, y, rot = v[0], v[1] * s, v[2] * s, v[3] * s, v[4] * s, v[5]
      return _rotate(box(x - w / 2.0, y - h / 2.0, x + w / 2.0, y + h / 2.0), rot), exposure >= 0.5
    if code == 22:                      # lower left line (deprecated)
      need(6)
      exposure, w, h, x, y, rot = v[0], v[1] * s, v[2] * s, v[3] * s, v[4] * s, v[5]
      return _rotate(box(x, y, x + w, y + h), rot), exposure >= 0.5
    if code == 4:                       # outline
      need(2)
      exposure, n = v[0], int(round(v[1]))
      need(2 + 2 * (n + 1) + 1)
      pts = [(v[2 + 2 * i] * s, v[3 + 2 * i] * s) for i in range(n + 1)]
      rot = v[2 + 2 * (n + 1)]
      return _rotate(_fix(Polygon(pts)), rot), exposure >= 0.5
    if code == 5:                       # regular polygon
      need(6)
      exposure, n, x, y, d, rot = v[0], int(round(v[1])), v[2] * s, v[3] * s, v[4] * s, v[5]
      # vertices are placed relative to the polygon centre, then the whole
      # primitive is rotated about the macro origin
      return _rotate(regular_polygon(x, y, d, n, 0.0), rot), exposure >= 0.5
    if code == 7:                       # thermal
      need(6)
      x, y, od, idia, gap, rot = v[0] * s, v[1] * s, v[2] * s, v[3] * s, v[4] * s, v[5]
      ring = circle(x, y, od, tol).difference(circle(x, y, idia, tol))
      cross = unary_union([box(x - od, y - gap / 2.0, x + od, y + gap / 2.0),
                           box(x - gap / 2.0, y - od, x + gap / 2.0, y + od)])
      return _rotate(ring.difference(cross), rot), True
    if code == 6:
      raise GerberError('moire macro primitive (6) is not supported (macro %s)' % self.name)
    raise GerberError('unknown macro primitive code %d in macro %s' % (code, self.name))


# ---------------------------------------------------------------------------
# apertures
# ---------------------------------------------------------------------------

class Aperture:
  """An aperture with its flash geometry (mm, centred on the origin)."""

  def __init__(self, kind, shape, size=None):
    self.kind = kind          # 'C', 'R', 'O', 'P' or 'M' (macro)
    self.shape = shape        # shapely geometry in mm, origin-centred
    self.size = size          # (w, h) in mm for C and R, used for draws


def _with_hole(geom, params, index, s, tol):
  if len(params) > index and params[index] > 0:
    geom = geom.difference(circle(0, 0, params[index] * s, tol))
  return geom


def make_standard_aperture(template, params, s, tol):
  if template == 'C':
    if not params:
      raise GerberError('circle aperture needs a diameter')
    d = params[0] * s
    return Aperture('C', _with_hole(circle(0, 0, d, tol), params, 1, s, tol), (d, d))
  if template == 'R':
    if len(params) < 2:
      raise GerberError('rectangle aperture needs width and height')
    w, h = params[0] * s, params[1] * s
    return Aperture('R', _with_hole(box(-w / 2, -h / 2, w / 2, h / 2), params, 2, s, tol), (w, h))
  if template == 'O':
    if len(params) < 2:
      raise GerberError('obround aperture needs width and height')
    w, h = params[0] * s, params[1] * s
    return Aperture('O', _with_hole(stadium(w, h, tol), params, 2, s, tol), (w, h))
  if template == 'P':
    if len(params) < 2:
      raise GerberError('polygon aperture needs a diameter and a vertex count')
    d, n = params[0] * s, int(round(params[1]))
    rot = params[2] if len(params) > 2 else 0.0
    if n < 3 or n > 12:
      raise GerberError('polygon aperture vertex count %d is outside 3..12' % n)
    return Aperture('P', _with_hole(regular_polygon(0, 0, d, n, rot), params, 3, s, tol), (d, d))
  raise GerberError('unknown standard aperture template %r' % template)


# ---------------------------------------------------------------------------
# the reader
# ---------------------------------------------------------------------------

_WORD = re.compile(r'([GDMXYIJ])([-+]?[\d.]+)')


class GerberReader:
  def __init__(self, tol_mm=0.0025):
    self.tol = tol_mm
    self.units = None            # 'mm' or 'in'
    self.int_digits = None
    self.dec_digits = None
    self.zero_omission = 'L'
    self.incremental = False
    self.apertures = {}
    self.macros = {}
    self.current_aperture = None
    self.x = 0.0                 # current point, file units
    self.y = 0.0
    self.interp = 'G01'
    self.quadrant = 'multi'      # G75 is the only mode allowed by current spec
    self.region = False
    self.contour = None          # list of points (mm) while in region mode
    self.region_contours = []
    self.polarity_dark = True
    self.mirror = ''             # '', 'X', 'Y', 'XY'
    self.rotation = 0.0
    self.scale = 1.0
    # accumulation: list of (dark, [geoms]) batches inside the current
    # step-and-repeat block (or the top level)
    self.batches = []
    self.sr = None               # (nx, ny, dx, dy) in file units
    self.sr_outer_batches = None
    self.warnings = []
    self.stats = {'flashes': 0, 'draws': 0, 'regions': 0}
    self.finished = False

  # -- units -----------------------------------------------------------

  def s(self):
    """Scale factor from file units to mm."""
    if self.units is None:
      raise GerberError('units (%MO) must be set before geometry')
    return MM_PER_INCH if self.units == 'in' else 1.0

  def coord(self, text):
    if '.' in text:
      return float(text)
    if self.int_digits is None:
      raise GerberError('coordinate format (%FS) must be set before coordinates')
    sign = 1.0
    if text[0] in '+-':
      if text[0] == '-':
        sign = -1.0
      text = text[1:]
    if self.zero_omission == 'T':
      text = text.ljust(self.int_digits + self.dec_digits, '0')
    return sign * int(text) / (10.0 ** self.dec_digits)

  # -- accumulation ----------------------------------------------------

  def add(self, geom):
    if geom is None or geom.is_empty:
      return
    if self.batches and self.batches[-1][0] == self.polarity_dark:
      self.batches[-1][1].append(geom)
    else:
      self.batches.append((self.polarity_dark, [geom]))

  @staticmethod
  def resolve(batches):
    result = Polygon()
    for dark, geoms in batches:
      g = _fix(unary_union([_fix(x) for x in geoms]))
      if dark:
        result = result.union(g)
      else:
        result = result.difference(g)
    return _fix(result)

  def close_step_repeat(self):
    if self.sr is None:
      return
    nx, ny, dx, dy = self.sr
    block = self.resolve(self.batches)
    s = self.s()
    copies = []
    for j in range(ny):
      for i in range(nx):
        copies.append(affinity.translate(block, i * dx * s, j * dy * s))
    self.batches = self.sr_outer_batches
    self.sr = None
    self.sr_outer_batches = None
    saved = self.polarity_dark
    self.polarity_dark = True
    self.add(unary_union(copies))
    self.polarity_dark = saved

  # -- aperture transformation -----------------------------------------

  def transformed(self, geom):
    if self.mirror:
      geom = affinity.scale(geom, -1 if 'X' in self.mirror else 1,
                            -1 if 'Y' in self.mirror else 1, origin=(0, 0))
    if self.rotation:
      geom = affinity.rotate(geom, self.rotation, origin=(0, 0))
    if self.scale != 1.0:
      geom = affinity.scale(geom, self.scale, self.scale, origin=(0, 0))
    return geom

  # -- parsing ---------------------------------------------------------

  def read(self, text):
    pos = 0
    n = len(text)
    while pos < n and not self.finished:
      c = text[pos]
      if c in ' \t\r\n':
        pos += 1
        continue
      if c == '%':
        end = text.find('%', pos + 1)
        if end < 0:
          raise GerberError('unterminated extended command at offset %d' % pos)
        self.extended(text[pos + 1:end])
        pos = end + 1
      else:
        end = text.find('*', pos)
        if end < 0:
          # trailing garbage without terminator; tolerate whitespace only
          if text[pos:].strip():
            raise GerberError('unterminated command at offset %d' % pos)
          break
        self.word_block(text[pos:end])
        pos = end + 1
    if self.region:
      raise GerberError('file ended inside a region (missing G37)')
    self.close_step_repeat()

  def extended(self, block):
    # an extended command block holds one or more '*'-terminated statements
    block = block.replace('\r', '').replace('\n', '')
    if block.startswith('AM'):
      parts = block.split('*')
      name = parts[0][2:].strip()
      self.macros[name] = ApertureMacro(name, [p for p in parts[1:] if p.strip()])
      return
    for st in block.split('*'):
      st = st.strip()
      if st:
        self.extended_statement(st)

  def extended_statement(self, st):
    head = st[:2]
    body = st[2:]
    if head == 'FS':
      m = re.match(r'([LTD]?)([AI]?)(?:N\d+)?(?:G\d+)?X(\d)(\d)Y(\d)(\d)', body)
      if not m:
        raise GerberError('cannot parse format statement %%FS%s' % body)
      if m.group(1) in ('L', 'T'):
        self.zero_omission = m.group(1)
      self.incremental = (m.group(2) == 'I')
      if (m.group(3), m.group(4)) != (m.group(5), m.group(6)):
        raise GerberError('different X and Y coordinate formats are not supported')
      self.int_digits = int(m.group(3))
      self.dec_digits = int(m.group(4))
    elif head == 'MO':
      if body.startswith('MM'):
        self.units = 'mm'
      elif body.startswith('IN'):
        self.units = 'in'
      else:
        raise GerberError('unknown units %%MO%s' % body)
    elif head == 'AD':
      self.define_aperture(body)
    elif head == 'LP':
      self.polarity_dark = not body.startswith('C')
    elif head == 'LM':
      self.mirror = '' if body.startswith('N') else body
    elif head == 'LR':
      self.rotation = float(body)
    elif head == 'LS':
      self.scale = float(body)
    elif head == 'SR':
      self.close_step_repeat()
      if body:
        vals = dict((k, float(v)) for k, v in re.findall(r'([XYIJ])([-+]?[\d.]+)', body))
        nx, ny = int(vals.get('X', 1)), int(vals.get('Y', 1))
        if nx > 1 or ny > 1:
          self.sr = (nx, ny, vals.get('I', 0.0), vals.get('J', 0.0))
          self.sr_outer_batches = self.batches
          self.batches = []
    elif head == 'IP':
      if body.startswith('NEG'):
        raise GerberError('negative image polarity (%IPNEG) is not supported')
    elif head == 'AB':
      raise GerberError('block apertures (%AB) are not supported yet')
    elif head in ('TF', 'TA', 'TO', 'TD', 'LN', 'IN', 'IJ', 'IO', 'IR', 'AS',
                  'OF', 'MI', 'SF', 'KO', 'PF'):
      if head in ('IR', 'OF', 'MI', 'SF') and re.search(r'[1-9]', body):
        raise GerberError('deprecated image transform %%%s%s is not supported' % (head, body))
    else:
      self.warnings.append('ignored unknown extended command %%%s' % st)

  def define_aperture(self, body):
    m = re.match(r'D(\d+)([^,]+)(?:,(.*))?$', body)
    if not m:
      raise GerberError('cannot parse aperture definition %%AD%s' % body)
    number = int(m.group(1))
    template = m.group(2)
    params = []
    if m.group(3):
      params = [float(p) for p in m.group(3).split('X') if p.strip() != '']
    s = self.s()
    if template in ('C', 'R', 'O', 'P'):
      self.apertures[number] = make_standard_aperture(template, params, s, self.tol)
    elif template in self.macros:
      shape = self.macros[template].render(params, self.tol, s)
      self.apertures[number] = Aperture('M', _fix(shape))
    else:
      raise GerberError('aperture D%d uses undefined macro %r' % (number, template))

  def word_block(self, block):
    block = block.replace('\r', '').replace('\n', '').strip()
    if not block:
      return
    if block.startswith('G04') or block.startswith('G4 ') or block == 'G4':
      return   # comment
    words = _WORD.findall(block)
    if not words:
      self.warnings.append('ignored block %r' % block)
      return
    coords = {}
    d_code = None
    for letter, value in words:
      if letter == 'G':
        self.g_code(int(float(value)))
      elif letter == 'M':
        if int(float(value)) in (0, 1, 2):
          self.finished = True
          return
      elif letter == 'D':
        d_code = int(value)
      else:
        coords[letter] = value
    if d_code is not None and d_code >= 10:
      if d_code not in self.apertures:
        raise GerberError('aperture D%d is used but never defined' % d_code)
      self.current_aperture = self.apertures[d_code]
      return
    if coords or d_code in (1, 2, 3):
      self.operation(coords, d_code if d_code is not None else 1)

  def g_code(self, g):
    if g in (1, 2, 3):
      self.interp = 'G%02d' % g
    elif g == 36:
      self.region = True
      self.contour = None
      self.region_contours = []
    elif g == 37:
      self.end_contour()
      for pts in self.region_contours:
        if len(pts) >= 3:
          self.add(_fix(Polygon(pts)))
          self.stats['regions'] += 1
      self.region = False
      self.region_contours = []
    elif g == 74:
      self.quadrant = 'single'
    elif g == 75:
      self.quadrant = 'multi'
    elif g == 70:
      self.units = 'in'
    elif g == 71:
      self.units = 'mm'
    elif g == 90:
      self.incremental = False
    elif g == 91:
      self.incremental = True
    elif g in (54, 55, 4):
      pass
    else:
      self.warnings.append('ignored G%02d' % g)

  def end_contour(self):
    if self.contour and len(self.contour) >= 3:
      self.region_contours.append(self.contour)
    self.contour = None

  def operation(self, coords, d):
    s = self.s()
    x0, y0 = self.x, self.y
    if self.incremental:
      x1 = x0 + self.coord(coords['X']) if 'X' in coords else x0
      y1 = y0 + self.coord(coords['Y']) if 'Y' in coords else y0
    else:
      x1 = self.coord(coords['X']) if 'X' in coords else x0
      y1 = self.coord(coords['Y']) if 'Y' in coords else y0
    i = self.coord(coords['I']) if 'I' in coords else 0.0
    j = self.coord(coords['J']) if 'J' in coords else 0.0
    self.x, self.y = x1, y1
    p0 = (x0 * s, y0 * s)
    p1 = (x1 * s, y1 * s)

    if d == 2:
      if self.region:
        self.end_contour()
        self.contour = [p1]
      return

    if d == 3:
      if self.region:
        raise GerberError('flash (D03) inside a region is not allowed')
      self.flash(p1)
      return

    # d == 1: interpolate
    if self.interp == 'G01':
      path = [p0, p1]
    else:
      center = self.arc_center(p0, p1, (i * s, j * s), self.interp == 'G02')
      path = arc_points(p0, p1, center, self.interp == 'G02', self.tol)

    if self.region:
      if self.contour is None:
        self.contour = [p0]
      self.contour.extend(path[1:])
      return
    self.draw(path)

  def arc_center(self, p0, p1, ij, clockwise):
    if self.quadrant == 'multi':
      return (p0[0] + ij[0], p0[1] + ij[1])
    # single quadrant: I and J are unsigned; pick the centre that gives an
    # arc of at most 90 degrees in the right direction with matching radii
    best = None
    for sx in (1, -1):
      for sy in (1, -1):
        c = (p0[0] + sx * abs(ij[0]), p0[1] + sy * abs(ij[1]))
        r0 = math.hypot(p0[0] - c[0], p0[1] - c[1])
        r1 = math.hypot(p1[0] - c[0], p1[1] - c[1])
        a0 = math.atan2(p0[1] - c[1], p0[0] - c[0])
        a1 = math.atan2(p1[1] - c[1], p1[0] - c[0])
        sweep = a1 - a0
        if clockwise:
          while sweep > 0:
            sweep -= 2 * math.pi
        else:
          while sweep < 0:
            sweep += 2 * math.pi
        if abs(sweep) <= math.pi / 2 + 1e-6:
          err = abs(r0 - r1)
          if best is None or err < best[0]:
            best = (err, c)
    if best is None:
      raise GerberError('cannot resolve single-quadrant arc centre')
    return best[1]

  def flash(self, p):
    ap = self.current_aperture
    if ap is None:
      raise GerberError('flash with no aperture selected')
    geom = affinity.translate(self.transformed(ap.shape), p[0], p[1])
    self.add(geom)
    self.stats['flashes'] += 1

  def draw(self, path):
    ap = self.current_aperture
    if ap is None:
      raise GerberError('draw with no aperture selected')
    if ap.kind == 'C':
      d = ap.size[0] * self.scale
      if d <= 0:
        return
      if len(path) == 2 and path[0] == path[1]:
        geom = circle(path[0][0], path[0][1], d, self.tol)
      else:
        geom = LineString(path).buffer(d / 2.0, quad_segs=_quad_segs(d / 2.0, self.tol))
    elif ap.kind == 'R' and len(path) == 2:
      w, h = ap.size[0] * self.scale, ap.size[1] * self.scale
      (xa, ya), (xb, yb) = path
      rects = [box(xa - w / 2, ya - h / 2, xa + w / 2, ya + h / 2),
               box(xb - w / 2, yb - h / 2, xb + w / 2, yb + h / 2)]
      geom = unary_union(rects).convex_hull
    else:
      raise GerberError('drawing with a %s aperture is not supported '
                        '(only circles, and rectangles for straight lines)' % ap.kind)
    self.add(geom)
    self.stats['draws'] += 1


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def read_geometry(path, tol_mm=0.0025):
  """Return (geometry_mm, reader) for a Gerber file."""
  with open(path, 'r', errors='replace') as f:
    text = f.read()
  reader = GerberReader(tol_mm=tol_mm)
  reader.read(text)
  geom = reader.resolve(reader.batches)
  return geom, reader


def polygons(geom):
  if geom.is_empty:
    return []
  if isinstance(geom, Polygon):
    return [geom]
  if isinstance(geom, MultiPolygon):
    return list(geom.geoms)
  if hasattr(geom, 'geoms'):
    out = []
    for g in geom.geoms:
      out.extend(polygons(g))
    return out
  return []


def rings_mm(geom, simplify_mm=0.0005):
  """Closed outlines (exteriors and holes) as lists of (x, y) in mm.
     The closing point is not repeated."""
  rings = []
  for poly in polygons(geom):
    if poly.area <= 0:
      continue
    if simplify_mm:
      poly = poly.simplify(simplify_mm, preserve_topology=True)
    poly = orient(poly)
    for ring in [poly.exterior] + list(poly.interiors):
      pts = list(ring.coords)[:-1]
      if len(pts) >= 3:
        rings.append(pts)
  return rings


def read_strokes(path, tol_mm=0.0025):
  """Read a Gerber paste layer and return closed strokes in inches with Y up,
     the format the rest of SMTCut (optimize.py) expects."""
  geom, reader = read_geometry(path, tol_mm=tol_mm)
  strokes = []
  for ring in rings_mm(geom):
    strokes.append([(x / MM_PER_INCH, y / MM_PER_INCH) for (x, y) in ring])
  return strokes, geom, reader

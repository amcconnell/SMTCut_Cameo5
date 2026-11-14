# Tests for gerber_reader.py
#
# The reference values come from the Gerber files' own aperture sizes; the
# gEDA-generated examples were also checked pixel by pixel against gerbv
# renders while this reader was written.
#
# Licensed under the terms of the GPL2, see LICENSE file.

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import gerber_reader as gr

EXAMPLES = os.path.join(ROOT, 'examples')


def read_text(text, tol=0.0025):
  r = gr.GerberReader(tol_mm=tol)
  r.read(text)
  return r.resolve(r.batches), r


def pad_at(geom, x, y):
  for p in gr.polygons(geom):
    if p.buffer(1e-6).contains(gr.Point(x, y)):
      return p
  raise AssertionError('no pad at (%g, %g)' % (x, y))


# -- expressions ----------------------------------------------------------

@pytest.mark.parametrize('expr,expected', [
  ('1+2x3', 7.0),
  ('(1+2)x3', 9.0),
  ('-$1+4', 2.0),
  ('$1/4', 0.5),
  ('$1X$2', 6.0),
  ('2-3-4', -5.0),
  ('.5x2', 1.0),
])
def test_expressions(expr, expected):
  assert gr.eval_expression(expr, {1: 2.0, 2: 3.0}) == pytest.approx(expected)


def test_bad_expression():
  with pytest.raises(gr.GerberError):
    gr.eval_expression('1+', {})


# -- example files ----------------------------------------------------------

def test_corners_flashes():
  geom, r = gr.read_geometry(os.path.join(EXAMPLES, 'corners.gbr'))
  polys = gr.polygons(geom)
  assert len(polys) == 4
  side = 0.0393 * 25.4
  for p in polys:
    assert p.area == pytest.approx(side * side, rel=1e-6)
  minx, miny, maxx, maxy = geom.bounds
  assert maxx - minx == pytest.approx((4.9803 - 0.0197) * 25.4 + side, abs=1e-4)


@pytest.mark.parametrize('name,outlines', [
  ('test_tiny.gbr', 9),
  ('test_0.5mm_0402.gbr', 82),
  ('fine_pitch_sampler.gbr', 147),
  ('example1.gbr', 1211),
  ('kicad_style_paste.gbr', 13),
  # real KiCad 7 output: 16 rectangles and 16 RoundRect macro pads; the old
  # gerbv/pstoedit chain turned this into 160 overlapping outlines
  ('kicad7_simple_2layer-F_Paste.gbr', 32),
])
def test_examples_outline_counts(name, outlines):
  strokes, geom, r = gr.read_strokes(os.path.join(EXAMPLES, name))
  assert len(strokes) == outlines
  assert not r.warnings


def test_rectangle_draw_is_a_swept_rectangle():
  # a square aperture dragged sideways must give a rectangle with square
  # corners (gerbonara and others approximate it with round caps)
  geom, _ = read_text('%MOMM*%%FSLAX46Y46*%%ADD10R,1.0X1.0*%D10*'
                      'X0Y0D02*X2000000Y0D01*M02*')
  assert geom.area == pytest.approx(3.0)
  assert geom.bounds == pytest.approx((-0.5, -0.5, 2.5, 0.5))


def test_circle_draw_has_round_caps():
  geom, _ = read_text('%MOMM*%%FSLAX46Y46*%%ADD10C,1.0*%D10*'
                      'X0Y0D02*X2000000Y0D01*M02*')
  assert geom.area == pytest.approx(2.0 + math.pi * 0.25, rel=2e-3)


# -- KiCad style macros, rotation and regions ------------------------------------

@pytest.fixture(scope='module')
def kicad():
  geom, r = gr.read_geometry(os.path.join(EXAMPLES, 'kicad_style_paste.gbr'))
  return geom


def test_roundrect_is_one_outline_with_right_area(kicad):
  pad = pad_at(kicad, 10, 10)
  r = 0.25
  expected = 1.4 * 1.2 - (4 - math.pi) * r * r
  assert pad.area == pytest.approx(expected, rel=5e-3)
  assert len(pad.interiors) == 0
  assert pad.bounds == pytest.approx((9.3, 9.4, 10.7, 10.6), abs=1e-6)
  # one closed outline per roundrect pad
  rings = gr.rings_mm(pad)
  assert len(rings) == 1


def test_obround(kicad):
  pad = pad_at(kicad, 15, 10)
  assert pad.bounds == pytest.approx((14.7, 9.4, 15.3, 10.6), abs=1e-6)
  assert pad.area == pytest.approx(0.6 * 0.6 + math.pi * 0.09, rel=5e-3)


def test_macro_rotation(kicad):
  pad = pad_at(kicad, 10, 14)
  assert pad.area == pytest.approx(0.5)
  half = (1.0 + 0.5) / 2 / math.sqrt(2)
  assert pad.bounds == pytest.approx((10 - half, 14 - half, 10 + half, 14 + half), abs=1e-6)


def test_aperture_rotation_lr(kicad):
  pad = pad_at(kicad, 13, 14)
  minx, miny, maxx, maxy = pad.bounds
  assert maxx - minx == pytest.approx(0.8)
  assert maxy - miny == pytest.approx(0.3)


def test_macro_exposure_off_makes_a_hole(kicad):
  pad = pad_at(kicad, 16.4, 14)
  assert len(pad.interiors) == 1
  assert pad.area == pytest.approx(math.pi * (0.25 - 0.0625), rel=5e-3)


def test_polygon_aperture(kicad):
  pad = pad_at(kicad, 19, 14)
  assert pad.area == pytest.approx(3 * math.sqrt(3) / 2 * 0.25, rel=1e-6)


def test_region_with_arc(kicad):
  pad = pad_at(kicad, 10.5, 17.5)
  assert pad.area == pytest.approx(2.0 + math.pi * 0.5, rel=2e-3)
  # arcs are approximated by chords within 0.0025 mm
  assert pad.bounds[3] == pytest.approx(19.0, abs=3e-3)


def test_clear_polarity(kicad):
  pad = pad_at(kicad, 13.2, 17.2)
  assert len(pad.interiors) == 1
  assert pad.area == pytest.approx(4.0 - math.pi * 0.16, rel=2e-3)


# -- format details -----------------------------------------------------------

def test_trailing_zero_omission_and_inches():
  geom, _ = read_text('%MOIN*%%FSTAX24Y24*%%ADD10R,0.1X0.1*%D10*X01Y01D03*M02*')
  c = geom.centroid
  assert (c.x, c.y) == pytest.approx((25.4, 25.4))


def test_incremental_coordinates():
  geom, _ = read_text('%MOMM*%%FSLIX33Y33*%%ADD10C,0.5*%D10*'
                      'X1000Y1000D03*X1000Y0D03*M02*')
  centres = sorted((round(p.centroid.x, 3), round(p.centroid.y, 3)) for p in gr.polygons(geom))
  assert centres == [(1.0, 1.0), (2.0, 1.0)]


def test_single_quadrant_arc_region():
  # quarter disc built with G74 (unsigned I/J)
  geom, _ = read_text('%MOMM*%%FSLAX33Y33*%%ADD10C,0.1*%D10*G74*G36*'
                      'X0Y0D02*X1000Y0D01*G03X0Y1000I1000J0D01*G01X0Y0D01*G37*M02*')
  assert geom.area == pytest.approx(math.pi / 4, rel=5e-3)


def test_step_and_repeat():
  geom, _ = read_text('%MOMM*%%FSLAX33Y33*%%ADD10R,1X1*%'
                      '%SRX3Y2I2.0J3.0*%D10*X0Y0D03*%SR*%M02*')
  polys = gr.polygons(geom)
  assert len(polys) == 6
  assert geom.area == pytest.approx(6.0)


def test_undefined_aperture_fails():
  with pytest.raises(gr.GerberError):
    read_text('%MOMM*%%FSLAX33Y33*%D11*X0Y0D03*M02*')


def test_unclosed_region_fails():
  with pytest.raises(gr.GerberError):
    read_text('%MOMM*%%FSLAX33Y33*%G36*X0Y0D02*X1000Y0D01*X0Y1000D01*')


def test_strokes_are_inches_y_up():
  strokes, geom, _ = gr.read_strokes(os.path.join(EXAMPLES, 'corners.gbr'))
  xs = [p[0] for s in strokes for p in s]
  ys = [p[1] for s in strokes for p in s]
  assert max(xs) - min(xs) == pytest.approx(4.9803 - 0.0197 + 0.0393, abs=1e-6)
  assert max(ys) - min(ys) == pytest.approx(4.9803 - 0.0197 + 0.0393, abs=1e-6)

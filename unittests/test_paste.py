# Tests for paste.py (release check, window-paning, shrink) and for saved
# materials in smtcut.py.
#
# Licensed under the terms of the GPL2, see LICENSE file.

import json
import math
import os
import subprocess
import sys

import pytest
from shapely import affinity
from shapely.geometry import Point, box

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import gerber_reader as gr
import paste

ALPHA = 'Silhouette_Cameo5_Alpha'

# a QFN-style footprint: 4 x 4 mm centre pad and two rows of 0.3 x 0.8 mm pads
QFN = ('%MOMM*%%FSLAX46Y46*%%ADD10R,4.0X4.0*%%ADD11R,0.3X0.8*%'
       'D10*X0Y0D03*D11*'
       + ''.join('X%dY-2900000D03*X%dY2900000D03*' % (x, x)
                 for x in range(-1500000, 1500001, 500000))
       + 'M02*')


def qfn_geometry():
  r = gr.GerberReader()
  r.read(QFN)
  return r.resolve(r.batches)


# -- thickness parsing and the release check ----------------------------------

@pytest.mark.parametrize('text,mm', [('5mil', 0.127), ('0.1mm', 0.1), ('0.127', 0.127), (0.076, 0.076)])
def test_parse_thickness(text, mm):
  assert paste.parse_thickness(text) == pytest.approx(mm)


def test_area_ratio_of_square_and_circle():
  t = 0.127
  big = paste.Opening(box(0, 0, 1, 1), t)
  assert big.area_ratio == pytest.approx(1 / (4 * t))
  assert big.ok
  small = paste.Opening(box(0, 0, 0.25, 0.25), t)
  assert small.area_ratio == pytest.approx(0.0625 / (1.0 * t))
  assert not small.ok
  # for a round opening the area ratio is D / 4T
  disc = paste.Opening(Point(0, 0).buffer(0.25, quad_segs=64), t)
  assert disc.area_ratio == pytest.approx(0.5 / (4 * t), rel=1e-3)


def test_aspect_ratio_uses_the_narrow_side():
  o = paste.Opening(affinity.rotate(box(0, 0, 3, 0.15), 30), 0.127)
  assert o.width == pytest.approx(0.15, rel=1e-6)
  assert o.aspect_ratio == pytest.approx(0.15 / 0.127)
  assert not o.ok


def test_max_thickness_makes_the_opening_pass():
  o = paste.Opening(box(0, 0, 0.25, 0.25), 0.127)
  t = o.max_thickness()
  again = paste.Opening(box(0, 0, 0.25, 0.25), t * 0.999)
  assert again.ok


def test_release_report_lists_failures():
  geom = box(0, 0, 1, 1).union(box(5, 5, 5.2, 5.2))
  lines, openings, failing = paste.release_report(geom, 0.127)
  assert len(openings) == 2 and len(failing) == 1
  assert 'below the IPC-7525' in lines[0]
  assert '5.10' in lines[3]           # location of the failing opening


# -- window-paning ---------------------------------------------------------------

def test_windowpane_square_centre_pad():
  geom = qfn_geometry()
  paned, split, warnings = paste.windowpane(geom, 2.5, coverage=0.65)
  assert split == 1 and not warnings
  centre = [p for p in gr.polygons(paned) if p.bounds[0] >= -2 and p.bounds[2] <= 2
            and p.bounds[1] >= -2 and p.bounds[3] <= 2]
  assert len(centre) == 4
  assert sum(p.area for p in centre) == pytest.approx(0.65 * 16, rel=1e-6)
  # the small pads are untouched
  assert len(gr.polygons(paned)) == 4 + 14


def test_windowpane_follows_rotation():
  geom = affinity.rotate(box(-2, -2, 2, 2), 30, origin=(0, 0))
  paned, split, _ = paste.windowpane(geom, 2.5, coverage=0.5)
  assert split == 1
  panes = gr.polygons(paned)
  assert len(panes) == 4
  assert paned.area == pytest.approx(8.0, rel=1e-6)
  assert geom.buffer(1e-9).contains(paned)


def test_windowpane_minimum_web():
  paned, split, warnings = paste.windowpane(box(0, 0, 2.6, 2.6), 2.5, coverage=0.95, min_web=0.2)
  assert split == 1 and warnings
  panes = sorted(gr.polygons(paned), key=lambda p: p.bounds)
  assert panes[1].bounds[1] - panes[0].bounds[3] == pytest.approx(0.2) or \
         panes[2].bounds[0] - panes[0].bounds[2] == pytest.approx(0.2)


# -- shrink -----------------------------------------------------------------------

def test_shrink_keeps_square_corners():
  g, lost = paste.shrink(box(0, 0, 1, 1), 0.05)
  assert lost == 0
  assert g.bounds == pytest.approx((0.05, 0.05, 0.95, 0.95))
  assert g.area == pytest.approx(0.81)


def test_shrink_drops_and_counts_vanishing_openings():
  g, lost = paste.shrink(box(0, 0, 1, 1).union(box(3, 0, 3.08, 1)), 0.05)
  assert lost == 1
  assert len(gr.polygons(g)) == 1


# -- CLI: design options and saved materials -------------------------------------

def run_cli(tmp_path, *args, config=None):
  env = dict(os.environ, SMTCUT_CONFIG=str(config or tmp_path / 'config.json'))
  return subprocess.run([sys.executable, os.path.join(ROOT, 'smtcut.py')] + list(args),
                        capture_output=True, text=True, cwd=str(tmp_path), env=env)


def test_cli_design_options(tmp_path):
  gbr = tmp_path / 'qfn.gbr'
  gbr.write_text(QFN)
  r = run_cli(tmp_path, '--dry_run', '--hardware', ALPHA, '--thickness', '5mil',
              '--windowpane', '2.5', '--shrink', '0.05', str(gbr))
  assert r.returncode == 0, r.stderr
  assert 'window-paned 1 openings' in r.stderr
  assert 'paste release: all 18 openings pass' in r.stderr
  assert 'shrank openings by 0.05 mm' in r.stderr


def test_saved_material_round_trip_and_precedence(tmp_path):
  cfg = tmp_path / 'config.json'
  gbr = os.path.join(ROOT, 'examples', 'test_tiny.gbr')
  r = run_cli(tmp_path, '--dry_run', '--hardware', ALPHA, '--speed', '3,3', '--force', '10,28',
              '--depth', '2', '--thickness', '4mil', '--save_material', 'duralar4', gbr, config=cfg)
  assert r.returncode == 0, r.stderr
  saved = json.loads(cfg.read_text())['materials']['duralar4']
  assert saved['force'] == [10, 28] and saved['depth'] == 2
  assert saved['thickness'] == pytest.approx(0.1016)

  t = str(tmp_path / 'job.gpgl')
  r = run_cli(tmp_path, '--dry_run', '--hardware', ALPHA, '--material', 'duralar4',
              '--force', '9,27', '--transcript', t, gbr, config=cfg)
  assert r.returncode == 0, r.stderr
  data = open(t, 'rb').read()
  assert b'FX9,1' in data and b'FX27,1' in data       # explicit option wins
  assert b'!3,1' in data and b'TF2,1' in data           # the rest from the material
  assert 'paste release' in r.stderr                    # thickness came from the material

  r = run_cli(tmp_path, '--list_materials', config=cfg)
  assert r.returncode == 0 and r.stdout.startswith('duralar4:')


def test_unknown_material(tmp_path):
  r = run_cli(tmp_path, '--material', 'nope', os.path.join(ROOT, 'examples', 'test_tiny.gbr'))
  assert r.returncode == 1
  assert "no saved material 'nope'" in r.stderr

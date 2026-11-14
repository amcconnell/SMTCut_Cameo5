# Tests for the cameo backend, run as dry runs against the vendored driver
# with the hardware forced to a Cameo 5 Alpha (no cutter needed).
#
# Licensed under the terms of the GPL2, see LICENSE file.

import io
import os
import re
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import cameo
import preview

ALPHA = 'Silhouette_Cameo5_Alpha'


def commands(path):
  data = open(path, 'rb').read()
  # strip escape sequences, split on ETX
  data = re.sub(rb'\x1b.', b'', data)
  return [c.decode() for c in data.split(b'\x03') if c]


def dry_cutter(tmp_path, **kw):
  t = str(tmp_path / 'job.gpgl')
  g = cameo.cameo(dry_run=True, transcript=t, hardware=ALPHA,
                  log=io.StringIO(), **kw)
  return g, t


def test_setup_commands_for_alpha(tmp_path):
  g, t = dry_cutter(tmp_path, depth=2, overcut_mm=0)
  g.set(offset=(1, 1))
  g.start()
  g.set(speed=2, force=8)
  g.line(0, 0, 1, 0)
  g.end()
  cmds = commands(t)
  # cutting mat, tool holder 1, pressure/speed with the tool holder suffix,
  # AutoBlade depth
  for expected in ('TG1', 'J1', 'FX8,1', '!2,1', 'TF2,1', 'FC18,1,1'):
    assert expected in cmds, expected
  # the old Cameo 1 forms must not appear
  assert 'FX8,0' not in cmds and '!2' not in cmds


def test_coordinates_and_units(tmp_path):
  g, t = dry_cutter(tmp_path, overcut_mm=0)
  g.set(offset=(1, 2))
  g.start()
  g.set(speed=1, force=5)
  g.line(0, 0, 1, 0)          # one inch along the feed
  g.end()
  cmds = commands(t)
  moves = [c for c in cmds if re.match(r'^[MD]\d', c)]
  # 1 inch = 508 device units; first coordinate is along the feed
  assert moves[0] == 'M508,1016'
  assert moves[1] == 'D1016,1016'


def test_overcut_extends_both_ends(tmp_path):
  g, t = dry_cutter(tmp_path, overcut_mm=0.5)
  g.set(offset=(1, 1))
  g.start()
  g.set(speed=1, force=5)
  g.line(0, 0, 1, 0)
  g.end()
  moves = [c for c in commands(t) if re.match(r'^[MD]\d', c)]
  assert moves[0] == 'M498,508'     # 0.5 mm = 10 units before the start
  assert moves[1] == 'D1026,508'    # and 10 units past the end


def test_multiple_passes(tmp_path):
  g, t = dry_cutter(tmp_path, depth=1)
  g.set(offset=(1, 1))
  g.start()
  for s, f in ((2, 8), (2, 30)):
    g.set(speed=s, force=f)
    g.closed_path([(0, 0), (0.2, 0), (0.2, 0.2), (0, 0.2)])
  res = g.end()
  assert res['passes'] == 2 and res['clipped'] == 0
  passes = preview.parse(open(t, 'rb').read())
  assert [p['force'] for p in passes] == [8, 30]
  assert [sum(1 for o in p['ops'] if o[0] == 'D') for p in passes] == [4, 4]


def test_out_of_bounds_is_refused_before_anything_is_sent(tmp_path):
  g, t = dry_cutter(tmp_path)
  g.set(offset=(11.5, 1))       # 11.5 in along the feed on a 12x12 mat
  g.start()
  g.set(speed=1, force=5)
  g.line(0, 0, 1, 0)
  with pytest.raises(cameo.OutOfBounds):
    g.end()
  assert not os.path.exists(t)


def test_media_size_limits_the_job(tmp_path):
  g, t = dry_cutter(tmp_path)
  g.set(media_size=(8.5, 11), offset=(1, 8))   # 8 in across on an 8.5 in sheet
  g.start()
  g.set(speed=1, force=5)
  g.line(0, 0, 0, 1)            # ends 9 in across
  with pytest.raises(cameo.OutOfBounds):
    g.end()


def test_force_per_square_like_test_forces(tmp_path):
  g, t = dry_cutter(tmp_path)
  g.set(offset=(5, 1))
  g.start()
  g.set(speed=2)
  for i in range(3):
    g.set(force=1 + i)
    g.closed_path([(0.5 * i, 0), (0.5 * i + 0.3, 0), (0.5 * i + 0.3, 0.3), (0.5 * i, 0.3)])
  res = g.end()
  assert res['passes'] == 3


def test_smtcut_cli_dry_run(tmp_path):
  t = str(tmp_path / 'tiny.gpgl')
  r = subprocess.run([sys.executable, os.path.join(ROOT, 'smtcut.py'), '--dry_run',
                      '--hardware', ALPHA, '--depth', '2', '--transcript', t,
                      os.path.join(ROOT, 'examples', 'test_tiny.gbr')],
                     capture_output=True, text=True, cwd=str(tmp_path))
  assert r.returncode == 0, r.stderr
  passes = preview.parse(open(t, 'rb').read())
  assert [p['force'] for p in passes] == [8, 30]
  # 9 rectangles diced into 4 edges cut in both directions, 4 training
  # lines and the 4-sided border, per pass
  for p in passes:
    assert sum(1 for o in p['ops'] if o[0] == 'D') == 9 * 4 * 2 + 4 + 4


def test_smtcut_cli_reports_oversize_job(tmp_path):
  r = subprocess.run([sys.executable, os.path.join(ROOT, 'smtcut.py'), '--dry_run',
                      '--hardware', ALPHA, '--media_size', '2,2',
                      os.path.join(ROOT, 'examples', 'test_tiny.gbr')],
                     capture_output=True, text=True, cwd=str(tmp_path))
  assert r.returncode == 1
  assert 'does not fit' in r.stderr


def test_raw_backend_still_writes_stdout(tmp_path):
  r = subprocess.run([sys.executable, os.path.join(ROOT, 'smtcut.py'), '--cutter', 'raw',
                      os.path.join(ROOT, 'examples', 'test_tiny.gbr')],
                     capture_output=True, cwd=str(tmp_path))
  assert r.returncode == 0, r.stderr
  passes = preview.parse(r.stdout)
  assert [p['force'] for p in passes] == [8, 30]


# -- the non-dry-run path, against a simulated cutter ----------------------------

class FakeCutter:
  """Stands in for the USB transport: records what is written and answers
     the driver's queries the way a Cameo 5 Alpha with an AutoBlade in tool
     holder 1 would."""

  def __init__(self, status=b'0'):
    self.written = bytearray()
    self.pending = None
    self.status = status
    self.location = 'fake'
    self.timeout_exceptions = (OSError,)

  def write_bytes(self, data, timeout):
    self.written += data
    if data == b'\x1b\x05':
      self.pending = self.status + b'\x03'
      self.status = b'0'          # becomes ready after the first poll
    elif data == b'\x1b\x15':
      self.pending = b'2,0\x03'   # AutoBlade in holder 1, holder 2 empty
    elif data == b'FG\x03':
      self.pending = b'CAMEO 5 ALPHA V1.00\x03'
    elif data in (b'TB71\x03', b'FA\x03'):
      self.pending = b'    0,    0\x03'
    return len(data)

  def read_bytes(self, size, timeout):
    data, self.pending = self.pending, None
    if data is None:
      raise OSError('timeout')
    return data

  def close(self):
    pass


def test_live_path_with_simulated_cutter(tmp_path, monkeypatch):
  from silhouette import Graphtec
  fake = FakeCutter(status=b'2')   # mat not loaded yet

  def find(self):
    self.hardware = Graphtec._hardware_by_product_id(Graphtec.PRODUCT_ID_SILHOUETTE_CAMEO5ALPHA)
    return object()

  monkeypatch.setattr(Graphtec.SilhouetteCameo, '_find_usb_device', find)
  monkeypatch.setattr(Graphtec, 'USBTransport', lambda dev, need_interface=False: fake)
  monkeypatch.setattr(Graphtec.time, 'sleep', lambda s: None)

  g = cameo.cameo(depth=2, log=io.StringIO())
  g.set(offset=(1, 1))
  g.start()
  for f in (8, 30):
    g.set(speed=2, force=f)
    g.closed_path([(0, 0), (0.2, 0), (0.2, 0.2), (0, 0.2)])
  res = g.end()

  assert res['hardware'] == 'Silhouette_Cameo5_Alpha'
  cmds = [c for c in re.sub(rb'\x1b.', b'', bytes(fake.written)).split(b'\x03') if c]
  cmds = [c.decode() for c in cmds]
  assert 'TF2,1' in cmds                       # depth set: the tool is an AutoBlade
  assert cmds.count('FX30,1') >= 1 and cmds.count('FX8,1') >= 1
  assert sum(1 for c in cmds if c.startswith('D')) == 8
  assert cmds[-1] == 'SO0'                     # media moved below the job at the end

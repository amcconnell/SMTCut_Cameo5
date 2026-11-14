<p align="center">
  <img width="600" src="logo.png">
  <h3 align="center">cut SMT stencils from Gerber files</h3>
</p>
<br><br>

[![GPL](https://img.shields.io/badge/license-GPL-blue)](https://github.com/Jeinzi/SMTCut/blob/master/LICENSE)
[![Python](https://img.shields.io/badge/language-Python3-orange)](https://www.python.org)
[![GitHub Release](https://img.shields.io/badge/release-v0.2-brightgreen)](https://github.com/Jeinzi/SMTCut/releases)

## About
SMTCut, previously called **gerber2graphtec**, is a tool for cutting accurate SMT stencils on a **Graphtec** or **Silhouette** vinyl cutter from Gerber, SVG, DXF or PDF files.<br>

Techniques include separately drawn line segments (no complex paths), antibacklash, drag-knife-angle "training", and multiple passes.<br>

This produces stencils usable down to approximately **0.5 mm pitch** (QFP/QFN) and **0201** discrete components, or perhaps even slightly better; but it is slower than normal cutting.

## This fork: SMTCut_Cameo5

This fork of [Jeinzi/SMTCut](https://github.com/Jeinzi/SMTCut) adds support for current Silhouette cutters, in particular the **Cameo 5 Alpha**, and a new Gerber reader.

| | Upstream SMTCut | This fork |
|---|---|---|
| Cutters | Cameo 1/2/3, Portrait 1/2/3 (raw Cameo 1 command stream) | Adds Cameo 4, Cameo 5, **Cameo 5 Alpha** and Alpha Plus, Portrait 4, Cameo Pro MK-II, through the [inkscape-silhouette](https://github.com/fablabnbg/inkscape-silhouette) driver (vendored in `silhouette/`) |
| Sending a job | write to stdout, then `file2graphtec.py` or `/dev/usb/lp0` | sent straight to the cutter over USB (or Bluetooth LE) |
| Gerber input | gerbv → PDF → Ghostscript → pstoedit | built-in reader (`gerber_reader.py`, needs only Shapely) |
| Safety | none | the whole job is checked against the mat and sheet size before anything moves |
| Preview | none | `--dry_run --transcript job.gpgl`, then `tools/preview.py job.gpgl` |
| Python | uses `distutils`, which Python 3.12 removed | runs on current Python |

Why the new cutters needed more than a new USB ID: the Cameo 5 Alpha has a different USB vendor ID (`0x3844` instead of Graphtec's `0x0b4d`), and the Cameo 3 and later have two tool holders, so tool selection (`J1`), pressure (`FX8,1`), speed (`!2,1`) and AutoBlade depth (`TF2,1`) all name the tool holder. The original command stream predates that.

Why the new Gerber reader: KiCad draws its default pads with a rounded-rectangle aperture macro. The gerbv/pstoedit chain outlines every primitive of the macro separately, so a KiCad 7 paste layer with 32 pads (`examples/kicad7_simple_2layer-F_Paste.gbr`) became **160 overlapping outlines**, and the knife would cut through the middle of every rounded pad. The built-in reader merges all copper first and produces one outline per opening (32 for that file). It handles standard and macro apertures, regions with arcs, clear polarity, aperture rotation and step-and-repeat, and stops with an error on Gerber features it doesn't support instead of cutting something wrong (unknown commands are reported as warnings). SVG, DXF and PDF input still use the old chain, and `--legacy_gerbv` forces it for Gerber files.

**Status:** the code paths are tested in dry runs against the real driver with the hardware set to a Cameo 5 Alpha (`python3 -m pytest`), but this fork has **not yet cut a stencil on a real Cameo 5 Alpha**. Expect to tune speed, force and blade depth (see [First cuts on a Cameo 5 Alpha](#first-cuts-on-a-cameo-5-alpha)).

## Getting Started

Install the Python packages:

```
pip3 install -r requirements.txt
```

#### macOS
```
brew install libusb
```

#### Linux
Allow your user to open the cutter without root:

```
sudo cp silhouette/silhouette-udev.rules /etc/udev/rules.d/99-silhouette.rules
sudo udevadm control --reload && sudo udevadm trigger
```

Only the legacy paths (SVG, DXF and PDF input, `--legacy_gerbv`) need [gerbv](https://gerbv.github.io), [Ghostscript](https://ghostscript.com), [pstoedit](http://www.calvina.de/pstoedit) and, for SVG/DXF, [Inkscape](https://inkscape.org).

## Usage (Cameo 3/4/5 and Cameo 5 Alpha)

Check the job first. A dry run needs no cutter; `--hardware` says which one to plan for:

```
python3 smtcut.py --dry_run --hardware Silhouette_Cameo5_Alpha --media_size 8.5,11 \
    --transcript job.gpgl paste.gbr
python3 tools/preview.py job.gpgl job.png
```

`job.png` shows the cuts as seen from above the mat, with the feed direction pointing down. Then cut for real (load the mat first):

```
python3 smtcut.py --depth 2 --media_size 8.5,11 paste.gbr
```

Useful options (all of them: `python3 smtcut.py --help`):

| Option | Meaning |
|---|---|
| `--speed 2,2 --force 8,30` | one value per pass; two passes by default, a light scoring pass and a cutting pass |
| `--depth N` | AutoBlade depth 0–10; omit to leave the blade where it is |
| `--media_size W,L` | sheet size in inches, width across the carriage and length along the feed; e.g. `8.5,11` for a letter-size sheet in the top-left corner of the mat |
| `--mat cameo_12x12` | cutting mat (default 12×12) |
| `--offset x,y` | inches from the top-left corner of the mat (x along the feed, y across) |
| `--border bx,by` | cut a frame this far (inches) around the stencil; `0,0` to disable |
| `--toolholder 1` | tool holder with the blade (1 is the AutoBlade side) |
| `--matrix a,b,c,d` | scale/skew correction from the calibration pattern |
| `--thickness 5mil` | check paste release for this film thickness |
| `--shrink 0.05` | shrink openings (mm) to compensate for the knife |
| `--windowpane 2.5` | split openings at least 2.5 mm wide into panes |
| `--material NAME` | use saved settings (see below) |
| `--cutter raw` | the original output to stdout for Cameo 1/2 and `file2graphtec.py` |

The stencil is rotated 90° on the mat (Gerber X runs along the feed) but never mirrored. For a bottom-side stencil, flip the cut film over.

## Stencil design

These work on Gerber files read by the built-in reader.

**Paste-release check.** Give the film thickness and every opening is checked against the IPC-7525 rules of thumb: area ratio (opening area ÷ wall area) of at least 0.66, and width ÷ thickness of at least 1.5. Below those, paste tends to stay in the opening instead of printing on the pad.

```
python3 smtcut.py --dry_run --hardware Silhouette_Cameo5_Alpha --thickness 5mil paste.gbr
```

```
paste release: 46 of 147 openings are below the IPC-7525 guidelines at 0.127 mm (5.0 mil) film; paste may stay in them
  all openings would pass with film up to 0.066 mm (2.6 mil)
  worst (Gerber coordinates, mm):
    at (   2.30,    1.42)  0.10 x 1.00 mm  area ratio 0.35  aspect ratio 0.78
```

The check only reports; it never stops a job.

**Shrink.** `--shrink 0.05` offsets every opening inward by 0.05 mm to make up for the knife spreading the cut (the original author measured about 2 mil). Square corners stay square. Openings narrower than twice the shrink are dropped, with a warning. The release check runs before the shrink, on the openings you want to end up with.

**Window-paning.** Large openings, such as QFN centre pads, print too much paste if they're cut as one hole. `--windowpane 2.5` splits every opening whose narrow side is at least 2.5 mm into a grid of panes, sized for `--pane_coverage` (default 0.65, so 65% of the pad gets paste). Webs are kept at least 0.2 mm wide.

## Saved materials

Once a material cuts well, save its settings so you don't have to retype them:

```
python3 smtcut.py --dry_run --hardware Silhouette_Cameo5_Alpha --speed 2,2 --force 8,30 \
    --depth 2 --thickness 5mil --shrink 0.05 --media_size 8.5,11 --save_material duralar5 paste.gbr
python3 smtcut.py --material duralar5 paste.gbr
python3 smtcut.py --list_materials
```

A material stores speed, force, AutoBlade depth, tool holder, blade diameter, overcut, mat, media size, thickness, shrink and window-pane settings in `config.json` next to the scripts (not in git; `SMTCUT_CONFIG` points elsewhere). Options on the command line override the saved values, so `--material duralar5 --force 8,32` changes only the force.

## First cuts on a Cameo 5 Alpha

The speed and force defaults come from the original Cameo; nobody has tuned them for the Alpha yet. A sensible order:

1. Put a 3–5 mil polyester film sheet (e.g. Grafix Dura-Lar) in the top-left corner of the 12×12 mat.
2. Cut the force ladder: 30 small squares at forces 1 to 30. Dry-run it first and look at the preview.
   ```
   python3 tests/test_forces.py --cameo --dry_run --hardware Silhouette_Cameo5_Alpha --transcript forces.gpgl
   python3 tools/preview.py forces.gpgl
   python3 tests/test_forces.py --cameo --depth 2
   ```
   The lowest force whose square drops out cleanly is your cutting force; the defaults (8, then 30) use roughly a quarter of the cutting force for the light first pass. Adjust `--depth` if no square drops out, or if the knife scores the mat deeply.
3. Cut the fine-pitch sampler with those values and inspect it under magnification:
   ```
   python3 smtcut.py --depth 2 --media_size 8.5,11 --force 8,30 examples/fine_pitch_sampler.gbr
   ```
4. For dimensional accuracy, cut `tests/test_calibrate.py --cameo`, measure the verniers, and pass the correction with `--matrix`.
5. Save what works with `--save_material` (see [Saved materials](#saved-materials)).

The calibration scripts in `tests/` default to the original stdout output; add `--cameo` (and optionally `--dry_run --hardware ... --transcript FILE`) to use the new backend.

## Usage (original Cameo, Portrait 1/2, raw output)

The original behaviour is still available with `--cutter raw`.

### Linux
E.g. a solderpaste gerber file (paste.gbr), and default settings:

```
./smtcut.py --cutter raw paste.gbr >/dev/usb/lp0
```

Or a more elaborate command line with linear map (to correct spatial miscalibration) and multiple passes with different speeds and forces:

```
./smtcut.py --cutter raw --offset 3,4 --matrix 1.001,0,-0.0005,0.9985 --speed 2,1 --force 5,25 paste.gbr >/dev/usb/lp0
```

### Mac OSX and Windows
With Mac OS X or Windows, the file2graphtec script can take the place of /dev/usb/lp0. But keep in mind that the software is tested on Mac OS X but not on Windows!

```
python3 smtcut.py --cutter raw paste.gbr > tmpfile
python3 file2graphtec.py tmpfile
```

## Calibration
To achieve the best results, one should run different calibrations, which can be found in the test folder after the blade is set. The following example shows how to calibrate the cutter on Mac OSX but the commands are also applicable to other operating systems (make sure you run these files from the main folder).

```
python3 tests/test_forces.py > tmpfile
python3 file2graphtec.py tmpfile
```

## Development

```
python3 -m pytest
```

The tests run the Gerber reader against the examples (including a real KiCad 7 paste layer) and run the Cameo backend in dry runs with the hardware set to a Cameo 5 Alpha, checking the command stream it would send.

## GUI
The GUI still uses the original raw output path. An optional GUI has been provided by [jesuscf](https://github.com/jesuscfv). It allows interactive selection of the input Gerber file, parameters, and cutting operations.

To use it, run the following command:

```
python3 smtcut-gui.py
```

## Tips & Tricks
You may want to have your CAM tool shrink the paste features by about 2 mils before exporting to gerber.  The craft-cutter knife, when cutting thin mylar, seems to spread out the geometry by about this amount.  I'd suggest using mylar with thickness between about 3 and 5 mils; the IPC-recommended thicknesses for fine-pitch stencils are approximately in this range.  (Typical inexpensive laser-transparency sheets happen to be just right, being somewhere between 3.5 and 4.3 mils.)  You may have to experiment with the cutting speeds and forces for best quality with your materials.

For Fedora-like systems, "yum install gerbv pstoedit tkinter".

On some Linux distributions, permissions on /dev/usb/lp0 are restricted by default.  To fix this, add yourself to the lp group and then log out and log back in:

```
sudo usermod -a --group lp your_userid
```

## Additional Information
These pages have hints on usage (e.g. on Windows), materials, performance, calibration, etc:

- [pmonta.com](http://pmonta.com/smt-stencil-cutting.html)
- [dangerousprototypes.com](http://dangerousprototypes.com/forum/viewtopic.php?f=68&t=5341)
- [hackaday.com](http://hackaday.com/2012/12/27/diy-smd-stencils-made-with-a-craft-cutter/)

## Credits
Thanks to the authors of robocut and graphtecprint for protocol documentation:

- [robocut](https://github.com/Timmmm/robocut)
- [graphtecprint](https://github.com/jnweiger/graphtecprint)

Also thanks to this web page (Cathy Sexton) for inspiration:

- [idleloop.com](https://www.idleloop.com/robotics/cutter/index.php)

Her cutter seems to be quite a bit better than mine was out of the box.
With Silhouette's default software my 0.5 mm pitch pads were distorted to the point of unusability.

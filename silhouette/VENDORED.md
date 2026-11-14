# Vendored Silhouette driver

These files are copied unchanged from
[fablabnbg/inkscape-silhouette](https://github.com/fablabnbg/inkscape-silhouette):

| File | Purpose |
|------|---------|
| `Graphtec.py` | `SilhouetteCameo` driver: device detection, setup, command transport |
| `Transport.py` | USB and Bluetooth RFCOMM transports |
| `BLETransport.py` | Bluetooth LE transport (optional `bleak` dependency, imported lazily) |
| `__init__.py` | package marker |
| `silhouette-udev.rules` | Linux udev rules so a normal user can open the cutter |

Upstream commit: `e782d599b799861b8e2aaacc49bb2ccc7ec78633` (2026-11-13).

Upstream license: "Distribute under GPLv2 or ask." SMTCut is GPLv2, so the
combination stays GPLv2.

The driver knows the Cameo 5 Alpha (USB vendor `0x3844`, product `0x0001`)
and the Alpha Plus (`0x3844:0x0002`), which the original `file2graphtec`
does not: it only looks for Graphtec's vendor ID `0x0b4d`.

To update, copy the same files from a newer upstream checkout and rerun
`python3 -m pytest`.

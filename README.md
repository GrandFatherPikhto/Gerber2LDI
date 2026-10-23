# Gerber2LDI

Laser direct imaging (LDI) of photoresist from Gerber files: a 405 nm laser exposes
the resist line by line, switching on and off by position.

Pipeline: Gerber geometry → per-row on/off segments → spot compensation, mirror,
polarity, calibration → machine job (G-code for GRBL-family firmware). There is no
raster stage: a raster image would only add quantisation and memory.

Status: early development. The program builds and checks a job and writes a file; the
job has **not been run on the machine yet** — everything marked so below is unproven.

## What works today

```sh
gerber2ldi info layer.gbr                     # units, size, apertures, polarity
gerber2ldi raster layer.gbr -o layer.tif      # rows + a preview TIFF for the eyes
gerber2ldi job layer.gbr --profile profiles/china_ldi_grbl.toml \
    --power <from the dose strip> -o layer.nc # the machine program
gerber2ldi preview layer.nc -o layer.png      # what the machine will draw
gerber2ldi pattern bars   --profile P --power 0.05 -o bars.nc
gerber2ldi pattern dose   --profile P --from 0.02 --to 0.16 --count 6 -o dose.nc
gerber2ldi pattern square --profile P --power 0.05 -o square.nc
gerber2ldi pattern grid   --profile P --power 0.05 \
    --width-mm 50 --height-mm 40 --line-mm 0.2 --gap-mm 0.15 -o grid.nc
```

The power in the examples is deliberately small: the first boards are exposed at a
low fraction, and the working value comes from the dose strip (step 9), never from
this page.

The pixel-bound test masks for an MSLA printer are a separate script, not a package
command: `tools/msla_tests.py` (see “MSLA printer: test masks” below).

## What this program does not do

* **It does not export Gerbers from KiCad.** Use KiCad's own command line
  (`kicad-cli pcb export gerbers`, `kicad-cli pcb export drill`); this project reads
  the files, it does not make them.
* **It does not set the work zero.** G54 belongs to the operator: jog the head to the
  table corner named by `profile.table.origin` and send `G10 L20 P1 X0 Y0` (or press
  the sender's “zero” button).
* **It does not talk to the machine.** No port, no streaming: it writes a file. Send
  it with UGS, LaserGRBL or Candle.
* **It does not calibrate itself.** The bars, the dose strip and the square are exposed
  and measured by you, with calipers and a microscope; the numbers go into the
  calibration file. The grid is not a measurement at all: it is the coating test.
* **It does not diagnose the process.** It refuses what it can prove is wrong; the
  rest is the symptom table at the end.
* **It does not make the process safe.** The screen, the goggles and the stop button
  are on your side of the table.

## Working with the machine

### 1. Gerbers from KiCad

Export copper, the board outline and the drill file:

```sh
kicad-cli pcb export gerbers board.kicad_pcb -o gerbers/
kicad-cli pcb export drill board.kicad_pcb -o gerbers/
```

Then look at what arrived — `gerber2ldi info` prints units, size, aperture count,
shape count and whether clear polarity (`%LPC*%`) is present. A size that does not
match the board is the first thing to catch.

*Not exercised in this project: the two `kicad-cli` lines are from KiCad's
documentation, not from a run here.*

### 2. Profile and calibration

`profiles/china_ldi_grbl.toml` is a starting point: copy it, then change the table
size, `machine.steps_per_mm_x/y`, `scan.row_step_um`, `scan.speed_mm_s`,
`scan.overscan_mm`, `laser.s_max`, `laser.usable_fraction`. The profile describes a
TYPE of machine and is in git.

`machine.steps_per_mm_x` and `_y` are your `$$` answer, `$100` and `$101`: how many
microsteps the controller counts per millimetre. They are mandatory, because
`scan.row_step_um` must be a WHOLE number of microsteps along Y. On `$101 = 80`
step/mm one microstep is 12.5 µm: 25 µm rows are 2 steps, and 20 µm rows are 1.6 —
such a profile is **refused** with both numbers, because the rows would land 12.5 and
25 µm apart in turn and the exposure comes out banded. Edges shorter than one
microstep along X are not refused: the job counts them and says so in one line,
because their ends snap to the nearest step.

`laser.modulation` says HOW the driver switches the diode, and it decides what a power
word means:

* `pwm` — the firmware pulses the beam; `S` is a fraction of `s_max` and
  `usable_fraction` is the hard ceiling (this is `profiles/china_ldi_grbl.toml`);
* `onoff` — the driver switches the diode at a fixed current; `S` is always `s_max`,
  `usable_fraction` does **not** apply (there is no duty cycle to cap), and the dose is
  set by the SPEED plus the driver current and an ND filter. `--power` is then optional,
  and any value other than `1` is refused with the advice to vary `--speeds`.
  `profiles/china_ldi_grbl_sld3232vf.toml` is that mode for the old table with the
  SLD3232VF diode; it has its own calibration file, because the spot is a different one.

`machine.max_rate_x_mm_s` (`$110`/60) and `machine.accel_x_mm_s2` (`$120`) are mandatory
too: a speed above the limit is refused (GRBL would cut `F` silently and the dose would
come out higher than asked), and so is a run-up shorter than `v²/(2a)`, because the first
copper would be lit while the head is still accelerating.

The measured error of your instance lives next to it, in
`profiles/<name>.calibration.toml`:

```toml
[calibration]
scale_x = 1.002      # real / commanded
scale_y = 0.998
skew_deg = 0.1       # > 0: the real Y axis leans towards +X
spot_radius_um = 150 # measured with the bars pattern
measured = 2026-10-21
```

No calibration file — a warning, and neutral values (scale 1, skew 0) are assumed. No
spot measurement — **a refusal** to compensate; `--no-spot-compensation` says out loud
that you accept a wider line than drawn.

### 3. Build the job

```sh
gerber2ldi job F_Cu.gbr --profile profiles/china_ldi_grbl.toml \
    --power 0.05 --frame Edge_Cuts.gbr -o top.nc
gerber2ldi job B_Cu.gbr --profile profiles/china_ldi_grbl.toml \
    --power 0.05 --side bottom --process etch-resist \
    --frame Edge_Cuts.gbr --place 20,30 -o bottom.nc
```

* `--frame` is the board outline, the SAME file for both sides. It is the rectangle
  the figure is mirrored about and placed by, so the two sides land on each other.
  Without it each side is placed by its own copper: good for one side, and the program
  says so — a warning names `--frame` for the second side.
* `--side bottom` mirrors the figure: the resist is exposed from the other side.
* `--process` is what the resist is for: `etch-resist` lights the COPPER (the resist
  stays where the copper is), `plating-resist` lights the GAPS. **One board is one
  process**: both sides of an etch-resist board are etched, so both jobs use the same
  value; only a board that changes process between sides would differ.
* `--power` is a fraction of the module and above `usable_fraction` it is a refusal,
  never a clamp. `0.05` above is a safe first value — take the real one from the dose
  strip.
* `--place X,Y` puts the run-up strip start at that table point, and the frame's bottom
  edge on `Y`. Use ONE `--place` for both sides. KiCad writes negative Y coordinates;
  the program places the frame on the table and checks the whole pass, run-up included,
  against the table. Without `--place` the frame goes to the origin.
* every row is exposed from one overscan BEFORE its own first lit pixel to one overscan
  past its last one, always towards +X, and a row with nothing on it is not travelled at
  all. The run-up takes up the belt slack in the same direction every time, and nothing
  moves: `--frame` and `--place` place the figure exactly as before — only the idle ends
  of the rows are gone.
* the job says what that bought, on stdout and in the `.nc` header: `passes: 1234.5 mm
  exposed run (full-width would be 2345.6 mm, -47%), >= 4 min 06 s at 50 mm/s` — the run
  the head really travels against the run the same rows would cost at full width. The
  time is a **lower bound**: rapids, accelerations and M4/M5 are not counted (the profile
  has no rapid speed to count them with).

**Two sides line up only through the frame and the blank's stop.** Expose the first
side, turn the blank over left to right against the same stop, expose the second. There
is no camera and no hole-based alignment in this program: if the blank can move between
the runs, the sides will not line up, and nothing in the file can fix that.

### 4. Check before exposing

```sh
gerber2ldi preview top.nc -o top.png
```

Look at: the top of the picture is the larger Y (a top view, as in GerbView); the
bottom side comes out mirrored; copper versus gaps follows `--process`; the proportions
match the board. A preview from the Gerber plus a profile also works
(`preview layer.gbr --profile … -o layer.png`) and shows the job before the file.

### 5. The machine, once

Two GRBL settings decide what the numbers in the file mean; both are named in its
header, so a file is never ambiguous:

* **`$32=1` — laser mode.** `M4`/`M5` switch the beam with or without it. Without
  laser mode, though, GRBL **stops at every change of `S`**: the head decelerates, the
  beam stays on while it waits, and every place where power changes comes out
  over-exposed. With `$32=1` power changes on the fly.
* **`$30` = `laser.s_max` of the profile** (1000 in the China profile). `S` is a
  fraction of THIS number: a smaller `$30` in the firmware makes `S600` mean more than
  the file intended, and a larger `$30` makes it mean less.

Check them with `$$`, then set them: `$32=1`, `$30=1000`.

*Not tested on the machine yet: the header is checked by a test, the behaviour of a
controller with a wrong `$30` is the controller's.*

### 6. Before every run

Work zero G54 at the table corner that `profile.table.origin` names (`front-left`).
Jog the head over the corner, then in the sender's console:

```
G10 L20 P1 X0 Y0
```

(or the sender's “set zero” button). The numbers in the program are work coordinates;
the “inside the table” check compares them with a frame that has to exist. Then: the
board flat on the vacuum table, focus set, first pass at low power.

### 7. Sending

A ready `.nc` file, sent by a program made for GRBL: **UGS** (Universal Gcode Sender),
**LaserGRBL** or **Candle**. What matters:

* streaming send with “character counting” (the sender keeps a few lines ahead), not
  a line-by-line send with a wait after each;
* no transformations inside the sender — no scaling, no offset, no “optimisation”, no
  reordering: it is a file of coordinates, not a picture.

*Not tested on the machine: none of the three senders has been used with this program
yet. `gerber2ldi` itself never opens the port.*

### 8. Safety

The laser is 405 nm — visible violet, and the eye focuses it on the retina.

* **Screen** of orange acrylic for 405 nm around the working area, or **goggles** with
  a stated optical density at 405 nm (OD4+). Choose by the MARKING (the range must
  include 405 nm and the OD must be stated), never by the colour of the lens.
* **Ordinary glasses do not protect**: glass passes 405 nm; “safety glasses” from a
  hardware shop say nothing about this wavelength.
* First passes at low power; a test exposure on a scrap board before a real one.
* **Stop** with the machine's button or the sender's reset (`Ctrl-X` / reset) — not by
  closing the window of the program.
* Never leave the laser on while the head is not moving.

*Not tested here: the choice of screen and goggles is the operator's; the program
neither sees nor controls them.*

The program's own guards are modest and real: the power ceiling is a refusal, and the
pass with its run-up must fit the table. Everything that can stop the beam — hard
limits, the emergency stop, the watchdog — is in the firmware and on the machine,
because a dialog on the PC is a promise nobody can keep while the motion is under way.

### 9. Calibration, step by step

1. `pattern bars --profile … --power 0.05 -o bars.nc` — expose and develop. Measure the
   WIDTH of a drawn line under a microscope, in the 200 µm group: without compensation
   a line drawn `w` wide comes out `w + 2r`, so
   `spot_radius_um = (measured − w) / 2`. Check it on the 150 µm group: both groups must
   give the same radius. That radius already includes the spread of development, which
   is exactly what the compensation has to erode away. A rough check without a
   microscope: the narrowest group whose lines AND gaps survived is about `2r` wide.
2. `pattern dose --profile … --from 0.02 --to 0.16 --count 6 -o dose.nc` — expose and
   develop; the tile that looks right gives `--power` for the boards. The mapping
   “tile, bottom to top → y, power, S” is printed and written into the header.
3. `pattern square --profile … --power 0.05 -o square.nc` — expose, then measure both
   diagonals and both sides with calipers. `d1` runs from the front-left corner to the
   back-right one, `d2` from the front-right to the back-left, and
   `theta = (d1 - d2) / (√2 · L)` with `L` measured, not nominal. The command prints
   the formulas with your current numbers in place:
   `scale_x = measured X side / 150.000 mm`, `skew_deg = theta`.
4. Write them into `profiles/<name>.calibration.toml` with `measured = YYYY-MM-DD`.
5. Check: `pattern square … --with-calibration` draws the square THROUGH the
   calibration and prints `scale_x_new = 1.002000 * (measured X side / 150.000 mm)`,
   `skew_new = 0.1000 deg + theta`. A scale is a factor, so it is multiplied; a skew is
   an angle, so it is added.

6. `pattern grid --profile … --power 0.05 --width-mm 50 --height-mm 40 --line-mm 0.2
   --gap-mm 0.15 -o grid.nc` — the COATING test, not a measurement: the rectangle is
   filled with lines, so the whole working area is exposed at once and the development is
   judged over square centimetres instead of one bar. `--direction both|x|y` picks vertical
   lines, horizontal ones or both. The lines start flush with the near edge and the
   leftover at the far edge is a gap. A line ALONG X is a band of rows, so its width and
   the gap above it are rounded to the row step (`scan.row_step_um`): the command names
   both widths you will actually get (0.21 mm on a 0.025 mm step is 0.200 or 0.225), and
   it prints how many lines went along each axis.

The square ignores the profile's scale and skew unless `--with-calibration` is given:
it measures them, and measuring a value through itself gives a residual, not a value.
All four patterns run without spot compensation unless `--with-compensation` is given,
because the bars are what the spot is measured FROM, and a grid drawn through an
unmeasured radius would hide exactly what it exists to show.

**A dose strip by SPEED.** When the dose is set by how fast the head moves instead of by
the power word — a laser that only switches on and off — the strip varies the speed and
keeps ONE power:

    gerber2ldi pattern dose --profile P --speeds 80,57,40,28,20,14,10,7,5 --power P -o dose.nc
    gerber2ldi pattern dose --profile P --speed-from 80 --speed-to 5 --count 9 --power P -o dose.nc

The tiles run bottom to top with the speed FALLING, so the dose RISES; equal neighbours are
refused, because two tiles that cannot be told apart are a wasted exposure. Powers and
speeds in one command are refused too: one knob per strip. For every tile the command
prints `S`, the speed and the RELATIVE dose (`power × profile speed / row speed`, so 1.0
anywhere on the profile's own speed), and — when `laser.power_w` has been measured — the
absolute surface dose `E = 1e5 · P[W] · power / (v[mm/s] · Δy[mm])` in mJ/cm². With
`power_w = 0` it says `absolute dose unknown`: a number nobody measured would be worse than
none.

Every speed is checked against the machine before a byte of the program exists:
`machine.max_rate_x_mm_s` (`$110`/60) and `machine.accel_x_mm_s2` (`$120`). A speed above
the limit is refused, because GRBL would cut `F` silently and the dose would come out higher
than asked; a strip shorter than `v²/(2a)` is refused too, because the first copper would be
lit while the head is still accelerating.

Write next to each tile: the driver current, the photodiode reading (if there is one), and
the development (concentration, temperature, time) plus how the layer was dried. A dose is
comparable to another dose only when those stand on the same line.

### 10. If something is wrong

| symptom | likely cause | what to do |
|---|---|---|
| edges and power changes over-exposed, “steps” along a line | no laser mode: GRBL stops at every `S` change | `$32=1` |
| power has little effect, everything too weak or too strong | `$30` ≠ `laser.s_max` | set `$30` to the profile's `s_max` |
| the wrong side came out | `--side` | `top` for the side you draw first, `bottom` for the mirrored one |
| the figure sits on the table edge or outside it | work zero not set, or no `--place` | `G10 L20 P1 X0 Y0` at `front-left`, or `--place X,Y` |
| tracks thicker than drawn, gaps closed | no compensation, or a wrong `spot_radius_um` | measure again with `bars`, set the radius |
| the two sides do not line up | no common frame (`--frame`), the blank moved between the runs, wrong sign of `skew_deg`, or a wrong scale | one `--frame` and one `--place` for both sides and the same stop for the blank; then `pattern square --with-calibration` and use the printed formulas |
| nothing is exposed at all | an old program without `M4` after `G0` | regenerate the file; check the pattern below |

A correct pass looks like this (the first lines of any `.nc`):

```
M5
G0 X0 Y0
M4 S0
G1 X5.09 S0
G1 X80.821 S600
```

## How to read the .nc header

```
; requires GRBL laser mode $32=1
; work coordinates G54: the operator sets the work zero at the table corner named by profile.table.origin (front-left)
; spot compensation: off
; calibration in force: scale_x=1.002000 scale_y=0.998000 skew=0.1000 deg
; GRBL: $32=1 (laser mode), $30=1000 (max spindle speed: the power word is a fraction of it)
```

* the first two lines say what the file assumes: laser mode, and a work zero at a
  named corner;
* `spot compensation` — whether the figure was narrowed by the spot radius;
* `calibration in force` — the scale and skew actually applied (they are NOT the file's
  values when a pattern deliberately drew without them);
* the `GRBL` line repeats both settings with the profile's `s_max`, so the file carries
  the frame its `S` words are read in;
* a pattern adds its own lines: what it draws, how to measure it, whether the
  calibration was applied, and the formulas for the calibration file.

## Coordinates and refusals

Coordinates in the program are G54 work coordinates: the “inside the table” check is
only as true as the zero the operator set. The calibration file holds the machine's
error (`scale_x`, `scale_y` are real/commanded, `skew_deg` is positive when the Y axis
leans towards +X); the program divides by the scales and shifts every row by
`−y·tg(skew)`.

`job` refuses rather than adjusts: a power above the ceiling, a figure that does not
fit the table with its run-up, a spot not measured without `--no-spot-compensation`, an
unknown table origin, a scan axis other than X. Warnings — an uncalibrated machine, a
PWM period coarser than the spot — are lines on stderr, never dialogs.

Geometry comes straight from the Gerber — there is no raster stage in the pipeline, and
the TIFF is a preview for the eyes only. Millimetres throughout, the X axis is not
discretised (the machine decides that), and a row is taken at the centre of the machine
pass: `y0 + (i + 0.5) · step_y`.

## MSLA printer: test masks

The same resist is also exposed on an MSLA printer (LCD mask + 405 nm UV). There the mask
is the whole story: the LCD lights WHOLE pixels, so a test pattern has to be drawn in
pixels and has to say what the requested width actually became. `tools/msla_tests.py`
does that; it is not part of the laser chain and does not import the package.

```sh
.venv/bin/python tools/msla_tests.py bars --screen-px 5760x3600 --pixel-um 34x34 \
    -o out/bars.png --widths-px 6,5,4,3,2,1
.venv/bin/python tools/msla_tests.py grid --screen-px 5760x3600 --pixel-um 34x34 \
    -o out/grid.png --width-mm 50 --height-mm 40 --line-px 6 --gap-px 4
```

`--widths-px 6,5,4,3,2,1` is the list to start with on this screen: six DIFFERENT steps
of 34 um (6 px = 204, 5 = 170, 4 = 136, 3 = 102, 2 = 68, 1 = 34 um). The millimetre list
0.2/0.15/0.1/0.08/0.06/0.05 would give 6, 4, 3, 2, 2, 1 — two identical groups — and the
tool announces that.

* `--screen-px` and `--pixel-um` are **mandatory**: an invented number is worse than a
  refusal. For the Anycubic Photon Mono X 6Ks the printer profile in UVtools (and
  PrusaSlicer's `Anycubic Photon Mono X 6Ks.ini`) says 195.84 × 122.4 mm over
  5760 × 3600 px, i.e. **34 µm per pixel** — that is a profile, not a verified spec sheet.
  A pixel may be not square: two sizes, one per axis.
* every width becomes `n = round(width / pitch)` pixels of its OWN axis, and the report
  prints the requested width beside the ACTUAL one: a 0.100 mm line on a 34 um pixel is
  3 px = 0.102 mm, and 0.080 mm is 2 px = 0.068 mm. That actual number is the one to
  expect on the plate.
* every figure carries the letter **F** in its top-left corner (2 mm, 3 px lines) and the
  pattern starts to its right: the F is asymmetric in both axes, so a mirrored exposure
  (the stem on the right) or an upside-down one (the arms at the bottom) is visible on the
  plate without a microscope. `--no-marker` turns it off.
* a width below half a pixel is a refusal; two widths that land on the same `n` are
  announced, because their groups will be identical;
* a width may be asked in millimetres or in WHOLE PIXELS (`--widths-px`, `--line-px`,
  `--gap-px`); the two forms exclude each other. Pixels are exactly what will be drawn:
  `n` pixels of EACH axis, so on a non-square pixel one count is two physical widths;
* a millimetre request is drawn at the nearest whole pixel, and when that is more than
  10 % off the tool says so — «0.050 mm along Y is drawn as 34.0 um (-32 %)». The pixel
  form never carries that warning: there is no millimetre request to be off from.
* `bars` — groups of lines with the gap equal to the line width: a horizontal block (the
  Y resolution) and a vertical block (the X resolution), each square so the two axes can
  be compared;
* `grid` — a rectangle filled with lines: the first line flush with the near edge, a line
  only whole, the leftover at the far edge a gap;
* white is an exposed pixel: the LINES are white by default, `--invert` swaps that, and
  `--mirror` reflects the finished picture along X for a resist facing the screen;
* the report — screen, pixel, widths requested and actual, mirror, polarity — is also
  written into the PNG text chunk `msla_tests`, so the file remembers what it is.

Getting the PNG to the printer (Claude's route, Денис has not verified it yet): open any
one-layer file of that printer in **UVtools** (one made by the slicer will do), replace
the layer with the imported PNG, set the exposure time and save. The mask fixes the
geometry; the exposure time is the dose.

## Known library behaviour we work around

gerbonara's `Rectangle.to_arc_poly()` returns the bounding box of a rotated rectangle,
so rectangles are built from their rotation; pygerber ignores the rotation of macro
primitive 5, so rotated polygon pads are measured against analytic geometry, not
against it.

## Install (development)

```sh
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Tests

```sh
.venv/bin/python -m pytest -q -n auto
```

Russian version: [README_ru.md](README_ru.md).

# Gerber2LDI

Laser direct imaging (LDI) of photoresist from Gerber files: a 405 nm laser
exposes the resist line by line, switching on and off by position.

Pipeline: Gerber geometry → per-row on/off segments → spot compensation, mirror,
polarity, alignment → machine job (G-code for GRBL-family firmware first). There
is no raster stage: a raster image would only add quantisation and memory.

Status: early development, nothing usable yet.

## What works today

```sh
gerber2ldi info layer.gbr                    # units, size, apertures, polarity
gerber2ldi raster layer.gbr -o layer.tif    # machine rows + a preview TIFF
gerber2ldi job layer.gbr --profile profiles/china_ldi_grbl.toml \
    --power 0.8 -o layer.nc                 # the machine program
gerber2ldi preview layer.nc -o layer.png    # what the machine will draw
gerber2ldi pattern bars --profile P --power 0.6 -o bars.nc      # spot and resolution
gerber2ldi pattern dose --profile P --from 0.02 --to 0.16 --count 6 -o dose.nc
gerber2ldi pattern square --profile P --power 0.6 -o square.nc   # skew, 150 mm
```

Coordinates in the program are G54 work coordinates: before the run the operator
sets the work zero at the same table corner that `profile.table.origin` names
(`front-left`), otherwise the "inside the table" check compares numbers to a frame
nobody established. The calibration file holds the machine's error (`scale_x`,
`scale_y` are real/commanded, `skew_deg` is positive when the Y axis leans towards
+X); the program divides by the scales and shifts the rows by `−y·tg(skew)`.

`job` refuses rather than adjusts: a power fraction above the profile's
`usable_fraction` ceiling, a figure that does not fit the table together with the
run-up strip, or a mask without a measured spot radius (use
`--no-spot-compensation` to say so out loud). Warnings — an uncalibrated machine,
a PWM period coarser than the spot — are lines on stderr, never dialogs.

## Calibration patterns

`bars` draws five lines per group with a gap equal to the line width, in the order
200, 150, 100, 80, 60, 50 µm — first horizontal (Y resolution: the spot plus the row
step), then vertical (X resolution: the spot edges and the PWM dashes). `dose` stacks
tiles in Y, bottom to top with rising power; the levels are either an explicit
`--powers` list or a geometric series (`--from/--to/--count`, because dose acts
multiplicatively), and the mapping "tile → y, power, S" is printed and written into
the `.nc` header. A level above `usable_fraction` is a refusal, not a clamp.

`square` draws an L×L contour. Measure both diagonals AND both sides with calipers:
`d1` from the front-left corner to the back-right one, `d2` from the front-right to
the back-left, then `theta = (d1 - d2) / (√2 · L)` with L MEASURED, not nominal.
Without `--with-calibration` the square ignores the profile's scale and skew, so the
measurement is absolute: `scale_x = measured X side / nominal side`, `skew_deg = theta`.
With the flag the square is drawn through them and the command prints the update with
the machine's numbers already substituted:

```
scale_x_new = 1.002000 * (measured X side / 150.000 mm)
skew_new    = 0.1000 deg + theta
```

A scale is a FACTOR, so it is multiplied; a skew is an ANGLE, so it is added — the
same lines go into the `.nc` header, so the copy on stdout and the copy on the
machine cannot drift.

All three run without spot compensation unless `--with-compensation` is given: the
bars are what the spot is measured FROM, so demanding the measurement first would be
a closed circle. A pattern is ordinary geometry (`Shape` polygons) and goes through
the same `job` as a Gerber layer — there is no second path — which is how a strip of
rows carries its own power; per-row power is refused together with compensation,
because compensation adds and removes rows.

Geometry comes straight from the Gerber — there is no raster stage in the
pipeline, and the TIFF is a preview for the eyes only. Millimetres throughout,
the X axis is not discretised (the machine decides that), and a row is taken at
the centre of the machine pass: `y0 + (i + 0.5) · step_y`.

Known library behaviour we work around: gerbonara's `Rectangle.to_arc_poly()`
returns the bounding box of a rotated rectangle, so rectangles are built from
their rotation; pygerber ignores the rotation of macro primitive 5, so rotated
polygon pads are measured against analytic geometry, not against it.

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

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
```

`job` refuses rather than adjusts: a power fraction above the profile's
`usable_fraction` ceiling, a figure that does not fit the table together with the
run-up strip, or a mask without a measured spot radius (use
`--no-spot-compensation` to say so out loud). Warnings — an uncalibrated machine,
a PWM period coarser than the spot — are lines on stderr, never dialogs.

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

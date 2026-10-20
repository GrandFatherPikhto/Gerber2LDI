# Gerber2LDI

Laser direct imaging (LDI) of photoresist from Gerber files: a 405 nm laser
exposes the resist line by line, switching on and off by position.

Pipeline: Gerber layer → raster → per-row on/off segments → spot compensation,
mirror, polarity, alignment → machine job (G-code for GRBL-family firmware
first).

Status: early development, nothing usable yet.

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

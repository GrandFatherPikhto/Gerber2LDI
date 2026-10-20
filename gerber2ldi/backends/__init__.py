"""Backends: the job → machine dialect (G-code for GRBL-family firmware first)."""
from .gcode import gc_number, read_segments, render_gcode, write_gcode

__all__ = ["gc_number", "read_segments", "render_gcode", "write_gcode"]

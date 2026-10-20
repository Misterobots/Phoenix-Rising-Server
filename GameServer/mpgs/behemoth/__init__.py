"""Server-side behemoth simulation (hunts): data-driven action selection, cooldowns, states and montage timelines.

Built from the 2.1.1 paks (see docs/BEHEMOTH_SPEC_ROCK_2026-10-14.md). Not yet wired into a live session.
"""
from .brain import ActionRun, Brain, Target, load_rock  # noqa: F401

"""Configuration shared by every test tree (unit, security, chaos, ...)."""
from __future__ import annotations

import os

# No test may pin its objects for the whole run: the lifespan's start-up heap
# freeze (app.utils.gc_pause_monitor) is off for tests, in every tree that can
# start the application.
os.environ.setdefault("GC_FREEZE_STARTUP_HEAP", "0")

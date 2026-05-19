"""
simulation_data.py — dead stub.

This file is never loaded during normal server operation.  interface.py
pre-registers simulation_config under this name in sys.modules before any
Realtime module is imported, so Python finds the registered entry and never
touches this file.

If you are importing this outside the server (e.g. a standalone script),
you get a re-export of simulation_config as a best-effort fallback.
"""
from simulation_config import *  # noqa: F401, F403 — fallback only

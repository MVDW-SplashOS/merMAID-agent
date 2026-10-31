"""merMAID server — a generic Python multi-agent coding agent daemon.

Architecture modeled on Astra (planner/developer/tester/reviewer/safety),
running directly on the host with full global access (no container).
Custom tools are registered at runtime over HTTP (see server.tools_registry)
and live in server.registry.
"""

from . import tools  # register built-in tools

__version__ = "0.2.0"

"""Bootstrap layer — the ONLY layer that wires the entire application together.

FastAPI imports ONLY from this package.
Nothing else should perform wiring or initialization.
"""

from searchops.bootstrap.lifespan import create_lifespan
from searchops.bootstrap.container import ApplicationContainer

__all__ = ["create_lifespan", "ApplicationContainer"]

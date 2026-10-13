"""Bootstrap layer — the ONLY layer that wires the entire application together.

FastAPI imports ONLY from this package.
Nothing else should perform wiring or initialization.
"""

from searchops.bootstrap.container import ApplicationContainer
from searchops.bootstrap.lifespan import create_lifespan

__all__ = ["ApplicationContainer", "create_lifespan"]

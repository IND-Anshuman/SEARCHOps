import importlib
import inspect
import pkgutil
import structlog
from typing import Dict, List, Set, Tuple

from searchops.search.contracts import ISearchProvider, SearchCapability

log = structlog.get_logger(__name__)

class SearchProviderRegistry:
    """Registry that dynamically discovers, registers, and manages search providers."""

    def __init__(self) -> None:
        self._providers: Dict[str, ISearchProvider] = {}
        self._priorities: Dict[str, int] = {}
        self._enabled: Dict[str, bool] = {}

    def register(self, provider: ISearchProvider, priority: int = 100, enabled: bool = True) -> None:
        """Register a search provider."""
        self._providers[provider.name] = provider
        self._priorities[provider.name] = priority
        self._enabled[provider.name] = enabled
        log.info("Registered search provider", name=provider.name, priority=priority, enabled=enabled)

    def get_provider(self, name: str) -> ISearchProvider | None:
        """Get provider by name if enabled."""
        if self._enabled.get(name, False):
            return self._providers.get(name)
        return None

    def list_providers(self) -> List[ISearchProvider]:
        """List all registered and enabled providers."""
        return [p for name, p in self._providers.items() if self._enabled.get(name, False)]

    def resolve_by_capabilities(self, required: Set[SearchCapability]) -> List[ISearchProvider]:
        """Resolve providers that satisfy all required capabilities, sorted by priority."""
        candidates: List[Tuple[int, ISearchProvider]] = []
        for name, provider in self._providers.items():
            if not self._enabled.get(name, False):
                continue
            if required.issubset(provider.capabilities):
                candidates.append((self._priorities[name], provider))
        
        # Sort by priority ascending (lower priority index = run first)
        candidates.sort(key=lambda x: x[0])
        return [c[1] for c in candidates]

    def discover_plugins(self) -> None:
        """Scan providers/ folder to dynamically load and register implementations.

        Priority is read from each provider class's ``priority`` class attribute
        (default 100). This eliminates the fragile name-string matching that was
        previously used to assign priorities.

        Provider classes should declare:
            class MyProvider(ISearchProvider):
                priority: int = 50  # Lower = tried first
        """
        import searchops.search.providers as providers_pkg
        package_name = providers_pkg.__name__

        for _, module_name, is_pkg in pkgutil.iter_modules(providers_pkg.__path__):
            if is_pkg:
                continue
            full_module_name = f"{package_name}.{module_name}"
            try:
                mod = importlib.import_module(full_module_name)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        inspect.isclass(attr)
                        and issubclass(attr, ISearchProvider)
                        and attr is not ISearchProvider
                        and not inspect.isabstract(attr)
                    ):
                        try:
                            provider_instance = attr()
                            # Read priority from class attribute; default 100
                            prio = getattr(provider_instance, "priority", 100)
                            self.register(provider_instance, priority=prio, enabled=True)
                        except Exception as e:
                            log.error(
                                "Failed to instantiate discovered search provider class",
                                name=attr_name,
                                error=str(e),
                            )
            except Exception as e:
                log.error(
                    "Failed to dynamically import search provider module",
                    module=full_module_name,
                    error=str(e),
                )


# Global singleton registry instance
registry = SearchProviderRegistry()

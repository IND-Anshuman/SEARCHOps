import importlib
import inspect
import pkgutil

import structlog

from searchops.search.contracts import DEFAULT_PRIORITY, ISearchProvider, SearchCapability

log = structlog.get_logger(__name__)


class SearchProviderRegistry:
    """Registry that dynamically discovers, registers, and manages search providers."""

    def __init__(self, auto_discover: bool = False) -> None:
        self._providers: dict[str, ISearchProvider] = {}
        self._priorities: dict[str, int] = {}
        self._enabled: dict[str, bool] = {}
        if auto_discover:
            self.discover_plugins()

    def register(
        self,
        provider: ISearchProvider,
        priority: int | None = None,
        enabled: bool = True,
    ) -> None:
        """Register a search provider.

        Args:
            provider: The provider instance to register.
            priority: Explicit ordering value. When omitted, the provider's own
                declared ``priority`` is used, so a provider can control its own
                ordering without every call site restating it.
            enabled: Whether the provider participates in routing.
        """
        resolved = priority if priority is not None else provider.priority
        self._providers[provider.name] = provider
        self._priorities[provider.name] = resolved
        self._enabled[provider.name] = enabled
        log.info("Registered search provider", name=provider.name, priority=resolved, enabled=enabled)

    def get_provider(self, name: str) -> ISearchProvider | None:
        """Get provider by name if enabled."""
        if self._enabled.get(name, False):
            return self._providers.get(name)
        return None

    def list_providers(self) -> list[ISearchProvider]:
        """List all registered and enabled providers."""
        return [p for name, p in self._providers.items() if self._enabled.get(name, False)]

    def resolve_by_capabilities(
        self,
        required: set[SearchCapability],
        *,
        prefer_key_free: bool = True,
    ) -> list[ISearchProvider]:
        """Resolve providers satisfying all required capabilities, best-first.

        Args:
            required: Capabilities the provider must support.
            prefer_key_free: When True (default), key-free providers sort ahead
                of credential-dependent ones. The platform is designed to work
                end-to-end on its internal framework (SearXNG + Playwright
                browser) with no API keys at all, so those must be tried first —
                a configured-but-broken paid provider must not be the reason a
                user gets a degraded run. Set False to use strict priority order.
        """
        candidates: list[tuple[int, int, ISearchProvider]] = []
        for name, provider in self._providers.items():
            if not self._enabled.get(name, False):
                continue
            if not required.issubset(provider.capabilities):
                continue
            tier = 0 if (prefer_key_free and provider.is_key_free) else 1
            candidates.append((tier, self._priorities[name], provider))

        # Sort by (key-free tier, priority) ascending: internal framework first,
        # then declared priority within each tier.
        candidates.sort(key=lambda x: (x[0], x[1]))
        return [c[2] for c in candidates]

    def key_free_providers(self) -> list[ISearchProvider]:
        """Enabled providers that need no external credentials."""
        return [p for name, p in self._providers.items() if self._enabled.get(name, False) and p.is_key_free]

    def credential_providers(self) -> list[ISearchProvider]:
        """Enabled providers that require an external API key."""
        return [p for name, p in self._providers.items() if self._enabled.get(name, False) and p.requires_credentials]

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
                            # Read priority from the class attribute; the base
                            # contract supplies DEFAULT_PRIORITY so an undeclared
                            # provider is explicit about it rather than silently
                            # inheriting 100 from a getattr default.
                            prio = getattr(type(provider_instance), "priority", DEFAULT_PRIORITY)
                            self.register(
                                provider_instance, priority=prio, enabled=_allowed_by_profile(provider_instance)
                            )
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


def _allowed_by_profile(provider: ISearchProvider) -> bool:
    """Key-free providers (SearXNG, the headless browser) run only with ``TOOL_PROFILE=local``.

    In the API profile they are registered but disabled: they would otherwise
    be tried before Tavily (SearXNG has the higher priority) and be the
    fallback when a keyed provider returns nothing, and the slim image has
    neither a SearXNG instance nor a browser.
    """
    from searchops.config.settings import get_settings

    return provider.requires_credentials or get_settings().scraping.tool_profile == "local"


# Global singleton registry instance. It discovers providers as this module is
# imported, so everything discovery calls must be defined above this line.
registry = SearchProviderRegistry(auto_discover=True)

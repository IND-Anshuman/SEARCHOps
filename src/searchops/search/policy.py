import structlog
from typing import Dict, Set

from searchops.search.domain.models import SearchCapability, SearchProfile
from searchops.search.contracts import SearchQuery

log = structlog.get_logger(__name__)

class SearchBudgetService:
    """Monitors search cost quotas and limits per job."""

    def __init__(self, limit_per_job_usd: float = 1.0) -> None:
        self.limit_per_job_usd = limit_per_job_usd
        self._job_spent: Dict[str, float] = {}

    def get_spent(self, job_id: str) -> float:
        return self._job_spent.get(job_id, 0.0)

    def is_within_budget(self, job_id: str, estimated_cost: float) -> bool:
        """Return True if the estimated search query cost fits within the remaining job budget."""
        current = self.get_spent(job_id)
        if current + estimated_cost > self.limit_per_job_usd:
            log.warn("Search budget exceeded for job", job_id=job_id, current_spent=current, estimated=estimated_cost, limit=self.limit_per_job_usd)
            return False
        return True

    def record_cost(self, job_id: str, cost: float) -> None:
        """Record cost incurred by a query execution."""
        if job_id:
            self._job_spent[job_id] = self.get_spent(job_id) + cost


class SearchPolicyEngine:
    """Resolves SearchProfile defaults and injects intent capability matrices."""

    @staticmethod
    def apply_profile(query: SearchQuery) -> set[SearchCapability]:
        """Maps query SearchProfile to a set of concrete capability requirements."""
        caps: Set[SearchCapability] = set(query.required_capabilities)
        
        if query.profile == SearchProfile.FAST:
            caps.add(SearchCapability.SEMANTIC)
        elif query.profile == SearchProfile.DEEP:
            caps.update({
                SearchCapability.SEMANTIC,
                SearchCapability.KEYWORD,
                SearchCapability.METADATA
            })
        elif query.profile == SearchProfile.ACADEMIC:
            caps.update({
                SearchCapability.KEYWORD,
                SearchCapability.METADATA,
                SearchCapability.MARKDOWN
            })
        elif query.profile == SearchProfile.NEWS:
            caps.update({
                SearchCapability.NEWS,
                SearchCapability.FRESHNESS
            })
        elif query.profile == SearchProfile.PREMIUM:
            # Full premium capability set — routes to brightdata_serp first
            caps.update({
                SearchCapability.SEMANTIC,
                SearchCapability.KEYWORD,
                SearchCapability.METADATA,
                SearchCapability.FRESHNESS,
                SearchCapability.LOCALIZATION,
                SearchCapability.SERP_FEATURES,
                SearchCapability.ANTI_BOT,
            })
            
        return caps


# Global budget service singleton
budget_service = SearchBudgetService()
policy_engine = SearchPolicyEngine()

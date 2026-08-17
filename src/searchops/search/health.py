import time
from enum import Enum
import structlog
from typing import Dict

log = structlog.get_logger(__name__)

class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker:
    """Sliding-window circuit breaker state machine per search provider."""

    def __init__(self, failure_threshold: int = 3, recovery_timeout_sec: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout_sec = recovery_timeout_sec
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_state_change = time.time()

    def can_execute(self) -> bool:
        """Returns True if the circuit allows execution."""
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            # Shift to HALF_OPEN trial if cooldown expired
            if time.time() - self.last_state_change > self.recovery_timeout_sec:
                self._transition_to(CircuitState.HALF_OPEN)
                return True
            return False
        return True  # HALF_OPEN allows a trial query

    def record_success(self) -> None:
        """Record a successful execution, resetting failures and closing circuit."""
        self.failure_count = 0
        if self.state != CircuitState.CLOSED:
            self._transition_to(CircuitState.CLOSED)

    def record_failure(self) -> None:
        """Record a execution failure. Tripping the circuit if threshold crossed."""
        self.failure_count += 1
        if self.state == CircuitState.CLOSED and self.failure_count >= self.failure_threshold:
            self._transition_to(CircuitState.OPEN)
        elif self.state == CircuitState.HALF_OPEN:
            self._transition_to(CircuitState.OPEN)

    def _transition_to(self, new_state: CircuitState) -> None:
        log.warn("Circuit Breaker state transition", old_state=self.state, new_state=new_state, failures=self.failure_count)
        self.state = new_state
        self.last_state_change = time.time()


class SearchHealthMonitor:
    """Tracks latencies, error counts, and availability rate of providers."""

    def __init__(self) -> None:
        self._circuit_breakers: Dict[str, CircuitBreaker] = {}
        self._latencies: Dict[str, list[float]] = {}
        self._success_counts: Dict[str, int] = {}
        self._failure_counts: Dict[str, int] = {}

    def get_breaker(self, provider_name: str) -> CircuitBreaker:
        """Get or initialize CircuitBreaker for a provider."""
        if provider_name not in self._circuit_breakers:
            self._circuit_breakers[provider_name] = CircuitBreaker()
        return self._circuit_breakers[provider_name]

    def record_query(self, provider_name: str, latency_ms: float, success: bool) -> None:
        """Record the performance of an executed query."""
        breaker = self.get_breaker(provider_name)
        
        # Append latency
        if provider_name not in self._latencies:
            self._latencies[provider_name] = []
        self._latencies[provider_name].append(latency_ms)
        # Keep last 50 queries latency metrics
        if len(self._latencies[provider_name]) > 50:
            self._latencies[provider_name].pop(0)

        if success:
            self._success_counts[provider_name] = self._success_counts.get(provider_name, 0) + 1
            breaker.record_success()
        else:
            self._failure_counts[provider_name] = self._failure_counts.get(provider_name, 0) + 1
            breaker.record_failure()

    def get_average_latency(self, provider_name: str) -> float:
        """Get average latency of the last queries in ms."""
        l_list = self._latencies.get(provider_name, [])
        if not l_list:
            return 0.0
        return sum(l_list) / len(l_list)

    def get_success_rate(self, provider_name: str) -> float:
        """Get availability / success rate as ratio (0.0 to 1.0)."""
        successes = self._success_counts.get(provider_name, 0)
        failures = self._failure_counts.get(provider_name, 0)
        total = successes + failures
        if total == 0:
            return 1.0
        return successes / total


# Global singleton health monitor instance
health_monitor = SearchHealthMonitor()

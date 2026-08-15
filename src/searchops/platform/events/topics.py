"""
Event topic definitions.

All event topics are defined here as string constants to prevent typos
and enable IDE auto-complete across the codebase.
"""
from __future__ import annotations

import enum


class EventTopic(enum.StrEnum):
    """Platform event topics (Redis Stream names / Kafka topics)."""
    
    # Research domain
    RESEARCH_STARTED = "searchops.research.started"
    RESEARCH_COMPLETED = "searchops.research.completed"
    RESEARCH_FAILED = "searchops.research.failed"
    RESEARCH_CANCELLED = "searchops.research.cancelled"
    RESEARCH_PROGRESS = "searchops.research.progress"
    
    # Scraping
    SCRAPING_STARTED = "searchops.scraping.started"
    SCRAPING_COMPLETED = "searchops.scraping.completed"
    SCRAPING_FAILED = "searchops.scraping.failed"
    
    # Knowledge graph
    ENTITY_EXTRACTED = "searchops.kg.entity_extracted"
    RELATION_EXTRACTED = "searchops.kg.relation_extracted"
    GRAPH_UPDATED = "searchops.kg.graph_updated"
    CONTRADICTION_DETECTED = "searchops.kg.contradiction_detected"
    
    # Agents
    AGENT_TASK_STARTED = "searchops.agent.task_started"
    AGENT_TASK_COMPLETED = "searchops.agent.task_completed"
    AGENT_TASK_FAILED = "searchops.agent.task_failed"
    AGENT_TASK_CANCELLED = "searchops.agent.task_cancelled"
    AGENT_HEARTBEAT = "searchops.agent.heartbeat"
    AGENT_REGISTERED = "searchops.agent.registered"
    AGENT_DEREGISTERED = "searchops.agent.deregistered"
    
    # Reports
    REPORT_GENERATION_STARTED = "searchops.report.generation_started"
    REPORT_GENERATED = "searchops.report.generated"
    REPORT_FAILED = "searchops.report.failed"
    
    # Monitoring
    HEALTH_CHECK_FAILED = "searchops.monitoring.health_check_failed"
    BUDGET_THRESHOLD_REACHED = "searchops.monitoring.budget_threshold"
    COST_LIMIT_EXCEEDED = "searchops.monitoring.cost_limit_exceeded"
    
    # Security
    PROMPT_INJECTION_DETECTED = "searchops.security.prompt_injection"
    AUTH_FAILURE = "searchops.security.auth_failure"
    RATE_LIMIT_EXCEEDED = "searchops.security.rate_limit"
    
    # System
    SYSTEM_STARTUP = "searchops.system.startup"
    SYSTEM_SHUTDOWN = "searchops.system.shutdown"
    PLUGIN_REGISTERED = "searchops.system.plugin_registered"

# Phase 1: Web Scraping Infrastructure Enhancement

## Overview
Implement production-grade browser pooling, content pruning, and rate limiting for SEARCHOps scraping pipeline.

## Requirements

### 1. Browser Pool Manager
- **Requirement**: Reuse Playwright browser contexts across requests to eliminate 300-1200ms cold start latency
- **Target**: <50ms latency per request via context reuse
- **Scale**: Support 10-50 concurrent contexts per browser instance
- **Constraints**: Memory-safe with automatic cleanup on context leaks

### 2. Pre-warmed Browser Instances
- **Requirement**: Maintain active browser instances in background to eliminate cold starts
- **Target**: Zero browser launch time on request acquisition
- **Constraints**: Auto-recycle browsers after N requests to prevent memory leaks

### 3. HTML-to-Markdown Content Pruning
- **Requirement**: Convert raw HTML to clean, token-optimized Markdown before LLM processing
- **Target**: 67% token reduction while preserving semantic content
- **Constraints**: Handle tables, code blocks, headers, lists - strip ads/nav/footer

### 4. Adaptive Rate Limiting
- **Requirement**: Per-domain rate limiting with dynamic backoff on 429/403 responses
- **Target**: Prevent IP bans while maximizing throughput
- **Constraints**: Sliding window algorithm, jittered exponential backoff

### 5. Network Interception (Bonus)
- **Requirement**: Intercept XHR/Fetch/GraphQL responses before DOM rendering
- **Target**: Extract structured JSON data from dynamic SPAs
- **Constraints**: Optional - fallback to DOM extraction if interception fails

## Implementation Tasks

- [x] 1.1 Create BrowserPool class with context acquisition/release
- [x] 1.2 Implement pre-warmed browser lifecycle management
- [x] 1.3 Add /dev/shm configuration for shared memory
- [x] 1.4 Implement browser auto-recycle after N requests
- [x] 1.5 Create ContentPruner with HTML-to-Markdown conversion
- [x] 1.6 Add table preservation in Markdown conversion
- [x] 1.7 Implement DomainRateLimiter with sliding window
- [x] 1.8 Add dynamic backoff on HTTP 429/403
- [x] 1.9 Update ScrapingPipeline to use new components
- [x] 1.10 Add network interception capability
- [x] 1.11 Write unit tests for all new components

## Acceptance Criteria
- [x] Browser context reuse achieves <100ms average latency
- [x] Content pruning reduces token count by >50%
- [x] Rate limiter correctly blocks when domain limit exceeded
- [x] All existing tests pass
- [x] No memory leaks after 1000 requests
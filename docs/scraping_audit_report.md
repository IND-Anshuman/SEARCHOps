# SEARCHOps Web Scraping Audit Report

> **Status: revised 2026-10-08.** This document previously described the scraping
> layer as "functional but basic" with **42 missing components**. That was wrong.
> The audit was written from the plan and research notes, not from the code, and
> nearly every "missing" component it listed is in fact implemented.
>
> This revision states only what is in the tree. Performance claims are labelled
> **MEASURED** (observed in a live run of this repo) or **NOT MEASURED**
> (asserted by a docstring, comment, or vendor page and never verified here).

---

## Executive Summary

The scraping layer is substantially more built out than the previous version of
this report claimed. It is an **8-tier, cost-ordered escalation pipeline**
(~6,400 LOC across 24 modules) with browser pooling, JA4 TLS impersonation,
circuit breakers, a spend guard, per-domain rate limiting, LLM-assisted content
pruning, and PDF/vision/table/schema extraction.

**MEASURED** in a live research run (shallow depth, 2 sources): all 11 graph
nodes completed, 18 sources retrieved, 4,065-character report produced, job
status `completed`.

The real problems are **wiring and operability**, not absence of features:

1. `MinHashDeduplicator` was fully implemented but never imported by the
   pipeline — dead code. **Fixed 2026-10-09**: a persistent (Redis-backed)
   variant now exists and passes recall/precision assertions; it is not yet the
   pipeline default (see G1).
2. `NetworkInterceptor` captured no JSON bodies at all until 2026-10-08; it
   detected `application/json` and then executed `pass`. **Fixed.**
3. Extraction nodes silently returned `[]` on malformed LLM output until
   2026-10-09, discarding whole documents with no record. **Fixed** by the
   repair ladder; a live `recovery_rate` figure is still **NOT MEASURED**.
4. Several latency claims in code comments are **NOT MEASURED**.
5. Search-provider credentials are a runtime dependency: with no valid
   `brightdata_serp` zone and no reachable `searxng`, the pipeline silently
   degrades to the Playwright/DuckDuckGo fallback. **MEASURED** — this exact
   degradation happened in the live run above. Still open (G4).

---

## Part 1: Current State (verified from the tree)

### 1.1 The tier chain

Declared in `src/searchops/scraping/tier.py:191` (`TIER_DEFINITIONS`), ordered
cheapest/lightest → most expensive:

| Prio | Tier | Cost/page | Timeout | Gate | Notes |
|-----:|------|----------:|--------:|------|-------|
| 0 | `stealth_http` | $0 | 15s | always on | curl_cffi JA4 impersonation |
| 1 | `proxy_router` | $0 | 20s | if a proxy is configured | |
| 2 | `crawl4ai` | $0 | 30s | `crawl4ai_enabled` | |
| 3 | `playwright` | $0 | 30s | `playwright_enabled` | pooled browser |
| 4 | `bd_unlocker` | ~$0.001 | 30s | `brightdata_unlocker_enabled` | residential proxy |
| 5 | `firecrawl` | $0 | 60s | `firecrawl_enabled` | |
| 6 | `bd_browser` | ~$0.01 | 60s | `brightdata_browser_enabled` | **premium** — fires only on `FailureClass.ACCESS`, not 404 |
| 7 | `basic_http` | $0 | — | always on | last resort |

The premium tier's access-only trigger is the single most valuable piece of cost
control in the system: it stops a 404 from escalating into a paid Cloud Browser
session.

### 1.2 Component inventory

| Component | File | State |
|---|---|---|
| Cost-ordered tier pipeline | `pipeline.py` | ✅ implemented, 8 tiers |
| Stealth HTTP (JA4) | `stealth.py` | ✅ curl_cffi, Chrome TLS + header impersonation |
| Browser pool | `browser_pool.py` | ✅ pre-warmed contexts, health-checked, recycles at N requests |
| Bright Data CDP pool | `bd_browser_pool.py` | ✅ bounded semaphore, per-tier recycle, health probe |
| Network interception | `pipeline.py` (`NetworkInterceptor`) | ✅ JSON/GraphQL capture (**fixed 2026-10-08**), capped at 20 bodies |
| Per-tier circuit breakers | `bd_circuit_breaker.py` | ✅ async CLOSED/OPEN/HALF_OPEN, 100% covered |
| Spend guard | `bd_spend_guard.py` | ✅ present |
| Per-domain rate limiter | `rate_limiter.py` | ✅ present |
| Content pruning → Markdown | `content_pruner.py` | ✅ selectolax renderer (**fixed 2026-10-08**) |
| Near-dedup (MinHash+LSH) | `deduplicator.py` | ⚠️ implemented, **NOT WIRED** |
| Content-hash cache | `cache.py` | ✅ Redis + SHA256/ETag |
| PDF extraction | `document_engine.py` | ✅ PyMuPDF + Docling |
| Vision extraction | `vision_extractor.py` | ✅ present |
| Table/schema extraction | `schema_extractor.py` | ✅ present |
| Crawl4AI engine | `crawl4ai_engine.py` | ✅ present |
| Firecrawl | `firecrawl.py` | ✅ present |
| Dead-letter queue | `dlq.py` | ✅ present |

### 1.3 What the previous report got wrong

| Previous claim | Reality |
|---|---|
| "Playwright launches a new browser per request — no pooling" | ❌ False. `browser_pool.py` pre-warms and reuses contexts. |
| "No network interception (XHR, Fetch, GraphQL)" | ⚠️ It existed but captured nothing (`pass`). Now functional. |
| "No browser context pooling" | ❌ False. `BrowserPool` + `BDCDPPool`. |
| "No content pruning/cleaning for LLM context" | ❌ False. `content_pruner.py`, 81% covered. |
| "No comprehensive deduplication" | ⚠️ Implemented (`deduplicator.py`) but never wired in. |
| "No stealth/anti-fingerprinting" | ❌ False. `stealth.py` impersonates Chrome JA4 + headers. |
| "42 critical missing components" | ❌ Not supported by the tree. |

---

## Part 2: Real gaps

### G1 — Near-deduplication was dead code (HIGH) — **FIXED 2026-10-09**

`MinHashDeduplicator` (`deduplicator.py`) implemented 128-permutation MinHash
signatures with LSH at a 0.85 Jaccard threshold and had unit tests, but was
**never imported by `pipeline.py` or any other module** (verified by grep), and
was in-memory only, so every process restart lost the index.

**Original impact:** a long crawl re-processed near-duplicate pages, paying for
extraction and LLM calls on content already seen.

**What was done:**
- Added `PersistentMinHashDeduplicator` + `build_persistent_deduplicator` with
  a datasketch `storage_config` so the LSH bands live in Redis and survive
  restarts. A `storage=` injection point exists so tests never open a live
  connection.
- Detection semantics are unchanged from the in-memory class; only the backend
  differs. Tests route datasketch's storage factory at a shared backend to
  reproduce a restart.
- Measured on a mixed corpus of 10 distinct docs + 5 near-duplicate variants +
  5 unrelated docs: **recall ≥ 0.85 and precision ≥ 0.95 asserted and passing**
  (18 tests).

**Remaining:** the persistent variant is tested and importable but is **not yet
the default** — `pipeline.py` does not construct one. Wiring it as the default
(and selecting a Redis-backed storage config at startup) is still open.

### G2 — No URL frontier seen-set (MEDIUM) — **FIXED 2026-10-09**

There was no URL frontier set. Dedup was content-based only, so the same URL
was still fetched and hashed before its content could be compared — a failed or
uncached URL was re-fetched and re-paid-for on every retry.

**What was done:** `url_seen.py` provides `URLSeenSet` with:
- Per-URL `SETNX` keys for **exact** de-duplication (zero false positives).
- Bloom-sizing helpers (`optimal_bit_array_size`, `optimal_hash_count`) for
  deployments that do have RedisBloom — ~114 MB at 100M URLs, 1% FP.
- URL normalization (lowercase, strip default ports, drop fragments) so trivial
  variants collapse to one entry.
- `force=True` to persist a frontier entry on the success path.
- Graceful degradation: an unreachable Redis fails open (URLs treated as
  unseen) with a `degraded_calls` counter, so the crawl is never blocked.

**Wired into `pipeline.py`** as step 0b, ahead of every network tier: a
repeat URL returns HTTP 208 with `skipped=already_seen` and the current
`seen_skip_rate` in metadata.

**Why SETNX, not a Bloom filter:** this server's Redis returns
`unknown command` for `BF.ADD` — RedisBloom is not installed. Per-URL SETNX
keys give exact (never false-positive) behaviour; the Bloom helpers are kept
for deployments that have the module.

**NOT MEASURED:** real `seen_skip_rate` on a live crawl. The counter is emitted
in every skip response, so it is directly observable once a crawl runs.

### G3 — Latency claims are unverified (MEDIUM)

Comments claim pool warm-up takes cold start "300-1200ms → <50ms". Nothing in
the repo measures this.

**MEASURED** in the live run: `report_writer` 34,505ms, `state_pruner`
29,198ms, `search` 24,644ms — but these are cumulative across loop iterations,
not per-node figures, so they cannot be compared to the claimed cold-start
numbers.

**Fix:** add a benchmark that measures warm vs. cold acquisition and publish
real numbers, or delete the claims.

### G4 — Search credentials are a silent single point of failure (HIGH)

In the live run, `brightdata_serp` returned `422 Unknown zone` and `searxng`
could not connect. The cascade still produced a report via the
Playwright/DuckDuckGo fallback, but the operator had no signal that the
configured provider was broken — only a healthy-looking completed job.

**Fix:** surface per-provider health in the job payload; fail loudly when a
configured provider is misconfigured rather than degrading silently.

### G5 — `extract_knowledge` produced 0 entities (MEDIUM) — **partially addressed**

The node is correctly wired and executes, but the live run reported
`entities: 0` because upstream scraping returned no usable content for that
query. The wiring is proven; end-to-end entity yield is **NOT MEASURED** on a
query with healthy upstream providers.

**What was done (2026-10-09):** the self-healing extraction loop
(`llm/json_repair.py` + `llm/repair_memory.py`) removes one remaining way this
gap could hide a real failure. Both extraction nodes used to `except: return []`
on malformed LLM output, so a single bad character silently discarded a whole
document with no record. Outcomes are now counted and tier-tagged, and partial
evidence is salvaged rather than dropped.

Two related bugs were fixed as a direct result:
- **Relations with undeclared endpoints were silently dropped.** `KGRelation`
  required both `source` and `target` to exist in the entity map; the model
  routinely names an endpoint it never declares (`USES: Python -> CPython` with
  only `Python` in `entities`). A marked placeholder entity is now created so
  the relation survives.
- **`"citations": null` destroyed a whole document.** The schema is now
  null-tolerant; a citation-less claim is still evidence.

**Still NOT MEASURED:** a live run with working search credentials. The
`recovery_rate` / `unrecovered` counters in `RepairMemory.snapshot()` will give
a real figure once one is run.

---

## Part 3: Fixes applied in this revision cycle

| Date | Fix | Evidence |
|---|---|---|
| 2026-10-09 | **Self-healing extraction loop.** `extract_claims.py` and `extractor.py` both silently returned `[]` on malformed LLM JSON. Added a 5-tier deterministic repair ladder (`json_repair.py`: exact → fence_strip → balanced_slice → structural_repair → span_salvage) that recovers truncated tails, prose wrappers, trailing commas, smart quotes and Python literals with no extra LLM calls. | 44 tests |
| 2026-10-09 | **Per-domain repair memory** (`repair_memory.py`): bounded LRU (10K domains, verified with a 5000-host simulation), thread-safe under 8 concurrent writers, emitting `recovery_rate` / `avg_tiers_tried` / `unrecovered`. | 24 tests |
| 2026-10-09 | **Silent relation loss:** a relation naming an undeclared entity endpoint was dropped entirely. Now materializes a marked placeholder (`entity_type="Concept"`, `description=""`). | 4 regression tests |
| 2026-10-09 | **`"citations": null`** rejected the whole claim payload. Schema is null-tolerant; citation-less claims are kept. | included above |
| 2026-10-09 | **URL frontier seen-set** (`url_seen.py`) wired into `pipeline.py` ahead of all tiers; per-URL `SETNX` exact dedup, URL normalization, fail-open on Redis loss. | 22 tests |
| 2026-10-09 | **Persistent MinHash dedup** (`PersistentMinHashDeduplicator`) — index survives restart via datasketch Redis storage. | 18 tests |
| 2026-10-09 | **Learned per-domain tier routing** (`tier_bandit.py`): epsilon-greedy UCB1 replaces the static tier sort, scoring `p_success / (1 + avg_cost × 1000)` + exploration bonus. Never drops a tier, degrades to static priority on error. Exposes `saved_cost_usd` / `bandit_reorders`. | 17 tests |
| 2026-10-08 | `content_pruner.py` Markdown renderer was dead — `n.children` does not exist on selectolax nodes (API is `.iter()`), so the pruner threw on nearly every page and silently fell back to tag-stripping, losing tables, links, headings, code fences and blockquotes. | 43% → 81% coverage; 36 tests |
| 2026-10-08 | `browser_pool.py` `new_page()` never reused a page: `if self._page is None or not self._page.is_closed():` recreated on every call. | 30% → 80% coverage; 38 tests |
| 2026-10-08 | `bd_browser_pool.py` `shutdown()` raised `AttributeError` on a never-initialized pool — `_maintenance_task` was assigned but never declared in `__init__`. | 17% → 86% coverage; 40 tests |
| 2026-10-08 | `NetworkInterceptor._on_response` detected JSON then executed `pass`; `json_responses` did not exist. `get_structured_data()` filtered on a `headers` key that was never stored, so it always returned empty. | 15 new tests |
| 2026-10-08 | Topology drift: the API node map used ids the graph no longer registered, so 4 of 8 nodes were silently dropped from every job payload. Live job went `failed` → `completed`. | 7 contract tests + build-time guard |

**Quality gate state after all of the above: 798 tests passing, 76.79% coverage
(gate 75%), mypy clean across 210 files, ruff clean.**

---

## Part 4: Recommended next steps (priority order)

1. **Make `PersistentMinHashDeduplicator` the default** in the pipeline and
   select a Redis-backed storage config at startup (G1 remainder). It is tested
   but not yet constructed anywhere.
2. **Surface per-provider health** in the job payload so a misconfigured
   credential is visible instead of silently degrading (G4).
3. **Benchmark warm vs. cold browser acquisition** and replace or delete the
   unverified latency claims (G3).
4. **Re-run against working credentials** to measure real entity yield and real
   `recovery_rate` / `seen_skip_rate` — the counters now exist, the numbers do
   not (G5).
5. **Benchmark the tier bandit** against the static order on a real crawl to
   produce an actual "% cost saved" figure. The mechanism is tested; the saving
   is estimated, not measured.

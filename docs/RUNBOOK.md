# SEARCHOps Operations Runbook

> **Purpose:** when something breaks, this tells you *where the failure shows up*
> and *what to do about it*. Every check below was written against code that
> exists in this repo — commands and endpoints are real.
>
> **Verified:** 2026-10-10. Commands were executed against the repo; the health
> behaviour described in §2 was confirmed by driving the real ASGI app with a
> simulated dead dependency.

---

## 1. First 60 seconds

Run these in order. Each one narrows the problem before you read any logs.

```bash
make docker-ps          # are the four data services actually up?
curl -s localhost:8000/health | python -m json.tool   # per-component health
curl -s -o /dev/null -w "%{http_code}\n" localhost:8000/health/ready   # 200 or 503
make check              # lint + format + types, no side effects
```

| Result | Meaning | Go to |
|---|---|---|
| `/health` shows a component `unhealthy` | that service is the problem | §2 |
| `/health/ready` returns 503 | do not send traffic; a required dep is down | §2 |
| All components healthy but research fails | the problem is upstream of storage | §5 |
| Tests fail | `make check` first, then §7 | §7 |

---

## 2. Component health — where failures surface

`GET /health` reports each component independently with its own latency. This is
the single most useful diagnostic in the platform: it names the failing
dependency instead of leaving you to infer it from a broken research job.

| Component | Unhealthy means | First action |
|---|---|---|
| `redis` | Cache, URL seen-set, and rate-limiter state are all unavailable. Scrapes still run but nothing is deduplicated or cached. | `docker compose restart redis`; check `docker logs searchops-redis` |
| `postgres` | Job state is unavailable — research jobs cannot be persisted. | `docker compose restart postgres` |
| `qdrant` | Vector search unavailable; hybrid retrieval degrades. | `docker compose restart qdrant` |
| `neo4j` | Knowledge graph unavailable; GraphRAG extraction cannot write. | `docker compose restart neo4j` |
| *(absent / `configured: false`)* | The container never built that client — usually an optional dependency. **Not a fault.** | none |

**Readiness is separate from health.** `/health/ready` returns **503** when any
*configured* component is unhealthy, so an orchestrator stops routing traffic to
a pod that cannot serve it. An unconfigured optional dependency never triggers
503.

Each probe has a 2-second timeout, so a wedged dependency delays the endpoint by
milliseconds rather than hanging it.

---

## 3. Scraping failures

The pipeline escalates through eight tiers, cheapest first. Failure handling has
several independent guards, and each surfaces in a different place.

| Symptom | Where it shows | Recovery |
|---|---|---|
| Every URL returns HTTP 208 with `skipped=already_seen` | The URL seen-set is working. The crawl genuinely has nothing new. | Expected. Reset with `URLSeenSet.reset()` for a re-crawl. |
| HTTP 429 immediately, `Rate limited` in metadata | `DomainRateLimiter` is throttling that host. | Wait it out; do not raise limits — that is how you get IP-banned. |
| Premium tier skipped, `circuit_open` in logs | `bd_circuit_breaker` tripped for that tier after repeated ACCESS failures. | It self-heals after the cooldown. Investigate *why* the site is blocking. |
| Premium tier skipped, `budget_exceeded` in logs | `BrightDataSpendGuard` hit a per-job/hour/day cap. | Working as intended. Raise the cap only if the spend is justified. |
| `All scraping tiers exhausted` | Every tier failed — usually an ACCESS block on all of them. | Check whether the site added a WAF; consider the Playwright tier. |
| `near_duplicate_of` in result metadata | MinHash dedup matched an earlier document. | Expected. Downstream may discard it. |

**Which tier ran** is in the response as `scrape_mode_used`, and the bandit's
reordering decisions are visible in `GET /api/v1/.../stats` under
`tier_bandit` (`bandit_reorders`, `saved_cost_usd`).

---

## 4. Search provider failures

Routing is **key-free-first**: SearXNG and the internal Playwright browser are
tried before any credential-dependent provider. A broken API key therefore
degrades quality — it does not break the platform.

| Symptom | Cause | Action |
|---|---|---|
| Poor result quality, everything still completes | Paid providers failing, key-free fallback succeeding. | Check `make check-env` for missing/invalid keys. |
| `Unknown zone` from BrightData SERP | The configured zone name does not exist. | Fix `BRIGHTDATA_API_KEY` / zone, or unset it to rely on the key-free path. |
| Nothing resolves at all | Both SearXNG and Playwright are unreachable. | `curl $SEARXNG_BASE_URL/search?q=test&format=json` to test SearXNG directly. |

---

## 5. Research job failures

| Symptom | Where it shows | Action |
|---|---|---|
| Job goes `failed` | Job payload `error` field. | Read it — it names the failing node. |
| Report is short but present, `report_quality` is `no_evidence` | No sources could be read. **Honest degradation, not a crash.** | Check search + scraping; the report says so on purpose. |
| `report_quality: raw_empty` or `refusal` | The LLM returned nothing usable; the report was rebuilt from evidence. | Inspect the LLM router logs. |
| `entities: 0` | `extract_knowledge` found nothing — usually empty upstream content. | Check that scraping returned content. **End-to-end entity yield is NOT MEASURED in this repo.** |
| A node is missing from the streamed UI | Topology drift — caught at graph build time by a hard guard. | It cannot happen silently; the app refuses to start. |

The Dead Letter Queue (`searchops/scraping/dlq.py`) records permanently failed
scrapes with their retry count. Inspect it before re-running a crawl, or you
will re-pay for the same failures.

---

## 6. LLM and cost failures

| Symptom | Where it shows | Action |
|---|---|---|
| Model returns empty text | Report shaper rebuilds from evidence; `report_quality` explains it. | Check provider status and quota. |
| Malformed JSON from the model | Repair ladder recovers it; counters in `RepairMemory.snapshot()`. | If `recovery_rate` is low, the model needs a better prompt or a swap. |
| Spend climbing unexpectedly | `saved_cost_usd` / `bandit_reorders` in pipeline stats. | The bandit is still exploring; check after the decay window. |

---

## 7. Quality gates

```bash
make check      # ruff + format + mypy, read-only
make test       # full suite with the coverage gate
```

**The gates are a contract, not a suggestion.** Coverage is enforced at 75%
(`--cov-fail-under=75`); mypy runs strict. Do not lower a threshold or blanket-
ignore an error code to make a gate pass — fix the code.

If a test fails, read the failing assertion before changing anything. Most
failures in this repo's history were **mock drift** (a fake that no longer
matches the real API) or a test encoding a wrong expectation, not a real bug —
but both look identical in the output, so read the code path first.

---

## 8. Restart and recovery

```bash
make docker-down && make docker-up     # restart infrastructure
make docker-nuke                       # DESTRUCTIVE: also deletes volumes
```

**Before `docker-nuke`, know what you lose:** Postgres job history, the Redis
seen-set and scrape cache, Qdrant vectors, and the Neo4j graph. A crawl run
after a nuke re-fetches and re-pays for everything.

**Configuration precedence:** settings are read from `.env` (loaded
automatically at startup) and from environment variables, which override the
file. `APP_ENV` selects the profile (`development` / `staging` / `production`
/ `testing`) and is the only environment variable the config layer reads —
`SEARCHOPS_ENV` does nothing and must not be used. Restart the process after
editing `.env` — settings are frozen at startup. API authentication is
enforced in **every** environment; it is not inferred off from `APP_ENV`.

---

## 9. Known limitations (do not mistake these for bugs)

These are gaps, documented rather than hidden:

- **End-to-end entity yield is NOT MEASURED.** `extract_knowledge` reported 0
  entities in the only live run performed. The wiring is proven; the yield is not.
- **Live recovery rate is NOT MEASURED.** The repair ladder's counters exist but
  no production traffic has populated them.
- **Latency claims in code comments are NOT MEASURED.** Browser cold-start,
  "10–20× latency reduction", and "67% token reduction" have no benchmark
  behind them in this repo.
- **Bandit cost savings are estimated, not measured.** The mechanism is tested;
  the saving figure is a projection.
- **The E2E Playwright test against the real frontend does not exist yet.** The
  topology contract tests cover the same class of bug at the unit level.

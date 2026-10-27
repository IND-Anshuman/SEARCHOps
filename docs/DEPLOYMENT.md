# Deploying SEARCHOps

One container serves the API and the built web app from a single origin. Put a
TLS-terminating reverse proxy (Caddy, nginx, a cloud load balancer) in front of it.

## 1. Clerk (sign-in)

1. In the Clerk dashboard, create a **production instance** for the app and add your domain
   (you need a domain you own, and DNS access to add the records Clerk lists).
   Development keys (`pk_test_…`) work but show a "development mode" banner and are
   rate-limited; Clerk's docs cap a development instance at 100 users.
2. Decide who may sign up. **Restricted** sign-up mode (invitation-only) is the safe default
   for an app that spends LLM and search credits: Dashboard → User & authentication →
   Restrictions → Sign-up mode.
3. Copy the **publishable** key. It is public by design. The API verifies session tokens
   against the instance's public JWKS, so no Clerk secret key is needed anywhere.

## 2. Build

```bash
docker build -t searchops \
  --build-arg VITE_CLERK_PUBLISHABLE_KEY=pk_live_... .
```

The key is compiled into the web app, so it is a build argument. The build fails if it is missing.

By default the image is **API-only** (`TOOL_PROFILE=api`): Tavily does the searching and returns
page text, so the image carries no browser and none of the free scraping packages. The free,
self-hosted stack (SearXNG, Crawl4AI, Playwright + Chromium, stealth HTTP, PDF parsing) is still
in the code; build it in with `--build-arg TOOL_PROFILE=local` (about 1.5 GB larger) and run
SearXNG with `docker compose --profile local-tools`. In the API-only image the PDF export is
unavailable (it prints through Chromium); Word and PowerPoint exports work.

## 3. Configure

Environment for the container (see `.env.example` for every setting):

| Variable | Required | Notes |
|---|---|---|
| `APP_ENV` | set by the image | `production` — enables the start-up checks below |
| `CLERK_PUBLISHABLE_KEY` | yes* | Same value as the build argument |
| `SEARCHOPS_API_KEY` | optional | For scripts/monitoring; `openssl rand -base64 32`. Needed to scrape `/metrics` |
| `ALLOWED_HOSTS` | yes | The host name users reach you on, e.g. `app.example.com` |
| `API_ALLOWED_ORIGINS` | yes | `https://app.example.com` (no `*`) |
| `REDIS_HOST` / `REDIS_PASSWORD` | yes | Job state lives in Redis |
| `TAVILY_API_KEY` | yes (API-only) | Search and page text. `TAVILY_EXTRACT_DEPTH=basic` costs 1 credit per 5 pages |
| LLM key | yes | At least one LLM provider key |
| `FORWARDED_ALLOW_IPS` | behind a proxy | The proxy's address, so client IPs and `https` are trusted |
| `TOOL_PROFILE` | set by the image | `api` (default) or `local`; must match how the image was built |
| `SEARXNG_BASE_URL` | `local` only | Your SearXNG instance; set `SEARXNG_SECRET` on it |
| `API_MAX_JOBS_PER_USER_PER_DAY` | optional | Runs one user may start per UTC day (default 20, `0` = unlimited) |
| `OTEL_ENABLED` | optional | Default `false`; set `true` only with a collector at `OTEL_EXPORTER_OTLP_ENDPOINT` |
| `CONTENT_SECURITY_POLICY` | optional | Leave empty to use the built-in policy (below) |

\* Authentication must be configured: Clerk, an API key, or both.

### What production refuses

With `APP_ENV=production` (or `staging`) the process **will not start** if:

- no authentication is configured, or `AUTH_DISABLED_EXPLICITLY=true`;
- `API_ALLOWED_ORIGINS` contains `*`.

It starts with a logged **warning** if localhost is still an allowed origin, the Clerk key is
a `pk_test_` key, `ALLOWED_HOSTS` is only localhost, or a database password is a known
development value.

### Built-in protections

- Clerk session tokens are verified (signature, issuer, expiry, origin).
- A report belongs to the user who started it; other users get a 404.
- MCP tool execution, A2A registration and `/metrics` are for API-key (service) callers only.
- Each user may have `API_MAX_CONCURRENT_JOBS_PER_USER` unfinished runs (default 2) and start
  `API_MAX_JOBS_PER_USER_PER_DAY` runs a day (default 20). Both are counted in Redis, so they
  hold across containers and restarts. Each run is stopped after `API_JOB_MAX_RUNTIME_SECONDS`
  (default 30 minutes).
- **Runs survive a redeploy.** A running job holds a short Redis lease that its process keeps
  fresh. When the process dies, the lease lapses (about 45 s) and the next API process to
  start — or any other running one — picks the job up and runs it again from the start. A job
  interrupted twice is reported as failed instead of looping.
- A run with no progress for `API_JOB_STALE_SECONDS` is reported as failed, so nothing is left
  spinning forever.
- Security headers on every response, including a Content-Security-Policy built for this app
  and its Clerk instance (read from `CLERK_PUBLISHABLE_KEY`); HSTS when served over HTTPS;
  `Host` allowlist; interactive docs hidden.
- Finished reports are archived to `/data/reports` (mount a volume there).

## 4. Run

```bash
docker run -d --name searchops -p 127.0.0.1:8000:8000 \
  --env-file .env -v searchops-data:/data \
  -e ALLOWED_HOSTS=app.example.com -e API_ALLOWED_ORIGINS=https://app.example.com \
  searchops
```

- Liveness: `GET /health/live`. Readiness and dependency detail: `GET /health`, `/health/ready`.
- `docker compose --profile api up -d` runs what a deployment needs: Redis and the API.
  SearXNG is behind `--profile local-tools` (only for `TOOL_PROFILE=local`); Postgres, Qdrant
  and Neo4j are behind `--profile full`; the pipeline does not use them yet and the API does
  not wait for them. Data services bind to `127.0.0.1` only; set
  `REDIS_PASSWORD` (and the others, if you run them) in `.env` for anything but local use.

## 5. Where to host it

What the app needs from a host: a **container that keeps running** (a research run takes
5–30 minutes in the background, after the request that started it has returned),
**WebSockets**, a **Redis** and a **persistent disk** for `/data`. The API-only image has no
browser, so 1 GB of RAM is a comfortable start; the `local` image needs about 2 GB for Chromium.

| Platform | Fits? | How |
|---|---|---|
| **Oracle Cloud Always Free VM** | **Yes — the free option** | Walkthrough in section 6: an Ampere A1 VM, your own domain, Caddy for HTTPS |
| Another VM (Hetzner, DigitalOcean Droplet, EC2, Lightsail, Azure VM) | Yes — simplest and cheapest | Same steps as section 6: `COMPOSE_PROFILES=api,proxy` plus `docker compose up -d --build` |
| Render | Yes, on a paid plan | Docker web service, Render Key Value for Redis, a persistent disk at `/data`. The free plan spins down after 15 minutes without inbound traffic, which kills runs in progress |
| Railway | Yes | Deploy from the Dockerfile, add the Redis service and a volume at `/data` |
| Fly.io | Yes | `fly launch` from the Dockerfile, a volume at `/data`, Upstash Redis; keep one machine always running |
| Google Cloud Run | With settings | CPU always allocated and min instances 1 (otherwise background runs are throttled), Memorystore or Upstash Redis; no local disk, so reports live only in Redis (7 days) unless you mount a bucket |
| Azure Container Apps / AWS ECS Fargate | Yes, more setup | Min replicas 1, managed Redis, a file share (Azure Files / EFS) at `/data` |
| DigitalOcean App Platform, Heroku | Partly | Work, but have no persistent disk: archived reports are lost on redeploy |
| Vercel, Netlify (or any serverless functions) | **No, not for the API** | Functions stop when the response ends and cannot hold WebSockets; runs would die. They can host the static web app only, pointed at an API hosted elsewhere |

Whatever the host: never let it scale the API to zero while runs are in progress (a stopped
container is resumed by the lease mechanism, but the run starts over), and give Redis the
`noeviction` policy so job state is not dropped under memory pressure.

## 6. Walkthrough: an Oracle Cloud Always Free VM with your own domain

This is the free route. Oracle's Always Free tier includes an Arm VM that is large enough for the
API-only image. What it gives you, from [Oracle's Always Free resources page](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
(check it before you rely on it; the figures have changed before):

- **Ampere A1 (Arm) compute:** 1,500 OCPU-hours and 9,000 GB-hours a month, i.e. **2 OCPU and
  12 GB RAM** in total (older posts say 4 OCPU / 24 GB; that is out of date).
- **Block storage:** 200 GB in total, including the boot volume (47 GB minimum per VM).
- **Outbound data:** 10 TB a month.
- **Home region only:** Always Free compute can only be created in the region you pick at
  sign-up, and as far as I know that choice cannot be changed later.
- **Do not exceed the A1 allowance.** If you do, Oracle disables the A1 VMs and deletes them after 30 days
  unless you upgrade the account.
- The two "AMD Micro" VMs (1 GB RAM, 1/8 OCPU each) are too small to build this image on.

Every package in the API-only image has a ready-made Arm build (checked against `uv.lock`), so
nothing has to be compiled on the VM.

1. **Account and VM.** Sign up for Oracle Cloud (a payment card is needed for identity
   verification) and pick your home region with care. Create an instance with image
   *Canonical Ubuntu 24.04*, shape *VM.Standard.A1.Flex* (2 OCPU, 12 GB is the whole allowance;
   1 OCPU and 6 GB is plenty for the API-only image), a boot volume of 50–100 GB, a public IP,
   and your SSH public key. If Oracle answers "Out of host capacity", try another availability
   domain or try again later: free Arm capacity is shared and not guaranteed.
2. **Open the ports (two firewalls).** In the OCI console, open the VM's subnet → *Security
   List* → add ingress rules for TCP **80** and **443** (source `0.0.0.0/0`), and UDP 443 if you
   want HTTP/3. Keep port 22 limited to your own IP. Ubuntu images on OCI also ship host
   firewall rules (iptables) that reject other inbound traffic. Docker-published ports normally
   still work, because Docker adds its own rules; if the site is unreachable after step 7, add
   accept rules for 80 and 443 above the final `REJECT` rule and save them with
   `sudo netfilter-persistent save`.
3. **Install Docker and git** on the VM:
   `sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 git`, then
   `sudo usermod -aG docker $USER` and log in again. (Or follow Docker's official install guide.)
4. **DNS.** At your domain's DNS provider, add an **A record** for the host name you want
   (for example `app.example.com`) pointing at the VM's public IP.
5. **Clerk.** Create a **production instance** for that domain (section 1). Clerk lists a few
   DNS records (CNAMEs) to add at the same DNS provider; set sign-up to **Restricted**; copy the
   `pk_live_...` publishable key.
6. **Configure.** On the VM: `git clone <your repository> && cd SEARCHOps && cp .env.example .env`,
   then set at least:

   ```
   COMPOSE_PROFILES=api,proxy
   APP_DOMAIN=app.example.com
   ALLOWED_HOSTS=app.example.com
   API_ALLOWED_ORIGINS=https://app.example.com
   CLERK_PUBLISHABLE_KEY=pk_live_...
   TAVILY_API_KEY=tvly-...
   REDIS_PASSWORD=<a long random string>
   # plus your LLM provider key
   ```

   `COMPOSE_PROFILES=api,proxy` makes a plain `docker compose up` start Redis, the API and Caddy.
7. **Start.** `docker compose up -d --build` (the first build on the Arm VM takes a few
   minutes). Caddy requests the certificate as soon as the DNS record resolves. Check
   `docker compose ps`, `docker compose logs api`, and open
   `https://app.example.com/health/ready`, which should answer `{"status":"ready"}`.
8. **Update later:** `git pull && docker compose up -d --build`. An unfinished run is picked up
   again after the restart (once).

**Keeping the VM.** Oracle treats an Always Free VM as idle, and may stop it, if for a 7-day
period its CPU, network and (on Arm) memory use all stay under 20% at the 95th percentile. An
app that mostly waits for visitors can meet that. For a short event this does not matter; for
anything longer, upgrading the account to Pay As You Go is the usual way to avoid the idle
check while staying inside the free limits. I have not confirmed that in Oracle's own docs, so
read the current policy first, and set a budget alert so nothing is billed by surprise.

## 7. Things to know

- A run takes minutes and executes in the API process. Run one container; it handles several
  runs at once. More containers work for job state, leases and caps (all in Redis), but
  finished reports are archived on each container's own `/data` disk: give every container the
  same volume, or stay on one.
- Run duration is dominated by the LLM model you configure. A small reasoning model can take
  several minutes per run and sometimes returns empty output; use a faster model for demos.
- The built-in Content-Security-Policy allows the app's own origin, Google Fonts and the Clerk
  hosts Clerk documents. To load anything else (analytics, say), set `CONTENT_SECURITY_POLICY`
  to a complete policy.
- Qdrant and Neo4j are optional; the research pipeline does not use them yet.

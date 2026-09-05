# URL Shortener with Sharding

🔗 **Live demo:** [url-shortener-sharded.onrender.com/docs](https://url-shortener-sharded.onrender.com/docs)
_(hosted on Render's free tier — the first request after a period of inactivity may take
30-60 seconds while the instance wakes up)_

A URL shortener (like bit.ly) built as a hands-on system design project — the interesting part
isn't the shortening itself, it's the infrastructure underneath: **database sharding via
consistent hashing**, a **Redis cache-aside layer**, and the tradeoffs that come with both.

## What it does

- `POST /shorten` — submit a long URL, get back a short code and a redirect link.
- `GET /{code}` — visiting a short link redirects to the original long URL.

## Architecture

```
Client
  │
  ▼
FastAPI app
  │
  ├── Write path (POST /shorten)
  │     └── Hash the long URL → derive a short code → route to a shard → write
  │
  └── Read path (GET /{code})
        └── Check Redis cache
              ├── Hit  → redirect immediately
              └── Miss → decode the code → route to a shard → read → cache it → redirect
```

Three independent Postgres databases (shards) sit behind a small in-app router built on
**consistent hashing**: both shards and short codes are hashed onto a shared ring, and a code
is routed to whichever shard is "next" clockwise from its position. The same router function
is used on both the write path and the read path, so a code always resolves to the shard it
was written to.

A Redis cache sits in front of the read path, since redirects happen far more often than
new URLs are created.

## Tech stack

| Layer         | Choice                                                                   |
| ------------- | ------------------------------------------------------------------------ |
| API framework | FastAPI                                                                  |
| Database      | PostgreSQL (3 shards, hosted on Supabase)                                |
| Sharding      | Consistent hashing (custom ring implementation, 100 virtual nodes/shard) |
| ORM           | SQLAlchemy                                                               |
| Cache         | Redis (hosted on Upstash)                                                |
| Hashing       | Python's `hashlib.shake_256`                                             |
| Hosting       | Render                                                                   |

## How a short code is generated

Rather than relying on a database auto-increment ID (which breaks once you have more than one
database — see [Design decisions](#design-decisions) below), the short code is derived directly
from the long URL:

1. `shake_256` hashes the long URL to a fixed-length hex digest.
2. The hex digest is parsed into an integer.
3. That integer is Base62-encoded to produce the short code (e.g. `aZ9kT2Qw`).

## How a short code is routed to a shard

Routing is handled separately from code generation, using **consistent hashing**:

1. Each shard is hashed onto several points on a ring (100 "virtual nodes" per shard, using
   `hash(f"{shard_id}-{i}")` for `i` in `0..99`) — this smooths out uneven ring coverage that a
   single point per shard would otherwise produce.
2. A short code is hashed onto the same ring.
3. The shard owning that code is whichever shard's virtual node comes next, walking clockwise
   from the code's position (wrapping back to the start of the ring if the code's position is
   past every shard's position).

Both `POST /shorten` and `GET /{code}` call the same `get_shard(code)` function, so a code
always resolves to the shard it was written to — no separate decode step is needed on the read
path, since the code itself (not a recovered integer) is hashed directly.

## Setup

### 1. Environment variables

Create a `.env` file:

```
SHARD_0_DATABASE_URL=postgresql://user:password@host:5432/postgres
SHARD_1_DATABASE_URL=postgresql://user:password@host:5432/postgres
SHARD_2_DATABASE_URL=postgresql://user:password@host:5432/postgres
REDIS_URL=rediss://default:password@endpoint.upstash.io:6379
```

- The three `DATABASE_URL`s point at three separate Postgres instances (this project uses
  three separate Supabase projects, in the same region, connected via Supabase's **session
  pooler** rather than the direct connection string — the direct connection is IPv6-only on
  Supabase's free tier and will fail to connect from most home networks).
- `REDIS_URL` points at an Upstash Redis instance.

### 2. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate     # or .venv\Scripts\activate on Windows
pip install fastapi uvicorn sqlalchemy psycopg2-binary python-dotenv redis
```

### 3. Run it

```bash
uvicorn main:app --reload
```

All three shards' tables are created automatically on startup (`Base.metadata.create_all` runs
once per shard engine).

Visit `http://127.0.0.1:8000/docs` for interactive API docs.

## Project structure

```
main.py               # FastAPI app, endpoints — wiring only
models.py              # URLMapping (shared by main.py and the migration script)
db.py                 # Engines/sessions for all shards, get_db(shard_id)
redis_client.py       # Redis connection
sharding.py           # Consistent hashing ring: add_shard(), get_shard()
encoding.py           # Base62 encode/decode, hash-based code generation
scripts/
  ring_experiment.py  # Standalone test proving the ring's reassignment %
                       # on shard add — not imported by the running app
  data_migration.py   # Moves rows to their correct shard after the ring
                       # changes (e.g. after adding a shard). Run manually:
                       # .venv/bin/python -m scripts.data_migration
```

Helper logic is split by responsibility rather than left in `main.py` — `main.py` stays
limited to the FastAPI app and endpoint functions, with encoding, sharding, and data-access
concerns each owning their own file. `scripts/` is reserved for one-off, human-run tasks (like
the ring experiment used to verify the numbers below), as distinct from modules the app
actually imports at runtime.

## Design decisions

**Why hash-based codes instead of auto-increment IDs?**
Auto-increment IDs are scoped to a single database. Once you have two shards, each generating
its own IDs independently, they'll both produce `id = 1`, `id = 2`, etc. — a guaranteed
collision the moment you try to use that as a globally unique short code. Deriving the code
from a hash of the URL sidesteps the problem: no database is involved in generating it at all,
so there's nothing to collide between shards. As a side effect, shortening the same URL twice
produces the same code — free deduplication, though not the primary reason for this choice.

**Known limitation — hash collisions.** The short code is truncated to 8 hex characters
(~4.3 billion possible values). Two _different_ long URLs could, in rare cases, hash to the
same code. This is detected (a second write attempt with a mismatched `long_url` for an
existing code returns `409 Conflict`), but it isn't resolved automatically — a production
system would need a collision-resolution strategy (e.g. re-hashing with a salt, or falling
back to a Snowflake-style generator).

**Why consistent hashing instead of plain `hash % num_shards`?**
Modulo sharding is simple, but has a well-known failure mode: changing the shard count changes
the divisor for _every_ key, so adding or removing a shard reshuffles the routing for almost
every existing key — not just new ones. **Consistent hashing** avoids this by mapping both
shards and keys onto a shared ring; adding a shard only reassigns the keys that happen to fall
in its newly-claimed arc, leaving the rest of the ring (and the relationships between existing
shards) completely undisturbed.

This was verified directly rather than just assumed: routing 1,000 sample keys through a
2-shard ring, then adding a 3rd shard and re-routing the same keys, showed **35.5%** moved
(close to the theoretical ~33% for going from 2→3 shards) — and, more importantly, **0** of
those keys moved _between_ the two original shards. Every single reassignment was a key moving
specifically to the new shard, confirming the ring doesn't disturb routing decisions it didn't
need to.

**Known limitation — consistent hashing doesn't migrate data.** The ring correctly minimizes
_how many_ keys get reassigned when a shard is added, but it has no awareness of where rows are
_physically_ stored. If shard 2 is added to a live system, `get_shard(code)` will immediately
route some existing codes to shard 2 — even though their rows are still sitting in shard 0 or
shard 1, unmoved. A production system needs a separate **migration/rebalancing step**: walking
existing rows, recomputing their shard under the new ring, and copying/moving any row whose
target shard changed, before the new shard is safe to route live traffic to. This project
does not implement that migration step — adding a shard here would require running one
manually against existing data. Both modulo sharding and consistent hashing require this kind
of migration when a shard is added; consistent hashing just shrinks the size of the migration
from touching ~100% of rows to touching roughly `1/N` of them.

**Why the collision check runs on both the cache-hit and cache-miss paths.**
An earlier version only checked for a code/URL mismatch inside the cache-miss branch — meaning
once a code was cached, a second request reusing that code with a _different_ URL would skip
the check entirely and silently succeed. The fix compares the submitted URL against whichever
source currently answers the request (the cache if present, the database otherwise), so the
check can't be bypassed by cache state. Note this doesn't change the common case: shortening
the _same_ URL twice still returns the same code with no error, since the hash is deterministic
and the "existing" URL always matches — the check only ever fires when two different URLs
genuinely collide on the same 8-character hash.
The read path (`GET /{code}`) uses classic cache-aside: check cache, fall back to the database
on a miss, then populate the cache. The write path (`POST /shorten`) also proactively caches
the new mapping immediately, so the very first visitor to a newly created link is already a
cache hit rather than a guaranteed miss.

**Manual database session handling.** FastAPI's usual `Depends(get_db)` pattern assumes there's
one database to inject. Here, _which_ database a request needs depends on the short code —
something only known partway through the request, not before it starts. Because of that,
sessions are created and closed manually inside each endpoint (`next(get_db(shard_id))`,
then `.close()`) instead of via dependency injection. This is a rougher, more error-prone
pattern than FastAPI's usual approach, and is a direct, visible tradeoff of routing to a
shard dynamically per-request.

## API reference

**`GET /`**
Redirects to `/docs` — this exists so visiting the bare deployed URL lands somewhere useful
instead of a bare 404.

**`GET /ping`**
Health check, returns `{"health": "Healthy"}`. Useful for uptime monitors (or a keep-alive
ping, given the free-tier cold start mentioned above).

**`POST /shorten`**

```json
// Request
{ "url": "https://example.com/some/very/long/path" }

// Response
{
  "id": "aZ9kT2Qw",
  "long_url": "https://example.com/some/very/long/path",
  "link": "http://127.0.0.1:8000/aZ9kT2Qw"
}
```

Returns `409 Conflict` if the generated code already exists for a _different_ URL.

**`GET /{code}`**
Redirects to the original long URL, or returns `404` if the code doesn't exist.

## What's not implemented (known limitations)

- **Shard migration is manual, not automatic.** Adding a shard to `db.py`'s `engines` dict
  makes the ring aware of it immediately, but existing rows don't move themselves — the
  migration script (see [Roadmap](#roadmap)) has to be run by hand afterward. A production
  system would likely trigger this automatically as part of the shard-add process, with the
  new shard excluded from serving traffic until migration completes.
- **No persistent deduplication** — the same-URL-returns-same-code behavior relies on the hash
  being deterministic; there's no separate `long_url` uniqueness constraint in Postgres, so a
  hash collision (see above) could in theory create two rows.
- **No rate limiting** on `/shorten`.
- **No authentication** — anyone can create or look up short links.
- **No load testing yet** — a Locust-based comparison of cache-enabled vs. cache-disabled
  latency would be a natural next addition.
- **Hosted on free tiers** — Render's free web service spins down after inactivity; the first
  request after a period of inactivity may take 30-60 seconds while the instance wakes up.

## Roadmap

**Done: shard migration script** (`scripts/data_migration.py`). Walks existing rows across all
shards, recomputes each row's target shard under the current ring (`get_shard(short_code)`),
and moves any row whose target shard has changed. This closes the gap described above:
consistent hashing already minimizes _how many_ rows need to move (~1/N instead of ~100%), and
this script provides the mechanism that actually moves them — making it safe to add a new
shard to a live system without manually relocating data first.

- **Move order:** insert into the target shard first, commit, _then_ delete from the source
  shard — chosen deliberately over the reverse order. If the script crashes between the two
  steps, the row is temporarily duplicated across two shards rather than missing entirely.
  Since routing (`get_shard`) always points at exactly one shard regardless of what data
  physically exists elsewhere, a stray duplicate in the old shard is inert — never queried,
  never served — whereas a delete-then-insert crash would cause real `404`s for valid codes.
- **Idempotent by construction:** re-running the script after a crash or interruption is safe.
  Any row already moved now reports its current shard as already correct, so it's skipped on
  the next pass — no separate resume logic needed.
- **Failure logging:** insert failures and delete failures are logged separately to
  `migration_failures.json` (distinct `stage` field), since they mean different things — a
  failed insert leaves the row untouched and safe to retry, while a failed delete (after a
  successful insert) leaves a harmless-but-orphaned duplicate that won't self-heal on a future
  run and needs manual cleanup.
- **Verified against a real shard addition:** run after adding a 3rd shard to this project's
  live ring, the script correctly identified and moved rows out of both existing shards (some
  into the new shard, some redistributed between the two originals, matching the ring's
  territory reassignment) with zero failures.

**Later:**

- Replace hash-based codes with a Snowflake-style distributed ID generator (timestamp +
  worker ID + sequence number, bit-packed into one integer) — trades away free deduplication
  for guaranteed collision-free IDs, and allows the shard ID to be embedded directly in the
  ID rather than derived by hashing.
- Add a minimal single-page UI for creating and viewing short links.
- Deploy behind a real load balancer in front of multiple app instances.

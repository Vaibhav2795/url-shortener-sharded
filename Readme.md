# URL Shortener with Sharding

🔗 **Live demo:** [url-shortener-sharded.onrender.com](https://url-shortener-sharded.onrender.com)
*(hosted on Render's free tier — the first request after a period of inactivity may take 30-60 seconds while the instance wakes up)*

A URL shortener (like bit.ly) built as a hands-on system design project — the interesting part
isn't the shortening itself, it's the infrastructure underneath: **database sharding**, a
**Redis cache-aside layer**, and the tradeoffs that come with both.

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

Two independent Postgres databases (shards) sit behind a small in-app router. Requests are
routed to a shard based on a hash of the short code — the same router function is used on both
the write path and the read path, so a code always resolves to the same shard it was written to.

A Redis cache sits in front of the read path, since redirects happen far more often than
new URLs are created.

## Tech stack

| Layer         | Choice                                    |
| ------------- | ----------------------------------------- |
| API framework | FastAPI                                   |
| Database      | PostgreSQL (2 shards, hosted on Supabase) |
| ORM           | SQLAlchemy                                |
| Cache         | Redis (hosted on Upstash)                 |
| Hashing       | Python's `hashlib.shake_256`              |

## How a short code is generated

Rather than relying on a database auto-increment ID (which breaks once you have more than one
database — see [Design decisions](#design-decisions) below), the short code is derived directly
from the long URL:

1. `shake_256` hashes the long URL to a fixed-length hex digest.
2. The hex digest is parsed into an integer.
3. That integer is Base62-encoded to produce the short code (e.g. `aZ9kT2Qw`).
4. The same integer, mod the number of shards, decides which shard the row is written to.

On the read path, the short code is decoded back into the same integer (Base62 decode is the
exact inverse of the encode step), which recovers the shard index without needing to hash
anything again.

## Setup

### 1. Environment variables

Create a `.env` file:

```
SHARD_0_DATABASE_URL=postgresql+psycopg://user:password@host:5432/postgres
SHARD_1_DATABASE_URL=postgresql+psycopg://user:password@host:5432/postgres
REDIS_URL=rediss://default:password@endpoint.upstash.io:6379
```

- The two `DATABASE_URL`s point at two separate Postgres instances (this project uses two
  separate Supabase projects, in the same region, connected via Supabase's **session pooler**
  rather than the direct connection string — the direct connection is IPv6-only on Supabase's
  free tier and will fail to connect from most home networks).
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

Both shards' tables are created automatically on startup (`Base.metadata.create_all` runs once
per shard engine).

Visit `http://127.0.0.1:8000/docs` for interactive API docs.

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

**Why modulo sharding instead of consistent hashing?**
`hash % num_shards` is simple and correct as long as the shard count never changes. Its
well-known weakness: adding or removing a shard reshuffles the mapping for almost every
existing key, since the modulus itself changes. **Consistent hashing** solves this by mapping
both keys and shards onto a ring, so adding a shard only affects a small fraction of keys. This
project uses modulo sharding deliberately, to keep the routing logic easy to reason about,
with the explicit tradeoff noted here rather than solved.

**Why Redis cache-aside instead of write-through everywhere?**
The read path (`GET /{code}`) uses classic cache-aside: check cache, fall back to the database
on a miss, then populate the cache. The write path (`POST /shorten`) also proactively caches
the new mapping immediately, so the very first visitor to a newly created link is already a
cache hit rather than a guaranteed miss.

**Manual database session handling.** FastAPI's usual `Depends(get_db)` pattern assumes there's
one database to inject. Here, _which_ database a request needs depends on the short code —
something only known partway through the request, not before it starts. Because of that,
sessions are created and closed manually inside each endpoint (`next(get_db(shard_index))`,
then `.close()`) instead of via dependency injection. This is a rougher, more error-prone
pattern than FastAPI's usual approach, and is a direct, visible tradeoff of routing to a
shard dynamically per-request.

## API reference

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

- **No consistent hashing** — adding a third shard would reshuffle most existing routing (see
  above). A hash-ring implementation would fix this.
- **No persistent deduplication** — the same-URL-returns-same-code behavior relies on the hash
  being deterministic; there's no separate `long_url` uniqueness constraint in Postgres, so a
  hash collision (see above) could in theory create two rows.
- **No rate limiting** on `/shorten`.
- **No authentication** — anyone can create or look up short links.
- **No load testing yet** — a Locust-based comparison of cache-enabled vs. cache-disabled
  latency would be a natural next addition.

## Possible extensions

- Replace modulo sharding with consistent hashing to make adding shards non-disruptive.
- Replace hash-based codes with a Snowflake-style distributed ID generator (timestamp +
  worker ID + sequence number, bit-packed into one integer) — trades away free deduplication
  for guaranteed collision-free IDs, and allows the shard index to be embedded directly in the
  ID rather than derived by hashing.
- Add a minimal single-page UI for creating and viewing short links.
- Deploy behind a real load balancer in front of multiple app instances.

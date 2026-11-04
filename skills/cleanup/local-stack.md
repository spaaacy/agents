# Local stack: up to date and running (shared by /cleanup and /spotlight)

The end state every run of /cleanup or /spotlight leaves behind, checked last, after any checkout move: the backend's docker stack is running, its database is migrated to the backend main checkout's newest revision, and the api answers. Run it whether or not anything moved; a database can be behind (or the api down) before the skill started.

Work only from the backend main checkout (`B=~/dev/envoyai-backend`, every command `docker compose -f $B/docker-compose.yaml ...`), never from a worktree: the stack belongs to the main checkout and runs its code (the source tree is mounted into the containers). Read the database only through the stack, never through the `pg-readonly` MCP or any configured connection, which point at production or staging.

How the stack behaves, which is why the steps are what they are: the api's entrypoint runs `poetry install` and then `poe migration-upgrade` every time the container starts, then serves with `--reload`; the worker and beat run the same `poetry install` on start and reload code on their own. So a code change needs nothing, a new migration needs the api restarted (the reload never re-runs migrations), and a `poetry.lock` change needs every service restarted.

## 1. Running

- Docker itself not answering (`docker info` fails): start Docker Desktop (`open -a Docker`), wait up to two minutes for `docker info`, else report and stop.
- Stack not up (`docker compose -f $B/docker-compose.yaml ps --format '{{.Service}} {{.State}}'` lacks a running `postgres` or `api`): `docker compose -f $B/docker-compose.yaml up -d`. This starts postgres first (the api waits for it to be healthy) and the api migrates as it starts.
- Already up, and this run moved the backend checkout (or found the database behind its head): `docker compose -f $B/docker-compose.yaml restart api` so its startup migrates; when `poetry.lock` differs between the old and new HEAD (`git -C $B diff --name-only <old> <new> -- poetry.lock`), restart `api worker beat livekit-worker` together instead.

Then wait for the api: `curl -s -o /dev/null -w '%{http_code}' --max-time 5 localhost:8000/api/openapi.json` every 5 s, up to three minutes (the startup `poetry install` can take a while), stopping early if `docker compose -f $B/docker-compose.yaml ps -a api --format '{{.State}}'` reads `exited`.

## 2. Answering 200: confirm the migrations

`docker compose -f $B/docker-compose.yaml exec -T postgres psql -U envoy -d envoy -Atc "select version_num from alembic_version"` against `docker compose -f $B/docker-compose.yaml exec -T api alembic heads`. Equal: done. Two heads: a branch problem, not a local one; name `python scripts/check_single_migration_head.py` and change nothing.

## 3. Exited: read why, fix the cases that are certain

`docker compose -f $B/docker-compose.yaml logs --tail 80 api`, and match:

- **"already exists" on a migration** (`DuplicateColumnError`, `DuplicateTableError`, `DuplicateObjectError`, a duplicate enum value) while `Running upgrade <parent> -> <R>`: the database already holds what `<R>` creates, with its stamp left at `<parent>` (a branch switch or a re-parented revision). Read `<R>`'s `upgrade()` in `$B/alembic/versions/` and check, through psql, that EVERY object it creates is there exactly (columns by table and name, tables, indexes by name, constraints by name, enum values) and that nothing it drops is still there. All of it present: move the stamp past it, `update alembic_version set version_num='<R>' where version_num='<parent>' returning version_num`, start the api again, and repeat from step 1's wait (at most five revisions in one run; a sixth means something wider, so stop and report). Any object missing or different: change nothing, report which objects are and are not there, and leave it to the user (it is /cleanup's migration case c).
- **The stamp is not in the chain** (`Can't locate revision`): /cleanup's migration case b; report the stamp and stop.
- **A missing package** (`ModuleNotFoundError`) or a system library error, when `$B/Dockerfile` changed after the api image was built (`git -C $B log -1 --format=%cI -- Dockerfile` later than `docker image inspect envoyai-backend-api --format '{{.Created}}'`): `docker compose -f $B/docker-compose.yaml up -d --build api worker beat livekit-worker`, then step 1's wait. Not newer: report the error.
- **Anything else**: report the last error line and stop.

## Never

Downgrade, stamp backwards, stamp past a revision whose objects are not all present, `docker compose down -v`, delete `dockerdata/postgres-data`, drop the dev database, run `docker compose` from a worktree, or run `az`. Wiping the database is /cleanup's case d and always the user's call.

## Report line

`local stack: <stamp before> → <stamp after> (<migrated | current | stamped past <R>, objects checked | left: why>); api <answering 200 | exited: <error line> | not started: why>; <restarted: services | started | rebuilt | nothing>`

# enochecker3

A FastAPI based checker library for writing async checkers in python. It is called enochecker3 even though enochecker2 never existed, because it is intended to be the reference implementation for version 3 of the enochecker API specification which is yet to come.

## Quick Start

Install `enochecker3` using
```
pip3 install enochecker3
```

Create an `example.py` file with the following content:
```python
import secrets
from typing import Optional

from httpx import AsyncClient

from enochecker3 import (
    ChainDB,
    Enochecker,
    GetflagCheckerTaskMessage,
    PutflagCheckerTaskMessage,
)
from enochecker3.utils import FlagSearcher, assert_equals, assert_in

checker = Enochecker("ExampleService", 1337)


@checker.putflag(0)
async def putflag_test(
    task: PutflagCheckerTaskMessage,
    client: AsyncClient,
    db: ChainDB,
) -> None:
    token = secrets.token_hex(32)
    r = await client.post("/note", json={"token": token, "flag": task.flag})
    assert_equals(r.status_code, 200, "storing note with flag failed")

    await db.set("token", token)


@checker.getflag(0)
async def getflag_test(
    task: GetflagCheckerTaskMessage, client: AsyncClient, db: ChainDB
) -> None:
    token = await db.get("token")

    r = await client.get(f"/note/{token}")
    assert_equals(r.status_code, 200, "getting note with flag failed")
    assert_in(task.flag, r.text, "flag missing from note")


@checker.exploit(0, 0)
async def exploit_test(searcher: FlagSearcher, client: AsyncClient) -> Optional[str]:
    r = await client.get(
        "/note/*",
    )
    assert not r.is_error

    if flag := searcher.search_flag(r.text):
        return flag
```

Start the checker using
```
uvicorn --reload example:checker.app
```

And browse to (http://localhost:8000/docs) to explore the web interface, which allows you to send requests to the checker.


## Exploits and Flag Stores

Every exploit targets exactly one flag store. Unlike the other methods, which take
a list of variant ids, `exploit` takes a single exploit variant id followed by the
flag variant it targets:

```python
@checker.exploit(0, 0)  # exploit variant 0, against flag store 0
async def exploit_note_idor(searcher: FlagSearcher, client: AsyncClient) -> Optional[str]:
    ...

@checker.exploit(1, 1)  # exploit variant 1, against flag store 1
async def exploit_admin_sqli(searcher: FlagSearcher, client: AsyncClient) -> Optional[str]:
    ...
```

The checker reports this mapping as `exploitVariants` in its `GET /service`
response: a list indexed by exploit variant id, whose length is the number of
exploits. Testing tools use it to pair each exploit with the `putflag` variant that
stored the flag it is supposed to retrieve, instead of trying every combination.
Every flag store should be covered by at least one exploit.

## Testing

Checker methods that use `ChainDB` or the other MongoDB-backed dependencies
normally need a MongoDB server. For tests, use the in-memory
`enochecker3.mock.MockAsyncMongoClient` instead, so you don't need Docker or a
real server:

```
pip3 install "enochecker3[testing]"
```

```python
from enochecker3 import Enochecker
from enochecker3.mock import MockAsyncMongoClient

checker = Enochecker("ExampleService", 1337, mongo_client=MockAsyncMongoClient())
```

See [docs/faq.md](docs/faq.md#do-i-need-mongodb-running) for a full example.

## Health Endpoint

The checker serves `GET /health` for infrastructure probes (Kubernetes, Docker,
uptime monitoring). By default it pings MongoDB and answers `200` with
`{"status": "ok"}` if that succeeds, or `503` with
`{"status": "error", "detail": "MongoDB unreachable: ..."}` if it does not. The
ping is bounded to two seconds, so set the prober's own timeout above that.

MongoDB is probed because it is the dependency every checker has and the one a
running checker can lose silently: losing it at startup takes the app down before
it serves anything, so an answer at all already rules that out. Both the endpoint
and the ping it makes are excluded from OpenTelemetry tracing, so a short probe
interval does not flood the collector.

Replace the default with `@checker.health`:

```python
@checker.health
async def health() -> dict:
    await probe_my_dependency()
    return {"status": "ok", "revision": REVISION}
```

The handler takes no arguments and may be sync or async:

- Return `None` to keep the plain `{"status": "ok"}` body.
- Return anything else to use it as the response body, unvalidated.
- Raise to report the checker as unhealthy with status `503` and
  `{"status": "error", "detail": "<exception message>"}`.
- Raise `fastapi.HTTPException` to choose the status code yourself.

Defining a handler turns the default MongoDB ping off rather than adding to it, so
a handler that wants the database covered has to probe it itself.

Both bodies the library itself emits are
`enochecker_core.CheckerHealthMessage`, so a consumer can validate a `/health`
response against that model rather than matching keys by hand. The model allows
extra fields, which is how a handler's own additions survive validation. A
handler's body is not checked against it, so you are free to report whatever
shape you want.

Dependencies are not injected here, because there is no task to resolve them
against. Engine-side reachability checks use `GET /service` rather than `/health`,
so this endpoint is yours to shape for your own infrastructure.

## Telemetry
The checker supports OpenTelemetry, with this configuration:
- Set the environment variable `OTEL_EXPORTER_OTLP_ENDPOINT` to the URL of your OpenTelemetry collector. This is the switch: unset, nothing is exported.
- Set the environment variable `OTEL_EXPORTER_OTLP_HEADERS_AUTHORIZATION` to your authentication token. It is sent as the `authorization` header on every export.

Traces and logs can go to different collectors over different transports,
following the standard `OTEL_EXPORTER_OTLP_*` variables. The signal-specific
name wins over the generic one:

| Variable | Default |
| --- | --- |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` (`http/protobuf` is the other) |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL` | the generic ones |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`, `OTEL_EXPORTER_OTLP_LOGS_PROTOCOL` | the generic ones |

`OTEL_RESOURCE_ATTRIBUTES` (for example
`k8s.cluster.name=ecsc-2026,k8s.namespace.name=ecsc-2026-prod`) is
merged into every span and log record; `service.name` is always the
checker's own.

An HTTP endpoint is used verbatim and so has to carry its signal path, for
example `https://loki.example/otlp/v1/logs`. A gRPC endpoint is host and
port; an `http://` scheme selects plaintext.

## Local Debug Logs
Setting `ENOCHECKER_LOG_DIR` writes every log message, down to `DEBUG`, to
size capped JSON lines files in that directory. Only this sink takes `DEBUG`
records; stdout and the OTLP export keep carrying `INFO` and above, so turning
it on does not change what leaves the machine.

| Variable | Default |
| --- | --- |
| `ENOCHECKER_LOG_DIR` | unset, which disables file logging |
| `ENOCHECKER_LOG_BUDGET` | `1G`, an upper bound on the disk used by the whole checker, with an optional `K`, `M`, `G` or `T` suffix |
| `ENOCHECKER_LOG_WORKERS` | inferred from `WEB_CONCURRENCY`, else a `-w`/`--workers` argument, else `1` |
| `ENOCHECKER_DROP_LOG_OVERRUN` | unset, which makes an unusable log directory fatal instead of degrading to stdout only |

The budget is an upper bound, not a target. Each worker gets `budget / workers`
and spreads its share over four rotating files, so the directory holds
`workers * 4` files of `budget / (workers * 4)` bytes and never more. Workers
claim slots with byte range locks on an empty `<CheckerName>.slots` file, which
keeps the file set identical across restarts rather than growing by a set per
process lifetime.

New messages are never refused to stay inside the budget; the oldest of a
worker's four files is dropped instead. A message too large for one file is
truncated with a `... <SNIP>` marker, and the budget has to leave each file
8 KB, so it must be at least `workers * 32 KB`.

The checker refuses to start when the log directory cannot be created, or when
more processes log into one directory than the worker count accounts for, since
neither can honour the budget. `ENOCHECKER_DROP_LOG_OVERRUN` turns both into a
warning on stderr and keeps serving without file logging. A malformed or too
small budget stays fatal either way. Workers have to be forked after the
checker is constructed, so `gunicorn --preload` is rejected outright.

Every line carries the full task context, which is what makes a single round or
flag chain greppable:
```
cat logs/*.jsonl* | jq -c 'select(.currentRoundId == 42)'
cat logs/*.jsonl* | jq -c 'select(.taskChainId == "flag_s1_r42_t3_i0")'
cat logs/*.jsonl* | jq -r 'select(.taskId == 1337) | "\(.timestamp) \(.severity) \(.message)"'
```

If the volume runs out of space, the affected messages are dropped with a
traceback on stderr. Tasks keep succeeding, the other sinks are unaffected, the
files already written stay valid, and logging resumes once space is freed.

The logs contain flags, so the directory needs the same protection as the flags
themselves.

## Built-In Dependencies

See [docs/builtin_dependencies.md](docs/builtin_dependencies.md)

## Adding Custom Dependencies

See [docs/adding_custom_dependencies.md](docs/adding_custom_dependencies.md)

## Good to Know / FAQ

See [docs/faq.md](docs/faq.md)

# enochecker3

A FastAPI-based library for writing async checkers in Python. It is called enochecker3 even though enochecker2 never existed; it is the reference implementation for version 3 of the enochecker API specification, which is still being defined.

The minor version tracks [enochecker-core](https://github.com/attacking-lab/enochecker-core): enochecker3 `0.X.*` depends on enochecker-core `0.X.*`.

## Quick start

```
pip3 install enochecker3
```

Save this as `example.py`:

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

Start it:

```
uvicorn --reload example:checker.app
```

Open http://localhost:8000/docs to try requests against it.


## Exploits and flag stores

Every exploit targets exactly one flag store. `exploit` takes a single exploit variant id followed by the flag variant it targets, unlike other methods which take a list of variant ids:

```python
@checker.exploit(0, 0)  # exploit variant 0, against flag store 0
async def exploit_note_idor(searcher: FlagSearcher, client: AsyncClient) -> Optional[str]:
    ...

@checker.exploit(1, 1)  # exploit variant 1, against flag store 1
async def exploit_admin_sqli(searcher: FlagSearcher, client: AsyncClient) -> Optional[str]:
    ...
```

The checker reports this mapping as `exploitVariants` in its `GET /service` response: a list indexed by exploit variant id, with length equal to the number of exploits. Testing tools use it to pair each exploit with the `putflag` variant that stored the flag it targets, rather than trying every combination. Every flag store should be covered by at least one exploit.

## Testing

Methods that use `ChainDB` or other MongoDB-backed dependencies normally need a MongoDB server. For tests, use the in-memory `MockAsyncMongoClient` instead:

```
pip3 install "enochecker3[testing]"
```

```python
from enochecker3 import Enochecker
from enochecker3.mock import MockAsyncMongoClient

checker = Enochecker("ExampleService", 1337, mongo_client=MockAsyncMongoClient())
```

See [docs/faq.md](docs/faq.md#do-i-need-mongodb-running) for a full example.

## Health endpoint

The checker serves `GET /health` for infrastructure probes. By default it pings MongoDB with a two-second timeout and returns `200 {"status": "ok"}` or `503` on failure. Override it with `@checker.health`.

See [docs/health.md](docs/health.md).

## Telemetry

Set `OTEL_EXPORTER_OTLP_ENDPOINT` to export traces and logs via OpenTelemetry. Signal-specific endpoints and protocols follow the standard `OTEL_EXPORTER_OTLP_*` variables.

See [docs/telemetry.md](docs/telemetry.md).

## Local debug logs

Set `ENOCHECKER_LOG_DIR` to write all log messages, down to `DEBUG`, to rotating, size-capped JSON lines files.

See [docs/logging.md](docs/logging.md).

## Built-in dependencies

See [docs/builtin_dependencies.md](docs/builtin_dependencies.md)

## Adding custom dependencies

See [docs/adding_custom_dependencies.md](docs/adding_custom_dependencies.md)

## FAQ

See [docs/faq.md](docs/faq.md)

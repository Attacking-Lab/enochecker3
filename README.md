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
    MumbleException,
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
    try:
        token = await db.get("token")
    except KeyError:
        raise MumbleException("Missing database entry from putflag")

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

## Telemetry
The checker supports OpenTelemetry, with this configuration:
- Set the environment variable `OTEL_EXPORTER_OTLP_ENDPOINT` to the URL of your OpenTelemetry collector.
- Set the environment variable `OTEL_EXPORTER_OTLP_HEADERS_AUTHORIZATION` to your authentication token.

## Built-In Dependencies

See [docs/builtin_dependencies.md](docs/builtin_dependencies.md)

## Adding Custom Dependencies

See [docs/adding_custom_dependencies.md](docs/adding_custom_dependencies.md)

## Good to Know / FAQ

See [docs/faq.md](docs/faq.md)

# Adding Custom Dependencies

You can register your own custom dependencies to encapsulate complex setup logic and make it reusable across checker methods.

### Key Points for Custom Dependencies

1. **Type annotations are required**: The return type is used to match parameters
2. **Dependencies can depend on dependencies**: They're recursively injected
3. **Context managers are supported**: Use `@asynccontextmanager` for automatic cleanup
4. **Circular dependencies are detected**: Will raise `CircularDependencyException`
5. **Each parameter gets a fresh instance**: Even if multiple parameters have the same type, each gets its own instance (see FAQ below)

### Example: Database Connection with Cleanup

Context manager dependencies are supported for automatic resource cleanup. Note that for context manager dependencies, the DI system matches on the return type annotation, so you must use a type alias and use the same alias in the parameter annotation.

```python
from contextlib import asynccontextmanager
from typing import AsyncIterator
import asyncpg

# Type alias for the managed connection. The DI system matches parameters
# by this alias. At runtime, the parameter receives the yielded value
# (asyncpg.Connection), not the iterator itself.
ManagedPgConnection = AsyncIterator[asyncpg.Connection]

@checker.register_dependency
@asynccontextmanager
async def _get_db_connection(task: CheckerTaskMessage) -> ManagedPgConnection:
    """PostgreSQL connection with automatic cleanup."""
    conn = await asyncpg.connect(
        host=task.address,
        port=5432,
        user="checker",
        password="checker_pass",
        database="ctf_service"
    )

    try:
        yield conn
    finally:
        await conn.close()

@checker.exploit(0, 0)
async def exploit_sql_injection(
    task: ExploitCheckerTaskMessage,
    conn: ManagedPgConnection,  # Must use the same type alias
    searcher: FlagSearcher
) -> Optional[str]:
    rows = await conn.fetch(
        "SELECT * FROM secrets WHERE public = true"
    )

    all_data = "\n".join(str(row) for row in rows)
    return searcher.search_flag(all_data)
```

### Example: Authenticated HTTP Client (Named Dependency)

Named dependencies let you create multiple variants of the same type. This example demonstrates using named dependencies to create an authenticated client.

```python
import secrets
from httpx import AsyncClient
from enochecker3 import Enochecker, PutflagCheckerTaskMessage, GetflagCheckerTaskMessage, MumbleException, ChainDB
from enochecker3.utils import assert_equals, assert_in

checker = Enochecker("MyService", 8080)

# Register a named dependency called "authenticated"
# Named dependencies are resolved by matching the parameter name prefix (before underscore)
# to the name registered with register_named_dependency
@checker.register_named_dependency("authenticated")
async def _get_authenticated_client(
    db: ChainDB,
    client: AsyncClient  # Dependencies can depend on other dependencies!
) -> AsyncClient:
    """
    Retrieve credentials from ChainDB and return an authenticated HTTP client.

    This dependency will be injected into parameters that:
    - Have type annotation AsyncClient
    - Have parameter name starting with "authenticated_" (e.g., authenticated_client)
    """
    # Get credentials stored during putflag (raises MumbleException if missing)
    username = await db.get("username")
    password = await db.get("password")

    # Login to get session token/cookie
    login_response = await client.post("/auth/login", json={
        "username": username,
        "password": password
    })

    if login_response.status_code != 200:
        raise MumbleException("Failed to login with stored credentials")

    # Extract session token and configure client
    session_token = login_response.json()["token"]
    client.headers["Authorization"] = f"Bearer {session_token}"

    # Return the authenticated client
    return client

# putflag creates the user and stores credentials
@checker.putflag(0)
async def putflag_with_auth(
    task: PutflagCheckerTaskMessage,
    client: AsyncClient,  # Regular unauthenticated client
    db: ChainDB
) -> str:
    # Generate credentials
    username = f"user_{secrets.token_hex(8)}"
    password = secrets.token_hex(16)

    # Register new account
    response = await client.post("/auth/register", json={
        "username": username,
        "password": password
    })
    assert_equals(response.status_code, 201, "Failed to register user")

    # Login and store flag in user's profile
    login_response = await client.post("/auth/login", json={
        "username": username,
        "password": password
    })
    token = login_response.json()["token"]

    await client.post(
        "/api/profile/bio",
        json={"bio": task.flag},
        headers={"Authorization": f"Bearer {token}"}
    )

    # Store credentials for getflag (PRIVATE - only checker knows these)
    await db.set("username", username)
    await db.set("password", password)

    # Return PUBLIC targeting info (tells players which user to attack)
    return username

# getflag uses the authenticated client dependency
@checker.getflag(0)
async def getflag_with_auth(
    task: GetflagCheckerTaskMessage,
    authenticated_client: AsyncClient,  # Named dependency resolved by "authenticated_" prefix
    db: ChainDB
) -> None:
    # Client is already authenticated via the named dependency
    username = await db.get("username")
    response = await authenticated_client.get(f"/api/user/{username}/bio")
    assert_in(task.flag, response.text)
```

### Flow Accounting on Custom HTTP Clients

The built-in `httpx.AsyncClient` dependency records every TCP connection its pool opens under `metadata["flows"]` (see [Result Metadata](builtin_dependencies.md#result-metadata-taskmetadata)). A client you build yourself does not get this for free — call `record_http_flows` on it and its traffic shows up in the result alongside everything else.

```python
import httpx
from enochecker3 import CheckerTaskMessage, TaskMetadata, record_http_flows

@checker.register_named_dependency("slow")
def _get_slow_client(
    task: CheckerTaskMessage,
    metadata: TaskMetadata,  # the shared per-task bag, injected like any dependency
) -> httpx.AsyncClient:
    """A client with a longer timeout, still accounted for in metadata["flows"]."""
    client = httpx.AsyncClient(
        base_url=f"http://{task.address}:{checker.service_port}",
        timeout=60.0,
    )
    record_http_flows(client, metadata)
    return client
```

Notes:

- Call it **before** the client makes its first request; connections already open are not retroactively recorded.
- Calling it twice on the same client is a no-op, so passing the built-in client through a wrapper dependency that also calls it will not double-count.
- Transports registered via httpx's `mounts=` argument are covered as well.
- It returns `False` if the client exposes no connection pool to hook into (an `httpx.MockTransport`, an ASGI transport, or a future httpx release that moves its internals). Assert on the return value if a missing flow record would be a problem for you.
- Raw sockets opened outside of httpx are not covered. Use the built-in `AsyncSocket` dependency, which records its connection, or build flow entries yourself with `enochecker3.flows.record_flow` / `close_flow`.

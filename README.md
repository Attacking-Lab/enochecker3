# enochecker3

A FastAPI-based library for writing async A/D service checkers in Python compatible with the ENOchecker task specification.

The minor version tracks [enochecker-core](https://github.com/attacking-lab/enochecker-core): enochecker3 `0.X.*` depends on enochecker-core `0.X.*`.

See `example/` for a minimal checker example.

## Task types

Each checker method handles one task type. Register it with the matching decorator and a variant id:

```python
checker = Enochecker("ServiceName", 3141)

@checker.putflag(0)
async def putflag0(task: PutflagCheckerTaskMessage, client: AsyncClient, db: ChainDB) -> None:
    ...
```

| Decorator | Task message type | Purpose | Return value |
|---|---|---|---|
| `@checker.putflag(n)` | `PutflagCheckerTaskMessage` | Store a flag in the service | `str` attack_info (optional) |
| `@checker.getflag(n)` | `GetflagCheckerTaskMessage` | Retrieve and verify the flag | — |
| `@checker.putnoise(n)` | `PutnoiseCheckerTaskMessage` | Write benign data to the service | — |
| `@checker.getnoise(n)` | `GetnoiseCheckerTaskMessage` | Verify the noise is still there | — |
| `@checker.havoc(n)` | `HavocCheckerTaskMessage` | Probe service behavior without storing anything | — |
| `@checker.exploit(n, f)` | `ExploitCheckerTaskMessage` | Extract a flag from a previous round | `str` flag (or `None`) |

`exploit(n, f)` takes two ids: exploit variant `n` and the flag store `f` it targets. All other decorators take only the variant id. Every variant id starts from 0; gaps are not allowed.

All task messages share a common set of fields:

| Field | Type | Description |
|---|---|---|
| `task_id` | `int` | Unique id for this task |
| `team_id` | `int` | Target team's id |
| `team_name` | `str` | Target team's name |
| `service_id` | `int` | Service id |
| `service_name` | `str` | Service name |
| `address` | `str` | Target host |
| `flag_regex` | `str` | Regex matching valid flags (exploit only) |
| `flag_hash` | `str` | Expected flag hash for validation (exploit only) |
| `attack_info` | `str \| None` | Public info from putflag, e.g. a user id (exploit only) |
| `flag` | `str` | Flag to store or verify (putflag/getflag only) |
| `task_chain_id` | `str` | Links the putflag and getflag for the same flag slot |
| `current_round_id` | `int` | Current round number |
| `related_round_id` | `int` | Round the flag was put (getflag/exploit) |
| `timeout` | `int` | Milliseconds until the engine cancels the task |

## Injectable dependencies

Declare any of these as typed parameters and they are provided automatically:

| Type | Description |
|---|---|
| `httpx.AsyncClient` | HTTP client pre-configured with `base_url=http://{task.address}:{port}`. Records flows. |
| `ChainDB` | Key-value store scoped to the task chain, backed by MongoDB. Raises `MumbleException` on missing key. |
| `AsyncCollection` | Raw MongoDB collection scoped to the team. For queries beyond what ChainDB covers. |
| `AsyncDatabase` | Raw MongoDB database. For cross-collection operations. |
| `AsyncSocket` | TCP socket connecting to `task.address:port`. Returns `(StreamReader, StreamWriter)`. |
| `FlagSearcher` | Searches text or bytes for a valid flag. Only available in `exploit` methods. |
| `logging.LoggerAdapter` | Logger with task context pre-attached. |
| `random.Random` | Seeded with `task_id` for reproducible test data. Do not use for secrets. |
| `TaskMetadata` | Mutable `dict` attached to the result. Useful for out-of-band diagnostics. |
| `DependencyInjector` | Resolves dependencies dynamically at runtime. |

See [docs/builtin_dependencies.md](docs/builtin_dependencies.md) for details on each.

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

## License

MIT. See [LICENSE](LICENSE).

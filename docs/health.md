# Health endpoint

The checker serves `GET /health` for infrastructure probes (Kubernetes, Docker, uptime monitors). By default it pings MongoDB and returns `200 {"status": "ok"}` if that succeeds, or `503 {"status": "error", "detail": "MongoDB unreachable: ..."}` if it does not. The ping has a two-second timeout, so set the prober's own timeout above that.

MongoDB is checked because it is the one dependency every checker has and the one it can lose silently: if it is unreachable at startup, the app crashes before it serves anything, so getting a response at all already rules that out. Both the endpoint and its MongoDB ping are excluded from OpenTelemetry tracing, so a short probe interval does not flood the collector.

Replace the default with `@checker.health`:

```python
@checker.health
async def health() -> dict:
    await probe_my_dependency()
    return {"status": "ok", "revision": REVISION}
```

The handler takes no arguments and can be sync or async:

- Return `None` to keep the plain `{"status": "ok"}` body.
- Return anything else to use it as the response body, unvalidated.
- Raise to report the checker as unhealthy: status `503`, body `{"status": "error", "detail": "<exception message>"}`.
- Raise `fastapi.HTTPException` to set the status code yourself.

Defining a handler turns the default MongoDB ping off rather than adding to it; a handler that wants the database checked has to probe it itself.

Both response bodies the library emits are `enochecker_core.CheckerHealthMessage`, so a consumer can validate them against that model. It allows extra fields, which is how a handler's own additions survive validation. A handler's body is not validated against it, so you can return any shape you want.

Dependencies are not injected here because there is no task to resolve them against. The engine uses `GET /service` for reachability checks, not `/health`, so this endpoint is yours to shape for your own infrastructure.

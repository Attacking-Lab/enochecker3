# Local debug logs

Set `ENOCHECKER_LOG_DIR` to write every log message, down to `DEBUG`, to size-capped JSON lines files in that directory. Only this sink takes `DEBUG` records; stdout and the OTLP export keep carrying `INFO` and above, so turning it on does not change what leaves the machine.

| Variable | Default |
| --- | --- |
| `ENOCHECKER_LOG_DIR` | unset, which disables file logging |
| `ENOCHECKER_LOG_BUDGET` | `1G`, an upper bound on the disk used by the whole checker, with an optional `K`, `M`, `G` or `T` suffix |
| `ENOCHECKER_LOG_WORKERS` | inferred from `WEB_CONCURRENCY`, else a `-w`/`--workers` argument, else `1` |
| `ENOCHECKER_DROP_LOG_OVERRUN` | unset, which makes an unusable log directory fatal instead of degrading to stdout only |

The budget is an upper bound, not a target. Each worker gets `budget / workers` and spreads its share over four rotating files, so the directory holds `workers * 4` files of `budget / (workers * 4)` bytes at most. Workers claim slots with byte range locks on an empty `<CheckerName>.slots` file, which keeps the file set identical across restarts rather than growing by a set per process lifetime.

New messages are never refused to stay within budget; the oldest of a worker's four files is dropped instead. A message too large for one file is truncated with a `... <SNIP>` marker. The budget must leave each file at least 8 KB, so it must be at least `workers * 32 KB`.

The checker refuses to start when the log directory cannot be created, or when more processes log into one directory than the worker count accounts for, since neither can honor the budget. `ENOCHECKER_DROP_LOG_OVERRUN` turns both into a warning on stderr and keeps serving without file logging. A malformed or too small budget is always fatal.

A slot is claimed when a worker starts, not when the checker is constructed, so `gunicorn --preload` and `preload_app = True` are safe: each worker takes a slot of its own after forking from the master.

Every line carries full task context, which makes a single round or flag chain greppable:

```bash
cat logs/*.jsonl* | jq -c 'select(.currentRoundId == 42)'
cat logs/*.jsonl* | jq -c 'select(.taskChainId == "flag_s1_r42_t3_i0")'
cat logs/*.jsonl* | jq -r 'select(.taskId == 1337) | "\(.timestamp) \(.severity) \(.message)"'
```

If the volume runs out of space, affected messages are dropped with a traceback on stderr. Tasks keep succeeding, the other sinks are unaffected, and files already written stay valid.

The logs contain flags, so the directory needs the same protection as the flags themselves.

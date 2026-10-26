# Telemetry

Set `OTEL_EXPORTER_OTLP_ENDPOINT` to export traces and logs. Without it, nothing is exported regardless of other `OTEL_*` settings.

Set `OTEL_EXPORTER_OTLP_HEADERS_AUTHORIZATION` to your auth token. It is sent as the `authorization` header on every export request.

Traces and logs can go to different collectors via the signal-specific variables. The signal-specific name takes precedence over the generic one:

| Variable | Default |
| --- | --- |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `grpc` (`http/protobuf` is the other) |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`, `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL` | the generic ones |
| `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`, `OTEL_EXPORTER_OTLP_LOGS_PROTOCOL` | the generic ones |

Whatever a task writes into its `TaskMetadata` bag is attached to its span as `enochecker.result.metadata.<key>` once the task ends, alongside `enochecker.result`. A task that ended in Mumble, Offline or internal error carries its metadata too, so the trace explains the verdict on its own.

Every span attribute holding a checker's own data (a `TaskMetadata` value, a `ChainDB` value) is cut to `ENOCHECKER_SPAN_ATTRIBUTE_LIMIT` bytes and marked with a `... <SNIP>` suffix:

| Variable | Default |
| --- | --- |
| `ENOCHECKER_SPAN_ATTRIBUTE_LIMIT` | `256`, with an optional `K`, `M`, `G` or `T` suffix; `0` lifts the bound |

The default keeps an attribute to a line or two of context rather than a payload, which is what a trace is useful for. It is also a necessity for some checkers: a crypto service stores keys, ciphertexts and proofs in its `ChainDB`, each of which can render into hundreds of kilobytes, and a batch of such spans can exceed the message size a collector accepts on one export, costing the whole batch rather than just the oversized attribute. Raise the limit when a checker's values are worth keeping whole, keeping in mind that whatever stays is readable by everyone with collector access, secret keys included.

`OTEL_RESOURCE_ATTRIBUTES` (for example `k8s.cluster.name=ecsc-2026,k8s.namespace.name=ecsc-2026-prod`) is merged into every span and log record. `service.name` is always the checker's own.

An HTTP endpoint is used verbatim and must carry its signal path, for example `https://loki.example/otlp/v1/logs`. A gRPC endpoint is host and port; an `http://` scheme selects plaintext.

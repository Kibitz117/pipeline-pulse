# Operating-agent evidence

`insights/` contains complete local operating-agent evidence:

- `*.packet.json`: deterministic database input;
- `*.prompt.txt`: exact instructions and embedded packet;
- `*.schema.json`: required structured-output contract;
- `*.events.jsonl`: raw non-interactive Codex event stream;
- `*.output.json`: final structured memo;
- `*.stderr.log`: process diagnostics;
- `*.validation.json`: evidence-ID and quality-gate validation.

Do not commit API keys, design conversations, or unrelated private history.
The `sessions/design/` path is ignored intentionally; only auditable product
runs belong here.

Each export should have a manifest entry containing:

- session filename and SHA-256;
- model and tool/runtime version when available;
- start and end timestamps;
- task or agent role;
- input artifact IDs used by the session;
- code commit associated with the output;
- whether the output passed deterministic and agent verification.

Preferred raw format is JSONL or lossless JSONL gzip when GitHub size limits
require it. Markdown summaries may accompany raw exports but must not replace
them.

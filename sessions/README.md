# AI session exports

`design/` contains the single Codex session used to design and implement the
project. The raw JSONL is losslessly gzip-compressed because the uncompressed
rollout exceeds GitHub's individual-file limit. Its metadata records both the
compressed and recovered-byte checksums, size, runtime, model, and capture
window.

`insights/` contains complete local operating-agent evidence:

- `*.packet.json`: deterministic database input;
- `*.prompt.txt`: exact instructions and embedded packet;
- `*.schema.json`: required structured-output contract;
- `*.events.jsonl`: raw non-interactive Codex event stream;
- `*.output.json`: final structured memo;
- `*.stderr.log`: process diagnostics;
- `*.validation.json`: evidence-ID and quality-gate validation.

Do not commit API keys or unrelated private conversation history. The design
export is limited to the one project session and was checked for common secret
formats before inclusion.

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

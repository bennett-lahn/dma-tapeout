# TinyDMA Documentation

Documentation for **TinyDMA**. Split into two audiences:

| Tree | Audience | Style |
|---|---|---|
| `human/` | Humans | Condensed, complete summaries (not stubs) |
| `llm/` | AI agents / future sessions | Verbose elaboration of the same topics |
| `datasheets/` | Both | Manufacturer PDFs plus converted markdown (`pdfs/`, `md/`) |

Architecture and verification are parallel documentation sets under both `human/` and `llm/`.

### Human / llm parity

- `human/` stays **condensed**, but it must still carry every durable requirement, decision, and architectural choice in some form (short section, table, or bullets).
- `llm/` may expand rationale, catalogs, edge cases, sequences, and implementation detail. It must **not** be the only place a fact exists.
- Prefer expanding an existing human doc over adding an llm-only dump with a human pointer.
- **Anti-pattern:** human verification naming `CHK-*` and linking to [`llm/verification/06-checkers.md`](llm/verification/06-checkers.md) without summarizing the invariants. That catalog is currently llm-heavy debt; do not copy that shape for new docs.

## Start here

- Human: [`human/overview.md`](human/overview.md)
- Human architecture: [`human/architecture/00-index.md`](human/architecture/00-index.md)
- Human verification: [`human/verification/00-index.md`](human/verification/00-index.md)
- Agent: [`llm/00-index.md`](llm/00-index.md)
- Agent verification: [`llm/verification/00-index.md`](llm/verification/00-index.md)
- Local LibreLane harden: [`human/architecture/hardening.md`](human/architecture/hardening.md) / [`llm/13-hardening-librelane.md`](llm/13-hardening-librelane.md)

## Prior art (separate)

[`llm/prior-art/tinydma-2c.md`](llm/prior-art/tinydma-2c.md) holds Andrew Kim's TinyDMA-2C (TT 296) reference material. It is **not** this project's architecture. Anything drawn from it must be attributed explicitly.

## External handwritten notes

Obsidian vault (read-only from this repo):

`C:\Users\lahnb\Documents\Obsidian Vault\Projects\Tiny Tapeout\`

Do not edit vault notes via this project workflow. Promote durable decisions into `docs/` instead.

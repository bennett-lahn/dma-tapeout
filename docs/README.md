# Project Documentation

Split into two audiences:

| Tree | Audience | Style |
|---|---|---|
| `human/` | Humans | Condensed summaries |
| `llm/` | AI agents / future sessions | Verbose, organized context |
| `datasheets/` | Both | Manufacturer PDFs plus converted markdown (`pdfs/`, `md/`) |

## Start here

- Human: [`human/overview.md`](human/overview.md)
- Human architecture: [`human/architecture/00-index.md`](human/architecture/00-index.md)
- Post-V1 features: [`human/architecture/post-v1.md`](human/architecture/post-v1.md) / [`llm/10-post-v1-features.md`](llm/10-post-v1-features.md)
- Agent: [`llm/00-index.md`](llm/00-index.md)

## Prior art (separate)

[`llm/prior-art/tinydma-2c.md`](llm/prior-art/tinydma-2c.md) holds Andrew Kim's TinyDMA-2C (TT 296) reference material. It is **not** this project's architecture. Anything drawn from it must be attributed explicitly.

## External handwritten notes

Obsidian vault (read-only from this repo):

`C:\Users\lahnb\Documents\Obsidian Vault\Projects\Tiny Tapeout\`

Do not edit vault notes via this project workflow. Promote durable decisions into `docs/` instead.

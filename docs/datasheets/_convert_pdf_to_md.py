#!/usr/bin/env python3
"""Wrap pdftotext -layout output as page-fenced markdown for agent search."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_txt", type=Path)
    parser.add_argument("out_md", type=Path)
    parser.add_argument("pdf_rel")
    args = parser.parse_args()

    raw = args.raw_txt.read_text(encoding="utf-8", errors="replace")
    pages = raw.split("\f")
    fence = "`" * 3

    out: list[str] = [
        f"# {args.out_md.stem} Datasheet (converted)",
        "",
        f"Source PDF: `{args.pdf_rel}`",
        "",
        "Converted with poppler-utils `pdftotext -layout`. "
        "See `docs/datasheets/README.md`.",
        "",
        "> Machine-extracted text. Tables/figures may be misaligned; "
        "PDF remains authoritative.",
        "",
    ]

    for i, page in enumerate(pages, 1):
        text = page.strip("\n")
        if not text.strip():
            continue
        out.extend(
            [
                f"## Page {i}",
                "",
                f"{fence}text",
                text,
                fence,
                "",
            ]
        )

    args.out_md.write_text("\n".join(out), encoding="utf-8")
    print(f"wrote {args.out_md} pages={len(pages)} bytes={args.out_md.stat().st_size}")


if __name__ == "__main__":
    main()

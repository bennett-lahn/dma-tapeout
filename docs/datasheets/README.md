# Datasheets

Manufacturer source PDFs and LLM-searchable markdown conversions for devices this project talks to.

## Layout

| Path | Contents |
|---|---|
| `pdfs/` | Raw manufacturer PDFs (source of truth for figures, timing, legal revision) |
| `md/` | Text/markdown extracted for search and agent context |
| `_convert_pdf_to_md.py` | Helper that wraps `pdftotext` output into page-fenced markdown |

Current devices / guides:

- `pdfs/APS6404L_3SQR.pdf` - AP Memory APS6404L-3SQR QSPI PSRAM (Rev 2.3)
- `md/APS6404L_3SQR.md` - converted text dump
- `pdfs/W25Q128JV.pdf` - Winbond W25Q128JV 128 M-bit Serial Flash (Rev I, 2021-08-23; renamed from vendor `W25Q128JV RevI 08232021 Plus.pdf`)
- `md/W25Q128JV.md` - converted text dump
- `pdfs/Using_QSPI_TinyTapeout.pdf` - Tiny Tapeout "Configuring and flashing the QSPI Pmod" guide (may be image-only)
- `md/Using_QSPI_TinyTapeout.md` - transcribed notes + **code catalog** (ETR pin map, `PIOSPI`, PSRAM/flash SPI patterns) for firmware groundwork

Prefer the PDF when a table or timing diagram must be exact. Prefer the markdown when grepping opcodes, limits, or section text in agent workflows.

## Conversion process (WSL + poppler-utils)

Use this when adding a new PDF or refreshing an existing conversion. Run from the repo root in WSL.

### 1. Install tools (once per WSL distro)

```bash
sudo apt-get update
sudo apt-get install -y poppler-utils
```

Verify:

```bash
pdfinfo -v
pdftotext -v
```

`poppler-utils` provides `pdfinfo`, `pdftotext`, and `pdftoppm`. No other converter is required for this repo's workflow.

### 2. Place the PDF

```bash
mkdir -p docs/datasheets/pdfs docs/datasheets/md
# copy manufacturer file into pdfs/, keep the vendor filename when possible
```

### 3. Inspect, extract, wrap as markdown

```bash
# Example: PSRAM. For flash use PDF=docs/datasheets/pdfs/W25Q128JV.pdf
PDF=docs/datasheets/pdfs/APS6404L_3SQR.pdf
BASE="$(basename "$PDF" .pdf)"
RAW="/tmp/${BASE}.raw.txt"

pdfinfo "$PDF"
pdftotext -layout "$PDF" "$RAW"
python3 docs/datasheets/_convert_pdf_to_md.py \
  "$RAW" \
  "docs/datasheets/md/${BASE}.md" \
  "docs/datasheets/pdfs/${BASE}.pdf"
rm -f "$RAW"
```

Prefer PDF filenames without spaces (rename vendor dumps if needed) so shell conversion stays reliable.

`-layout` preserves column-ish spacing better than the default stream order for command tables.

### 4. After conversion

- Point `docs/llm/09-references.md` (and any device-specific LLM doc) at both `pdfs/` and `md/`
- Do not paste long datasheet body text into `docs/human/`
- If command opcodes or timing limits change the architecture, update `docs/llm/05-qspi-psram.md` and the decision/open-question docs

### Fallback if apt/poppler fails

If `poppler-utils` cannot be installed, try (in order):

1. Already-installed `pdftotext` elsewhere on PATH
2. `python3` + `pypdf` / `pymupdf` text extraction into a `.raw.txt`, then the same `_convert_pdf_to_md.py` wrap
3. Manual copy of critical tables into `docs/llm/05-qspi-psram.md` only (last resort; still keep the PDF in `pdfs/`)

Never invent opcodes from memory when a PDF is present; re-extract or read the PDF.

## Related project docs

- QSPI engine / command requirements: `docs/llm/05-qspi-psram.md`
- References index: `docs/llm/09-references.md`
- LLM reading order: `docs/llm/00-index.md`

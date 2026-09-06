"""Dump / peek / poke / decode TCD chains over chunked QPI.

All reads and writes go through `Psram.read` / `Psram.write` so CE# pulses stay
under tCEM (max CE# low time). Do not dump a multi-kilobyte span in one
unchunked transaction.

Debug APIs require a `DmaController` that already holds BUS_GNT (or rst_n=0);
they do not drive uio without grant (D26 bus keeper).

Chain interpretation (what a chain should have copied) is not here: the golden
oracle lives on the host PC, not on the MCU. These helpers only report what the
devices currently hold.
"""

from .constants import PTR_MAX
from .dma import DmaError
from .tcd import TCD_BYTES, decode_tcd, format_bytes, format_tcd, validate_tcd

# NEXT is followed through A[22:0]; ptr[23] is don't-care (D35).
ADDR_MAX = PTR_MAX


def _require_grant(dma):
    if dma is None:
        raise DmaError("debug QSPI requires a DmaController with BUS_GNT=1 or rst_n=0")
    if not dma.bus_gnt and not dma.rst_n_low:
        raise DmaError("debug QSPI requires BUS_GNT=1 or rst_n=0")


def peek(dma, psram, cs, addr, n=1):
    _require_grant(dma)
    return psram.read(cs, addr, n)


def poke(dma, psram, cs, addr, data):
    _require_grant(dma)
    psram.write(cs, addr, data)


def dump(dma, psram, cs, addr, length, width=16):
    """Print hex lines for [addr, addr+length) on *cs* (device 0 or 1)."""
    _require_grant(dma)
    data = psram.read(cs, addr, length)
    lines = []
    offset = 0
    while offset < len(data):
        chunk = data[offset : offset + width]
        line = "dev%d 0x%06X  %s" % (cs, addr + offset, format_bytes(chunk))
        lines.append(line)
        print(line)
        offset += width
    return data


def decode_chain(dma, psram, head_addr=0, head_dev=0, max_nodes=64):
    """Fetch 11-byte records following NEXT_*; stop on QUIT, cycle, or max_nodes.

    NEXT is masked to ADDR_MAX (A[22:0]; ptr[23] don't-care, D35), matching the
    host oracle. A validate_tcd error is printed and stops the walk.
    """
    _require_grant(dma)
    device = head_dev
    addr = head_addr & ADDR_MAX
    seen = []
    visited = set()
    for _ in range(max_nodes):
        key = (device, addr)
        if key in visited:
            print("cycle at %d:0x%06X" % (device, addr))
            break
        visited.add(key)
        raw = psram.read(device, addr, TCD_BYTES)
        tcd = decode_tcd(raw)
        record = (device, addr, raw, tcd)
        seen.append(record)
        raw_next = tcd.next_tcd
        masked_next = raw_next & ADDR_MAX
        line = "%d:0x%06X  [%s]  %s" % (
            device,
            addr,
            format_bytes(raw),
            format_tcd(tcd),
        )
        if raw_next != masked_next:
            line += " next_raw=0x%06X next_masked=0x%06X" % (raw_next, masked_next)
        print(line)
        if tcd.quit:
            break
        try:
            validate_tcd(tcd)
        except Exception as error:
            print("validate: %s" % error)
            break
        device, addr = tcd.next_device, masked_next
    return seen

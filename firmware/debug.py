"""Dump / peek / poke / decode TCD chains over chunked QPI.

All reads and writes go through `Psram.read` / `Psram.write` so CE# pulses stay
under tCEM. Do not dump a multi-kilobyte span in one unchunked transaction.
"""

from .tcd import TCD_BYTES, decode_tcd, format_bytes, format_tcd, validate_tcd


def peek(psram, cs, addr, n=1):
    return psram.read(cs, addr, n)


def poke(psram, cs, addr, data):
    psram.write(cs, addr, data)


def dump(psram, cs, addr, length, width=16):
    """Print hex lines for [addr, addr+length) on *cs* (device 0 or 1)."""
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


def decode_chain(psram, head_addr=0, head_dev=0, max_nodes=64):
    """Fetch 11-byte records following NEXT_*; stop on QUIT or max_nodes."""
    device = head_dev
    addr = head_addr
    seen = []
    for _ in range(max_nodes):
        raw = psram.read(device, addr, TCD_BYTES)
        tcd = decode_tcd(raw)
        record = (device, addr, raw, tcd)
        seen.append(record)
        line = "%d:0x%06X  [%s]  %s" % (
            device,
            addr,
            format_bytes(raw),
            format_tcd(tcd),
        )
        print(line)
        if tcd.quit:
            break
        try:
            validate_tcd(tcd)
        except Exception:
            break
        device, addr = tcd.next_device, tcd.next_tcd
    return seen

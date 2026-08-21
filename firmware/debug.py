"""Dump / peek / poke / decode TCD chains over chunked QPI.

All reads and writes go through `Psram.read` / `Psram.write` so CE# pulses stay
under tCEM (max CE# low). Do not dump a multi-kilobyte span in one unchunked
transaction.

Debug APIs require a Host that already holds BUS_GNT (or rst_n=0). They do
not drive uio without grant (D26).
"""

from .asic import HostError
from .chain import ADDR_MAX
from .tcd import TCD_BYTES, decode_tcd, format_bytes, format_tcd, validate_tcd


def _require_grant(host):
    if host is None:
        raise HostError("debug QSPI requires a Host with BUS_GNT=1 or rst_n=0")
    if not host.bus_gnt and not host.rst_n_low:
        raise HostError("debug QSPI requires BUS_GNT=1 or rst_n=0")


def peek(host, psram, cs, addr, n=1):
    _require_grant(host)
    return psram.read(cs, addr, n)


def poke(host, psram, cs, addr, data):
    _require_grant(host)
    psram.write(cs, addr, data)


def dump(host, psram, cs, addr, length, width=16):
    """Print hex lines for [addr, addr+length) on *cs* (device 0 or 1)."""
    _require_grant(host)
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


def decode_chain(host, psram, head_addr=0, head_dev=0, max_nodes=64):
    """Fetch 11-byte records following NEXT_*; stop on QUIT, cycle, or max_nodes.

    NEXT is masked to ADDR_MAX (A[22:0]; ptr[23] don't-care, D35), matching
    interpret_chain. A validate_tcd error is printed and stops the walk.
    """
    _require_grant(host)
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

"""Thin helpers to assemble arbitrary TCD chains on a MemoryImage.

Copied encode/decode/validate in tcd.py remain the 11-byte contract. This
module is the REPL/script layer so callers do not hand-place descriptor bytes.

Head convention: the first TCD (or a QUIT TCD for an empty run) lives at
PSRAM 0 address 0. Address 0 is a valid link, not a terminator.
"""

from .chain import HEAD_ADDRESS, HEAD_DEVICE, MemoryImage
from .tcd import TCD_BYTES, Tcd, decode_tcd, encode_tcd

try:
    from dataclasses import replace
except ImportError:  # MicroPython UF2 without stdlib dataclasses
    from ._compat import replace


def new_image(fill=None):
    """Return an empty sparse MemoryImage (optional fill byte for undefined reads)."""
    return MemoryImage(fill=fill)


def place_tcd(mem, device, addr, tcd):
    """Encode *tcd*, span-check via MemoryImage.write, and store 11 bytes."""
    raw = encode_tcd(tcd)
    mem.write(device, addr, raw)
    return raw


def place_bytes(mem, device, addr, data):
    """Write payload bytes at (device, addr). Span-checked; no 8 MB allocation."""
    payload = bytes(data)
    mem.write(device, addr, payload)
    return payload


def add_copy(
    mem,
    tcd_device,
    tcd_addr,
    src_ptr,
    dest_ptr,
    length,
    src_device=0,
    dest_device=0,
    next_tcd=0,
    next_device=0,
):
    """Place one data TCD with SRC/DEST/LEN/NEXT and device bits in CTRL_FLAGS."""
    tcd = Tcd(
        src_ptr=src_ptr,
        dest_ptr=dest_ptr,
        transfer_len=length,
        next_tcd=next_tcd,
        quit=False,
        src_device=src_device,
        dest_device=dest_device,
        next_device=next_device,
    )
    place_tcd(mem, tcd_device, tcd_addr, tcd)
    return tcd


def add_quit(mem, device, addr):
    """Place a QUIT=1 terminator TCD (no copy; does not follow its own NEXT)."""
    tcd = Tcd(quit=True)
    place_tcd(mem, device, addr, tcd)
    return tcd


def link(mem, prev_device, prev_addr, next_device, next_addr):
    """Fill NEXT_TCD / NEXT_DEVICE on the descriptor already stored at prev_*."""
    raw = mem.read(prev_device, prev_addr, TCD_BYTES)
    updated = replace(decode_tcd(raw), next_tcd=next_addr, next_device=next_device)
    place_tcd(mem, prev_device, prev_addr, updated)
    return updated


def place_head_quit(mem):
    """Empty run: QUIT TCD at the fixed head (PSRAM 0, address 0)."""
    return add_quit(mem, HEAD_DEVICE, HEAD_ADDRESS)

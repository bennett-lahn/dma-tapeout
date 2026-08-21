"""Grant, enter QPI, install a MemoryImage, START, wait idle, dump, compare.

Golden expected memory is `interpret_chain(initial_memory).final_memory`.
Install/dump use chunked QPI (`psram.Psram.write` / `read`); never a single
unchunked CE# dump. After START, `wait_idle_after_start` treats a missed DONE
low pulse as already idle (fast completion), not a timeout.

`bring_up=False` is QPI-only: devices are already in QPI and this path must
not issue SPI Enter Quad `0x35`. Empty dest (no expected writes) is not a
PASS; dest that already matched before START cannot prove DMA ran.
"""

from .chain import DEFAULT_DMA_BUF_DEPTH, interpret_chain


class RunError(Exception):
    """Install/dump compare failed, empty dest check, or host sequencing failed."""


def coalesced_spans(mem):
    """Yield (device, start, bytes) for each contiguous defined run."""
    snapshot = mem.snapshot()
    for device in snapshot:
        addrs = sorted(snapshot[device])
        if not addrs:
            continue
        run_start = addrs[0]
        run_bytes = [snapshot[device][addrs[0]]]
        prev = addrs[0]
        for addr in addrs[1:]:
            if addr == prev + 1:
                run_bytes.append(snapshot[device][addr])
            else:
                yield device, run_start, bytes(run_bytes)
                run_start = addr
                run_bytes = [snapshot[device][addr]]
            prev = addr
        yield device, run_start, bytes(run_bytes)


def dest_extents(result):
    """Coalesce `expected_writes` into (device, start, length) dump windows."""
    writes = result.expected_writes
    if not writes:
        return ()
    keys = sorted(writes)
    extents = []
    device0, addr0 = keys[0]
    length = 1
    for device, addr in keys[1:]:
        if device == device0 and addr == addr0 + length:
            length += 1
        else:
            extents.append((device0, addr0, length))
            device0, addr0 = device, addr
            length = 1
    extents.append((device0, addr0, length))
    return tuple(extents)


def install_image(psram, mem):
    """QPI-write every defined byte of *mem* (chunked per tCEM)."""
    for device, addr, data in coalesced_spans(mem):
        psram.write(device, addr, data)


def dump_extents(psram, extents):
    """QPI-read dest extents into {(device, addr): byte}."""
    dumped = {}
    for device, addr, length in extents:
        data = psram.read(device, addr, length)
        for offset, value in enumerate(data):
            dumped[(device, addr + offset)] = value
    return dumped


def compare_final(result, dumped):
    """Return a list of (device, addr, expected, got) mismatches."""
    mismatches = []
    for (device, addr), expected in sorted(result.expected_writes.items()):
        got = dumped.get((device, addr))
        if got != expected:
            mismatches.append((device, addr, expected, got))
    return mismatches


def _format_got(got):
    if got is None:
        return "None"
    return "0x%02X" % got


def run_chain(
    host,
    psram,
    mem,
    exit_qpi=False,
    timeout_ms=5000,
    bring_up=True,
    dma_buf_depth=DEFAULT_DMA_BUF_DEPTH,
    allow_empty_dest=False,
):
    """Program-start-compare loop. Returns (ok, chain_result, mismatches).

    Raises RunError on dest mismatch, empty dest (unless allow_empty_dest),
    or dest that already matched the oracle before START.
    """
    if getattr(psram, "host", None) is None:
        psram.host = host
    result = interpret_chain(mem, dma_buf_depth=dma_buf_depth)
    extents = dest_extents(result)
    held = False
    try:
        host.request_bus()
        held = True
        if bring_up:
            psram.bring_up_both()
        install_image(psram, mem)
        if not result.expected_writes:
            if not allow_empty_dest:
                raise RunError(
                    "no dest writes to check (QUIT-only or empty chain); "
                    "pass allow_empty_dest=True to skip compare"
                )
            if exit_qpi:
                psram.exit_qpi_both()
            host.release_bus()
            held = False
            return (False, result, [])
        pre_start = dump_extents(psram, extents)
        if pre_start == result.expected_writes:
            for device, addr, length in extents:
                psram.write(device, addr, bytes(length))
            pre_start = dump_extents(psram, extents)
        host.release_bus()
        held = False
        host.pulse_start()
        host.wait_idle_after_start(timeout_ms)
        host.request_bus()
        held = True
        dumped = dump_extents(psram, extents)
        mismatches = compare_final(result, dumped)
        if mismatches:
            parts = [
                "dev%d 0x%06X expected=0x%02X got=%s"
                % (device, addr, exp, _format_got(got))
                for device, addr, exp, got in mismatches
            ]
            raise RunError("dest mismatch: " + "; ".join(parts))
        if dumped == pre_start:
            raise RunError(
                "dest already matched expected bytes before START; "
                "cannot prove DMA ran (missed START would also match)"
            )
        if exit_qpi:
            psram.exit_qpi_both()
        host.release_bus()
        held = False
        return (True, result, [])
    finally:
        if held:
            try:
                host.release_bus()
            except Exception:
                host.hiz()
                host.zero_ui_in()

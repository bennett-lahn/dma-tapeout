"""Grant, enter QPI, install a MemoryImage, START, wait idle, dump, compare.

Golden expected memory is `interpret_chain(initial_memory).final_memory`.
Install/dump use chunked QPI (`psram.Psram.write` / `read`); never a single
unchunked CE# dump. After START, `wait_idle_after_start` treats a missed DONE
low pulse as already idle (fast completion), not a timeout.
"""

from .chain import interpret_chain


class RunError(Exception):
    """Install/dump compare failed, or host sequencing failed."""


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


def run_chain(host, psram, mem, exit_qpi=False, timeout_ms=5000, bring_up=True):
    """Program-start-compare loop. Returns (ok, chain_result, mismatches)."""
    result = interpret_chain(mem)
    host.request_bus()
    if bring_up:
        psram.bring_up_both()
    else:
        psram.enter_qpi_both()
    install_image(psram, mem)
    host.release_bus()
    host.pulse_start()
    host.wait_idle_after_start(timeout_ms)
    host.request_bus()
    extents = dest_extents(result)
    dumped = dump_extents(psram, extents)
    mismatches = compare_final(result, dumped)
    if exit_qpi:
        psram.exit_qpi_both()
    host.release_bus()
    return (not mismatches, result, mismatches)

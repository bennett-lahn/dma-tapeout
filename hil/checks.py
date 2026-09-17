"""Succinct HIL assertions plus the diagnostics a failure needs to be actionable.

A HIL test should read as five lines - head TCD peek, dest writes, guard
bytes, descriptors, host port - and still print enough on failure to skip a
debug session. Each
`check_*` raises `AssertionError` with a formatted table instead of dumping two
raw dicts, and each `format_*` builds one reproduction or log excerpt.

The oracle side comes from `test.reference` (`interpret_chain` expected writes,
`format_log` transaction rendering); the observed side is always a `Session`
read-back, so these checks work unchanged against `--target=loopback`,
`--target=fpga`, or `--target=asic`. Host-only module: `firmware/` must never
import it.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_TEST = _REPO / "test"
for _path in (str(_REPO), str(_TEST)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from firmware.board.pins import OE_HIZ  # noqa: E402
from test.reference.chain import (  # noqa: E402
    ADDR_MAX,
    WRITE_KINDS,
    format_log,
    interpret_chain,
)
from test.reference.constants import (  # noqa: E402
    HEAD_ADDRESS,
    HEAD_DEVICE,
    TCD_BYTES,
)
from test.reference.scoreboard import (  # noqa: E402
    CLASS_MISSING_WRITE,
    CLASS_UNEXPECTED_WRITE,
    CLASS_WRONG_DATA,
    MISSING,
)
from test.reference.tcd import format_bytes  # noqa: E402

# Failure tables stay readable in a pytest tail; the count line still reports
# every differing byte, so truncation never hides the true failure size.
MAX_REPORTED_ROWS = 16

# `probe_qpi_read_phase` scratch, clear of the head TCD and of every directed
# case's data. Every nibble is distinct, so the observed stream names the exact
# permutation: a shift, a dropped sample, and a duplicated sample all look
# different. A pattern of complement pairs (A/5, 3/C) cannot do that, because
# bit-reverse, bitwise-NOT, and lane-swap all produce the same answer on it.
#
# Three bytes, read back as four: the frame ceiling is MCU_QPI_READ_FRAME_MAX
# (4 data bytes, once the dummy words and the realign spare are subtracted from
# the joined RX FIFO), so this is the longest pattern that still leaves spare
# nibbles for a residual shift to show up in.
PROBE_DEVICE = 0
PROBE_ADDRESS = 0x001000
PROBE_PATTERN = b"\x12\x34\x56"

# Bytes either side of a destination extent that an out-of-bounds write would
# land in. 16 covers a whole overrun chunk at any legal DMA_BUF_DEPTH (N, the
# on-chip scratch depth: 5 at tapeout, 8 maximum).
DEFAULT_GUARD_LEN = 16

DEFAULT_REPRO_TARGET = "loopback"

# Table cell for "the oracle expects nothing here" (an unexpected write), kept
# distinct from the scoreboard's `<missing>` ("expected, never observed").
NO_EXPECTATION = "<none>"


# --- shared span helpers -------------------------------------------------


def collapse_extents(keys):
    """Collapse `(device, address)` keys into contiguous `(device, addr, len)`.

    Sorted by device then address, so a read-back of a scattered expected-write
    map costs one QPI burst per contiguous run instead of one per byte.
    """
    by_device: dict[int, list[int]] = {}
    for device, address in keys:
        by_device.setdefault(int(device), []).append(int(address))
    extents: list[tuple[int, int, int]] = []
    for device, addresses in sorted(by_device.items()):
        addresses = sorted(set(addresses))
        start = prev = addresses[0]
        for address in addresses[1:]:
            if address != prev + 1:
                extents.append((device, start, prev - start + 1))
                start = address
            prev = address
        extents.append((device, start, prev - start + 1))
    return tuple(extents)


def contiguous_spans(memory):
    """Collapse a `MemoryImage`'s defined bytes into `(device, addr, data)` writes."""
    spans: list[tuple[int, int, bytes]] = []
    for device in memory.devices:
        addresses = sorted(memory.defined_addresses(device))
        if not addresses:
            continue
        start = prev = addresses[0]
        for address in addresses[1:]:
            if address != prev + 1:
                spans.append(
                    (device, start, memory.read(device, start, prev - start + 1))
                )
                start = address
            prev = address
        spans.append((device, start, memory.read(device, start, prev - start + 1)))
    return tuple(spans)


# --- formatting internals -----------------------------------------------


def format_address(address):
    """Render a 23-bit PSRAM address as `0xAABBCC`."""
    return "0x%06X" % (int(address) & ADDR_MAX)


def format_byte(value, absent=MISSING):
    """Render one byte as `0xNN`, or `absent` when there is no value."""
    return absent if value is None else "0x%02X" % (int(value) & 0xFF)


def format_table(header, rows, *, indent="  "):
    """Render `rows` under `header` as a fixed-width text table."""
    cells = [[str(cell) for cell in row] for row in rows]
    widths = [
        max([len(str(header[column]))] + [len(row[column]) for row in cells])
        for column in range(len(header))
    ]
    lines = [
        indent + "  ".join(str(name).ljust(widths[i]) for i, name in enumerate(header))
    ]
    for row in cells:
        lines.append(
            indent + "  ".join(row[i].ljust(widths[i]) for i in range(len(row)))
        )
    return lines


def _truncate(rows, max_rows):
    if max_rows is None or len(rows) <= max_rows:
        return rows, 0
    return rows[:max_rows], len(rows) - max_rows


# --- checks --------------------------------------------------------------


def check_dest_writes(
    expected_writes,
    dumped,
    *,
    label="dest writes",
    max_rows=MAX_REPORTED_ROWS,
):
    """Compare oracle expected writes against a destination read-back.

    Args:
        expected_writes: `{(device, address): byte}` the reference model says a
            correct DMA must produce (`interpret_chain(...).expected_writes`).
        dumped: `{(device, address): byte}` from `Session.read_spans`.

    Raises:
        AssertionError: any byte differs, is missing from the dump, or appears
            in the dump without an expectation. The message classifies each row
            as `missing_write`, `wrong_data`, or `unexpected_write`.
    """
    expected = dict(expected_writes)
    observed = dict(dumped)
    rows = []
    for key in sorted(set(expected) | set(observed)):
        want = expected.get(key)
        have = observed.get(key)
        if want == have:
            continue
        if want is None:
            kind = CLASS_UNEXPECTED_WRITE
        elif have is None:
            kind = CLASS_MISSING_WRITE
        else:
            kind = CLASS_WRONG_DATA
        device, address = key
        rows.append(
            (
                kind,
                device,
                format_address(address),
                format_byte(want, NO_EXPECTATION),
                format_byte(have),
            )
        )
    if not rows:
        return

    shown, hidden = _truncate(rows, max_rows)
    lines = [
        "%s: %d of %d byte(s) differ (%d expected, %d dumped)"
        % (
            label,
            len(rows),
            max(len(expected), len(observed)),
            len(expected),
            len(observed),
        )
    ]
    lines.extend(format_table(("class", "dev", "address", "expected", "got"), shown))
    if hidden:
        lines.append("  ... %d more differing byte(s)" % hidden)
    raise AssertionError("\n".join(lines))


def check_guard_bytes(
    initial_mem,
    extents,
    session,
    guard_len=DEFAULT_GUARD_LEN,
    *,
    skip_undefined=True,
):
    """Verify nothing wrote just outside the destination extents.

    Reads `guard_len` bytes below and above every extent and compares them with
    the image the host installed. Addresses inside any extent are excluded,
    since those bytes are supposed to change.

    Args:
        initial_mem: `MemoryImage` as installed before START.
        extents: `(device, address, length)` destination windows.
        session: brought-up `Session` for the read-back.
        guard_len: guard bytes to inspect either side of each extent.
        skip_undefined: when True (default) only bytes the host actually wrote
            are checked. Undefined bytes have no trustworthy baseline on real
            PSRAM, so they are compared against `initial_mem.fill` only when
            this is False and a fill byte is configured.

    Returns:
        dict: `{(device, address): expected_byte}` for every guard byte checked,
        so a caller can assert the check was not vacuous.

    Raises:
        AssertionError: a guard byte changed, meaning DMA wrote out of bounds.
    """
    windows = tuple((int(d), int(a), int(n)) for d, a, n in extents)
    if not windows or guard_len <= 0:
        return {}

    covered = set()
    for device, address, length in windows:
        covered.update((device, address + offset) for offset in range(length))

    wanted: dict[tuple[int, int], int] = {}
    for device, address, length in windows:
        for start in (address - guard_len, address + length):
            low = max(start, 0)
            high = min(start + guard_len - 1, ADDR_MAX)
            for guard_addr in range(low, high + 1):
                key = (device, guard_addr)
                if key in covered or key in wanted:
                    continue
                value = _image_byte(initial_mem, device, guard_addr, skip_undefined)
                if value is not None:
                    wanted[key] = value
    if not wanted:
        return {}

    observed = session.read_spans(collapse_extents(wanted))
    rows = []
    for key in sorted(wanted):
        want = wanted[key]
        have = observed.get(key)
        if want == have:
            continue
        device, address = key
        rows.append((device, format_address(address), format_byte(want), format_byte(have)))
    if not rows:
        return wanted

    shown, hidden = _truncate(rows, MAX_REPORTED_ROWS)
    lines = [
        "guard bytes: %d of %d byte(s) outside the destination extents changed "
        "(out-of-bounds write); guard_len=%d" % (len(rows), len(wanted), guard_len)
    ]
    lines.extend(format_table(("dev", "address", "expected", "got"), shown))
    if hidden:
        lines.append("  ... %d more corrupted guard byte(s)" % hidden)
    raise AssertionError("\n".join(lines))


def _image_byte(image, device, address, skip_undefined):
    """Return the installed byte at `address`, or None when it has no baseline."""
    if image.is_defined(device, address):
        return image.byte(device, address)
    if skip_undefined:
        return None
    fill = getattr(image, "fill", None)
    return None if fill is None else fill & 0xFF


def _nibbles(data):
    """Return *data* as the wire nibble sequence, high nibble of each byte first."""
    out = []
    for value in bytes(data):
        out.append((value >> 4) & 0x0F)
        out.append(value & 0x0F)
    return out


def _nibble_source_map(want, have):
    """Render each observed nibble as the index of the written nibble it equals.

    Only meaningful when `want` has no repeated nibble; then `0 1 2 3 ...` is a
    clean read, `- 0 1 2 ...` is one sample of lag, and a repeat such as
    `2 2` is a doubled sample.
    """
    index = {value: position for position, value in enumerate(want)}
    return " ".join(
        "%d" % index[value] if value in index else "-" for value in have
    )


def probe_qpi_read_phase(
    session, *, device=PROBE_DEVICE, address=PROBE_ADDRESS, pattern=PROBE_PATTERN
):
    """Write a distinct-nibble pattern and report how it comes back.

    A QPI byte is two SCK on the wire, high nibble first, so a capture-point
    error moves the whole nibble stream rather than scrambling bytes. Two reads
    are taken of the same bytes:

    * one `0xEB` frame covering the pattern plus a trailing byte, via
      `qpi_probe_read`, which shows the uninterrupted nibble stream;
    * the ordinary `read_spans` path, which chunks at `MCU_QPI_PAYLOAD_MAX`
      (the FIFO payload ceiling).

    Comparing the two separates the two candidate faults. If they disagree, the
    capture depends on frame length and the fault is in the read path. If they
    agree but differ from what was written, the bytes in the array really are
    wrong and the fault is in the write path.

    `pattern` must have no repeated nibble, or the source map is ambiguous.

    Returns:
        dict: `shift` in nibbles (0 = aligned, None = no shift explains it),
        `pattern`, `framed` (single frame), and `chunked` (ordinary read path).

    Raises:
        AssertionError: the pattern did not come back intact.
    """
    pattern = bytes(pattern)
    want = _nibbles(pattern)
    if len(set(want)) != len(want):
        raise ValueError("probe pattern %r repeats a nibble" % (pattern,))

    session.write_spans(((device, address, pattern),))
    framed = bytes(session.qpi_probe_read(device, address, len(pattern) + 1))
    observed = session.read_spans(((device, address, len(pattern)),))
    chunked = bytes(
        observed.get((device, address + offset), 0) for offset in range(len(pattern))
    )

    have = _nibbles(framed)
    shift = None
    for candidate in range(len(have) - len(want) + 1):
        if have[candidate : candidate + len(want)] == want:
            shift = candidate
            break

    report = {
        "shift": shift,
        "pattern": pattern,
        "framed": framed,
        "chunked": chunked,
    }
    if shift == 0 and chunked == pattern:
        return report

    lines = [
        "QPI read phase probe at %d:%s" % (device, format_address(address)),
        "wrote            %s" % format_bytes(pattern),
        "one %d-byte frame %s" % (len(framed), format_bytes(framed)),
        "chunked read     %s" % format_bytes(chunked),
        "nibble sources   %s   (index into the written nibbles, - = no match)"
        % _nibble_source_map(want, have),
    ]
    if shift is not None and shift > 0:
        lines.append(
            "the whole pattern appears %d nibble(s) late, so the capture point "
            "is %d SCK off (D16 read phase, tACLK / read data valid after "
            "falling SCK)" % (shift, shift)
        )
    elif chunked[: len(pattern)] != framed[: len(pattern)]:
        lines.append(
            "the two reads disagree, so what comes back depends on the frame "
            "length: the fault is in the read path, not in the stored bytes"
        )
    else:
        lines.append(
            "both reads agree and both differ from what was written, so the "
            "stored bytes really are wrong: the fault is in the write path"
        )
    raise AssertionError("\n".join(lines))


def _nibble_lag_hint(want, have):
    """Name the one-SCK read misalignment when the read-back shows it.

    Each byte is two SCK on the wire, high nibble first. If the capture point
    is one clock late, the low nibble of every observed byte holds the high
    nibble of the byte that was really installed, and the installed low
    nibbles are never clocked out at all. Requires a non-zero installed high
    nibble so an all-zero image cannot match by accident.
    """
    if len(want) != len(have) or not any(value >> 4 for value in want):
        return None
    if not all((h & 0x0F) == (w >> 4) for w, h in zip(want, have)):
        return None
    return (
        "every observed low nibble equals the installed high nibble: the QPI "
        "read is capturing one SCK late, so each byte is the turnaround "
        "sample followed by the real high nibble. Run probe_qpi_read_phase"
    )


def _read_head(session):
    """QPI-read the fixed head TCD as plain bytes; undefined reads as 0."""
    observed = session.read_spans(((HEAD_DEVICE, HEAD_ADDRESS, TCD_BYTES),))
    return bytes(
        observed.get((HEAD_DEVICE, HEAD_ADDRESS + offset), 0)
        for offset in range(TCD_BYTES)
    )


def check_head_tcd_installed(case, session):
    """QPI-read the fixed head TCD and require it to match the install image.

    Call after the last `write_spans` and before START. A mismatch means the
    MCU QPI install did not land: the device is still in SPI, the PIO write
    did not store, or the QPI read path is broken. Grant LEDs flashing is not
    enough; the 11 bytes must actually read back.

    An all-zero head is a `TRANSFER_LEN=0` self-pointing TCD without `QUIT`
    (D35). The QSPI engine is cycle-counted (no device ready handshake), so
    START still drops DONE, fetches those zeros, and never returns to idle.

    On mismatch the head is read a second time, with no write in between, to
    split two very different faults apart. See the `re-read` line in the
    failure text.
    """
    memory = _case_memory(case)
    want = bytes(memory.read(HEAD_DEVICE, HEAD_ADDRESS, TCD_BYTES))
    have = _read_head(session)
    if want == have:
        return

    lines = [
        "head TCD at %d:%s did not read back after QPI install (before START)"
        % (HEAD_DEVICE, format_address(HEAD_ADDRESS)),
        "installed  %s" % format_bytes(want),
        "read back  %s" % format_bytes(have),
    ]
    if have == bytes(TCD_BYTES):
        lines.append(
            "read-back is all zeros: START would fetch a no-op self-pointing "
            "TCD without QUIT and hang with DONE low (D35)"
        )

    again = _read_head(session)
    unstable = sum(1 for first, second in zip(have, again) if first != second)
    lines.append("re-read    %s" % format_bytes(again))
    if unstable == 0:
        lines.append(
            "re-read is identical, so the stored bytes are stable: a "
            "deterministic install or read-path fault, not array decay"
        )
    elif unstable * 4 <= len(have):
        lines.append(
            "re-read moves in %d of %d bytes, so the data is essentially "
            "stable. A couple of unstable samples at a phase boundary is a "
            "bus-turnaround or sampling-margin problem, not array decay"
            % (unstable, len(have))
        )
    else:
        lines.append(
            "re-read moves in %d of %d bytes with no write in between, so the "
            "array contents are changing on their own. Suspect blocked "
            "refresh: tCEM (max CE# low pulse width, 4 us extended grade) "
            "exceeded by host work inside the CE#-low window"
            % (unstable, len(have))
        )

    lag = _nibble_lag_hint(want, have)
    if lag is not None:
        lines.append(lag)

    lines.append(
        "PSRAM must be in QPI (Enter Quad 0x35) and the 11-byte head must be "
        "stored; the ASIC does not wait for a device handshake"
    )
    raise AssertionError("\n".join(lines))


def check_descriptors_intact(case, session, *, locations=None):
    """Verify every installed 11-byte TCD still holds its encoded bytes.

    The DMA engine only reads descriptors, so a changed descriptor slot means a
    destination pointer walked into the chain. Descriptor locations come from
    `case.descriptor_locations` when present (a `GeneratedChain`), otherwise
    from the oracle's fetch path over `case.initial_mem`.

    Raises:
        AssertionError: a descriptor slot no longer matches its encoded bytes.
    """
    memory = _case_memory(case)
    if locations is None:
        slots = _descriptor_locations(case, memory)
    else:
        slots = tuple((int(device), int(address)) for device, address in locations)
    if not slots:
        return

    observed = session.read_spans(
        tuple((device, address, TCD_BYTES) for device, address in slots)
    )
    rows = []
    for device, address in slots:
        want = bytes(memory.read(device, address, TCD_BYTES))
        have = bytes(
            observed.get((device, address + offset), 0) for offset in range(TCD_BYTES)
        )
        if want == have:
            continue
        offsets = [i for i in range(TCD_BYTES) if want[i] != have[i]]
        rows.append(
            (
                device,
                format_address(address),
                format_bytes(want),
                format_bytes(have),
                ",".join(str(i) for i in offsets),
            )
        )
    if not rows:
        return

    lines = [
        "descriptors: %d of %d installed TCD(s) were overwritten during the run"
        % (len(rows), len(slots))
    ]
    lines.extend(
        format_table(("dev", "address", "installed", "read back", "bad offsets"), rows)
    )
    raise AssertionError("\n".join(lines))


def _case_memory(case):
    """Return the installed `MemoryImage` for a HilCase or a GeneratedChain."""
    for name in ("initial_mem", "memory", "initial_memory"):
        image = getattr(case, name, None)
        if image is not None:
            return image
    raise TypeError(
        "%r has no initial_mem / memory image to check descriptors against"
        % (type(case).__name__,)
    )


def _descriptor_locations(case, memory):
    """Return deduplicated `(device, address)` descriptor slots in fetch order."""
    declared = getattr(case, "descriptor_locations", None)
    path = (
        tuple((int(d), int(a)) for d, a in declared)
        if declared
        else tuple(interpret_chain(memory).path)
    )
    seen = set()
    slots = []
    for entry in path:
        if entry not in seen:
            seen.add(entry)
            slots.append(entry)
    return tuple(slots)


def check_host_status(status, *, label="host status"):
    """Verify the host port is idle and the MCU is off the bus after a run.

    Expects DONE=1 (controller idle), BUS_GNT=0 (bus grant released),
    BUS_REQ=0 (no outstanding host bus request), rst_n released, and
    `uio_oe_pico` back at `OE_HIZ` (0x00) - the only legal MCU drive state
    while rst_n=1 and BUS_GNT=0 (D26).

    Raises:
        AssertionError: any of those signals is not in its idle state.
    """
    problems = []
    if status.get("done") != 1:
        problems.append("done=%r, expected 1 (controller idle)" % (status.get("done"),))
    if status.get("bus_gnt") != 0:
        problems.append(
            "bus_gnt=%r, expected 0 (bus grant released)" % (status.get("bus_gnt"),)
        )
    if status.get("bus_req") != 0:
        problems.append(
            "bus_req=%r, expected 0 (no host bus request)" % (status.get("bus_req"),)
        )
    if status.get("rst_n_low") is not False:
        problems.append(
            "rst_n_low=%r, expected False (rst_n released)" % (status.get("rst_n_low"),)
        )
    oe = status.get("oe")
    if oe != OE_HIZ:
        problems.append(
            "oe=%s, expected 0x%02X (MCU Hi-Z off the bus, D26)"
            % (format_byte(oe) if isinstance(oe, int) else repr(oe), OE_HIZ)
        )
    if problems:
        raise AssertionError(
            "\n".join(["%s: %d signal(s) not idle" % (label, len(problems))]
                      + ["  " + problem for problem in problems])
        )


# --- diagnostics ---------------------------------------------------------


def format_repro_banner(case_id, seed, target_name=DEFAULT_REPRO_TARGET, node_id=None):
    """Return the one-line command that replays this exact case and seed."""
    parts = [
        "REPRO: python -m hil",
        "--target=%s" % (target_name or DEFAULT_REPRO_TARGET),
        "--seed=%d" % int(seed),
    ]
    if case_id:
        parts.append("-k %s" % case_id)
    line = " ".join(parts)
    if node_id:
        line += "  # node: %s" % node_id
    return line


def format_windowed_mismatch(result, dumped, window=3):
    """Render the oracle transaction log windowed around the first bad byte.

    Locates the first expected write the dump disagrees with, finds the
    `DATA_WRITE` that produced it, and returns `format_log` over that record
    plus `window` records either side. Falls back to a plain note when there is
    no mismatch or no log to window.
    """
    transactions = tuple(getattr(result, "transactions", ()) or ())
    if not transactions:
        return "no transaction log available for windowed diagnostics"

    records = [txn.equality() for txn in transactions]
    expected = dict(getattr(result, "expected_writes", {}) or {})
    observed = dict(dumped)

    first_key = None
    for key, value in expected.items():
        if observed.get(key) != value:
            first_key = key
            break
    if first_key is None:
        for key in observed:
            if key not in expected:
                first_key = key
                break
    if first_key is None:
        return "no destination mismatch; %d transaction(s) in the oracle log" % len(
            records
        )

    index = _write_index(transactions, first_key)
    device, address = first_key
    header = (
        "first mismatch dev=%d addr=%s expected=%s got=%s; oracle log "
        % (
            device,
            format_address(address),
            format_byte(expected.get(first_key), NO_EXPECTATION),
            format_byte(observed.get(first_key)),
        )
    )
    if index is None:
        header += "(no write transaction covers that byte), full log:"
    else:
        header += "window +/-%d around #%d:" % (window, index)
    return header + "\n" + format_log(records, first=index, window=window)


def _write_index(transactions, key):
    """Return the log position of the first write covering `(device, address)`."""
    device, address = key
    for position, txn in enumerate(transactions):
        if txn.kind not in WRITE_KINDS or txn.device != device:
            continue
        if txn.address <= address < txn.address + txn.length:
            return position
    return None


__all__ = [
    "DEFAULT_GUARD_LEN",
    "MAX_REPORTED_ROWS",
    "check_descriptors_intact",
    "check_dest_writes",
    "check_head_tcd_installed",
    "check_guard_bytes",
    "check_host_status",
    "collapse_extents",
    "contiguous_spans",
    "format_address",
    "format_byte",
    "format_repro_banner",
    "format_table",
    "format_windowed_mismatch",
]

# Firmware Rules

This is the short firmware contract for programming the DMA. Detailed TCD behavior lives in [`blocks/tcd.md`](blocks/tcd.md), and pin-level behavior lives in [`blocks/host-interface.md`](blocks/host-interface.md).

## System I/O and bus ownership

1. Keep the MCU QSPI pins high-Z unless `BUS_GNT` is high.
2. To access either PSRAM or flash, assert `BUS_REQ`, wait for `BUS_GNT=1`, then enable the MCU QSPI drivers.
3. Before releasing the bus, finish the current transaction, drive every CE# high, make the MCU QSPI pins high-Z, then deassert `BUS_REQ`. Wait for `BUS_GNT=0` before asserting `START`.
4. Initialize both PSRAMs and leave them in QPI mode before `START`. The ASIC does not issue reset, Enter Quad, or Exit Quad commands.
5. Assert `START` only while `DONE=1` and `BUS_REQ=0`. Hold it long enough to cross the input synchronizer, then deassert it. A START edge while busy or while `BUS_REQ=1` is ignored and not queued.
6. `DONE=1` means the ASIC is idle. It does not grant MCU ownership of `uio`; firmware must still request and receive `BUS_GNT`.
7. A mid-run `BUS_REQ` pauses the DMA only after its current QPI transaction. To stop a runaway chain, assert `rst_n`; V1 has no soft abort.

The handoff rule is always release before seize. Driving the MCU and ASIC outputs at the same time causes bus contention and may damage pads.

## Writing TCDs

- Every run starts by fetching an 11-byte TCD at `0x000000` on PSRAM 0.
- Serialize TCDs explicitly into an 11-byte buffer. Do not write a native C structure or copy native MCU integers directly.
- Use this exact byte layout: offsets `0..2` are `SRC_PTR[23:16]`, `[15:8]`, `[7:0]`; offsets `3..5` are the same three bytes of `DEST_PTR`; offset `6` is `TRANSFER_LEN`; offsets `7..9` are the three bytes of `NEXT_TCD`; offset `10` is `CTRL_FLAGS`.
- The three 24-bit fields `SRC_PTR`, `DEST_PTR`, and `NEXT_TCD` are stored big-endian: most-significant byte first. For example, `0x123456` is written as `12 34 56`.
- MCU endianness does not affect payload data. Payload bytes are copied unchanged, with no byte swapping.
- `TRANSFER_LEN` and `CTRL_FLAGS` are single-byte fields. One data TCD can request at most 255 bytes; use another linked TCD for additional bytes.
- Set `CTRL_FLAGS.SRC_DEVICE`, `DEST_DEVICE`, and `NEXT_DEVICE` for the corresponding pointer. Device selection is not encoded in a pointer bit.
- Write reserved `CTRL_FLAGS[7:4]` bits as zero.
- `TRANSFER_LEN=0` is a no-op that follows `NEXT_TCD`; it does not end the chain.

## Terminating a transfer chain

Every finite transfer chain must end with a separate TCD whose `CTRL_FLAGS.QUIT` bit is set to `1`. The preceding data TCD must link to this quit TCD through `NEXT_TCD` and `NEXT_DEVICE`.

The ASIC fetches the quit TCD, observes `QUIT=1`, and returns to idle with `DONE=1`. The quit TCD is always a no-op: it performs no source read or destination write, regardless of its pointer or `TRANSFER_LEN` fields, and it does not follow its own `NEXT_TCD`.

Address zero is a valid link and is not a terminator. For an empty run, place the quit TCD directly at the fixed head, `0x000000` on PSRAM 0.

## PSRAM address limits

Each PSRAM has a 23-bit byte address, `A[22:0]`, so its valid range is `0x000000` through `0x7FFFFF` inclusive. Bit 23 of every 24-bit TCD pointer must be zero.

Firmware must validate the complete range of every memory operation before writing or starting a chain:

- An 11-byte TCD fetch is valid only when `NEXT_TCD + 10 <= 0x7FFFFF`. This applies to the fixed head and every linked descriptor.
- For `TRANSFER_LEN > 0`, the source is valid only when `SRC_PTR + TRANSFER_LEN - 1 <= 0x7FFFFF`.
- For `TRANSFER_LEN > 0`, the destination is valid only when `DEST_PTR + TRANSFER_LEN - 1 <= 0x7FFFFF`.
- Perform these checks in a widened integer type so the validation calculation itself cannot wrap.

A TCD that starts outside the valid range, or whose TCD fetch, source range, or destination range crosses `0x7FFFFF`, has undefined behavior. This remains undefined even when part of the requested operation lies inside the valid range. V1 does not currently promise an error, halt, clamp, or deterministic wrap response.

## Safe programming sequence

1. Request and receive the bus grant.
2. Initialize both PSRAMs into QPI mode.
3. Stage payloads and a fully validated TCD chain.
4. Finish QPI activity, drive CE# high, and make MCU QSPI pins high-Z.
5. Drop `BUS_REQ` and wait for `BUS_GNT=0`.
6. Pulse `START` while `DONE=1`.
7. Wait for `DONE=1`, or use `BUS_REQ` to pause and inspect memory between DMA transactions.
8. Request and receive `BUS_GNT` before the MCU drives QSPI again.

Unresolved error behavior, including whether out-of-range addresses should fail deterministically, is tracked in [`../../llm/08-open-questions.md`](../../llm/08-open-questions.md).

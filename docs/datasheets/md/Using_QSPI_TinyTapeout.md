# Configuring and flashing the QSPI Pmod (extracted notes)

Source: Tiny Tapeout guide **Configuring and flashing the QSPI Pmod**  
PDF copy: [`../pdfs/Using_QSPI_TinyTapeout.pdf`](../pdfs/Using_QSPI_TinyTapeout.pdf)  
Upstream: Tiny Tapeout Guides (same title). PDF in-repo may be image-only; this markdown is transcribed from the live page for search and for firmware groundwork.

**This project's target board: TT ETR demoboard (RP2350B).** Legacy TT04+ (RP2040) pin numbers are documented only for comparison.

Project firmware policy that consumes this guide: D30 / [`../../human/architecture/firmware.md`](../../human/architecture/firmware.md) / [`../../llm/12-firmware.md`](../../llm/12-firmware.md).

## What the guide covers

1. **Configure Quad SPI mode on third-party QSPI Pmods** by setting the Winbond W25Q128JV **QUAD ENABLE** bit in status register 2.
2. **Flash** a `.bin` image onto the PMOD flash via Tiny Tapeout Flasher (same flash steps for first- and third-party Pmods).
3. Appendix MicroPython scripts that prove BIDIR SPI access to flash and both PSRAMs on the demoboard.

### Project policy: flash QE already shipped (D30)

**First-party / Tiny Tapeout store QSPI Pmods ship with flash Quad SPI (QE) already enabled.** This project assumes that default. V1 firmware and the ASIC do **not** enable or disable flash QSPI mode as part of normal bring-up. Skip the guide's "Configuring Quad SPI mode" / QE activation script unless recovering a third-party board or a cleared QE bit.

This is **not** the same as APS6404L PSRAM Enter Quad (`0x35`), which remains MCU-owned before DMA START (D17).

First-party QSPI Pmod is compatible with **TT04 and later** demoboards. Plug the Pmod into the **BIDIR** (`uio`) header.

On a healthy first-party board, Flasher Flash ID should already report **`ef7018`** (not `000000`). Guide notes the memory may be used as a **1-bit or 4-bit** interface depending on design requirements.

## Demoboard workflow (Commander / Flasher)

1. Connect demoboard USB; open Tiny Tapeout Commander; CONNECT TO BOARD (WebSerial; Chromium-based browser).
2. Select chip ROM (address 0), open **REPL** tab.
3. Optional: paste the **ETR** appendix script to smoke-test SPI to flash/PSRAM (code catalog below). **Not required** to enable flash QSPI on a first-party Pmod (D30).
4. Flasher: CONNECT TO BOARD, pick `.bin` or provided image, program flash. Expected Flash ID on first-party boards: **`ef7018`**.

REPL paste tips from the guide: `Ctrl+E` enter paste mode, `Ctrl+Shift+V` paste, `Ctrl+D` exit paste mode.

## ETR pin map (binding for this project)

On the ETR demoboard, `uio[0..7]` maps to **GPIO25..GPIO32** (legacy RP2040 base GPIO21..28 shifted up by 4). Logical PMOD order is unchanged. GPIO32 is in pad bank 1; PIO still drives consecutive offsets from `QSPI_BASE`.

| `uio` | ETR GPIO | Legacy GPIO | Net |
|---|---|---|---|
| 0 | 25 | 21 | Flash CS (`CS0`) |
| 1 | 26 | 22 | SD0 / MOSI |
| 2 | 27 | 23 | SD1 / MISO |
| 3 | 28 | 24 | SCK |
| 4 | 29 | 25 | SD2 (was flash WP) |
| 5 | 30 | 26 | SD3 (was flash HOLD) |
| 6 | 31 | 27 | RAM A CS (`CS1`) |
| 7 | 32 | 28 | RAM B CS (`CS2`) |

ETR script constants (Rohan Verma, github.com/rohanverm94):

```python
QSPI_BASE = 25            # uio[0]; was 21 on RP2040 demoboard
PIN_FLASH_CS = QSPI_BASE + 0   # 25
PIN_MOSI     = QSPI_BASE + 1   # 26
PIN_MISO     = QSPI_BASE + 2   # 27
PIN_SCK      = QSPI_BASE + 3   # 28
PIN_SD2      = QSPI_BASE + 4   # 29
PIN_SD3      = QSPI_BASE + 5   # 30
PIN_RAM_A_CS = QSPI_BASE + 6   # 31
PIN_RAM_B_CS = QSPI_BASE + 7   # 32
```

ETR also pins the system clock for deterministic PIO dividers:

```python
machine.freq(150_000_000)
```

## SPI / QSPI transport conclusions

| Fact | Detail |
|---|---|
| Working master | Custom **PIO SPI** class `PIOSPI` (`rp2.StateMachine` + `@rp2.asm_pio` `spi_cpha0`) |
| Not used as master | Hardware `machine.SPI(...)` (imported but unused for transfers) |
| Not used | `SoftSPI` |
| SPI rates in guide | `PIOSPI` default ctor `freq=1_000_000`; flash/PSRAM helpers use **`freq=10_000_000`** |
| PSRAM path | Basic SPI only: write `0x02` + 24-bit addr + data; read `0x03` + 24-bit addr + data |
| Flash path | Basic SPI for ID / SR / erase / program / verify; separate PIO `qspi_read` for **flash quad-read** check after program |
| Idle CS | All three CS pins driven high when idle; only one CE# low per transaction |
| SD2/SD3 in 1-bit mode | Inputs with pull-ups so flash is not write-protected / held |

**Implication for this project's V1 firmware:** reuse `PIOSPI` + ETR pin constants for MCU PSRAM (and optional flash) under D26 drive windows. Do not treat SoftSPI or HW `machine.SPI` as primary. Do **not** require flash QE programming (D30). MCU-side APS6404L Enter Quad (`0x35`) remains a project requirement (D17); the guide's PSRAM test stays in SPI mode and does not issue Enter Quad.

## Bus / ASIC interaction in the guide

```python
DISABLE_TT_ASIC = False  # set True if no TT carrier is mounted

def disable_tt_board():
    if DISABLE_TT_ASIC:
        # Select chip ROM so bidirs are inputs and MCU can drive SPI
        tt = DemoBoard()
        tt.shuttle.tt_um_chip_rom.enable()
```

When the selected design holds bidirs as inputs (chip ROM path), MCU SPI on `uio` is intentional. Aligns with project D26: MCU may drive shared QSPI while `rst_n=0` (deselected / reset) or under `BUS_GNT`.

`TEST_RAM_B = True` includes RAM B in the PSRAM random R/W loop; set false when the Pmod is on the audio header without RAM B.

## Catalog: code patterns to reuse

Attribution: ETR script by **Rohan Verma** (github.com/rohanverm94); Legacy script by **Diego Satizabal** (github.com/dsatizabal). Prefer adapting these patterns into `firmware/` rather than inventing a new SPI bitbang.

### 1. PIO SPI engine (`spi_cpha0` + `PIOSPI`)

Mode 0 bit-bang in PIO: out on SCK low side, sample on SCK high side. `StateMachine` runs at `2*freq`.

```python
@rp2.asm_pio(
    out_shiftdir=0, autopull=True, pull_thresh=8,
    autopush=True, push_thresh=8,
    sideset_init=(rp2.PIO.OUT_LOW,), out_init=rp2.PIO.OUT_LOW,
)
def spi_cpha0():
    out(pins, 1)             .side(0x0)
    in_(pins, 1)             .side(0x1)

class PIOSPI:
    def __init__(self, sm_id, pin_mosi, pin_miso, pin_sck, freq=1000000):
        self._sm = rp2.StateMachine(
            sm_id, spi_cpha0, freq=2*freq,
            sideset_base=Pin(pin_sck),
            out_base=Pin(pin_mosi),
            in_base=Pin(pin_miso),
        )
        self._sm.active(1)

    @micropython.native
    def write(self, wdata):
        first = True
        for b in wdata:
            self._sm.put(b, 24)
            if not first:
                self._sm.get()
            else:
                first = False
        self._sm.get()

    def read(self, n):
        return self.write_read_blocking([0] * n)

    @micropython.native
    def readinto(self, rdata):
        self._sm.put(0)
        for i in range(len(rdata) - 1):
            self._sm.put(0)
            rdata[i] = self._sm.get()
        rdata[-1] = self._sm.get()

    @micropython.native
    def write_read_blocking(self, wdata):
        rdata = bytearray(len(wdata))
        i = -1
        for b in wdata:
            self._sm.put(b, 24)
            if i >= 0:
                rdata[i] = self._sm.get()
            i += 1
        rdata[i] = self._sm.get()
        return rdata
```

Construction used by flash/PSRAM helpers (10 MHz):

```python
spi = PIOSPI(1, Pin(PIN_MOSI), Pin(PIN_MISO), Pin(PIN_SCK), freq=10000000)
```

### 2. Generic CS-framed SPI command (flash and PSRAM)

```python
def spi_cmd(data, sel, dummy_len=0, read_len=0):
    dummy_buf = bytearray(dummy_len)
    read_buf = bytearray(read_len)
    sel.off()
    spi.write(bytearray(data))
    if dummy_len > 0:
        spi.readinto(dummy_buf)
    if read_len > 0:
        spi.readinto(read_buf)
    sel.on()
    return read_buf

def spi_cmd2(data, data2, sel):
    sel.off()
    spi.write(bytearray(data))
    spi.write(data2)
    sel.on()
```

Idle all CS high before traffic:

```python
flash_sel.on(); ram_a_sel.on(); ram_b_sel.on()
```

### 3. PSRAM basic SPI R/W (guide `test_psram`)

Guide uses 8-byte random payloads across the 8 MB space. Opcodes match this project's MCU SPI data path (`0x02` / `0x03`). Extend with chunking / `tCEM` and APS6404L reset / Enter Quad for DMA firmware.

```python
CMD_WRITE = 0x02
CMD_READ = 0x03

# write 8 bytes
spi_cmd2([CMD_WRITE, addr >> 16, (addr >> 8) & 0xFF, addr & 0xFF], buf, ram)

# read 8 bytes
data = spi_cmd([CMD_READ, addr >> 16, (addr >> 8) & 0xFF, addr & 0xFF], ram, 0, 8)
```

### 4. Flash SPI snippets (optional pass-through / diagnostics)

Useful if firmware exercises flash under grant. Not ASIC DMA. **QE write (`0x31`) is not part of V1 bring-up** on first-party Pmods (D30); keep only for third-party recovery.

| Opcode | Role in guide |
|---|---|
| `0xFF` | Leave continuous mode |
| `0x90` | Read ID (expect manufacturer `0xef`, device `0x17` in script check) |
| `0x05` / `0x35` | Read SR1 / SR2 |
| `0x06` | Write enable |
| `0x31` | Write SR2 (QE bit = 2) - **third-party / recovery only** |
| `0x20` | Sector erase |
| `0x02` / `0x03` | Page program / read |

SD2/SD3 while in single-SPI mode:

```python
flash_wp = Pin(PIN_SD2, Pin.IN, Pin.PULL_UP)
flash_hold = Pin(PIN_SD3, Pin.IN, Pin.PULL_UP)
```

### 5. Flash quad-read PIO (`qspi_read`) - reference only

The guide includes a second PIO program `qspi_read` that bit-packs flash continuous-mode quad reads (nibble packing on SD0..3, dynamic `pindirs`). **V1 project firmware does not need MCU flash QSPI or MCU PSRAM QPI.** Keep this as a reference if post-V1 MCU quad flash tooling is useful; do not confuse it with APS6404L Enter Quad (`0x35`) for DMA.

### 6. Script entry / serial bridge (ETR)

ETR appendix ends by calling `run_test()` then opening a UART serial bridge (`UART(1)` on `ui_in3` / `uo_out4` GPIOs) until Ctrl-C. Legacy uses `UART(0)` and different pin objects. Project demos should not depend on that bridge; use Commander REPL / `mpremote` instead.

## Legacy demoboard (not this project's primary target)

Same logical nets on GPIO21..28. Same `PIOSPI` / PSRAM / flash patterns with hard-coded pins (e.g. `Pin(21)` flash CS). Author: Diego Satizabal. Use only if validating on an older TT04+ board.

## Project mapping checklist

When implementing `firmware/psram_spi.py` on ETR:

1. Copy / adapt `PIOSPI` + `spi_cpha0` and the ETR `QSPI_BASE` constants above.
2. Hold flash + RAM A + RAM B CS high when idle.
3. Use basic SPI `0x02`/`0x03` for staging; add project `0x66`/`0x99`/`0x35`/`0xF5` for APS6404L mode control (D17).
4. Do **not** make flash QE activation part of normal bring-up (D30; first-party ships QE set).
5. Chunk long transfers for `tCEM` ([`../../llm/05-qspi-psram.md`](../../llm/05-qspi-psram.md)).
6. Respect D22/D26 drive windows (`BUS_GNT` or `rst_n=0`); release-before-seize with ASIC.
7. Prefer guide patterns over inventing SoftSPI/`machine.SPI` as the first path.

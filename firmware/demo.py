"""One pre-built PSRAM0-to-PSRAM0 copy plus QUIT.

This is the only canned vector. Custom chains use build.py + asic.py + runner.py
in the REPL or a user script. No per-TC-* catalog.
"""

from .asic import Host
from .build import add_copy, add_quit, new_image, place_bytes
from .psram import Psram, make_board_transport
from .runner import run_chain

DEMO_SRC = 0x000100
DEMO_DEST = 0x000200
DEMO_QUIT = 0x00000B
DEMO_PATTERN = bytes(
    [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88]
)


def build_demo_memory():
    """Head TCD at PSRAM0 address 0, payload at DEMO_SRC, QUIT at DEMO_QUIT."""
    mem = new_image()
    add_copy(
        mem,
        tcd_device=0,
        tcd_addr=0,
        src_ptr=DEMO_SRC,
        dest_ptr=DEMO_DEST,
        length=len(DEMO_PATTERN),
        src_device=0,
        dest_device=0,
        next_tcd=DEMO_QUIT,
        next_device=0,
    )
    add_quit(mem, 0, DEMO_QUIT)
    place_bytes(mem, 0, DEMO_SRC, DEMO_PATTERN)
    return mem


def main(tt=None, host=None, psram=None, exit_qpi=False):
    """Run the canned copy. Print PASS or FAIL. Returns True on match."""
    mem = build_demo_memory()
    if host is None:
        if tt is None:
            try:
                from ttboard.demoboard import DemoBoard

                tt = DemoBoard.get()
            except ImportError:
                print("FAIL: no DemoBoard (pass host= and psram= from tests)")
                return False
        host = Host(tt)
        host.enable_project()
    if psram is None:
        psram = Psram(make_board_transport())
    ok, result, mismatches = run_chain(host, psram, mem, exit_qpi=exit_qpi)
    expected = result.final_memory.read(0, DEMO_DEST, len(DEMO_PATTERN))
    if ok and expected == DEMO_PATTERN:
        print("PASS")
        return True
    print("FAIL")
    for device, addr, exp, got in mismatches:
        print("  mismatch dev%d 0x%06X expected=0x%02X got=%s" % (device, addr, exp, got))
    return False


if __name__ == "__main__":
    main()

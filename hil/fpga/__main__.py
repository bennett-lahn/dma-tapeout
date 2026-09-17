"""CLI for `python -m hil.fpga` (harden / configure / doctor)."""

from __future__ import annotations

import argparse
import sys

from .tool import (
    BREAKOUT_FABRICFOX,
    BREAKOUT_TARGETS,
    CONFIGURE_ENGINES,
    DEFAULT_PORT,
    ENGINE_MPREMOTE,
    ENGINE_NATIVE,
    HARDEN_ENGINES,
    FpgaToolError,
    bitstream_path,
    configure,
    describe_staleness,
    doctor_report,
    harden,
    load_project_info,
    repo_root,
    stale_inputs,
)


def build_parser() -> argparse.ArgumentParser:
    """Return the argparse tree for harden / configure / doctor."""
    parser = argparse.ArgumentParser(
        prog="python -m hil",
        description="TinyDMA HIL bitstream tools (compile / upload a UP5K .bin)",
    )
    parser.add_argument(
        "--project-dir",
        default=None,
        help="repo root (default: inferred from this package)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    harden_p = sub.add_parser(
        "bitstream",
        aliases=["harden"],
        help="synthesize and pack a .bin for HIL / firmware (does not run tests)",
    )
    harden_p.add_argument(
        "--breakout-target",
        choices=BREAKOUT_TARGETS,
        default=BREAKOUT_FABRICFOX,
        help="classic = TT04 UP5K ASIC-simulator breakout; "
        "fabricfox = TT ETR demoboard (default)",
    )
    harden_p.add_argument(
        "--engine",
        choices=HARDEN_ENGINES,
        default=ENGINE_NATIVE,
        help="native = Yosys read_slang recipe (default; needed for SV packages); "
        "official = tt_fpga.py (falls back to native)",
    )

    upload_p = sub.add_parser(
        "upload",
        help="copy hil/fpga/build/<top>.bin to /bitstreams (does not run tests)",
    )
    upload_p.add_argument("--port", default=DEFAULT_PORT, help="demoboard CDC port")
    upload_p.add_argument(
        "--stale",
        action="store_true",
        help="upload even though the .bin is older than info.yaml / src RTL "
        "(refused without this flag)",
    )
    upload_p.add_argument(
        "--engine",
        choices=CONFIGURE_ENGINES,
        default=ENGINE_MPREMOTE,
        help="mpremote = fs cp only (default); official = tt_fpga.py configure",
    )

    cfg = sub.add_parser("configure", help="upload .bin and/or edit config.ini")
    cfg.add_argument("--port", default=DEFAULT_PORT, help="demoboard CDC port")
    cfg.add_argument(
        "--upload",
        action="store_true",
        help="copy hil/fpga/build/<top>.bin to /bitstreams on the MCU",
    )
    cfg.add_argument(
        "--set-default",
        action="store_true",
        help="set this project as the demoboard power-up default (official only)",
    )
    cfg.add_argument(
        "--clockrate",
        type=int,
        default=None,
        help="write config.ini auto-clock Hz (official engine only; omit to leave unchanged)",
    )
    cfg.add_argument(
        "--engine",
        choices=CONFIGURE_ENGINES,
        default=ENGINE_MPREMOTE,
        help="mpremote = fs cp only (default); official = tt_fpga.py configure",
    )

    sub.add_parser("doctor", help="print FPGA toolchain health and exit non-zero on fail")
    return parser


def main(argv=None) -> int:
    """Parse argv, run the subcommand, and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    root = repo_root(args.project_dir)
    try:
        if args.command in ("harden", "bitstream"):
            path = harden(
                root=root,
                breakout=args.breakout_target,
                engine=args.engine,
            )
            print("bitstream: %s" % path)
            return 0
        if args.command == "upload":
            host_bin = bitstream_path(root)
            if not args.stale and stale_inputs(host_bin, root=root):
                print(
                    "fpga: refusing a stale bitstream: %s. Rebuild with "
                    "python -m hil bitstream, or upload it anyway with --stale"
                    % describe_staleness(host_bin, root=root),
                    file=sys.stderr,
                )
                return 2
            result = configure(
                root=root,
                port=args.port,
                upload=True,
                engine=args.engine,
            )
            print("configure: %s" % result)
            return 0
        if args.command == "configure":
            if not (args.upload or args.set_default or args.clockrate is not None):
                parser.error("configure needs --upload, --set-default, and/or --clockrate")
            result = configure(
                root=root,
                port=args.port,
                upload=args.upload,
                set_default=args.set_default,
                clockrate=args.clockrate,
                engine=args.engine,
            )
            print("configure: %s" % result)
            return 0
        report = doctor_report(root)
        info = report["info"] or load_project_info(root)
        print("=== dma-tapeout FPGA doctor ===")
        print("top_module=%s clock_hz=%s" % (info["top_module"], info["clock_hz"]))
        print("sources=%s" % (", ".join(info["source_files"]),))
        print("tt_dir=%s" % (report["tt_dir"] or "<missing>"))
        print("bitstream=%s" % (report["bitstream"] or "<unknown>"))
        for name, path in report["tools"].items():
            print("%s=%s" % (name, path or "MISSING"))
        for warn in report["warns"]:
            print("WARN: %s" % warn)
        for fail in report["fails"]:
            print("FAIL: %s" % fail)
        if not report["ok"]:
            return 1
        print("fpga doctor: PASS")
        return 0
    except FpgaToolError as exc:
        print("fpga: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

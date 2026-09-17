"""Locate Tiny Tapeout FPGA tools, stage RTL, harden a bitstream, and upload it.

The official recipe is Tiny Tapeout `tt_fpga.py` (`harden` / `configure`) from
https://github.com/TinyTapeout/tt-support-tools. This module:

* reads `info.yaml` for `top_module`, `source_files`, and `clock_hz`
* copies those sources into `hil/fpga/stage/` so `_tt_fpga_top.v` never lands
  in `src/`
* prefers the official script when it imports, otherwise runs the same Yosys /
  nextpnr-ice40 / icepack command line the script uses
* uploads with `mpremote fs cp` by default (same path firmware already uses)

Breakout targets match `tt_fpga.py`: `fabricfox` (TT ETR demoboard, default)
and `classic` (TT04-era UP5K ASIC-simulator breakout).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

BREAKOUT_FABRICFOX = "fabricfox"
BREAKOUT_CLASSIC = "classic"
BREAKOUT_TARGETS = (BREAKOUT_FABRICFOX, BREAKOUT_CLASSIC)

# PCF filenames inside tt-support-tools `fpga/` (same map as tt_fpga.py).
BREAKOUT_PCF = {
    BREAKOUT_CLASSIC: "tt_fpga_top.pcf",
    BREAKOUT_FABRICFOX: "tt_fpga_fabricfoxv2.pcf",
}

ENGINE_OFFICIAL = "official"
ENGINE_NATIVE = "native"
ENGINE_MPREMOTE = "mpremote"
HARDEN_ENGINES = (ENGINE_OFFICIAL, ENGINE_NATIVE)
CONFIGURE_ENGINES = (ENGINE_OFFICIAL, ENGINE_MPREMOTE)

DEFAULT_PORT = "/dev/ttyACM0"
DEFAULT_NEXTPNR_SEED = "10"
DEFAULT_NEXTPNR_FREQ = "12"

DESIGN_PLACEHOLDER = "__tt_um_placeholder"
FPGA_TOP_MODULE = "tt_fpga_top"
MCU_BITSTREAM_DIR = "/bitstreams"

_HIL_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _HIL_DIR.parent
_STAGE_DIR = _HIL_DIR / "fpga" / "stage"
_BUILD_DIR = _HIL_DIR / "fpga" / "build"


class FpgaToolError(RuntimeError):
    """The FPGA toolchain cannot honor the requested harden or configure step."""


def fpga_clock_hz(freq=None):
    """Return the FPGA project-clock constraint in Hz.

    `nextpnr-ice40 --freq` uses MHz. The same value must drive the live FPGA:
    a bitstream timed at 12 MHz must not be clocked at the ASIC's 66 MHz.
    """
    raw = freq
    if raw is None:
        raw = os.environ.get("TT_FPGA_FREQ", DEFAULT_NEXTPNR_FREQ)
    try:
        mhz = float(raw)
    except (TypeError, ValueError) as exc:
        raise FpgaToolError("invalid FPGA clock constraint %r MHz" % (raw,)) from exc
    if mhz <= 0:
        raise FpgaToolError("FPGA clock constraint must be positive, got %r MHz" % (raw,))
    return int(mhz * 1_000_000)


def repo_root(start: Path | None = None) -> Path:
    """Return the TinyDMA repo root (directory that contains `info.yaml`)."""
    if start is not None:
        return Path(start).resolve()
    return _REPO_ROOT


def info_yaml_path(root: Path | None = None) -> Path:
    """Return `<repo>/info.yaml`."""
    return repo_root(root) / "info.yaml"


def parse_info_yaml(text: str) -> dict:
    """Parse the Tiny Tapeout fields this flow needs from `info.yaml` text.

    Only `top_module`, `source_files`, and `clock_hz` are read. The file is
    small and pinned; this avoids adding PyYAML to `test/requirements.txt`.

    Returns:
        dict: `top_module` (str), `source_files` (list[str]), `clock_hz` (int).

    Raises:
        FpgaToolError: a required field is missing or malformed.
    """
    top_module = None
    clock_hz = None
    sources: list[str] = []
    in_sources = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("top_module:"):
            top_module = stripped.split(":", 1)[1].strip().strip("\"'")
            in_sources = False
            continue
        if stripped.startswith("clock_hz:"):
            token = stripped.split(":", 1)[1].strip().split()[0]
            try:
                clock_hz = int(token)
            except ValueError as exc:
                raise FpgaToolError(
                    "info.yaml clock_hz is not an integer: %r" % (token,)
                ) from exc
            in_sources = False
            continue
        if stripped.startswith("source_files:"):
            in_sources = True
            continue
        if in_sources:
            if stripped.startswith("-"):
                sources.append(stripped[1:].strip().strip("\"'"))
                continue
            in_sources = False
    if not top_module:
        raise FpgaToolError("info.yaml is missing top_module")
    if not sources:
        raise FpgaToolError("info.yaml is missing source_files")
    if clock_hz is None:
        raise FpgaToolError("info.yaml is missing clock_hz")
    return {
        "top_module": top_module,
        "source_files": sources,
        "clock_hz": clock_hz,
    }


def load_project_info(root: Path | None = None) -> dict:
    """Read and parse `<repo>/info.yaml`."""
    path = info_yaml_path(root)
    if not path.is_file():
        raise FpgaToolError("info.yaml not found: %s" % (path,))
    return parse_info_yaml(path.read_text(encoding="utf-8"))


def tt_search_candidates(root: Path | None = None, extra=None) -> list[Path]:
    """Return directories that may contain `tt_fpga.py`, first match wins.

    Order: `TT_SUPPORT_TOOLS`, `TT_FPGA_DIR`, `~/tt`, `~/tt-support-tools`,
    the gitignored `ttihp-verilog-template/tt` clone, then any `extra` paths.
    """
    root = repo_root(root)
    home = Path(os.path.expanduser("~"))
    named = []
    for key in ("TT_SUPPORT_TOOLS", "TT_FPGA_DIR"):
        value = os.environ.get(key)
        if value:
            named.append(Path(value).expanduser())
    named.extend(
        [
            home / "tt",
            home / "tt-support-tools",
            root / "ttihp-verilog-template" / "tt",
            root / "tools" / "tt-support-tools",
        ]
    )
    if extra:
        named.extend(Path(p) for p in extra)
    seen: set[Path] = set()
    out: list[Path] = []
    for path in named:
        resolved = path.resolve() if path.exists() else path
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(path)
    return out


def find_tt_dir(root: Path | None = None, extra=None) -> Path:
    """Return the first candidate directory that contains `tt_fpga.py`.

    Raises:
        FpgaToolError: no candidate has the official script.
    """
    tried = []
    for candidate in tt_search_candidates(root, extra=extra):
        script = candidate / "tt_fpga.py"
        tried.append(str(script))
        if script.is_file():
            return candidate.resolve()
    raise FpgaToolError(
        "tt_fpga.py not found. Clone TinyTapeout/tt-support-tools to ~/tt "
        "or set TT_SUPPORT_TOOLS. Looked at: %s" % (", ".join(tried),)
    )


def find_tt_fpga_py(root: Path | None = None, extra=None) -> Path:
    """Return the path to official `tt_fpga.py`."""
    return find_tt_dir(root, extra=extra) / "tt_fpga.py"


def pcf_path(tt_dir: Path, breakout: str) -> Path:
    """Return the PCF (physical constraints / pin map) for `breakout`."""
    if breakout not in BREAKOUT_PCF:
        raise FpgaToolError(
            "unknown breakout %r; known: %s"
            % (breakout, ", ".join(BREAKOUT_TARGETS))
        )
    path = Path(tt_dir) / "fpga" / BREAKOUT_PCF[breakout]
    if not path.is_file():
        raise FpgaToolError("PCF not found: %s" % (path,))
    return path


def yosys_harden_script(wrapper, rtl_sources, json_path) -> str:
    """Return the Yosys `-p` script that synthesizes the FPGA wrapper.

    Stock `tt_fpga.py` uses `read_verilog -sv` for every file, which cannot
    parse the `import` in `types.svh` (`sys_control_pkg` imports `qspi_pkg`).
    Native harden splits the read: the Tiny Tapeout wrapper (iCE40 `SB_IO`
    primitives) stays on `read_verilog -sv`; project RTL goes through
    `read_slang` (OSS CAD Yosys slang frontend, same SV reader as IHP GDS).
    """
    rtl = " ".join(str(path) for path in rtl_sources)
    return (
        "read_verilog -sv %s; read_slang -D SYNTH %s; synth_ice40 -top %s -json %s"
        % (wrapper, rtl, FPGA_TOP_MODULE, json_path)
    )


def wrap_fpga_top(template_text: str, top_module: str) -> str:
    """Instantiate the user top inside the Tiny Tapeout FPGA wrapper."""
    if DESIGN_PLACEHOLDER not in template_text:
        raise FpgaToolError(
            "FPGA top template is missing %s" % (DESIGN_PLACEHOLDER,)
        )
    return template_text.replace(DESIGN_PLACEHOLDER, top_module)


def stage_dir(root: Path | None = None) -> Path:
    """Return the isolated project directory used as `tt_fpga.py --project-dir`."""
    return _STAGE_DIR if root is None else repo_root(root) / "hil" / "fpga" / "stage"


def artifact_dir(root: Path | None = None) -> Path:
    """Return the stable bitstream directory (`hil/fpga/build/`)."""
    return _BUILD_DIR if root is None else repo_root(root) / "hil" / "fpga" / "build"


def bitstream_filename(top_module: str) -> str:
    """Return `<top_module>.bin` (MCU `/bitstreams` and `tt.shuttle` name)."""
    return "%s.bin" % (top_module,)


def bitstream_path(root: Path | None = None, top_module: str | None = None) -> Path:
    """Return the canonical host path of the last successful harden."""
    name = top_module or load_project_info(root)["top_module"]
    return artifact_dir(root) / bitstream_filename(name)


def bitstream_inputs(root: Path | None = None) -> tuple[Path, ...]:
    """Return every file the bitstream is built from (`info.yaml` + RTL).

    Used to decide whether an existing `.bin` still represents the RTL. The
    breakout PCF lives in the gitignored tt-support-tools clone and is not
    tracked here; a breakout change needs an explicit rebuild.
    """
    base = repo_root(root)
    info = load_project_info(base)
    paths = [info_yaml_path(base)]
    paths.extend(base / "src" / name for name in info["source_files"])
    return tuple(paths)


def stale_inputs(bin_path: Path | str, root: Path | None = None) -> tuple[Path, ...]:
    """Return the build inputs modified after `bin_path` was packed.

    An empty tuple means the bitstream is current. Comparison is by mtime, so
    touching a source without changing it also counts as stale - deliberately
    conservative, because silently testing last week's RTL is the worse error.

    Raises:
        FpgaToolError: `bin_path` does not exist.
    """
    path = Path(bin_path)
    if not path.is_file():
        raise FpgaToolError("bitstream not found: %s" % (path,))
    built = path.stat().st_mtime
    return tuple(
        candidate
        for candidate in bitstream_inputs(root)
        if candidate.is_file() and candidate.stat().st_mtime > built
    )


def describe_staleness(bin_path: Path | str, root: Path | None = None) -> str:
    """Return a one-line explanation of why `bin_path` is out of date."""
    newer = stale_inputs(bin_path, root=root)
    if not newer:
        return "bitstream is current"
    base = repo_root(root)
    names = []
    for path in newer:
        try:
            names.append(str(path.relative_to(base)))
        except ValueError:
            names.append(str(path))
    return "%d input(s) newer than the bitstream: %s" % (len(names), ", ".join(names))


def stage_project(root: Path | None = None, dest: Path | None = None) -> Path:
    """Copy `info.yaml` and listed RTL sources into an isolated project tree.

    Official `tt_fpga.py` writes `_tt_fpga_top.v` into `source_dir`. Staging
    keeps that generated wrapper out of `src/`.

    Returns:
        Path: the staged project directory (contains `info.yaml` and `src/`).
    """
    root = repo_root(root)
    info = load_project_info(root)
    dest = Path(dest) if dest is not None else stage_dir(root)
    src_dir = dest / "src"
    if dest.exists():
        shutil.rmtree(dest)
    src_dir.mkdir(parents=True)
    shutil.copy2(info_yaml_path(root), dest / "info.yaml")
    missing = []
    for name in info["source_files"]:
        src = root / "src" / name
        if not src.is_file():
            missing.append(str(src))
            continue
        shutil.copy2(src, src_dir / name)
    if missing:
        raise FpgaToolError("RTL sources missing: %s" % (", ".join(missing),))
    return dest


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(cmd, cwd=None, env=None):
    """Run `cmd` and raise `FpgaToolError` on a non-zero exit."""
    try:
        done = subprocess.run(
            list(cmd),
            cwd=None if cwd is None else str(cwd),
            env=env,
            check=False,
        )
    except OSError as exc:
        raise FpgaToolError("cannot run %s: %s" % (cmd[0], exc)) from exc
    if done.returncode != 0:
        raise FpgaToolError(
            "%s exited %d" % (" ".join(str(part) for part in cmd), done.returncode)
        )
    return done


def official_python(tt_dir: Path, python: str | None = None) -> str:
    """Return a Python that can `import project` from `tt_dir`.

    Tries the caller interpreter first, then `~/ttsetup/venv` (the WSL Tiny
    Tapeout venv that already has chevron / PyYAML / mpremote).
    """
    candidates = []
    if python:
        candidates.append(python)
    candidates.append(sys.executable)
    ttsetup = Path(os.path.expanduser("~")) / "ttsetup" / "venv" / "bin" / "python"
    if ttsetup.is_file():
        candidates.append(str(ttsetup))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tt_dir)
    seen = set()
    errors = []
    for exe in candidates:
        if not exe or exe in seen:
            continue
        seen.add(exe)
        probe = subprocess.run(
            [exe, "-c", "import project, tt_fpga"],
            cwd=str(tt_dir),
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if probe.returncode == 0:
            return exe
        errors.append("%s: %s" % (exe, (probe.stderr or probe.stdout or "").strip()))
    raise FpgaToolError(
        "no Python can import tt-support-tools from %s (%s)"
        % (tt_dir, " | ".join(errors) or "no candidates")
    )


def harden_official(
    *,
    root: Path | None = None,
    breakout: str = BREAKOUT_FABRICFOX,
    python: str | None = None,
    extra_tt=None,
) -> Path:
    """Run official `tt_fpga.py harden` against a staged copy of this project."""
    root = repo_root(root)
    info = load_project_info(root)
    tt_dir = find_tt_dir(root, extra=extra_tt)
    staged = stage_project(root)
    exe = official_python(tt_dir, python=python)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tt_dir)
    _run(
        [
            exe,
            str(tt_dir / "tt_fpga.py"),
            "--project-dir",
            str(staged),
            "harden",
            "--breakout-target",
            breakout,
        ],
        cwd=tt_dir,
        env=env,
    )
    produced = staged / "build" / bitstream_filename(info["top_module"])
    if not produced.is_file():
        raise FpgaToolError("official harden did not write %s" % (produced,))
    return _publish_bitstream(produced, root, info["top_module"])


def harden_native(
    *,
    root: Path | None = None,
    breakout: str = BREAKOUT_FABRICFOX,
    extra_tt=None,
    seed: str | None = None,
    freq: str | None = None,
) -> Path:
    """Run the Yosys / nextpnr-ice40 / icepack recipe from `tt_fpga.py`."""
    root = repo_root(root)
    info = load_project_info(root)
    tt_dir = find_tt_dir(root, extra=extra_tt)
    staged = stage_project(root)
    template = (tt_dir / "fpga" / "tt_fpga_top.v").read_text(encoding="utf-8")
    wrapper = wrap_fpga_top(template, info["top_module"])
    src_dir = staged / "src"
    wrapper_path = src_dir / "_tt_fpga_top.v"
    wrapper_path.write_text(wrapper, encoding="utf-8")

    build = staged / "build"
    build.mkdir(parents=True, exist_ok=True)
    name = info["top_module"]
    rtl_sources = [src_dir / src for src in info["source_files"]]
    for path in [wrapper_path] + rtl_sources:
        if not path.is_file():
            raise FpgaToolError("staged source missing: %s" % (path,))

    json_path = build / ("%s.json" % name)
    asc_path = build / ("%s.asc" % name)
    bin_path = build / bitstream_filename(name)
    synth_log = build / "01-synth.log"
    pnr_log = build / "02-nextpnr.log"

    yosys = _which("yosys")
    nextpnr = _which("nextpnr-ice40")
    icepack = _which("icepack")
    if not yosys or not nextpnr or not icepack:
        raise FpgaToolError(
            "native harden needs yosys, nextpnr-ice40, and icepack on PATH "
            "(source test/env.sh / OSS CAD Suite). missing: %s"
            % (
                ", ".join(
                    name
                    for name, path in (
                        ("yosys", yosys),
                        ("nextpnr-ice40", nextpnr),
                        ("icepack", icepack),
                    )
                    if not path
                ),
            )
        )

    _run(
        [
            yosys,
            "-l",
            str(synth_log),
            "-p",
            yosys_harden_script(wrapper_path, rtl_sources, json_path),
        ]
    )
    _run(
        [
            nextpnr,
            "-l",
            str(pnr_log),
            "--pcf-allow-unconstrained",
            "--seed",
            seed or os.environ.get("TT_FPGA_SEED", DEFAULT_NEXTPNR_SEED),
            "--freq",
            freq or os.environ.get("TT_FPGA_FREQ", DEFAULT_NEXTPNR_FREQ),
            "--package",
            "sg48",
            "--up5k",
            "--asc",
            str(asc_path),
            "--pcf",
            str(pcf_path(tt_dir, breakout)),
            "--json",
            str(json_path),
        ]
    )
    _run([icepack, str(asc_path), str(bin_path)])
    return _publish_bitstream(bin_path, root, name)


def _publish_bitstream(produced: Path, root: Path, top_module: str) -> Path:
    """Copy the stage `build/*.bin` to the stable `hil/fpga/build/` path."""
    dest_dir = artifact_dir(root)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / bitstream_filename(top_module)
    shutil.copy2(produced, dest)
    return dest


def harden(
    *,
    root: Path | None = None,
    breakout: str = BREAKOUT_FABRICFOX,
    engine: str = ENGINE_NATIVE,
    python: str | None = None,
    extra_tt=None,
) -> Path:
    """Create `hil/fpga/build/<top_module>.bin` for the selected breakout.

    `engine=official` runs `tt_fpga.py harden`. If that Python import fails,
    the call falls back to `engine=native` (same Yosys recipe).
    """
    if breakout not in BREAKOUT_TARGETS:
        raise FpgaToolError(
            "unknown breakout %r; known: %s"
            % (breakout, ", ".join(BREAKOUT_TARGETS))
        )
    if engine == ENGINE_NATIVE:
        return harden_native(root=root, breakout=breakout, extra_tt=extra_tt)
    if engine != ENGINE_OFFICIAL:
        raise FpgaToolError(
            "unknown harden engine %r; known: %s"
            % (engine, ", ".join(HARDEN_ENGINES))
        )
    try:
        return harden_official(
            root=root,
            breakout=breakout,
            python=python,
            extra_tt=extra_tt,
        )
    except FpgaToolError as exc:
        print("fpga: official harden unavailable (%s); trying native" % exc)
        return harden_native(root=root, breakout=breakout, extra_tt=extra_tt)


def upload_bitstream(
    bin_path: Path | str,
    *,
    port: str = DEFAULT_PORT,
    remote_name: str | None = None,
    mpremote: str = "mpremote",
    timeout_s: int = 60,
) -> str:
    """Copy a host `.bin` to `/bitstreams/` and reboot the demoboard.

    The SDK scans `/bitstreams/` only while booting, so the reset makes the
    just-uploaded design available through `tt.shuttle`.

    Raises:
        FpgaToolError: the file is missing or `mpremote` fails.
    """
    path = Path(bin_path)
    if not path.is_file():
        raise FpgaToolError("bitstream not found: %s" % (path,))
    name = remote_name or path.name
    remote = "%s/%s" % (MCU_BITSTREAM_DIR, name)
    mkdir = [mpremote, "connect", port, "fs", "mkdir", MCU_BITSTREAM_DIR]
    copy = [mpremote, "connect", port, "fs", "cp", str(path), ":%s" % remote]
    try:
        made = subprocess.run(
            mkdir, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except OSError as exc:
        raise FpgaToolError("cannot run %s: %s" % (mpremote, exc)) from exc
    # mkdir is optional: the directory may already exist on the MCU.
    if made.returncode != 0:
        detail = (made.stderr or made.stdout or "").lower()
        if "exist" not in detail and "eexist" not in detail:
            print("fpga: mkdir %s: %s" % (MCU_BITSTREAM_DIR, detail.strip()))
    try:
        done = subprocess.run(
            copy, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except OSError as exc:
        raise FpgaToolError("cannot run %s: %s" % (mpremote, exc)) from exc
    if done.returncode != 0:
        raise FpgaToolError(
            "upload failed: %s"
            % ((done.stderr or done.stdout or "").strip() or copy,)
        )
    reset = [mpremote, "connect", port, "reset"]
    try:
        rebooted = subprocess.run(
            reset, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except OSError as exc:
        raise FpgaToolError("cannot run %s: %s" % (mpremote, exc)) from exc
    if rebooted.returncode != 0:
        raise FpgaToolError(
            "reset after upload failed: %s"
            % ((rebooted.stderr or rebooted.stdout or "").strip() or reset,)
        )
    # Reset drops USB CDC. HIL's next mpremote (session preamble) must not
    # race the COM / ttyACM re-enumerate ("failed to access COM10").
    from hil.link import TransportError, wait_for_mpremote_port

    try:
        wait_for_mpremote_port(port, mpremote=mpremote, run=subprocess.run)
    except TransportError as exc:
        raise FpgaToolError(
            "port %s did not return after bitstream reset: %s" % (port, exc)
        ) from exc
    return remote


def configure_official(
    *,
    root: Path | None = None,
    port: str = DEFAULT_PORT,
    upload: bool = False,
    set_default: bool = False,
    clockrate: int | None = None,
    python: str | None = None,
    extra_tt=None,
) -> Path:
    """Run official `tt_fpga.py configure` against the staged project build."""
    if not (upload or set_default or clockrate is not None):
        raise FpgaToolError(
            "configure did nothing; pass --upload, --set-default, and/or --clockrate"
        )
    root = repo_root(root)
    info = load_project_info(root)
    host_bin = bitstream_path(root, info["top_module"])
    if not host_bin.is_file():
        raise FpgaToolError(
            "bitstream missing: %s (run harden first)" % (host_bin,)
        )
    tt_dir = find_tt_dir(root, extra=extra_tt)
    staged = stage_dir(root)
    staged_build = staged / "build"
    staged_build.mkdir(parents=True, exist_ok=True)
    staged_bin = staged_build / host_bin.name
    shutil.copy2(host_bin, staged_bin)
    if not (staged / "info.yaml").is_file():
        shutil.copy2(info_yaml_path(root), staged / "info.yaml")
    exe = official_python(tt_dir, python=python)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(tt_dir)
    cmd = [
        exe,
        str(tt_dir / "tt_fpga.py"),
        "--project-dir",
        str(staged),
        "configure",
        "--port",
        port,
        "--name",
        info["top_module"],
    ]
    if upload:
        cmd.append("--upload")
    if set_default:
        cmd.append("--set-default")
    if clockrate is not None:
        cmd.extend(["--clockrate", str(int(clockrate))])
    _run(cmd, cwd=tt_dir, env=env)
    return host_bin


def configure(
    *,
    root: Path | None = None,
    port: str = DEFAULT_PORT,
    upload: bool = True,
    set_default: bool = False,
    clockrate: int | None = None,
    engine: str = ENGINE_MPREMOTE,
    python: str | None = None,
    extra_tt=None,
) -> str | Path:
    """Install the bitstream and/or write demoboard `config.ini` extras.

    Default engine is `mpremote` (copy only). `--set-default` / `--clockrate`
    require `engine=official` because they edit `config.ini` through
    `tt_fpga.py configure`.
    """
    if set_default or clockrate is not None:
        engine = ENGINE_OFFICIAL
    if engine == ENGINE_OFFICIAL:
        return configure_official(
            root=root,
            port=port,
            upload=upload,
            set_default=set_default,
            clockrate=clockrate,
            python=python,
            extra_tt=extra_tt,
        )
    if engine != ENGINE_MPREMOTE:
        raise FpgaToolError(
            "unknown configure engine %r; known: %s"
            % (engine, ", ".join(CONFIGURE_ENGINES))
        )
    if not upload:
        raise FpgaToolError("mpremote configure only supports --upload")
    root = repo_root(root)
    info = load_project_info(root)
    host_bin = bitstream_path(root, info["top_module"])
    return upload_bitstream(
        host_bin, port=port, remote_name=bitstream_filename(info["top_module"])
    )


def doctor_report(root: Path | None = None, extra_tt=None) -> dict:
    """Collect FPGA toolchain health without exiting.

    Returns a dict with `ok` (harden tools present), `warns`, `fails`, and
    resolved paths. Missing `tt_fpga.py` is a warning when Yosys/nextpnr/icepack
    are present, because native harden can still run once the PCF tree exists.
    """
    root = repo_root(root)
    warns: list[str] = []
    fails: list[str] = []
    tools = {}
    for name in ("yosys", "nextpnr-ice40", "icepack", "mpremote"):
        path = _which(name)
        tools[name] = path
        if path is None and name != "mpremote":
            fails.append("%s not on PATH (source test/env.sh / OSS CAD Suite)" % name)
        elif path is None:
            warns.append("mpremote not on PATH (needed for configure --upload)")
    tt_dir = None
    try:
        tt_dir = find_tt_dir(root, extra=extra_tt)
    except FpgaToolError as exc:
        warns.append(str(exc))
    info = None
    try:
        info = load_project_info(root)
    except FpgaToolError as exc:
        fails.append(str(exc))
    ok = not fails
    return {
        "ok": ok,
        "warns": warns,
        "fails": fails,
        "tools": tools,
        "tt_dir": str(tt_dir) if tt_dir else None,
        "info": info,
        "bitstream": str(bitstream_path(root, info["top_module"])) if info else None,
    }

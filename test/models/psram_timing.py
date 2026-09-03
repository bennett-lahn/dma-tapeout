"""Runtime transport timing for an attached :mod:`models.psram` device.

``wrap_device`` is deliberately the sole delay layer.  It delays the parser's
view of DUT output transitions and delays read-data drive / model SIO OE at
the return plane; the parser itself remains a protocol model.  Every profile
returns a started ``_TimedPsramDevice``; zero delays drain synchronously via
the apply heap (``delay_fs == 0``).  ``ideal`` keeps datasheet device AC live
and zeros only TB path placeholders.

Duty-cycle ratios and ``PSRAM_TKHKL_NS`` / ``tKHKL`` (SCK rise or fall time)
remain in the resolved manifest as reporting placeholders only.  This Python
model does not police them; clock-quality closure is STA / ``T-CLKQ``.
"""

from __future__ import annotations

import heapq
from types import MappingProxyType
from typing import Mapping

import cocotb
from cocotb.simtime import get_sim_time
from cocotb.triggers import First, Timer


# Active device AC applied by the timed wrapper (response, setup/hold, CE#).
# Duty ratios and PSRAM_TKHKL_NS are reporting placeholders only (see module
# docstring); they are resolved into the manifest but never measured here.
_DEVICE_AC = {
    "PSRAM_TACLK_NS": 5.5,
    "PSRAM_TCSP_NS": 2.5,
    "PSRAM_TCHD_NS": 3.0,
    "PSRAM_TCPH_NS": 18.0,
    "PSRAM_THZ_NS": 5.5,
    "PSRAM_TSP_NS": 2.0,
    "PSRAM_THD_NS": 2.0,
    "PSRAM_TCEM_US_EXT": 4.0,
    "PSRAM_TCEM_US_STD": 8.0,
    # Reporting placeholders (not policed by this model):
    "PSRAM_TCH_MIN_RATIO": 0.45,
    "PSRAM_TCL_MIN_RATIO": 0.45,
    "PSRAM_TCH_MAX_RATIO": 0.55,
    "PSRAM_TCL_MAX_RATIO": 0.55,
    "PSRAM_TKHKL_NS": 1.5,
}

_TB_PATH = {
    "TB_TCO_NS": 0.0,
    "TB_FLIGHT_OUT_NS": 0.0,
    "TB_FLIGHT_IN_NS": 0.0,
    "TB_TCO_CE_NS": None,
    "TB_TCO_SCK_NS": None,
    "TB_TCO_SIO_NS": None,
    "TB_TCO_OE_NS": None,
    "TB_FLIGHT_OUT_CE_NS": None,
    "TB_FLIGHT_OUT_SCK_NS": None,
    "TB_FLIGHT_OUT_SIO_NS": None,
    "TB_FLIGHT_OUT_OE_NS": None,
    "TB_FLIGHT_IN_SIO_NS": None,
}

# Common spelling retained only as a parameter alias.  The resolved manifest
# always uses the concise CE name.
_PARAM_ALIASES = {
    "TB_TCO_CE_N_NS": "TB_TCO_CE_NS",
    "TB_FLIGHT_OUT_CE_N_NS": "TB_FLIGHT_OUT_CE_NS",
}

def _ns_to_fs(delay_ns: float) -> int:
    return int(round(float(delay_ns) * 1_000_000.0))


def _profile_defaults(profile: str) -> dict[str, float | None]:
    profile = profile.lower()
    if profile in ("ideal", "nominal", "sweep"):
        # ideal: datasheet AC live, TB_* path placeholders zero.
        # nominal/sweep: same AC base; sweep callers override points.
        params = dict(_DEVICE_AC)
        params.update(_TB_PATH)
        return params
    raise ValueError(
        f"unknown timing profile {profile!r}; expected 'ideal', 'nominal', or 'sweep'"
    )


def resolve_timing_params(profile: str = "ideal", **overrides) -> Mapping[str, float]:
    """Resolve a profile and explicit knobs into an immutable manifest mapping.

    ``sweep`` starts from the documented nominal point.  Its caller supplies
    one or more explicit values, such as ``PSRAM_TACLK_NS=2.0``; the returned
    mapping records the complete point rather than relying on ambient state.
    Per-signal output knobs inherit their corresponding base parameter.

    Duty ratios and ``PSRAM_TKHKL_NS`` are retained for run reports only; the
    timed wrapper never measures or fails them.
    """

    params = _profile_defaults(profile)
    for supplied_name, value in overrides.items():
        name = _PARAM_ALIASES.get(supplied_name.upper(), supplied_name.upper())
        if name not in params:
            raise ValueError(f"unknown timing parameter {supplied_name!r}")
        try:
            value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"timing parameter {supplied_name!r} must be a non-negative number"
            ) from exc
        if value < 0.0:
            raise ValueError(f"timing parameter {supplied_name!r} must be non-negative")
        params[name] = value

    for signal in ("CE", "SCK", "SIO", "OE"):
        key = f"TB_TCO_{signal}_NS"
        if params[key] is None:
            params[key] = params["TB_TCO_NS"]
        key = f"TB_FLIGHT_OUT_{signal}_NS"
        if params[key] is None:
            params[key] = params["TB_FLIGHT_OUT_NS"]
    if params["TB_FLIGHT_IN_SIO_NS"] is None:
        params["TB_FLIGHT_IN_SIO_NS"] = params["TB_FLIGHT_IN_NS"]

    for signal in ("CE", "SCK", "SIO", "OE"):
        params[f"D_OUT_{signal}_NS"] = (
            params[f"TB_TCO_{signal}_NS"] + params[f"TB_FLIGHT_OUT_{signal}_NS"]
        )
    params["D_IN_SIO_NS"] = params["TB_FLIGHT_IN_SIO_NS"]
    return MappingProxyType(params)


def active_timing_params(device) -> Mapping[str, float]:
    """Return the immutable resolved timing manifest for *device*.

    Prefer ``device.timing_params`` from a ``wrap_device`` result.  An
    unwrapped object falls back to the ``ideal`` defaults (datasheet AC live,
    TB path placeholders zero).
    """

    return getattr(device, "timing_params", resolve_timing_params())


class _TimedPsramDevice:
    """Delayed device-plane event dispatcher for one attached PSRAM agent."""

    def __init__(self, device, params: Mapping[str, float]) -> None:
        self._device = device
        self._agent = device.agent
        if self._agent is None:
            raise ValueError("wrap_device requires an attached device with a running agent")
        self.timing_params = params
        self._task = None
        self._delayed_tasks = set()
        self._cancelled_generations = set()
        self._transport_active = False
        self._generation = 0
        self._sequence = 0
        # Ordered device-plane apply queue: (due_fs, sequence, generation, cb, args).
        # Same-time callbacks run in increasing schedule sequence, not Python task order.
        self._apply_heap: list[tuple] = []
        self._apply_waiter = None
        self._release_sio = self._agent._release_sio
        self._drive_nibble = self._agent._drive_nibble
        self._sio_handle = self._find_sio_handle()
        self._device_sio = self._agent._read_nibble()
        # Device-plane CE# level after D_OUT_CE_NS (DUT-to-device CE# delay).
        # Parser clocking gates on this wire, not live source agent.ce_n.
        try:
            initial_ce = int(self._agent.ce_n) if self._agent.ce_n is not None else 1
        except (TypeError, ValueError):
            initial_ce = 1
        self._device_ce = initial_ce
        # Public, append-only observation stream consumed by Q-RXEDGE. It keeps
        # source, device-plane, and return-plane timestamps distinct without
        # widening the frozen parser API.
        self.timing_events = []

    def __getattr__(self, name):
        return getattr(self._device, name)

    @property
    def agent(self):
        """Expose the original agent for existing transaction-log consumers."""

        return self._agent

    def start(self):
        """Replace raw parser observation with delayed transport observation."""

        if self._task is not None:
            return self._task
        self._transport_active = True
        self._agent.stop()
        self._agent._thz_release_ns = self.timing_params["PSRAM_THZ_NS"]
        self._agent._release_sio = self._delayed_release
        self._agent._drive_nibble = self._delayed_drive
        self._task = cocotb.start_soon(self._run())
        self._agent._task = self._task
        return self._task

    def stop(self) -> None:
        """Stop delayed observation and restore the agent's release hook."""

        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.cancel_tasks()
        self._agent._release_sio = self._release_sio
        self._agent._drive_nibble = self._drive_nibble
        self._release_sio()

    def cancel_tasks(self) -> None:
        """Invalidate and cancel delayed callbacks owned by this wrapper.

        ``BringUp.stop()`` / ``dispose_run`` reach this method through the
        shared lifecycle participant registry. A generation change covers
        callbacks already runnable when cancellation arrives. Window ``clear()``
        must not leave the wrapper unable to see later CE#/SCK: if the
        observation task is still running, transport stays live and only
        in-flight delayed callbacks are dropped.
        """
        self._cancelled_generations.add(self._generation)
        self._generation += 1
        self._apply_heap.clear()
        self._apply_waiter = None
        tasks, self._delayed_tasks = self._delayed_tasks, set()
        for task in tasks:
            task.cancel()
        still_running = self._task is not None
        self._transport_active = still_running
        if still_running:
            self._agent._release_sio = self._delayed_release
            self._agent._drive_nibble = self._delayed_drive
        else:
            if self._agent._release_sio == self._delayed_release:
                self._agent._release_sio = self._release_sio
            if self._agent._drive_nibble == self._delayed_drive:
                self._agent._drive_nibble = self._drive_nibble

    def _start_delayed(self, coroutine) -> None:
        """Start and retain one transport callback until lifecycle cancellation."""
        self._delayed_tasks.add(cocotb.start_soon(coroutine))

    def _find_sio_handle(self):
        """Recover the source handle from the standard model reader closure.

        The model's public attach helpers construct ``read_nibble`` with
        ``uio_nibble_reader`` or ``sio_nibble_reader``.  Both capture their
        source cocotb handle in a closure.  Keeping this small adapter here
        avoids widening the frozen parser constructor merely for transport.
        """

        reader = self._agent._read_nibble
        freevars = getattr(reader, "__code__", None)
        closure = getattr(reader, "__closure__", None)
        if freevars is None or closure is None:
            return None
        for name, cell in zip(freevars.co_freevars, closure):
            if name != "handle":
                continue
            candidate = cell.cell_contents
            if hasattr(candidate, "value_change"):
                return candidate
        return None

    def _drain_due_applies(self) -> None:
        """Apply every due heap entry in ``(due_fs, sequence)`` order."""

        now_fs = int(get_sim_time(unit="fs"))
        while self._apply_heap and self._apply_heap[0][0] <= now_fs:
            _due_fs, _sequence, generation, callback, args = heapq.heappop(
                self._apply_heap
            )
            if generation != self._generation or not self._transport_active:
                continue
            callback(*args)

    def _ensure_apply_waiter(self) -> None:
        if self._apply_waiter is not None:
            return
        self._apply_waiter = cocotb.start_soon(self._apply_loop())
        self._delayed_tasks.add(self._apply_waiter)

    async def _apply_loop(self) -> None:
        """Wait for the next due time, then drain same-time entries by sequence."""

        try:
            while self._apply_heap:
                due_fs = self._apply_heap[0][0]
                now_fs = int(get_sim_time(unit="fs"))
                if due_fs > now_fs:
                    await Timer(due_fs - now_fs, unit="fs")
                    continue
                self._drain_due_applies()
        finally:
            self._apply_waiter = None

    def _schedule(self, delay_ns: float, callback, *args) -> None:
        """Queue a device-plane callback; same-time entries keep schedule order."""

        self._sequence += 1
        sequence = self._sequence
        generation = self._generation
        if not self._transport_active:
            return

        # Zero means unannotated / no transport delay, not a physical claim.
        now_fs = int(get_sim_time(unit="fs"))
        delay_fs = max(0, _ns_to_fs(delay_ns))
        due_fs = now_fs + delay_fs
        heapq.heappush(
            self._apply_heap,
            (due_fs, sequence, generation, callback, args),
        )
        if delay_fs == 0:
            self._drain_due_applies()
        else:
            self._ensure_apply_waiter()

    def _delayed_drive(self, nibble: int) -> None:
        """Delay model SIO value and OE by ``D_OUT_OE_NS`` on the return plane.

        ``D_OUT_OE_NS`` is the TB output-enable delay toward the device/return
        plane (``TB_TCO_OE_NS + TB_FLIGHT_OUT_OE_NS``).  The parser does not
        sample ASIC OE; this only defers the model's own drive enable.
        """

        self._schedule(
            self.timing_params["D_OUT_OE_NS"],
            self._drive_nibble,
            nibble,
        )

    def _delayed_release(self) -> None:
        """Hold then release model SIO OE after ``tHZ`` plus ``D_OUT_OE_NS``.

        ``PSRAM_THZ_NS`` / ``tHZ`` is CE# high to SIO Hi-Z.  ``D_OUT_OE_NS``
        further delays the OE clear on the return plane when non-zero.
        """

        if not self._transport_active or self._agent.selected:
            self._release_sio()
            return
        delay_ns = (
            self.timing_params["PSRAM_THZ_NS"] + self.timing_params["D_OUT_OE_NS"]
        )
        if delay_ns <= 0.0:
            self._release_sio()
            return

        def release_if_deselected() -> None:
            if not self._agent.selected:
                self._release_sio()

        self._schedule(delay_ns, release_if_deselected)

    def _launch_read_nibble(
        self,
        nibble: int,
        generation: int,
        *,
        source_fall_fs: int,
        device_fall_fs: int,
    ) -> None:
        """Apply tACLK then return flight, discarding stale responses."""
        self.timing_events.append(
            {
                "kind": "read-launch",
                "generation": generation,
                "nibble": nibble & 0xF,
                "source_fall_fs": source_fall_fs,
                "device_fall_fs": device_fall_fs,
            }
        )

        async def delayed_launch():
            await Timer(self.timing_params["PSRAM_TACLK_NS"], unit="ns")
            if generation != self._generation or not self._agent.selected:
                if generation in self._cancelled_generations:
                    return
                self.timing_events.append(
                    {
                        "kind": "read-stale",
                        "generation": generation,
                        "nibble": nibble & 0xF,
                        "time_fs": int(get_sim_time(unit="fs")),
                    }
                )
                return
            # Zero means unannotated / no transport delay, not a physical claim.
            din_ns = self.timing_params["D_IN_SIO_NS"]
            if din_ns > 0:
                await Timer(din_ns, unit="ns")
            if generation != self._generation or not self._agent.selected:
                if generation in self._cancelled_generations:
                    return
                self.timing_events.append(
                    {
                        "kind": "read-stale",
                        "generation": generation,
                        "nibble": nibble & 0xF,
                        "time_fs": int(get_sim_time(unit="fs")),
                    }
                )
                return

            # Return-plane OE delay (D_OUT_OE_NS) via the ordered apply queue.
            # Use the raw drive hook so _delayed_drive does not stack a second
            # D_OUT_OE_NS on top of this schedule.
            def commit_read() -> None:
                if generation != self._generation or not self._agent.selected:
                    if generation in self._cancelled_generations:
                        return
                    self.timing_events.append(
                        {
                            "kind": "read-stale",
                            "generation": generation,
                            "nibble": nibble & 0xF,
                            "time_fs": int(get_sim_time(unit="fs")),
                        }
                    )
                    return
                self._drive_nibble(nibble)
                self.timing_events.append(
                    {
                        "kind": "read-input-valid",
                        "generation": generation,
                        "nibble": nibble & 0xF,
                        "time_fs": int(get_sim_time(unit="fs")),
                        "source_fall_fs": source_fall_fs,
                        "device_fall_fs": device_fall_fs,
                    }
                )

            self._schedule(self.timing_params["D_OUT_OE_NS"], commit_read)

        self._start_delayed(delayed_launch())

    def _on_device_fall(self, source_fall_fs: int) -> None:
        """Run the parser's falling-edge action with response timing separated."""

        # Clock only while device-plane CE# is low (after D_OUT_CE_NS).
        if self._device_ce != 0:
            return
        agent = self._agent
        if agent.phase != "DATA" or agent._command.direction != "read":
            agent._release_sio()
            return
        nibble = agent._command.handler.on_data_fall(agent._access)
        if nibble is None:
            agent._release_sio()
        else:
            self._launch_read_nibble(
                nibble,
                self._generation,
                source_fall_fs=source_fall_fs,
                device_fall_fs=int(get_sim_time(unit="fs")),
            )

    def _on_device_rise(self, nibble) -> None:
        """Sample the transported SIO value at the delayed device SCK edge."""

        # Clock only while device-plane CE# is low (after D_OUT_CE_NS).
        if self._device_ce != 0:
            return
        agent = self._agent
        reader = agent._read_nibble
        agent._read_nibble = lambda: nibble
        try:
            agent._on_sck_rise()
        finally:
            agent._read_nibble = reader

    async def _run(self) -> None:
        """Collect raw source edges and dispatch independent device-plane tasks."""

        agent = self._agent
        prev_ce = agent.ce_n
        try:
            prev_sck = int(agent._sck.value)
        except ValueError:
            prev_sck = None
        rst_n = getattr(agent, "_rst_n", None)
        try:
            prev_rst = None if rst_n is None else int(rst_n.value)
        except ValueError:
            prev_rst = None

        while True:
            triggers = [agent._sck.value_change, agent._ce_n.value_change]
            if self._sio_handle is not None:
                triggers.append(self._sio_handle.value_change)
            if rst_n is not None and hasattr(rst_n, "value_change"):
                triggers.append(rst_n.value_change)
            await First(*triggers)

            # Falling rst_n (sync active-low ASIC reset) aborts immediately.
            # wrap_device stopped agent._run, so this path must call note_reset
            # itself. Classify as RESET-TRUNCATED (in-reset/truncated sample;
            # not a fail). Do this before CE#/SCK so a same-timestep CE# rise
            # cannot retire the frame as an ordinary termination.
            if rst_n is not None:
                try:
                    rst = int(rst_n.value)
                except ValueError:
                    rst = None
                if prev_rst == 1 and rst == 0:
                    agent.note_reset()
                prev_rst = rst

            ce = agent.ce_n
            try:
                sck = int(agent._sck.value)
            except ValueError:
                sck = None

            if self._sio_handle is not None:
                source_sio = agent._read_nibble()
                if source_sio != self._device_sio:
                    self._schedule(
                        self.timing_params["D_OUT_SIO_NS"],
                        setattr,
                        self,
                        "_device_sio",
                        source_sio,
                    )

            # CE# and SCK are independent delayed wires (D_OUT_CE_NS /
            # D_OUT_SCK_NS). Schedule every source SCK edge; parser callbacks
            # gate on delayed _device_ce so CE#>SCK skew cannot drop the first
            # nibble before _begin_transaction, and SCK>CE# skew cannot drop
            # the last nibble after source CE# has risen.
            if prev_ce != 0 and ce == 0:
                def begin_transaction():
                    self._device_ce = 0
                    agent._begin_transaction()

                self._schedule(
                    self.timing_params["D_OUT_CE_NS"], begin_transaction
                )
            elif prev_ce == 0 and ce != 0:
                def end_transaction():
                    self._device_ce = 1
                    self._generation += 1
                    agent._ce_rise_ns = get_sim_time(unit="ns")
                    agent._end_transaction()
                    # Only when D_OUT_CE_NS (DUT-to-device CE# transport delay)
                    # is non-zero is there a post-DUT-rise window where a
                    # device-plane SCK-fall can open Q-RXEDGE (each launched
                    # read nibble must be captured exactly once on the
                    # following rising SCK) after _on_ce_rise already
                    # scope-closed. With delay 0, emitting ce-rise-committed
                    # in the same evaluate as CE# rise races ahead of the
                    # prefetch silent-resolve in _on_ce_rise and falsely
                    # audits that nibble as reason=scope-close.
                    if self.timing_params["D_OUT_CE_NS"] > 0.0:
                        self.timing_events.append(
                            {
                                "kind": "ce-rise-committed",
                                "generation": self._generation,
                                "device_id": self.device_id,
                            }
                        )

                self._schedule(
                    self.timing_params["D_OUT_CE_NS"], end_transaction
                )

            if prev_sck is not None:
                if sck == 1 and prev_sck == 0:
                    self._schedule(
                        self.timing_params["D_OUT_SCK_NS"],
                        self._on_device_rise,
                        self._device_sio,
                    )
                elif sck == 0 and prev_sck == 1:
                    self._schedule(
                        self.timing_params["D_OUT_SCK_NS"],
                        self._on_device_fall,
                        int(get_sim_time(unit="fs")),
                    )

            # Unresolved SCK is not an edge: keep last known 0/1 so a later
            # resolved level does not lose the edge baseline (Z/X = no-edge).
            # 1->Z is not a fall; 1->Z->driven 0 is still a fall.
            prev_ce = ce
            if sck is not None:
                prev_sck = sck


def wrap_device(device, profile: str = "ideal", **overrides):
    """Return an already-started timed wrapper around *device*.

    Every profile constructs ``_TimedPsramDevice`` and calls ``start()``.
    Zero delays drain synchronously on the apply heap; ``ideal`` still
    exposes ``timing_params`` / ``timing_events`` for monitors and tests.
    """

    params = resolve_timing_params(profile, **overrides)
    wrapped = _TimedPsramDevice(device, params)
    wrapped.start()
    return wrapped

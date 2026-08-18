"""Runtime transport timing for an attached :mod:`models.psram` device.

``wrap_device`` is deliberately the sole delay layer.  It delays the parser's
view of DUT output transitions and delays read-data drive at the return plane;
the parser itself remains a protocol model.  ``ideal`` is an exact passthrough
so the pre-M3 attachment behavior remains available without a second code path.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

import cocotb
from cocotb.simtime import get_sim_time
from cocotb.triggers import First, Timer


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


def _profile_defaults(profile: str) -> dict[str, float | None]:
    profile = profile.lower()
    if profile == "ideal":
        params = {name: 0.0 for name in _DEVICE_AC}
        params.update(_TB_PATH)
        return params
    if profile in ("nominal", "sweep"):
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

    An unwrapped device is the ideal profile.  This keeps acceptance reporting
    simple while preserving ``wrap_device(..., profile="ideal") is device``.
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
        self._release_sio = self._agent._release_sio
        self._sio_handle = self._find_sio_handle()
        self._device_sio = self._agent._read_nibble()
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
        self._task = cocotb.start_soon(self._run())
        self._agent._task = self._task
        return self._task

    def stop(self) -> None:
        """Stop delayed observation and restore the agent's release hook."""

        self.cancel_tasks()
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._agent._release_sio = self._release_sio
        self._release_sio()

    def cancel_tasks(self) -> None:
        """Invalidate and cancel delayed callbacks owned by this wrapper.

        ``BringUp.stop()`` reaches this method through the shared lifecycle
        participant registry.  A generation change covers callbacks already
        runnable when cancellation arrives, while task cancellation prevents
        delayed transport from surviving into a later bring-up.
        """
        self._cancelled_generations.add(self._generation)
        self._generation += 1
        self._transport_active = False
        if self._agent._release_sio == self._delayed_release:
            self._agent._release_sio = self._release_sio
        tasks, self._delayed_tasks = self._delayed_tasks, set()
        for task in tasks:
            task.cancel()

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

    def _schedule(self, delay_ns: float, callback, *args) -> None:
        self._sequence += 1
        sequence = self._sequence
        generation = self._generation
        if not self._transport_active:
            return

        # Zero means unannotated / no transport delay, not a physical claim.
        if delay_ns <= 0:
            _ = sequence
            if generation == self._generation:
                callback(*args)
            return

        async def delayed():
            await Timer(delay_ns, unit="ns")
            # Tasks created in source-event order remain independently pending.
            # The sequence is retained for waveform/debug consumers and avoids
            # relying on a mutable source value after a delay.
            _ = sequence
            if generation == self._generation:
                callback(*args)

        self._start_delayed(delayed())

    def _delayed_release(self) -> None:
        """Hold the final device value for modeled tHZ after CE# rises."""

        if (
            not self._transport_active
            or self._agent.selected
            or self.timing_params["PSRAM_THZ_NS"] == 0.0
        ):
            self._release_sio()
            return
        generation = self._generation

        async def release_after_thz():
            await Timer(self.timing_params["PSRAM_THZ_NS"], unit="ns")
            if generation == self._generation and not self._agent.selected:
                self._release_sio()

        self._start_delayed(release_after_thz())

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
            if generation == self._generation and self._agent.selected:
                self._agent._drive_nibble(nibble)
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
            else:
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

        self._start_delayed(delayed_launch())

    def _on_device_fall(self, source_fall_fs: int) -> None:
        """Run the parser's falling-edge action with response timing separated."""

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

        while True:
            triggers = [agent._sck.value_change, agent._ce_n.value_change]
            if self._sio_handle is not None:
                triggers.append(self._sio_handle.value_change)
            await First(*triggers)
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

            if prev_ce != 0 and ce == 0:
                self._schedule(self.timing_params["D_OUT_CE_NS"], agent._begin_transaction)
            elif prev_ce == 0 and ce != 0:
                def end_transaction():
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

                self._schedule(self.timing_params["D_OUT_CE_NS"], end_transaction)

            if ce == 0 and prev_sck is not None:
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

            prev_ce, prev_sck = ce, sck


def wrap_device(device, profile: str = "ideal", **overrides):
    """Return *device* with the selected profile's transport delays.

    ``ideal`` with no overrides returns the original object and exposes no
    timing transport.  ``nominal`` and ``sweep`` return an already-started,
    transparent wrapper that replaces the raw agent task.
    """

    params = resolve_timing_params(profile, **overrides)
    if profile.lower() == "ideal" and not overrides:
        return device
    wrapped = _TimedPsramDevice(device, params)
    wrapped.start()
    return wrapped

"""Optional QPI timing delay layer wrapping ``models.psram`` (``Q-*`` checks).

Milestone M1+ applies named ``TIMING_PROFILE`` parameters from
``04-timing-in-sim.md``.
"""


def wrap_device(device, profile: str = "ideal"):
    """Return *device* wrapped with profile-specific pin delays.

    Raises:
        NotImplementedError: Phase 0 scaffold only.
    """
    raise NotImplementedError("M1+ implements PSRAM timing wrapper")

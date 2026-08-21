"""Host pytest of firmware/_compat.py (MicroPython dataclass shim)."""

import pytest

from firmware._compat import FrozenInstanceError, dataclass, field


def test_extra_positionals_rejected():
    @dataclass
    class Point:
        x: int = 0
        y: int = 0

    with pytest.raises(TypeError, match="positional"):
        Point(1, 2, 3)


def test_unfrozen_not_hashable():
    @dataclass
    class Box:
        n: int = 0

    with pytest.raises(TypeError):
        hash(Box(1))


def test_frozen_is_hashable_and_raises_frozen_instance_error():
    @dataclass(frozen=True)
    class Ice:
        n: int = 0

    ice = Ice(1)
    assert hash(ice) == hash(Ice(1))
    with pytest.raises(FrozenInstanceError):
        ice.n = 2
    with pytest.raises(AttributeError):
        ice.n = 3


def test_unannotated_class_does_not_grow_equality_fields():
    @dataclass
    class Plain:
        EQUALITY_FIELDS = ("index", "kind")
        helper = ("not", "a", "field")

    obj = Plain()
    names = [name for name, _spec in obj._dc_specs]
    assert "EQUALITY_FIELDS" not in names
    assert "helper" not in names
    assert obj.EQUALITY_FIELDS == ("index", "kind")


def test_post_init_runs_on_shim():
    @dataclass(frozen=True)
    class Flag:
        quit: int = 0

        def __post_init__(self):
            object.__setattr__(self, "quit", bool(self.quit))

    assert Flag(quit=1).quit is True
    assert Flag(quit=1) == Flag(quit=True)


def test_compare_false_excluded_from_eq():
    @dataclass
    class Rec:
        n: int
        meta: object = field(default=None, compare=False)

    assert Rec(1, meta="a") == Rec(1, meta="b")

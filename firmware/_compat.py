"""Minimal dataclasses stand-in for MicroPython UF2 images without the stdlib module.

CPython tests use the real dataclasses module. This shim is only imported when
`import dataclasses` fails. It covers `@dataclass` / `@dataclass(frozen=True)`,
`field(default=..., compare=...)`, and `replace` as used by tcd.py and chain.py.
"""


class _MISSING_TYPE:
    pass


MISSING = _MISSING_TYPE()


class _Field:
    def __init__(self, default=MISSING, default_factory=MISSING, compare=True):
        self.default = default
        self.default_factory = default_factory
        self.compare = compare


def field(*, default=MISSING, default_factory=MISSING, compare=True):
    if default is not MISSING and default_factory is not MISSING:
        raise ValueError("cannot specify both default and default_factory")
    return _Field(default, default_factory, compare)


def _iter_field_specs(cls):
    annotations = getattr(cls, "__annotations__", None)
    if annotations:
        names = list(annotations.keys())
    else:
        names = [
            name
            for name, value in cls.__dict__.items()
            if not name.startswith("_") and not callable(value)
        ]
    specs = []
    for name in names:
        value = cls.__dict__.get(name, MISSING)
        if isinstance(value, _Field):
            specs.append((name, value))
        elif value is not MISSING and not callable(value):
            specs.append((name, _Field(default=value)))
        else:
            specs.append((name, _Field()))
    return specs


def dataclass(cls=None, *, frozen=False):
    def wrap(cls):
        specs = _iter_field_specs(cls)

        def __init__(self, *args, **kwargs):
            values = {}
            idx = 0
            for name, spec in specs:
                if idx < len(args):
                    values[name] = args[idx]
                    idx += 1
                elif name in kwargs:
                    values[name] = kwargs.pop(name)
                elif spec.default_factory is not MISSING:
                    values[name] = spec.default_factory()
                elif spec.default is not MISSING:
                    values[name] = spec.default
                else:
                    raise TypeError("missing argument %s" % name)
            if kwargs:
                raise TypeError("unexpected kwargs %s" % sorted(kwargs))
            object.__setattr__(self, "_dc_frozen", frozen)
            for name, value in values.items():
                object.__setattr__(self, name, value)

        def __repr__(self):
            parts = ["%s=%r" % (name, getattr(self, name)) for name, _spec in specs]
            return "%s(%s)" % (type(self).__name__, ", ".join(parts))

        def __eq__(self, other):
            if other.__class__ is not self.__class__:
                return NotImplemented
            for name, spec in specs:
                if not spec.compare:
                    continue
                if getattr(self, name) != getattr(other, name):
                    return False
            return True

        def __hash__(self):
            items = tuple(
                getattr(self, name) for name, spec in specs if spec.compare
            )
            return hash((type(self), items))

        cls.__init__ = __init__
        cls.__repr__ = __repr__
        cls.__eq__ = __eq__
        cls._dc_specs = specs
        cls._dc_frozen = frozen
        if frozen:
            def __setattr__(self, name, value):
                if getattr(self, "_dc_frozen", False):
                    raise AttributeError("cannot assign to field %r" % name)
                object.__setattr__(self, name, value)

            def __delattr__(self, name):
                raise AttributeError("cannot delete field %r" % name)

            cls.__setattr__ = __setattr__
            cls.__delattr__ = __delattr__
            cls.__hash__ = __hash__
        return cls

    if cls is None:
        return wrap
    return wrap(cls)


def replace(obj, **changes):
    specs = getattr(obj, "_dc_specs", None)
    if specs is None:
        raise TypeError("replace() requires a dataclass instance")
    kwargs = {}
    for name, _spec in specs:
        if name in changes:
            kwargs[name] = changes.pop(name)
        else:
            kwargs[name] = getattr(obj, name)
    if changes:
        raise TypeError("unexpected kwargs %s" % sorted(changes))
    return type(obj)(**kwargs)

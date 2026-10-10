# Build compatibility shim, loaded automatically because the Makefile
# exports PYTHONPATH=. (Python imports `sitecustomize` at startup).
#
# The karakul FEZ plugins were written against a fork of fontTools
# ("variable-arithmetic" branch) whose VariableScalar supports arithmetic
# and has a `.default` property. Current fontTools moved that logic into a
# separate builder, so we add the small subset the plugins rely on here.
# Gulzar has a single master, so every scalar has exactly one value.
try:
    # fez's Kerning verb reads `font.features.namedClasses`; babelfont 3
    # calls the same dictionary `classes`.
    from babelfont.Features import Features as _BabelfontFeatures

    if not hasattr(_BabelfontFeatures, "namedClasses"):
        _BabelfontFeatures.namedClasses = property(lambda self: self.classes)
except ImportError:  # pragma: no cover
    pass

try:
    from fontTools.feaLib.variableScalar import VariableScalar, Location
except ImportError:  # pragma: no cover
    VariableScalar = None

if VariableScalar is not None and not hasattr(VariableScalar, "default"):

    def _loc(loc):
        if isinstance(loc, dict):
            return Location(loc)
        return tuple(loc)

    def value_at_location(self, location):
        loc = _loc(location)
        if loc in self.values:
            return self.values[loc]
        if len(self.values) == 1:
            return next(iter(self.values.values()))
        raise ValueError("Cannot interpolate %r at %r" % (self, location))

    def _default(self):
        if () in self.values:
            return self.values[()]
        if not self.values:
            return 0
        if len(self.values) == 1:
            return next(iter(self.values.values()))
        # Single-master font: the default location is the all-default one
        return min(self.values.items(), key=lambda kv: sum(abs(v) for _, v in kv[0]))[1]

    def _scalar(other):
        if isinstance(other, VariableScalar):
            return other.default
        return other

    def _map(self, fn):
        new = VariableScalar()
        new.axes = self.axes
        for loc, value in self.values.items():
            new.values[loc] = fn(value)
        return new

    def _num(op):
        def method(self, other):
            if not isinstance(other, (VariableScalar, int, float)):
                return NotImplemented
            if isinstance(other, VariableScalar) and other.does_vary:
                new = VariableScalar()
                new.axes = self.axes
                for loc, value in self.values.items():
                    new.values[loc] = op(value, other.value_at_location(loc))
                return new
            o = _scalar(other)
            return _map(self, lambda v: op(v, o))

        return method

    VariableScalar.value_at_location = value_at_location
    VariableScalar.default = property(_default)
    # Results are font units, so keep them integral (bin averages are floats).
    def _units(v):
        return round(v) if isinstance(v, float) else v

    VariableScalar.__add__ = _num(lambda a, b: _units(a + b))
    VariableScalar.__radd__ = _num(lambda a, b: _units(b + a))
    VariableScalar.__sub__ = _num(lambda a, b: _units(a - b))
    VariableScalar.__rsub__ = _num(lambda a, b: _units(b - a))
    VariableScalar.__mul__ = _num(lambda a, b: int(a * b))
    VariableScalar.__rmul__ = _num(lambda a, b: int(b * a))
    VariableScalar.__truediv__ = _num(lambda a, b: int(a / b))
    VariableScalar.__pos__ = lambda self: _map(self, lambda v: +v)
    VariableScalar.__neg__ = lambda self: _map(self, lambda v: -v)
    VariableScalar.__abs__ = lambda self: _map(self, abs)
    VariableScalar.__lt__ = lambda self, o: self.default < _scalar(o)
    VariableScalar.__le__ = lambda self, o: self.default <= _scalar(o)
    VariableScalar.__gt__ = lambda self, o: self.default > _scalar(o)
    VariableScalar.__ge__ = lambda self, o: self.default >= _scalar(o)

try:
    # glyphtools measures glyph extents on the curves, so a curve whose
    # extreme falls between nodes gives a fractional metric; the Nastaliq
    # plugins write these straight into feature code, which needs integers.
    import glyphtools as _glyphtools

    _orig_metrics = _glyphtools.get_glyph_metrics

    # The Nastaliq plugins group letters by "rise" (the height of the join),
    # and Gulzar's hand-written spacing and dot rules were tuned to those
    # groups. Where the reshaping deliberately moved a join, the build keeps
    # grouping the glyph by its original rise (scripts/nastaliq-reshape.py
    # writes these to sources/rise-pins.json).
    import json as _json
    import os as _os

    try:
        with open(_os.path.join("sources", "rise-pins.json")) as _fh:
            _rise_pins = _json.load(_fh)
    except (OSError, ValueError):
        _rise_pins = {}

    def _rounded_metrics(*args, **kwargs):
        metrics = _orig_metrics(*args, **kwargs)
        if isinstance(metrics, dict):
            for key, value in metrics.items():
                if isinstance(value, float):
                    metrics[key] = round(value)
            glyphname = args[1] if len(args) > 1 else kwargs.get("glyphname")
            if glyphname in _rise_pins and "rise" in metrics:
                metrics["rise"] = _rise_pins[glyphname]
        return metrics

    if not getattr(_orig_metrics, "_rounded", False):
        _rounded_metrics._rounded = True
        _glyphtools.get_glyph_metrics = _rounded_metrics
except ImportError:  # pragma: no cover
    pass

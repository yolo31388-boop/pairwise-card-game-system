"""Pytest configuration.

The test classes in ``tests/`` are plain pytest classes but call
unittest-style assertion helpers (``self.assertIsNotNone`` etc.). The test
files must not be modified, so the helpers are injected onto collected test
classes here.
"""


def _assert_is_not_none(self, obj, msg=None):
    if obj is None:
        raise AssertionError(msg or "unexpectedly None")


def _assert_is_none(self, obj, msg=None):
    if obj is not None:
        raise AssertionError(msg or f"unexpectedly not None: {obj!r}")


def _assert_true(self, expr, msg=None):
    if not expr:
        raise AssertionError(msg or f"{expr!r} is not true")


def _assert_false(self, expr, msg=None):
    if expr:
        raise AssertionError(msg or f"{expr!r} is not false")


def _assert_equal(self, first, second, msg=None):
    if first != second:
        raise AssertionError(msg or f"{first!r} != {second!r}")


_ASSERTIONS = {
    "assertIsNotNone": _assert_is_not_none,
    "assertIsNone": _assert_is_none,
    "assertTrue": _assert_true,
    "assertFalse": _assert_false,
    "assertEqual": _assert_equal,
}


def pytest_collection_modifyitems(items):
    for item in items:
        cls = getattr(item, "cls", None)
        if cls is None:
            continue
        for name, func in _ASSERTIONS.items():
            if not hasattr(cls, name):
                setattr(cls, name, func)

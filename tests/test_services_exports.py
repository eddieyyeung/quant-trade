"""The service package's lazy export table is internally consistent.

Registering a public name takes three edits — the ``TYPE_CHECKING`` import
block, ``__all__`` and ``_MODULE_BY_NAME`` — and forgetting the third leaves a
name that looks exported (it is listed, it type-checks, ruff is happy) but
raises ``ImportError`` at runtime, because ``__getattr__`` resolves through
``_MODULE_BY_NAME`` alone. A test is cheaper than remembering.
"""

from __future__ import annotations

import quant_trade.services as services
from quant_trade.services import _MODULE_BY_NAME
from quant_trade.services import __all__ as exported


def test_every_exported_name_resolves() -> None:
    """``__all__`` is a promise; importing each name keeps it."""
    missing = [name for name in exported if not hasattr(services, name)]
    assert missing == [], f"listed in __all__ but not importable: {missing}"


def test_every_exported_name_is_mapped() -> None:
    """A name with no module mapping can never be resolved lazily."""
    unmapped = sorted(set(exported) - set(_MODULE_BY_NAME))
    assert unmapped == [], f"listed in __all__ but absent from _MODULE_BY_NAME: {unmapped}"


def test_no_exported_name_is_listed_twice() -> None:
    assert len(exported) == len(set(exported))


def test_mapped_names_are_importable_from_their_module() -> None:
    """Every mapping points at a real attribute, not a typo or a stale move."""
    import importlib

    broken = []
    for name, module_path in _MODULE_BY_NAME.items():
        module = importlib.import_module(module_path)
        if not hasattr(module, name):
            broken.append(f"{name} -> {module_path}")
    assert broken == []

"""Every module in this package is imported automatically, so dropping a new
file with a ``@register("my_type")`` class in here is all it takes to add a widget."""
import importlib
import pkgutil

from .base import Context, Widget, WidgetError, get_widget_class, register, registered_types

for _mod in pkgutil.iter_modules(__path__):
    if _mod.name != "base":
        importlib.import_module(f"{__name__}.{_mod.name}")

__all__ = ["Context", "Widget", "WidgetError", "get_widget_class", "register", "registered_types"]

"""County plugins. Each module registers a factory producing that county's connectors.

Import this package to populate the registry. Adding a county is a new module here
plus a fixture directory; nothing else in the system changes.
"""

from ..sources.base import registry  # noqa: F401
from . import mecklenburg  # noqa: F401

__all__ = ["registry", "mecklenburg"]

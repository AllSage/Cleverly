"""Small call helpers for owner-aware APIs with narrow test doubles."""

import inspect
from typing import Any, Callable, Optional


def call_with_optional_owner(func: Callable[..., Any], *args: Any, owner: Optional[str] = None, **kwargs: Any) -> Any:
    """Call `func`, passing owner only when its signature accepts it."""
    if owner is not None:
        try:
            if "owner" in inspect.signature(func).parameters:
                kwargs["owner"] = owner
        except (TypeError, ValueError):
            pass
    return func(*args, **kwargs)

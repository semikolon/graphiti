import os as _os
import warnings as _warnings


def _check_editable_install():
    """Warn loudly if graphiti_core is loaded from a stale site-packages copy."""
    my_path = _os.path.abspath(__file__)
    if 'site-packages' in my_path:
        _warnings.warn(
            f'graphiti_core is loaded from a stale site-packages copy: {my_path}\n'
            f'Source edits will be SILENTLY IGNORED.\n'
            f'Fix: run `uv pip install -e .` from the graphiti-core root,\n'
            f'or use `uv sync` with workspace configuration.',
            UserWarning,
            stacklevel=2,
        )


_check_editable_install()

from .graphiti import Graphiti

__all__ = ['Graphiti']

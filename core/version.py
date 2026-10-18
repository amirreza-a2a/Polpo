"""
core.version
~~~~~~~~~~~~

Authoritative single-source application version and identity metadata.
Pure Python module with zero external dependencies, compliant with core/ architectural rules.
"""

APP_NAME: str = "Polpo"
__version__: str = "0.1.0"
VERSION_INFO: tuple[int, int, int] = (0, 1, 0)

__all__ = ["APP_NAME", "__version__", "VERSION_INFO"]

"""Public surface-design/2 facade.

The implementation lives in :mod:`vector_native_cad` so mechanical program
execution and independent station-network validation share one canonical
validator.  This facade gives workbench commands and tests a stable module
name without introducing a second geometry authority.
"""
from .vector_native_cad import (  # noqa: F401
    CURVE_CLASSIFICATIONS,
    SURFACE_SCHEMA,
    VectorNativeCadError,
    execute_surface_design,
    surface_digest,
    validate_surface_design,
)

__all__ = [
    "CURVE_CLASSIFICATIONS", "SURFACE_SCHEMA", "VectorNativeCadError",
    "execute_surface_design", "surface_digest", "validate_surface_design",
]

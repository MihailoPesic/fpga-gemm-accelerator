"""UART client for the bounded Nexys DDR diagnostic."""

from .client import DDRDiagnostic, load_manifest

__all__ = ["DDRDiagnostic", "load_manifest"]

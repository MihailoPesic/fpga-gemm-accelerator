"""Host API for the serial DDR-backed Nexys GEMM interface."""

from .client import Descriptor, GEMM, PackedInputs, golden, load_manifest, pack_inputs, validate
from host.preview.protocol import DeviceError, ProtocolError, TransportError

__all__ = ['Descriptor', 'GEMM', 'PackedInputs', 'golden', 'load_manifest',
           'pack_inputs', 'validate', 'DeviceError', 'ProtocolError', 'TransportError']

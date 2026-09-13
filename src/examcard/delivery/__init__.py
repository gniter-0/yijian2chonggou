"""Concrete delivery backends for the frozen P6 delivery contract."""

from .filesystem_v1 import (
    FILESYSTEM_DELIVERY_ALLOWED_SCOPE,
    FILESYSTEM_DELIVERY_MARKER_FILENAME,
    FILESYSTEM_DELIVERY_MARKER_SCHEMA,
    FILESYSTEM_DELIVERY_TARGET_TYPE,
    FilesystemDeliveredFile,
    FilesystemDeliveryMarker,
    FilesystemDirectoryDeliveryAdapter,
    build_filesystem_delivery_target,
)

__all__ = (
    "FILESYSTEM_DELIVERY_ALLOWED_SCOPE",
    "FILESYSTEM_DELIVERY_MARKER_FILENAME",
    "FILESYSTEM_DELIVERY_MARKER_SCHEMA",
    "FILESYSTEM_DELIVERY_TARGET_TYPE",
    "FilesystemDeliveredFile",
    "FilesystemDeliveryMarker",
    "FilesystemDirectoryDeliveryAdapter",
    "build_filesystem_delivery_target",
)

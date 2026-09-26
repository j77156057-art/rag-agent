"""Domain neutral preview protocol for autonomous workflow evidence."""
from .core import (
    PREVIEW_SCHEMA,
    PreviewAdapter,
    build_preview_bundle,
    infer_artifact_kind,
    normalize_artifact,
    preview_adapters,
    register_preview_adapter,
    unregister_preview_adapter,
)
from .domains import adapt_eda, adapt_game, adapt_native, register_builtin_adapters

__all__ = ["PREVIEW_SCHEMA", "PreviewAdapter", "build_preview_bundle", "infer_artifact_kind",
           "normalize_artifact", "preview_adapters", "register_preview_adapter",
           "unregister_preview_adapter", "adapt_eda", "adapt_game", "adapt_native",
           "register_builtin_adapters"]

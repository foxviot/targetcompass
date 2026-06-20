from .contracts import DatabaseAdapterContext, DatabaseAdapterResult
from .sqlite import SQLiteEvidenceAdapter
from .standard_sources import (
    DisGeNETAdapter,
    GWASCatalogAdapter,
    HPAAdapter,
    MSigDBAdapter,
    OpenTargetsAdapter,
    ReactomeAdapter,
    UniProtAdapter,
)
from .tabular import TabularEvidenceAdapter


ADAPTERS = [
    UniProtAdapter(),
    HPAAdapter(),
    OpenTargetsAdapter(),
    DisGeNETAdapter(),
    GWASCatalogAdapter(),
    MSigDBAdapter(),
    ReactomeAdapter(),
    TabularEvidenceAdapter(),
    SQLiteEvidenceAdapter(),
]


def available_database_adapters() -> list[dict[str, str]]:
    return [
        {"adapter_id": "auto", "label": "Auto-detect", "description": "Choose an adapter from file extension and schema."},
        *[
            {"adapter_id": adapter.adapter_id, "label": adapter.label, "description": adapter.description}
            for adapter in ADAPTERS
        ],
    ]


def adapt_database(context: DatabaseAdapterContext) -> DatabaseAdapterResult:
    candidates = ADAPTERS if context.adapter in {"", "copy", "auto"} else [a for a in ADAPTERS if a.adapter_id == context.adapter]
    for adapter in candidates:
        if adapter.can_handle(context):
            return adapter.adapt(context)
    return DatabaseAdapterResult(context.adapter or "auto", None, 0, "No database adapter matched this resource.")

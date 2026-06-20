from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class DatabaseAdapterContext:
    project_dir: Path
    resource_id: str
    source_path: Path
    adapter: str


@dataclass
class DatabaseAdapterResult:
    adapter_id: str
    normalized_evidence: Path | None
    row_count: int
    message: str
    input_rows: int = 0
    dropped_rows: int = 0
    field_mapping: dict[str, str] | None = None
    normalized_outputs: dict[str, str] | None = None


class DatabaseAdapter(Protocol):
    adapter_id: str
    label: str
    description: str

    def can_handle(self, context: DatabaseAdapterContext) -> bool:
        ...

    def adapt(self, context: DatabaseAdapterContext) -> DatabaseAdapterResult:
        ...

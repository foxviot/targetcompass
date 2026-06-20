from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class MethodContext:
    project_dir: Path
    interest: str
    parser: str
    selected_datasets: list[str]
    confirmed: bool
    idea_count: int
    role_id: str = ""
    input_refs: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class MethodResult:
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MethodSpec:
    method_id: str
    stage: str
    label: str
    description: str
    runner: Callable[[MethodContext], MethodResult]
    gpt_compatible: bool = True
    human_replaceable: bool = True

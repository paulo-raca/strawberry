from __future__ import annotations

from dataclasses import InitVar, dataclass, field
from typing import Any, Callable

from strawberry.description_sources import DescriptionSources
from strawberry.schema.name_converter import NameConverter


@dataclass
class StrawberryConfig:
    auto_camel_case: InitVar[bool] = None  # pyright: reportGeneralTypeIssues=false
    name_converter: NameConverter = field(default_factory=NameConverter)
    default_resolver: Callable[[Any, str], object] = getattr
    description_sources: DescriptionSources = DescriptionSources.STRAWBERRY_DESCRIPTIONS

    def __post_init__(
        self,
        auto_camel_case: bool,
    ):
        if auto_camel_case is not None:
            self.name_converter.auto_camel_case = auto_camel_case

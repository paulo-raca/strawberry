import dataclasses
from enum import Enum
from typing import List, Optional, Type, TypeVar

from strawberry.description_sources import DescriptionSources
from strawberry.object_type import _wrap_dataclass
from strawberry.types.type_resolver import _get_fields
from strawberry.utils.docstrings import Docstring

from .directive import directive_field
from .field import StrawberryField, field
from .utils.typing import __dataclass_transform__


class Location(Enum):
    SCHEMA = "schema"
    SCALAR = "scalar"
    OBJECT = "object"
    FIELD_DEFINITION = "field definition"
    ARGUMENT_DEFINITION = "argument definition"
    INTERFACE = "interface"
    UNION = "union"
    ENUM = "enum"
    ENUM_VALUE = "enum value"
    INPUT_OBJECT = "input object"
    INPUT_FIELD_DEFINITION = "input field definition"


@dataclasses.dataclass
class StrawberrySchemaDirective:
    python_name: str
    graphql_name: Optional[str]
    locations: List[Location]
    fields: List["StrawberryField"]
    description_sources: Optional[DescriptionSources] = None
    description: Optional[str] = None
    docstring: Optional[Docstring] = None
    repeatable: bool = False
    print_definition: bool = True
    origin: Optional[Type] = None


T = TypeVar("T", bound=Type)


@__dataclass_transform__(
    order_default=True,
    kw_only_default=True,
    field_descriptors=(directive_field, field, StrawberryField),
)
def schema_directive(
    *,
    locations: List[Location],
    description_sources: Optional[DescriptionSources] = None,
    description: Optional[str] = None,
    name: Optional[str] = None,
    repeatable: bool = False,
    print_definition: bool = True,
):
    def _wrap(cls: T) -> T:
        docstring = Docstring(cls)
        cls = _wrap_dataclass(cls)
        fields = _get_fields(cls)

        cls.__strawberry_directive__ = StrawberrySchemaDirective(
            python_name=cls.__name__,
            graphql_name=name,
            locations=locations,
            description_sources=description_sources,
            description=description,
            docstring=docstring,
            repeatable=repeatable,
            fields=fields,
            print_definition=print_definition,
            origin=cls,
        )

        return cls

    return _wrap


__all__ = ["Location", "StrawberrySchemaDirective", "schema_directive"]

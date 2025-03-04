from __future__ import annotations

import dataclasses
import sys
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
    cast,
)

from graphql import (
    GraphQLArgument,
    GraphQLDirective,
    GraphQLEnumType,
    GraphQLEnumValue,
    GraphQLField,
    GraphQLInputField,
    GraphQLInputObjectType,
    GraphQLInputType,
    GraphQLInterfaceType,
    GraphQLList,
    GraphQLNonNull,
    GraphQLNullableType,
    GraphQLObjectType,
    GraphQLOutputType,
    GraphQLResolveInfo,
    GraphQLScalarType,
    GraphQLUnionType,
    Undefined,
    ValueNode,
)
from graphql.language.directive_locations import DirectiveLocation

from strawberry.annotation import StrawberryAnnotation
from strawberry.arguments import StrawberryArgument, convert_arguments
from strawberry.custom_scalar import ScalarDefinition, ScalarWrapper
from strawberry.description_sources import DescriptionSources
from strawberry.directive import StrawberryDirective
from strawberry.enum import EnumDefinition, EnumValue
from strawberry.exceptions import (
    InvalidTypeInputForUnion,
    MissingTypesForGenericError,
    ScalarAlreadyRegisteredError,
    UnresolvedFieldTypeError,
)
from strawberry.field import UNRESOLVED, StrawberryField
from strawberry.lazy_type import LazyType
from strawberry.private import is_private
from strawberry.schema.config import StrawberryConfig
from strawberry.schema.types.scalar import _make_scalar_type
from strawberry.schema_directive import StrawberrySchemaDirective
from strawberry.type import (
    StrawberryAnnotated,
    StrawberryList,
    StrawberryOptional,
    StrawberryType,
)
from strawberry.types.info import Info
from strawberry.types.types import TypeDefinition
from strawberry.union import StrawberryUnion
from strawberry.unset import UNSET
from strawberry.utils.await_maybe import await_maybe
from strawberry.utils.docstrings import Docstring
from strawberry.utils.pick import pick_not_none

from . import compat
from .types.concrete_type import ConcreteType


# graphql-core expects a resolver for an Enum type to return
# the enum's *value* (not its name or an instance of the enum). We have to
# subclass the GraphQLEnumType class to enable returning Enum members from
# resolvers.
class CustomGraphQLEnumType(GraphQLEnumType):
    def __init__(self, enum: EnumDefinition, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.wrapped_cls = enum.wrapped_cls

    def serialize(self, output_value: Any) -> str:
        if isinstance(output_value, self.wrapped_cls):
            return output_value.name
        return super().serialize(output_value)

    def parse_value(self, input_value: str) -> Any:
        return self.wrapped_cls(super().parse_value(input_value))

    def parse_literal(
        self, value_node: ValueNode, _variables: Optional[Dict[str, Any]] = None
    ) -> Any:
        return self.wrapped_cls(super().parse_literal(value_node, _variables))


class GraphQLCoreConverter:
    # TODO: Make abstract

    # Extension key used to link a GraphQLType back into the Strawberry definition
    DEFINITION_BACKREF = "strawberry-definition"

    def __init__(
        self,
        config: StrawberryConfig,
        scalar_registry: Dict[object, Union[ScalarWrapper, ScalarDefinition]],
    ):
        self.type_map: Dict[str, ConcreteType] = {}
        self.config = config
        self.scalar_registry = scalar_registry

    def from_argument(
        self,
        argument: StrawberryArgument,
        description_sources: DescriptionSources,
        *,
        parent_resolver_docstring: Optional[Docstring] = None,
        parent_directive_docstring: Optional[Docstring] = None,
    ) -> GraphQLArgument:
        argument_type = cast(GraphQLInputType, self.from_maybe_optional(argument.type))
        default_value = Undefined if argument.default is UNSET else argument.default

        description_sources = pick_not_none(
            argument.description_sources,
            description_sources,
        )
        description = self._get_description(
            sources=description_sources,
            description=argument.description,
            parent_resolver_docstring=parent_resolver_docstring,
            parent_directive_docstring=parent_directive_docstring,
            child_name=argument.python_name,
        )

        return GraphQLArgument(
            type_=argument_type,
            default_value=default_value,
            description=description,
            deprecation_reason=argument.deprecation_reason,
            extensions={
                GraphQLCoreConverter.DEFINITION_BACKREF: argument,
            },
        )

    def from_enum(self, enum: EnumDefinition) -> CustomGraphQLEnumType:
        enum_name = self.config.name_converter.from_type(enum)

        assert enum_name is not None

        # Don't reevaluate known types
        if enum_name in self.type_map:
            graphql_enum = self.type_map[enum_name].implementation
            assert isinstance(graphql_enum, CustomGraphQLEnumType)  # For mypy
            return graphql_enum

        description_sources = pick_not_none(
            enum.description_sources,
            self.config.description_sources,
        )
        description = self._get_description(
            sources=description_sources,
            description=enum.description,
            enum_docstring=enum.docstring,
        )

        graphql_enum = CustomGraphQLEnumType(
            enum=enum,
            name=enum_name,
            values={
                item.name: self.from_enum_value(
                    item, enum.docstring, description_sources
                )
                for item in enum.values
            },
            description=description,
            extensions={
                GraphQLCoreConverter.DEFINITION_BACKREF: enum,
            },
        )

        self.type_map[enum_name] = ConcreteType(
            definition=enum, implementation=graphql_enum
        )

        return graphql_enum

    def from_enum_value(
        self,
        enum_value: EnumValue,
        parent_enum_docstring: Optional[Docstring],
        description_sources: DescriptionSources,
    ) -> GraphQLEnumValue:
        description_sources = pick_not_none(
            enum_value.description_sources,
            description_sources,
        )

        description = self._get_description(
            sources=description_sources,
            description=enum_value.description,
            parent_enum_docstring=parent_enum_docstring,
            child_name=enum_value.name,
        )

        return GraphQLEnumValue(
            enum_value.value,
            deprecation_reason=enum_value.deprecation_reason,
            description=description,
            extensions={
                GraphQLCoreConverter.DEFINITION_BACKREF: enum_value,
            },
        )

    def from_directive(self, directive: StrawberryDirective) -> GraphQLDirective:
        description_sources = pick_not_none(
            directive.description_sources,
            self.config.description_sources,
        )
        description = self._get_description(
            sources=description_sources,
            description=directive.description,
            directive_docstring=directive.docstring,
        )

        graphql_arguments = {}

        for argument in directive.arguments:
            argument_name = self.config.name_converter.from_argument(argument)
            graphql_arguments[argument_name] = self.from_argument(
                argument,
                description_sources,
                parent_directive_docstring=directive.docstring,
            )

        directive_name = self.config.name_converter.from_type(directive)

        return GraphQLDirective(
            name=directive_name,
            locations=directive.locations,
            args=graphql_arguments,
            description=description,
            extensions={
                GraphQLCoreConverter.DEFINITION_BACKREF: directive,
            },
        )

    def from_schema_directive(self, cls: Type) -> GraphQLDirective:
        strawberry_directive = cast(
            StrawberrySchemaDirective, cls.__strawberry_directive__
        )
        module = sys.modules[cls.__module__]

        description_sources = pick_not_none(
            strawberry_directive.description_sources,
            self.config.description_sources,
        )
        description = self._get_description(
            sources=description_sources,
            description=strawberry_directive.description,
            directive_docstring=strawberry_directive.docstring,
        )

        args: Dict[str, GraphQLArgument] = {}
        for field in strawberry_directive.fields:
            default = field.default
            if default == dataclasses.MISSING:
                default = UNSET

            name = self.config.name_converter.get_graphql_name(field)
            args[name] = self.from_argument(
                StrawberryArgument(
                    python_name=field.python_name or field.name,
                    graphql_name=None,
                    type_annotation=StrawberryAnnotation(
                        annotation=field.type,
                        namespace=module.__dict__,
                    ),
                    default=default,
                    description=field.description,
                    description_sources=field.description_sources,
                ),
                description_sources,
                parent_directive_docstring=strawberry_directive.docstring,
            )

        return GraphQLDirective(
            name=self.config.name_converter.from_directive(strawberry_directive),
            locations=[
                DirectiveLocation(loc.value) for loc in strawberry_directive.locations
            ],
            args=args,
            is_repeatable=strawberry_directive.repeatable,
            description=description,
            extensions={
                GraphQLCoreConverter.DEFINITION_BACKREF: strawberry_directive,
            },
        )

    def from_field(
        self,
        field: StrawberryField,
        description_sources: DescriptionSources,
        parent_type_docstring: Optional[Docstring],
    ) -> GraphQLField:
        field_type = cast(GraphQLOutputType, self.from_maybe_optional(field.type))

        resolver = self.from_resolver(field)
        subscribe = None

        if field.is_subscription:
            subscribe = resolver
            resolver = lambda event, *_, **__: event  # noqa: E731

        description_sources = pick_not_none(
            field.description_sources, description_sources
        )
        description = self._get_description(
            sources=description_sources,
            description=field.description,
            resolver_docstring=field.resolver_docstring,
            parent_type_docstring=parent_type_docstring,
            child_name=field.python_name,
        )

        graphql_arguments = {}
        for argument in field.arguments:
            argument_name = self.config.name_converter.from_argument(argument)
            graphql_arguments[argument_name] = self.from_argument(
                argument,
                description_sources,
                parent_resolver_docstring=field.resolver_docstring,
            )

        return GraphQLField(
            type_=field_type,
            args=graphql_arguments,
            resolve=resolver,
            subscribe=subscribe,
            description=description,
            deprecation_reason=field.deprecation_reason,
            extensions={
                GraphQLCoreConverter.DEFINITION_BACKREF: field,
            },
        )

    def from_input_field(
        self,
        field: StrawberryField,
        description_sources: DescriptionSources,
        parent_type_docstring: Optional[Docstring],
    ) -> GraphQLInputField:
        field_type = cast(GraphQLInputType, self.from_maybe_optional(field.type))
        default_value: object

        if field.default_value is UNSET or field.default_value is dataclasses.MISSING:
            default_value = Undefined
        else:
            default_value = field.default_value

        description_sources = pick_not_none(
            field.description_sources,
            description_sources,
        )
        description = self._get_description(
            sources=description_sources,
            description=field.description,
            resolver_docstring=field.resolver_docstring,
            parent_type_docstring=parent_type_docstring,
            child_name=field.python_name,
        )

        return GraphQLInputField(
            type_=field_type,
            default_value=default_value,
            description=description,
            deprecation_reason=field.deprecation_reason,
            extensions={
                GraphQLCoreConverter.DEFINITION_BACKREF: field,
            },
        )

    FieldType = TypeVar("FieldType", GraphQLField, GraphQLInputField)

    @staticmethod
    def _get_thunk_mapping(
        fields: List[StrawberryField],
        name_converter: Callable[[StrawberryField], str],
        field_converter: Callable[
            [StrawberryField, DescriptionSources, Optional[Docstring]], FieldType
        ],
        description_sources: DescriptionSources,
        parent_type_docstring: Optional[Docstring],
    ) -> Dict[str, FieldType]:
        """Create a GraphQL core `ThunkMapping` mapping of field names to field types.

        This method filters out remaining `strawberry.Private` annotated fields that
        could not be filtered during the initialization of a `TypeDefinition` due to
        postponed type-hint evaluation (PEP-563). Performing this filtering now (at
        schema conversion time) ensures that all types to be included in the schema
        should have already been resolved.

        Raises:
            TypeError: If the type of a field in ``fields`` is `UNRESOLVED`
        """
        thunk_mapping = {}

        for f in fields:
            if f.type is UNRESOLVED:
                raise UnresolvedFieldTypeError(f.name)

            if not is_private(f.type):
                thunk_mapping[name_converter(f)] = field_converter(
                    f, description_sources, parent_type_docstring
                )
        return thunk_mapping

    def get_graphql_fields(
        self,
        type_definition: TypeDefinition,
        description_sources: DescriptionSources,
        parent_type_docstring: Optional[Docstring],
    ) -> Dict[str, GraphQLField]:
        return self._get_thunk_mapping(
            fields=type_definition.fields,
            name_converter=self.config.name_converter.from_field,
            field_converter=self.from_field,
            description_sources=description_sources,
            parent_type_docstring=parent_type_docstring,
        )

    def get_graphql_input_fields(
        self,
        type_definition: TypeDefinition,
        description_sources: DescriptionSources,
        parent_type_docstring: Optional[Docstring],
    ) -> Dict[str, GraphQLInputField]:
        return self._get_thunk_mapping(
            fields=type_definition.fields,
            name_converter=self.config.name_converter.from_field,
            field_converter=self.from_input_field,
            description_sources=description_sources,
            parent_type_docstring=parent_type_docstring,
        )

    def from_input_object(self, object_type: type) -> GraphQLInputObjectType:
        type_definition = object_type._type_definition  # type: ignore

        type_name = self.config.name_converter.from_type(type_definition)

        # Don't reevaluate known types
        if type_name in self.type_map:
            graphql_object_type = self.type_map[type_name].implementation
            assert isinstance(graphql_object_type, GraphQLInputObjectType)  # For mypy
            return graphql_object_type

        description_sources = pick_not_none(
            type_definition.description_sources,
            self.config.description_sources,
        )
        description = self._get_description(
            sources=description_sources,
            description=type_definition.description,
            type_docstring=type_definition.docstring,
        )

        graphql_object_type = GraphQLInputObjectType(
            name=type_name,
            fields=lambda: self.get_graphql_input_fields(
                type_definition, description_sources, type_definition.docstring
            ),
            description=description,
            extensions={
                GraphQLCoreConverter.DEFINITION_BACKREF: type_definition,
            },
        )

        self.type_map[type_name] = ConcreteType(
            definition=type_definition, implementation=graphql_object_type
        )

        return graphql_object_type

    def from_interface(self, interface: TypeDefinition) -> GraphQLInterfaceType:
        # TODO: Use StrawberryInterface when it's implemented in another PR

        interface_name = self.config.name_converter.from_type(interface)

        # Don't reevaluate known types
        if interface_name in self.type_map:
            graphql_interface = self.type_map[interface_name].implementation
            assert isinstance(graphql_interface, GraphQLInterfaceType)  # For mypy
            return graphql_interface

        description_sources = pick_not_none(
            interface.description_sources,
            self.config.description_sources,
        )
        description = self._get_description(
            sources=description_sources,
            description=interface.description,
            type_docstring=interface.docstring,
        )

        graphql_interface = GraphQLInterfaceType(
            name=interface_name,
            fields=lambda: self.get_graphql_fields(
                interface, description_sources, interface.docstring
            ),
            interfaces=list(map(self.from_interface, interface.interfaces)),
            description=description,
            extensions={
                GraphQLCoreConverter.DEFINITION_BACKREF: interface,
            },
        )

        self.type_map[interface_name] = ConcreteType(
            definition=interface, implementation=graphql_interface
        )

        return graphql_interface

    def from_list(self, type_: StrawberryList) -> GraphQLList:
        of_type = self.from_maybe_optional(type_.of_type)

        return GraphQLList(of_type)

    def from_object(self, object_type: TypeDefinition) -> GraphQLObjectType:
        # TODO: Use StrawberryObjectType when it's implemented in another PR
        object_type_name = self.config.name_converter.from_type(object_type)

        # Don't reevaluate known types
        if object_type_name in self.type_map:
            graphql_object_type = self.type_map[object_type_name].implementation
            assert isinstance(graphql_object_type, GraphQLObjectType)  # For mypy
            return graphql_object_type

        description_sources = pick_not_none(
            object_type.description_sources,
            self.config.description_sources,
        )
        description = self._get_description(
            sources=description_sources,
            description=object_type.description,
            type_docstring=object_type.docstring,
        )

        def _get_is_type_of() -> Optional[Callable[[Any, GraphQLResolveInfo], bool]]:
            if object_type.is_type_of:
                return object_type.is_type_of

            if not object_type.interfaces:
                return None

            def is_type_of(obj: Any, _info: GraphQLResolveInfo) -> bool:
                if object_type.concrete_of and (
                    hasattr(obj, "_type_definition")
                    and obj._type_definition.origin is object_type.concrete_of.origin
                ):
                    return True

                return isinstance(obj, object_type.origin)

            return is_type_of

        graphql_object_type = GraphQLObjectType(
            name=object_type_name,
            fields=lambda: self.get_graphql_fields(
                object_type, description_sources, object_type.docstring
            ),
            interfaces=list(map(self.from_interface, object_type.interfaces)),
            description=description,
            is_type_of=_get_is_type_of(),
            extensions={
                GraphQLCoreConverter.DEFINITION_BACKREF: object_type,
            },
        )

        self.type_map[object_type_name] = ConcreteType(
            definition=object_type, implementation=graphql_object_type
        )

        return graphql_object_type

    def from_resolver(
        self, field: StrawberryField
    ) -> Callable:  # TODO: Take StrawberryResolver
        field.default_resolver = self.config.default_resolver

        if field.is_basic_field:

            def _get_basic_result(_source: Any, *args, **kwargs):
                # Call `get_result` without an info object or any args or
                # kwargs because this is a basic field with no resolver.
                return field.get_result(_source, info=None, args=[], kwargs={})

            _get_basic_result._is_default = True  # type: ignore

            return _get_basic_result

        def _get_arguments(
            source: Any,
            info: Info,
            kwargs: Dict[str, Any],
        ) -> Tuple[List[Any], Dict[str, Any]]:
            kwargs = convert_arguments(
                kwargs,
                field.arguments,
                scalar_registry=self.scalar_registry,
                config=self.config,
            )

            # the following code allows to omit info and root arguments
            # by inspecting the original resolver arguments,
            # if it asks for self, the source will be passed as first argument
            # if it asks for root, the source it will be passed as kwarg
            # if it asks for info, the info will be passed as kwarg

            args = []

            if field.base_resolver:
                if field.base_resolver.self_parameter:
                    args.append(source)

                root_parameter = field.base_resolver.root_parameter
                if root_parameter:
                    kwargs[root_parameter.name] = source

                info_parameter = field.base_resolver.info_parameter
                if info_parameter:
                    kwargs[info_parameter.name] = info

            return args, kwargs

        def _check_permissions(source: Any, info: Info, kwargs: Dict[str, Any]):
            """
            Checks if the permission should be accepted and
            raises an exception if not
            """
            for permission_class in field.permission_classes:
                permission = permission_class()

                if not permission.has_permission(source, info, **kwargs):
                    message = getattr(permission, "message", None)
                    raise PermissionError(message)

        async def _check_permissions_async(
            source: Any, info: Info, kwargs: Dict[str, Any]
        ):
            for permission_class in field.permission_classes:
                permission = permission_class()
                has_permission: bool

                has_permission = await await_maybe(
                    permission.has_permission(source, info, **kwargs)
                )

                if not has_permission:
                    message = getattr(permission, "message", None)
                    raise PermissionError(message)

        def _strawberry_info_from_graphql(info: GraphQLResolveInfo) -> Info:
            return Info(
                _raw_info=info,
                _field=field,
            )

        def _get_result(_source: Any, info: Info, **kwargs):
            field_args, field_kwargs = _get_arguments(
                source=_source, info=info, kwargs=kwargs
            )

            return field.get_result(
                _source, info=info, args=field_args, kwargs=field_kwargs
            )

        def _resolver(_source: Any, info: GraphQLResolveInfo, **kwargs):
            strawberry_info = _strawberry_info_from_graphql(info)
            _check_permissions(_source, strawberry_info, kwargs)

            return _get_result(_source, strawberry_info, **kwargs)

        async def _async_resolver(_source: Any, info: GraphQLResolveInfo, **kwargs):
            strawberry_info = _strawberry_info_from_graphql(info)
            await _check_permissions_async(_source, strawberry_info, kwargs)

            return await await_maybe(_get_result(_source, strawberry_info, **kwargs))

        if field.is_async:
            _async_resolver._is_default = not field.base_resolver  # type: ignore
            return _async_resolver
        else:
            _resolver._is_default = not field.base_resolver  # type: ignore
            return _resolver

    def from_scalar(self, scalar: Type) -> GraphQLScalarType:
        scalar_definition: ScalarDefinition

        if scalar in self.scalar_registry:
            _scalar_definition = self.scalar_registry[scalar]
            # TODO: check why we need the cast and we are not trying with getattr first
            if isinstance(_scalar_definition, ScalarWrapper):
                scalar_definition = _scalar_definition._scalar_definition
            else:
                scalar_definition = _scalar_definition
        else:
            scalar_definition = scalar._scalar_definition

        scalar_name = self.config.name_converter.from_type(scalar_definition)

        if scalar_name not in self.type_map:
            implementation = (
                scalar_definition.implementation
                if scalar_definition.implementation is not None
                else _make_scalar_type(scalar_definition)
            )

            self.type_map[scalar_name] = ConcreteType(
                definition=scalar_definition, implementation=implementation
            )
        else:
            if self.type_map[scalar_name].definition != scalar_definition:
                raise ScalarAlreadyRegisteredError(scalar_name)

            implementation = cast(
                GraphQLScalarType, self.type_map[scalar_name].implementation
            )

        return implementation

    def from_maybe_optional(
        self, type_: Union[StrawberryType, type]
    ) -> Union[GraphQLNullableType, GraphQLNonNull]:
        NoneType = type(None)
        type_, _ = StrawberryAnnotated.get_type_and_args(type_)

        if type_ is None or type_ is NoneType:
            return self.from_type(type_)
        elif isinstance(type_, StrawberryOptional):
            return self.from_type(type_.of_type)
        else:
            return GraphQLNonNull(self.from_type(type_))

    def from_type(self, type_: Union[StrawberryType, type]) -> GraphQLNullableType:
        type_, _ = StrawberryAnnotated.get_type_and_args(type_)

        if compat.is_generic(type_):
            raise MissingTypesForGenericError(type_)

        if isinstance(type_, EnumDefinition):  # TODO: Replace with StrawberryEnum
            return self.from_enum(type_)
        elif compat.is_input_type(type_):  # TODO: Replace with StrawberryInputObject
            return self.from_input_object(type_)
        elif isinstance(type_, StrawberryList):
            return self.from_list(type_)
        elif compat.is_interface_type(type_):  # TODO: Replace with StrawberryInterface
            type_definition: TypeDefinition = type_._type_definition  # type: ignore
            return self.from_interface(type_definition)
        elif compat.is_object_type(type_):  # TODO: Replace with StrawberryObject
            type_definition: TypeDefinition = type_._type_definition  # type: ignore
            return self.from_object(type_definition)
        elif compat.is_enum(type_):  # TODO: Replace with StrawberryEnum
            enum_definition: EnumDefinition = type_._enum_definition  # type: ignore
            return self.from_enum(enum_definition)
        elif isinstance(type_, TypeDefinition):  # TODO: Replace with StrawberryObject
            return self.from_object(type_)
        elif isinstance(type_, StrawberryUnion):
            return self.from_union(type_)
        elif isinstance(type_, LazyType):
            return self.from_type(type_.resolve_type())
        elif compat.is_scalar(
            type_, self.scalar_registry
        ):  # TODO: Replace with StrawberryScalar
            return self.from_scalar(type_)

        raise TypeError(f"Unexpected type '{type_}'")

    def from_union(self, union: StrawberryUnion) -> GraphQLUnionType:
        union_name = self.config.name_converter.from_type(union)

        # Don't reevaluate known types
        if union_name in self.type_map:
            graphql_union = self.type_map[union_name].implementation
            assert isinstance(graphql_union, GraphQLUnionType)  # For mypy
            return graphql_union

        graphql_types: List[GraphQLObjectType] = []
        for type_ in union.types:
            graphql_type = self.from_type(type_)

            if isinstance(graphql_type, GraphQLInputObjectType):
                raise InvalidTypeInputForUnion(graphql_type)
            assert isinstance(graphql_type, GraphQLObjectType)

            graphql_types.append(graphql_type)

        graphql_union = GraphQLUnionType(
            name=union_name,
            types=graphql_types,
            description=union.description,
            resolve_type=union.get_type_resolver(self.type_map),
            extensions={
                GraphQLCoreConverter.DEFINITION_BACKREF: union,
            },
        )

        self.type_map[union_name] = ConcreteType(
            definition=union, implementation=graphql_union
        )

        return graphql_union

    def _get_description(
        self,
        *,
        sources: DescriptionSources,
        description: Optional[str] = None,
        type_docstring: Optional[Docstring] = None,
        parent_type_docstring: Optional[Docstring] = None,
        enum_docstring: Optional[Docstring] = None,
        parent_enum_docstring: Optional[Docstring] = None,
        resolver_docstring: Optional[Docstring] = None,
        parent_resolver_docstring: Optional[Docstring] = None,
        directive_docstring: Optional[Docstring] = None,
        parent_directive_docstring: Optional[Docstring] = None,
        child_name: Optional[str] = None,
    ) -> Optional[str]:
        def gen_candidates():
            if sources & DescriptionSources.STRAWBERRY_DESCRIPTIONS:
                yield description

            if sources & DescriptionSources.RESOLVER_DOCSTRINGS:
                if resolver_docstring is not None:
                    yield resolver_docstring.main_description
                if parent_resolver_docstring is not None and child_name is not None:
                    yield parent_resolver_docstring.child_description(child_name)

            if sources & DescriptionSources.TYPE_ATTRIBUTE_DOCSTRINGS:
                if parent_type_docstring is not None and child_name is not None:
                    yield parent_type_docstring.attribute_docstring(child_name)

            if sources & DescriptionSources.TYPE_DOCSTRINGS:
                if type_docstring is not None:
                    yield type_docstring.main_description

                if parent_type_docstring is not None and child_name is not None:
                    yield parent_type_docstring.child_description(child_name)

            if sources & DescriptionSources.ENUM_ATTRIBUTE_DOCSTRINGS:
                if parent_enum_docstring is not None and child_name is not None:
                    yield parent_enum_docstring.attribute_docstring(child_name)

            if sources & DescriptionSources.ENUM_DOCSTRINGS:
                if enum_docstring:
                    yield enum_docstring.main_description
                if parent_enum_docstring is not None and child_name is not None:
                    yield parent_enum_docstring.child_description(child_name)

            if sources & DescriptionSources.DIRECTIVE_ATTRIBUTE_DOCSTRINGS:
                if parent_directive_docstring is not None and child_name is not None:
                    yield parent_directive_docstring.attribute_docstring(child_name)

            if sources & DescriptionSources.DIRECTIVE_DOCSTRINGS:
                if directive_docstring is not None:
                    yield directive_docstring.main_description
                if parent_directive_docstring is not None and child_name is not None:
                    yield parent_directive_docstring.child_description(child_name)

        for candidate in gen_candidates():
            if candidate is not None:
                return candidate

        return None

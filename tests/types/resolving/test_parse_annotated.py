from typing import List, Optional, Union

from typing_extensions import Annotated

from strawberry.annotation import StrawberryAnnotation


def test_parse_annotated():
    assert StrawberryAnnotation.parse_annotated(str) == str
    assert (
        StrawberryAnnotation.parse_annotated(Annotated[str, "foo"])
        == Annotated[str, "foo"]
    )


def test_parse_annotated_optional():
    assert StrawberryAnnotation.parse_annotated(Optional[str]) == Optional[str]
    assert (
        StrawberryAnnotation.parse_annotated(Annotated[Optional[str], "foo"])
        == Annotated[Optional[str], "foo"]
    )


def test_parse_annotated_list():
    assert StrawberryAnnotation.parse_annotated(List[str]) == List[str]
    assert (
        StrawberryAnnotation.parse_annotated(Annotated[List[str], "foo"])
        == Annotated[List[str], "foo"]
    )


def test_parse_annotated_union():
    assert StrawberryAnnotation.parse_annotated(Union[str, int]) == Union[str, int]
    assert (
        StrawberryAnnotation.parse_annotated(Annotated[Union[str, int], "foo"])
        == Annotated[Union[str, int], "foo"]
    )


def test_parse_annotated_optional_union():
    assert (
        StrawberryAnnotation.parse_annotated(Optional[Union[str, int]])
        == Optional[Union[str, int]]
    )
    assert (
        StrawberryAnnotation.parse_annotated(
            Annotated[Optional[Union[str, int]], "foo"]
        )
        == Annotated[Optional[Union[str, int]], "foo"]
    )


def test_parse_annotated_list_union():
    assert (
        StrawberryAnnotation.parse_annotated(List[Union[str, int]])
        == List[Union[str, int]]
    )
    assert (
        StrawberryAnnotation.parse_annotated(Annotated[List[Union[str, int]], "foo"])
        == Annotated[List[Union[str, int]], "foo"]
    )


def test_parse_annotated_recursive():
    assert (
        StrawberryAnnotation.parse_annotated(
            Annotated[List[Annotated[Union[str, int], "bar"]], "foo"]
        )
        == Annotated[List[Annotated[Union[str, int], "bar"]], "foo"]
    )

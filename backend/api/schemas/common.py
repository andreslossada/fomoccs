from typing import Annotated

from pydantic import AnyHttpUrl, BaseModel, BeforeValidator, ConfigDict


class TagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class PaginatedResponse[T](BaseModel):
    data: list[T]
    total: int


def _check_url(value: str) -> str:
    """Validate a URL without rewriting it.

    pydantic's AnyHttpUrl normalizes the string — it appends a trailing slash
    to a bare host, for instance. That normalized value is not what we want
    to store: the column is a string, and the string is what the crawler
    later fetches. Some servers answer differently for the two spellings, so
    the URL that was given is the one that is kept.
    """
    AnyHttpUrl(value)
    return value


# A URL that is checked for validity but stored exactly as it was given.
HttpUrlString = Annotated[str, BeforeValidator(_check_url)]

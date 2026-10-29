"""Strict API contracts shared by features."""

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @model_validator(mode="after")
    def plain_display_text(self) -> "Schema":
        for name in ("name", "memo", "jurisdiction", "code", "reason", "number"):
            value = getattr(self, name, None)
            if not isinstance(value, str):
                continue
            clean = "".join(char for char in value if ord(char) >= 32 or char in "\n\t")
            if re.search(r"<[A-Za-z/!][^>]*>", clean) or (name != "memo" and not clean.strip()):
                raise ValueError("Display text must be nonempty plain text")
            setattr(self, name, clean)
        return self


class PageQuery(Schema):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: str | None = Field(default=None, max_length=200)

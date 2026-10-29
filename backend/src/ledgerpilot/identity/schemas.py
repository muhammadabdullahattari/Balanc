"""Transient credentials never enter API responses or logs."""

from pydantic import Field, SecretStr

from ledgerpilot.shared.schemas import Schema


class Registration(Schema):
    email: str = Field(max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)


class Credentials(Registration):
    mfa_code: SecretStr | None = Field(default=None, min_length=6, max_length=6)
    recovery_code: SecretStr | None = Field(default=None, max_length=128)


class EmailRequest(Schema):
    email: str = Field(max_length=254)


class TokenRequest(Schema):
    token: SecretStr = Field(min_length=16, max_length=256)


class ResetRequest(TokenRequest):
    new_password: SecretStr = Field(min_length=15, max_length=128)


class MFARequest(Schema):
    code: SecretStr = Field(min_length=6, max_length=6)

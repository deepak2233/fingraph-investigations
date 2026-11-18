"""Versioned wire contracts. Monetary values retain decimal precision."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Account(Contract):
    id: Identifier
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    holder: Identifier
    owner: Identifier | None = None
    owner_percentage: Decimal | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    ownership_source: Text | None = None
    ownership_observed_at: datetime | None = None

    @model_validator(mode="after")
    def ownership_provenance(self):
        if self.owner and (
            self.owner_percentage is None
            or self.ownership_source is None
            or self.ownership_observed_at is None
        ):
            raise ValueError("Ownership requires an explicit percentage, source, and observation time")
        if self.ownership_observed_at and self.ownership_observed_at.tzinfo is None:
            raise ValueError("Ownership observation requires an explicit timezone")
        return self


class Transaction(Contract):
    id: Identifier
    sender: Identifier
    receiver: Identifier
    amount: Decimal = Field(gt=0, max_digits=28, decimal_places=8, allow_inf_nan=False)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    timestamp: datetime
    source_id: Text

    @field_validator("timestamp")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None:
            raise ValueError("timestamp requires an explicit timezone")
        return value.astimezone(timezone.utc)


class InvestigationCase(Contract):
    id: Identifier
    title: Text
    summary: Text
    accounts: list[Account] = Field(min_length=1, max_length=500)
    transactions: list[Transaction] = Field(min_length=1, max_length=1000)
    missing_ownership: bool = False
    stale_screening: bool = False
    data_origin: Text
    as_of: datetime

    @field_validator("as_of")
    @classmethod
    def timezone_required(cls, value):
        return Transaction.timezone_required(value)

    @model_validator(mode="after")
    def referential_integrity(self):
        accounts = {item.id for item in self.accounts}
        transaction_ids = {item.id for item in self.transactions}
        if len(accounts) != len(self.accounts) or len(transaction_ids) != len(self.transactions):
            raise ValueError("Account and transaction identifiers must be unique within a case")
        for account in self.accounts:
            if account.ownership_observed_at and account.ownership_observed_at > self.as_of:
                raise ValueError("Ownership observation is later than the investigation cutoff")
        for transaction in self.transactions:
            if transaction.sender not in accounts or transaction.receiver not in accounts:
                raise ValueError("Transaction references an unknown account")
            if transaction.timestamp > self.as_of:
                raise ValueError("Transaction is later than the investigation cutoff")
        return self


class CaseEvent(Contract):
    schema_version: Literal[1] = 1
    event_id: Identifier
    tenant_id: Identifier
    received_at: datetime
    case: InvestigationCase

    @field_validator("received_at")
    @classmethod
    def timezone_required(cls, value):
        return Transaction.timezone_required(value)


class Review(Contract):
    action: Literal["approve", "reject", "request_evidence"]
    reason: Text
    expected_version: int = Field(ge=1)
    request_id: Identifier

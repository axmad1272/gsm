import re
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.shipments.models import Operation, ReviewKind

_TIN = re.compile(r"^(\d{9}|\d{14})$")
_PINFL = re.compile(r"^\d{14}$")


def _tin(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if not _TIN.match(v):
        raise ValueError("ИНН — 9 цифр, ПИНФЛ — 14 цифр")
    return v


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PartyIn(Strict):
    name: str | None = Field(default=None, max_length=300)
    tin: str | None = None
    counterparty_id: int | None = None

    @field_validator("tin")
    @classmethod
    def _v_tin(cls, v):
        return _tin(v)


class BasisIn(Strict):
    mode: Literal["didox", "manual"] = "didox"
    # didox
    contract_didox_id: str | None = None
    poa_didox_id: str | None = None
    confirmed_by_poa: bool = False
    # manual (при недоступности Didox)
    contract_number: str | None = Field(default=None, max_length=100)
    contract_date: date | None = None
    poa_number: str | None = Field(default=None, max_length=100)
    poa_date: date | None = None


class ShipmentContentIn(Strict):
    warehouse_id: int
    operation: Operation = Operation.SALE
    destination_warehouse_id: int | None = None
    shipment_date: date | None = None
    customer: PartyIn | None = None
    consignee: PartyIn | None = None
    basis: BasisIn | None = None
    vehicle_plate: str | None = Field(default=None, max_length=20)
    vehicle_owner_id: str | None = None
    driver_pinfl: str | None = None
    fuel_name: str | None = Field(default=None, max_length=200)
    volume_l: Decimal | None = None
    density: Decimal | None = None
    mass_t: Decimal | None = None
    note: str | None = Field(default=None, max_length=2000)

    @field_validator("vehicle_owner_id")
    @classmethod
    def _owner(cls, v):
        return _tin(v)

    @field_validator("driver_pinfl")
    @classmethod
    def _pinfl(cls, v):
        if v is not None and not _PINFL.match(v.strip()):
            raise ValueError("ПИНФЛ — 14 цифр")
        return v.strip() if v else v

    @field_validator("vehicle_plate")
    @classmethod
    def _plate(cls, v):
        return re.sub(r"\s+", "", v).upper() if v else v


class EditIn(Strict):
    content: ShipmentContentIn
    reason: str | None = Field(default=None, max_length=2000)
    admin_mode: bool = False


class ConfirmDraftIn(Strict):
    content: ShipmentContentIn | None = None


class ReasonIn(Strict):
    reason: str = Field(min_length=3, max_length=2000)


class ReviewIn(Strict):
    kind: ReviewKind
    approved: bool
    comment: str | None = Field(default=None, max_length=2000)


class RemarkIn(Strict):
    text: str = Field(min_length=1, max_length=4000)
    grant_correction: bool = False


class AccountingDateIn(Strict):
    accounting_date: date | None

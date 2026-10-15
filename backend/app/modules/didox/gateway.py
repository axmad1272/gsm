"""Граница с Didox. Всё общение с внешним API — только через DidoxGateway.

Реальный HTTP-клиент (HttpDidoxGateway) будет реализован по документации Didox API
и текущему didox_glue.py; остальной код от него не зависит."""

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Protocol

from app.core.errors import ExternalUnavailable


class DocType(StrEnum):
    CONTRACT = "contract"
    POWER_OF_ATTORNEY = "power_of_attorney"
    OTHER = "other"


class Direction(StrEnum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"


class SignStatus(StrEnum):
    ACCEPTED = "accepted"  # подписан обеими сторонами
    AWAITING_PARTNER = "awaiting_partner"  # ждёт подписи партнёра
    AWAITING_US = "awaiting_us"  # ждёт нашей подписи
    REJECTED = "rejected"
    DRAFT = "draft"
    DELETED = "deleted"


# Допустимы как основание. Статус при этом показывается явно, а не как «Подписано».
SELECTABLE_STATUSES = frozenset(
    {SignStatus.ACCEPTED, SignStatus.AWAITING_PARTNER, SignStatus.AWAITING_US}
)


@dataclass(frozen=True)
class DidoxCompany:
    tin: str
    name: str


@dataclass(frozen=True)
class DidoxDocument:
    id: str
    doc_type: DocType
    direction: Direction
    status: SignStatus
    partner_tin: str
    number: str
    doc_date: date
    valid_from: date | None = None
    valid_to: date | None = None
    # Для доверенности — реквизиты договора-основания.
    contract_number: str | None = None
    contract_date: date | None = None
    linked_document_ids: tuple[str, ...] = field(default_factory=tuple)


class DidoxUnavailable(ExternalUnavailable):
    def __init__(self, message: str = "Didox недоступен"):
        super().__init__("didox_unavailable", message)


class DidoxGateway(Protocol):
    def search_companies(self, query: str) -> list[DidoxCompany]: ...

    def list_documents(self, partner_tin: str, doc_types: set[DocType]) -> list[DidoxDocument]: ...

    def get_document(self, doc_id: str) -> DidoxDocument | None: ...


class FakeDidoxGateway:
    """In-memory реализация для разработки и тестов."""

    def __init__(self) -> None:
        self.companies: list[DidoxCompany] = []
        self.documents: dict[str, DidoxDocument] = {}
        self.available = True

    def _check(self) -> None:
        if not self.available:
            raise DidoxUnavailable()

    def add(self, doc: DidoxDocument) -> DidoxDocument:
        self.documents[doc.id] = doc
        return doc

    def search_companies(self, query: str) -> list[DidoxCompany]:
        self._check()
        q = query.strip().lower()
        return [c for c in self.companies if q in c.name.lower() or c.tin == q]

    def list_documents(self, partner_tin, doc_types):
        self._check()
        return [
            d
            for d in self.documents.values()
            if d.partner_tin == partner_tin and d.doc_type in doc_types
        ]

    def get_document(self, doc_id):
        self._check()
        return self.documents.get(doc_id)


_fake_singleton: FakeDidoxGateway | None = None


def get_didox_gateway() -> DidoxGateway:
    from app.core.config import get_settings

    if get_settings().didox_mode == "fake":
        global _fake_singleton
        if _fake_singleton is None:
            _fake_singleton = FakeDidoxGateway()
        return _fake_singleton
    raise NotImplementedError("HttpDidoxGateway: нужна документация Didox API")

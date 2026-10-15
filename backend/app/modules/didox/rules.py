"""Правила подбора и проверки оснований (договор + доверенность) для отгрузки.
Чистые функции: одинаково работают при подборе в форме и при повторной проверке на сервере."""

from dataclasses import dataclass
from datetime import date

from app.core.errors import DomainError
from app.modules.didox.gateway import (
    SELECTABLE_STATUSES,
    DidoxDocument,
    DidoxGateway,
    Direction,
    DocType,
)
from app.modules.shipments.models import ContractKind


@dataclass(frozen=True)
class Candidate:
    document: DidoxDocument
    selectable: bool
    reason: str | None  # почему нельзя выбрать


def _valid_on(doc: DidoxDocument, on: date) -> str | None:
    if doc.status not in SELECTABLE_STATUSES:
        return f"Статус {doc.status.value}: нельзя использовать как основание"
    if doc.doc_date > on:
        return "Документ датирован позже даты ТТН"
    if doc.valid_from and doc.valid_from > on:
        return "Срок действия ещё не начался"
    if doc.valid_to and doc.valid_to < on:
        return "Срок действия истёк"
    return None


def _norm(s: str | None) -> str:
    return (s or "").strip().lower().replace(" ", "")


def contract_candidates(docs: list[DidoxDocument], partner_tin: str, on: date) -> list[Candidate]:
    out = []
    for d in docs:
        if (
            d.doc_type != DocType.CONTRACT
            or d.direction != Direction.OUTGOING
            or d.partner_tin != partner_tin
        ):
            continue
        reason = _valid_on(d, on)
        out.append(Candidate(d, reason is None, reason))
    # Сначала допустимые, затем по дате (свежие выше).
    return sorted(out, key=lambda c: (not c.selectable, -c.document.doc_date.toordinal()))


def _poa_matches_contract(
    poa: DidoxDocument, contract_id: str | None, number: str, cdate: date
) -> bool:
    if contract_id and contract_id in poa.linked_document_ids:
        return True
    return _norm(poa.contract_number) == _norm(number) and poa.contract_date == cdate


def poa_candidates(
    docs: list[DidoxDocument],
    partner_tin: str,
    on: date,
    *,
    contract_id: str | None,
    contract_number: str,
    contract_date: date,
) -> list[Candidate]:
    out = []
    for d in docs:
        if (
            d.doc_type != DocType.POWER_OF_ATTORNEY
            or d.direction != Direction.INCOMING
            or d.partner_tin != partner_tin
        ):
            continue
        if not _poa_matches_contract(d, contract_id, contract_number, contract_date):
            continue
        reason = _valid_on(d, on)
        out.append(Candidate(d, reason is None, reason))
    return sorted(out, key=lambda c: (not c.selectable, -c.document.doc_date.toordinal()))


def poa_confirmed_contracts(
    docs: list[DidoxDocument], partner_tin: str, on: date
) -> list[DidoxDocument]:
    """Старый бумажный договор с автопролонгацией: подходящего договора в Didox нет,
    но есть действующая входящая доверенность с его реквизитами."""
    if any(c.selectable for c in contract_candidates(docs, partner_tin, on)):
        return []
    return [
        d
        for d in docs
        if d.doc_type == DocType.POWER_OF_ATTORNEY
        and d.direction == Direction.INCOMING
        and d.partner_tin == partner_tin
        and d.contract_number
        and d.contract_date
        and _valid_on(d, on) is None
    ]


@dataclass(frozen=True)
class BasisSelection:
    poa_didox_id: str
    contract_didox_id: str | None = None
    confirmed_by_poa: bool = False


@dataclass(frozen=True)
class ValidatedBasis:
    contract_kind: ContractKind
    contract_number: str
    contract_date: date
    contract_didox_id: str | None
    contract_didox_status: str | None
    poa_number: str
    poa_date: date
    poa_didox_id: str
    poa_didox_status: str


def _fail(message: str, **details) -> DomainError:
    return DomainError("basis_invalid", message, **details)


def validate_basis(
    gw: DidoxGateway, sel: BasisSelection, partner_tin: str, on: date
) -> ValidatedBasis:
    """Повторная проверка на сервере по свежим данным Didox (не по ответу формы)."""
    poa = gw.get_document(sel.poa_didox_id)
    if poa is None or poa.doc_type != DocType.POWER_OF_ATTORNEY:
        raise _fail("Доверенность не найдена в Didox", field="poa")
    if poa.direction != Direction.INCOMING or poa.partner_tin != partner_tin:
        raise _fail("Доверенность выдана другим контрагентом", field="poa")
    if (reason := _valid_on(poa, on)) is not None:
        raise _fail(f"Доверенность: {reason}", field="poa", status=poa.status.value)

    if sel.confirmed_by_poa:
        if sel.contract_didox_id:
            raise _fail("Основание «подтверждён доверенностью» не ссылается на договор Didox")
        all_docs = gw.list_documents(partner_tin, {DocType.CONTRACT})
        if any(c.selectable for c in contract_candidates(all_docs, partner_tin, on)):
            raise _fail("В Didox есть действующий договор — выберите его", field="contract")
        if not (poa.contract_number and poa.contract_date):
            raise _fail("В доверенности нет реквизитов договора", field="poa")
        return ValidatedBasis(
            ContractKind.CONFIRMED_BY_POA,
            poa.contract_number,
            poa.contract_date,
            None,
            None,
            poa.number,
            poa.doc_date,
            poa.id,
            poa.status.value,
        )

    if not sel.contract_didox_id:
        raise _fail("Выберите договор", field="contract")
    contract = gw.get_document(sel.contract_didox_id)
    if contract is None or contract.doc_type != DocType.CONTRACT:
        raise _fail("Договор не найден в Didox", field="contract")
    if contract.direction != Direction.OUTGOING or contract.partner_tin != partner_tin:
        raise _fail("Договор относится к другому контрагенту", field="contract")
    if (reason := _valid_on(contract, on)) is not None:
        raise _fail(f"Договор: {reason}", field="contract", status=contract.status.value)
    if not _poa_matches_contract(poa, contract.id, contract.number, contract.doc_date):
        raise _fail("Доверенность не относится к выбранному договору", field="poa")
    return ValidatedBasis(
        ContractKind.DIDOX_CONTRACT,
        contract.number,
        contract.doc_date,
        contract.id,
        contract.status.value,
        poa.number,
        poa.doc_date,
        poa.id,
        poa.status.value,
    )

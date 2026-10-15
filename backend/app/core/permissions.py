"""Права проверяются только на сервере. Роль — это набор прав по умолчанию,
у конкретного пользователя набор можно расширить или сузить."""

from enum import StrEnum


class P(StrEnum):
    SHIPMENT_VIEW = "shipment.view"
    SHIPMENT_CREATE = "shipment.create"
    SHIPMENT_EDIT = "shipment.edit"
    SHIPMENT_DELETE = "shipment.delete"
    SHIPMENT_PRINT = "shipment.print"
    SHIPMENT_ANNUL = "shipment.annul"
    SHIPMENT_ADMIN_CORRECT = "shipment.admin_correct"
    SHIPMENT_VIEW_PERSONAL = "shipment.view_personal"
    SHIPMENT_REMARK = "shipment.remark"
    SHIPMENT_GRANT_CORRECTION = "shipment.grant_correction"
    SHIPMENT_CLOSE = "shipment.close"
    REVIEW_DOCUMENTS = "review.documents"
    REVIEW_ACTUAL = "review.actual"
    ACCOUNTING_DATE = "accounting.date"
    ONEC_VIEW = "onec.view"
    DIDOX_MANUAL_BASIS = "didox.manual_basis"
    PERIOD_CLOSE = "period.close"
    ADMIN_MANAGE = "admin.manage"
    TECH_DAILY = "tech.daily"


_VIEW = {P.SHIPMENT_VIEW}

ROLE_PRESETS: dict[str, frozenset[P]] = {
    "operator": frozenset(
        _VIEW
        | {
            P.SHIPMENT_CREATE,
            P.SHIPMENT_EDIT,
            P.SHIPMENT_DELETE,
            P.SHIPMENT_PRINT,
            P.SHIPMENT_VIEW_PERSONAL,
            P.DIDOX_MANUAL_BASIS,
        }
    ),
    "accountant": frozenset(
        _VIEW
        | {
            P.REVIEW_DOCUMENTS,
            P.ACCOUNTING_DATE,
            P.ONEC_VIEW,
            P.SHIPMENT_REMARK,
            P.SHIPMENT_GRANT_CORRECTION,
            P.SHIPMENT_ANNUL,
            P.SHIPMENT_CLOSE,
        }
    ),
    "financier": frozenset(
        _VIEW
        | {
            P.REVIEW_ACTUAL,
            P.ONEC_VIEW,
            P.SHIPMENT_REMARK,
            P.SHIPMENT_GRANT_CORRECTION,
            P.SHIPMENT_ANNUL,
            P.SHIPMENT_CLOSE,
            P.PERIOD_CLOSE,
        }
    ),
    # Аннулирование директору сознательно не назначено (как в текущей системе).
    "director": frozenset(_VIEW | {P.ONEC_VIEW, P.SHIPMENT_REMARK}),
    # Администратор управляет системой, но не подтверждает хозяйственные операции.
    "admin": frozenset(_VIEW | {P.ADMIN_MANAGE, P.SHIPMENT_ANNUL, P.SHIPMENT_ADMIN_CORRECT}),
    "technologist": frozenset(_VIEW | {P.TECH_DAILY}),
}


def effective_permissions(
    roles: list[str], granted: list[str], revoked: list[str]
) -> frozenset[str]:
    perms: set[str] = set()
    for role in roles:
        perms |= {p.value for p in ROLE_PRESETS.get(role, frozenset())}
    perms |= set(granted)
    perms -= set(revoked)
    return frozenset(perms)

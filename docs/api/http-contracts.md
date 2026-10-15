# HTTP-контракты бэкенда (фактическое состояние)

> Описывает то, что **реально реализовано** в `backend/app` на этом коммите. Требования, которые ещё
> не выполнены, вынесены в раздел «Ограничения» и в [сценарий](../ux/rashod-e2e-scenario.md).
> Статус: каркас для разработки, к рабочей эксплуатации не допущен.

## Общие правила

| Что | Как сейчас |
|---|---|
| Базовый путь | `/api` (кроме `/health`) |
| Формат | JSON, UTF-8. Даты — `YYYY-MM-DD`, время — ISO 8601 с часовым поясом |
| Количество в ответах | `volume_ml`, `mass_g` — целые; `density` — строка с 4 знаками (`"0.8400"`) |
| Количество в запросах | `volume_l`, `density`, `mass_t` — десятичные числа или строки |
| Аутентификация | **Только dev:** заголовок `X-User-Id: <uuid>` при `GSM_AUTH_MODE=dev`. При любом другом значении все запросы получают 401 `auth_not_configured`. Для приёмки прав и публикации не подходит |
| Идемпотентность | Заголовок `Idempotency-Key` (≤ 100 символов) на операциях из таблицы ниже. Повтор с тем же ключом и телом возвращает сохранённый ответ; с другим телом — 409 `idempotency_mismatch`. Неуспешная операция ключ не занимает |
| Ошибки бизнес-правил | `{"code": "...", "message": "...", "details": {...}}` со статусом 401/403/404/409/422/503 |
| Ошибки валидации схемы | стандартный ответ FastAPI 422 `{"detail": [...]}` |
| Доступ к чужому складу | чтение ТТН → 404 `not_found`; запись → 403 `warehouse_forbidden` |

## Справочники и пользователь

| Метод и путь | Право | Ответ |
|---|---|---|
| `GET /health` | — | `{"status":"ok"}` |
| `GET /api/me` | вход | `id, login, full_name, permissions[], warehouse_ids[]` |
| `GET /api/warehouses` | вход | `[{id, code, name, quantity_mode, allow_manual_basis, accessible}]` |
| `GET /api/warehouses/{id}/balance?on=` | `shipment.view` + склад | `{volume_ml, mass_g}` — сумма движений |
| `GET /api/periods` | вход | закрытые месяцы |
| `POST /api/periods/{year}/{month}/close` | `period.close` | закрывает месяц (идемпотентно) |

## ТТН: чтение

### `GET /api/shipments`

Параметры: `tab` = `registry` | `drafts` | `review` | `history` | `search`, `warehouse_id`, `date_from`,
`date_to` (по **дате отгрузки**), `operation` = `sale` | `transfer`, `q`, `limit` (1–500), `offset`.

| Вкладка | Что входит |
|---|---|
| `registry` | `created` и `issued`, не закрытые |
| `drafts` | `draft` |
| `review` | `issued`, не закрытые, без двух положительных проверок текущей редакции |
| `history` | закрытые и аннулированные |
| `search` | всё, кроме удалённых |

Ответ: `{"applied_filters": {...,"period_meaning":"по дате отгрузки"}, "items": [ТТН], "limit", "offset"}`.
Видимость: склад отправления **или** склад назначения входит в доступные пользователю.

### `GET /api/shipments/{id}` — карточка

Поля ТТН (`items[]` в списке содержит их же без вложенных коллекций):

```
id, number, number_year, number_fixed, revision_no,
warehouse_id, operation, destination_warehouse_id, shipment_date,
customer{name,tin,counterparty_id}, consignee{...},
basis{source, contract_kind, contract_number, contract_date, contract_didox_id,
      contract_didox_status, poa_number, poa_date, poa_didox_id, poa_didox_status},
vehicle_plate, vehicle_owner_id*, driver_pinfl*,
fuel{name, volume_ml, density, mass_g, quantity_mode}, note,
corrections{used, granted, left},
status{document, closed, didox, onec, review{documents, actual}, period},
accounting_date (только с accounting.date или onec.view),
annulled{at, reason} (только для аннулированной),
created_at, first_printed_at
```
`*` — `null` без `shipment.view_personal`, в том числе внутри `revisions[].content`.

Только в карточке: `revisions[]`, `prints[]` (со `status`), `movements[]`, `reviews[]`, `remarks[]`, `audit[]`.

Значения осей статуса:

| Ось | Значения |
|---|---|
| `status.document` | `draft`, `not_printed`, `active`, `reprint_required`, `annulled`, `deleted` |
| `status.didox` | `verified`, `manual_pending`, `manual_mismatch`, `not_applicable`, `null` (черновик) |
| `status.onec` | всегда `none` — сверка 1С не реализована |
| `status.review.*` | `pending`, `approved`, `rejected`, `stale` (решение по прежней редакции) |
| `status.period` | `open`, `closed` |

### PDF и QR

| Метод и путь | Право | Ответ |
|---|---|---|
| `GET /api/shipments/{id}/preview.pdf` | видимость | PDF с водяным знаком «ПРЕДПРОСМОТР», без QR. Ничего не записывает |
| `GET /api/prints/{print_id}/pdf` | видимость ТТН | сохранённый PDF выпуска |
| `GET /api/verify/prints/{print_id}` | **публичный** | `{print_id, status, number, shipment_date, warehouse_id, printed_revision, current_revision, printed_at}`; `status` = `current` / `superseded` / `outdated_revision` / `annulled` |

## ТТН: запись

| Метод и путь | Право | Idempotency-Key | Результат |
|---|---|---|---|
| `POST /api/shipments/drafts` | `shipment.create` + склад | да | 201, карточка черновика |
| `PUT /api/shipments/drafts/{id}` | `shipment.create` + склад | нет | карточка |
| `POST /api/shipments/drafts/{id}/confirm` | `shipment.create` + склад | да | ТТН `created`, номер зарезервирован. Тело `{content?}`; без него берётся сохранённый ввод черновика |
| `POST /api/shipments` | `shipment.create` + склад | да | 201, ТТН `created` |
| `PUT /api/shipments/{id}` | см. ниже | да | новая редакция |
| `DELETE /api/shipments/{id}` | `shipment.delete` + склад | нет | только `draft`/`created`; номер освобождается |
| `POST /api/shipments/{id}/print` | `shipment.print` + склад | да | `{print_id, is_first, shipment}` |
| `POST /api/shipments/{id}/annul` | `shipment.annul` | да | тело `{reason}` (≥ 3 символов) |
| `POST /api/shipments/{id}/reviews` | `review.documents` / `review.actual` | нет | тело `{kind, approved, comment}`; отказ требует комментария |
| `POST /api/shipments/{id}/remarks` | `shipment.remark` (+ `shipment.grant_correction`) | нет | тело `{text, grant_correction}` |
| `POST /api/shipments/{id}/close` | `shipment.close` | нет | закрытие |
| `PUT /api/shipments/{id}/accounting-date` | `accounting.date` | нет | тело `{accounting_date}` |

Тело создания (`ShipmentContentIn`, лишние поля запрещены):

```json
{
  "warehouse_id": 1, "operation": "sale", "destination_warehouse_id": null,
  "shipment_date": "2026-10-15",
  "customer": {"name": "...", "tin": "301234567", "counterparty_id": null},
  "consignee": {"name": "...", "tin": "301234567"},
  "basis": {"mode": "didox", "contract_didox_id": "C1", "poa_didox_id": "P1", "confirmed_by_poa": false},
  "vehicle_plate": "01A123BC", "vehicle_owner_id": null, "driver_pinfl": "31234567890123",
  "fuel_name": "Дизельное топливо", "volume_l": "20000", "density": "0.8400", "mass_t": null,
  "note": null
}
```

Ручное основание: `"basis": {"mode": "manual", "contract_number", "contract_date", "poa_number", "poa_date"}`.

Исправление `PUT /api/shipments/{id}`: тело `{content, reason, admin_mode}`.

- `created`: право `shipment.edit`, причина не нужна, попытка не расходуется. Смена склада или года
  переносит номер в другую последовательность.
- `issued`: обязательна причина. Без `admin_mode`: право `shipment.edit`, склад и дата неизменны, ТТН
  не закрыта, остались попытки. С `admin_mode`: право `shipment.admin_correct`, попытка не расходуется,
  закрытие снимается.

### Коды ошибок

| Код | HTTP | Когда |
|---|---|---|
| `unauthorized`, `auth_not_configured` | 401 | нет пользователя, не dev-режим |
| `forbidden` | 403 | нет права (`details.permission`) |
| `warehouse_forbidden` | 403 | склад недоступен |
| `admin_correction_required` | 403 | оператор меняет склад или дату после печати |
| `not_found` | 404 | нет ТТН или она не видна |
| `incomplete` | 422 | не заполнены поля (`details.fields`) |
| `same_warehouse` | 422 | перемещение на тот же склад |
| `basis_invalid` | 422 | основание не прошло проверку (`details.field`, `details.status`) |
| `manual_basis_disabled` | 422 | ручной режим не разрешён складу |
| `reason_required`, `no_changes` | 422 | исправление без причины или без изменений |
| `density_out_of_range`, `too_precise`, `must_be_positive`, `required`, `invalid_number` | 422 | количество |
| `comment_required` | 422 | отказ в проверке без комментария |
| `not_draft`, `not_editable`, `not_printable`, `not_issued`, `not_closable` | 409 | недопустимое состояние |
| `delete_after_print` | 409 | удаление напечатанной ТТН |
| `correction_limit` | 409 | исчерпан лимит |
| `limit_not_exhausted` | 409 | возврат попытки при неисчерпанном лимите |
| `closed` | 409 | оператор исправляет закрытую ТТН |
| `reprint_required`, `review_missing` | 409 | условия закрытия |
| `period_closed` | 409 | затронут закрытый месяц |
| `idempotency_mismatch` | 409 | ключ использован для другого запроса |
| `didox_unavailable` | 503 | Didox не ответил |

## Didox

| Метод и путь | Право | Ответ |
|---|---|---|
| `GET /api/counterparties/search?q=` | `shipment.create` | `{items:[{tin, name, counterparty_id, source: "saved"|"didox"}]}` |
| `POST /api/counterparties` | `shipment.create` | сохраняет «название/синоним → ИНН» |
| `GET /api/didox/contracts?partner_tin&on` | `shipment.create` | `{contracts:[док + selectable, reason], confirmed_by_poa:[док]}` |
| `GET /api/didox/powers-of-attorney?partner_tin&on&contract_number&contract_date[&contract_id]` | `shipment.create` | `{items:[док + selectable, reason]}` |

Документ: `{id, type, direction, sign_status, number, date, valid_from, valid_to, contract_number, contract_date}`;
`sign_status` = `accepted` / `awaiting_partner` / `awaiting_us` / `rejected` / `draft` / `deleted`.

## Ограничения (требования, ещё не выполненные)

1. **Didox — только заглушка.** `GSM_DIDOX_MODE` по умолчанию `fake`, и заглушка ставит `didox =
   verified`. По решению владельца: заглушка — только в явно включённом dev-режиме, её результат
   помечается как непроверенный, без настроенного адаптера успешной проверки не бывает.
2. **Нет настоящей аутентификации.** `X-User-Id` не подходит для приёмки прав.
3. **Нет даты и источника проверки Didox** в ТТН; повторное использование проверки сейчас шире
   решения владельца (см. сценарий).
4. **Право закрытия** сейчас у бухгалтера и финансиста; по решению — отдельное право, по умолчанию
   только у бухгалтера.
5. **Аннулирование, проверки и замечания** не требуют доступа к складу отправления: пользователь склада
   назначения с правом аннулирования может аннулировать перемещение.
6. **Сверка 1С** не реализована, `status.onec` всегда `none`.
7. **Проверить результат операции после сбоя** можно только повтором запроса с тем же
   `Idempotency-Key` (это безопасно). Отдельного запроса «чем закончилась операция с ключом X» нет.
8. Публичная проверка QR раскрывает номер, дату и склад без входа — нужно подтвердить.

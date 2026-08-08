# MCP `put_transaction_category` / `query_transactions` — `expense_owner`

**Связь:** [FIN-241](https://alexeielizarov.atlassian.net/browse/FIN-241); родитель [FIN-26](https://alexeielizarov.atlassian.net/browse/FIN-26); **Blocked by** [FIN-232](https://alexeielizarov.atlassian.net/browse/FIN-232) (backend `expense_owner`); **Relates** [FIN-211](https://alexeielizarov.atlassian.net/browse/FIN-211) (`put_transaction_category`), [FOPS-9](https://alexeielizarov.atlassian.net/browse/FOPS-9).

**Домен:** [operation-owner.md](../../../assistant/35-finance-assistant/methodology/accounting/operation-owner.md); backend — [fin-232-transaction-expense-owner.md](../../../PycharmProjects/FinancePlanningProject/.specs/fin/fin-232-transaction-expense-owner.md) (D-03…D-07, D-13 / F-01); базовый MCP tool — [fin-211-put-transaction-category.md](fin-211-put-transaction-category.md); mcp-only — [mcp-only.md](../../../assistant/35-finance-assistant/ops/mcp-only.md).

**Статус:** Утверждено (2026-07-26, rev.3)

## Назначение

[FIN-232](https://alexeielizarov.atlassian.net/browse/FIN-232) сохраняет владельца операции (`expense_owner`) через REST (`GET` / `PATCH …/category`). MCP ops по-прежнему не может задать или прочитать поле: `put_transaction_category` (FIN-211) требует только type+category без `expense_owner`, а `query_transactions` не отдаёт поле в rows. Назначение owner для july cutover и mcp-only workflows требует raw HTTP.

**Критерий приёмки:** на `test`/`cand` одним вызовом MCP задаётся `expense_owner` (active member) или сбрасывается в `null`; `query_transactions` возвращает `expense_owner` на неагрегированных rows; 422 `unknown_member` / `inactive_member` / `no_active_household` → clear tool error; вызов без новых параметров = FIN-211; `mcp-gaps.md` обновлён.

## Объём и границы

### Входит в объём

* Расширение MCP tool **`put_transaction_category`**: опциональный параметр `expense_owner` (set / clear) → body `PATCH …/category` (FIN-232 D-05/D-06).
* Owner-only PATCH через MCP, когда ключ `expense_owner` передан, а type/category **не** переданы (FIN-232 D-06 at-least-one).
* `reconciliation_note` как **дополнительное** поле к owner-only или type+category (D-14); note-only **не** открывается (D-15 / FIN-215).
* Проброс API 422 (`unknown_member`, `inactive_member`, `no_active_household`, `period_closed`, …) в tool error (существующий `format_api_error`).
* Ответ успеха: additive поле `expense_owner` в `transaction` subset.
* **`query_transactions`**: additive `expense_owner` на неагрегированных rows (`Row` + JSON path); missing API key → `null` (D-07).
* Unit-тесты (mock API); обновление `mcp-gaps.md`.
* Краткая пометка amend в FIN-211 (ссылка на FIN-241 для surface v2) — без смены Done-статуса FIN-211.

### Не входит в объём

* Backend API / миграция / FIN-102 `actual_spend` — [FIN-232](https://alexeielizarov.atlassian.net/browse/FIN-232).
* Полный FIN-87/FIN-74 surface (category-only, clear→pending, `category_source=derived`, **note-only**) — [FIN-215](https://alexeielizarov.atlassian.net/browse/FIN-215); эта задача **не** открывает их.
* Фильтр list по `expense_owner` — вне backend v1 (FIN-232 D-07) и вне MCP v1.
* UI — [FIN-243](https://alexeielizarov.atlassian.net/browse/FIN-243).
* Close readiness gate — [FIN-242](https://alexeielizarov.atlassian.net/browse/FIN-242).
* Удаление mapping fallback — [FIN-153](https://alexeielizarov.atlassian.net/browse/FIN-153).
* Ops assign July на prod — [FOPS-9](https://alexeielizarov.atlassian.net/browse/FOPS-9).
* Prod smoke без явной ops-команды.
* Отдельный MCP-параметр `clear_expense_owner` (D-13).

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| Backend `PATCH …/category` | FIN-232: optional `expense_owner`; owner-only OK | Нет MCP-проброса |
| `put_transaction_category` | FIN-211: required type+category; body без owner | Нельзя set/clear owner |
| Schema tool | `required: [transaction_id, transaction_type, transaction_category]` | Owner-only невозможен |
| Ответ tool | subset без `expense_owner` | Нет подтверждения persist |
| `query_transactions` rows | `id`, `transaction_type`, … (FIN-211 D-09) | Нет `expense_owner` |
| `mcp-gaps.md` | Описание без owner | Label `mcp-gap` на FIN-241 |

## Обратная совместимость

* Вызов `put_transaction_category` **без** ключа `expense_owner` и **с** type+category — **идентичен** FIN-211 (тот же body, те же pre-checks).
* Существующие unit-тесты FIN-211 (T1–T11) **не регрессируют**.
* `query_transactions`: поле `expense_owner` **additive**; существующие поля/фильтры/`group_by=month` без изменений.
* Если API старой версии **не** вернул ключ `expense_owner`, MCP row всё равно содержит `"expense_owner": null` (D-07).
* Клиенты, игнорирующие неизвестные ключи row, не ломаются.

## Целевое поведение

### Pipeline / формула

```
# put_transaction_category — FIN-241 (extends FIN-211)

1. finance_api_connect / get_session(profile, base)
2. validate MCP args:
   - transaction_id: non-empty strip (как FIN-211)
   - category_source: если ключ присутствует → ValueError (FIN-211 D-04, без изменений)
   - has_type := transaction_type key present AND non-empty after strip
   - has_category := transaction_category key present AND non-empty after strip
   - has_owner := ключ expense_owner присутствует в arguments
     # независимо от значения: null / "" / "   " / "nikolai" → has_owner = true (D-16)
   - if has_type XOR has_category → ValueError
     (type+category только парой; type-only / category-only не открываем)
   - if not (has_type and has_category) and not has_owner → ValueError
     (MCP at-least-one: (has_type ∧ has_category) ∨ has_owner)
     # reconciliation_note НЕ участвует в at-least-one (D-15); note-only → ValueError
   - if has_type: strip type + category (FIN-211)
3. body := {}
   if has_type:
     body["transaction_type"] = stripped type
     body["transaction_category"] = stripped category
   if has_owner:
     body["expense_owner"] = arguments["expense_owner"]
     # MCP НЕ trim/normalize (D-16); clear-семантика = FIN-232 D-05 на backend
   if reconciliation_note key present:
     body["reconciliation_note"] = value (как FIN-211; доп. к owner или type+category — D-14)
4. path := /api/v1/transactions/{id}/category?allow_closed={true|false}
5. status, resp := PATCH path body
6. if status == 200 → ok:true + transaction subset (incl. expense_owner)
   else → tool error с телом API (format_api_error); запись не применена
```

```
# query_transactions — additive field

row_from_api:
  expense_owner := raw.get("expense_owner")
  # ключ отсутствует в ответе API (старая версия) → null (D-07)
  # значение null из API → null
JSON rows (non-aggregated): всегда включают ключ "expense_owner" (string | null)
group_by=month: без изменений (агрегаты без owner)
```

### MCP: входные параметры `put_transaction_category`

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из сессии | URL API |
| `transaction_id` | string | **да** | — | UUID строки |
| `transaction_type` | string | условно | — | Обязателен **вместе** с `transaction_category`, если нет owner-only |
| `transaction_category` | string | условно | — | Пара к type (FIN-211) |
| `expense_owner` | string \| null | условно | — | Ключ отсутствует = не менять колонку; ключ present (в т.ч. `null` / `""` / whitespace) = `has_owner`; значение **без** MCP-нормализации → backend FIN-232 D-05 |
| `reconciliation_note` | string \| null | нет | — | Если ключ передан — в body; **сам по себе** at-least-one не удовлетворяет (D-15) |
| `allow_closed` | bool | нет | `false` | Query `allow_closed` |

**Schema `required`:** только `transaction_id`. Условная обязательность type/category/owner — в pipeline (шаг 2), не в JSON Schema `required[]`.

**Не принимать:** `category_source` (FIN-211 D-04); `clear_expense_owner` (D-13).

### MCP: ответ / side effects

Успех (минимальный контракт `transaction`):

```json
{
  "ok": true,
  "profile": "cand",
  "base": "http://127.0.0.1:8000",
  "transaction": {
    "id": "<uuid>",
    "transaction_type": "C",
    "transaction_category": "C0003",
    "category_source": "manual",
    "classification_status": "classified",
    "reconciliation_note": "",
    "expense_owner": "nikolai"
  }
}
```

* После owner-only: type/category/source/status — как в ответе API (не менялись, если не передавались).
* `expense_owner: null` после clear — контрактно.

### Резолв / валидация

| Ситуация | Поведение |
| -------- | --------- |
| Owner set: active member id | API 200; MCP `ok` |
| Owner clear: ключ present, значение `null` / `""` / whitespace | MCP пробрасывает as-is; API clear → `null`; MCP `ok` |
| `expense_owner: null` + `reconciliation_note` | `has_owner=true` (наличие ключа); body содержит оба ключа |
| Unknown / inactive member / no active household | API 422 → tool error (`format_api_error` с `error.code`) |
| Omit `expense_owner` + type+category | FIN-211 path; колонка owner **не** в body |
| Owner-only (только ключ `expense_owner`) | Body только owner; type/category не требуются |
| Owner-only + `reconciliation_note` | Оба в body (D-14) |
| Только `reconciliation_note` (без type/category и без ключа owner) | `ValueError` до HTTP; FIN-215 surface **не** открыт (D-15) |
| Type без category (и наоборот) | `ValueError` до HTTP |
| Ни type+category, ни ключ `expense_owner` | `ValueError` до HTTP |
| `category_source` передан | `ValueError` (FIN-211 D-04) |
| Closed period, `allow_closed=false` | API 422 `period_closed` → tool error |

Валидация member **только** на backend (FIN-232 D-03); MCP **не** дублирует lookup household members.

**Backend invariant (D-16):** MCP не trim/normalize `expense_owner`; значение передаётся backend без изменения. Семантика whitespace / empty / `null` → clear определяется **только** FIN-232 D-05.

### Конфликты

Не применимо (нет override arrays). Конфликт «пустое body» закрыт MCP at-least-one (D-05/D-15) + API.

### Инварианты (после pipeline)

1. Omit `expense_owner` → ключ отсутствует в PATCH body → колонка owner не меняется.
2. Key `expense_owner` present → ключ в body (даже при `null`); clear/set по FIN-232 D-05 на backend; MCP не нормализует (D-16).
3. Type+category без owner → поведение FIN-211 без регрессии.
4. Owner-only → допустим; не открывает category-only / type-only / derived / note-only.
5. `reconciliation_note` не удовлетворяет MCP at-least-one сам по себе (D-15).
6. Любая ошибка pre-HTTP или API → **нет** частичной записи.
7. Неагрегированный row `query_transactions` всегда содержит ключ `expense_owner` (`string | null`), в т.ч. когда API ключ не вернул.

## Открытые решения

Нет.

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Surface | Расширить существующий `put_transaction_category`; **не** новый tool |
| D-02 | Контракт set/clear | FIN-232 D-03…D-06; MCP pass-through |
| D-03 | Owner-only | Разрешён, когда ключ `expense_owner` present и type/category отсутствуют |
| D-04 | Type+category | По-прежнему только **парой**; XOR → error до HTTP |
| D-05 | At-least-one MCP | `(has_type ∧ has_category) ∨ has_owner`, где `has_owner` = **наличие ключа** (включая `null`/empty/whitespace), не truthiness значения |
| D-06 | Schema required | Только `transaction_id`; условные поля — в коде |
| D-07 | `query_transactions` | Additive `expense_owner` на non-aggregated rows; missing API key → `null`; `group_by=month` без изменений |
| D-08 | Ответ tool | Additive `expense_owner` в `transaction` subset |
| D-09 | `category_source` | По-прежнему forbid (FIN-211 D-04) |
| D-10 | Backend | Без изменений в этой задаче; **Blocked by** FIN-232 Done |
| D-11 | FIN-215 | Полный FIN-87 surface **не** открывается |
| D-12 | mcp-gaps | Обновить описание tools; снять `mcp-gap` с FIN-241 при Done |
| D-13 | Clear owner (O-01→A) | Отдельный `clear_expense_owner` **не** вводится. Omit = не менять; `null` / `""` / whitespace = clear по FIN-232 D-05 |
| D-14 | Note + owner (O-02→A) | `reconciliation_note` разрешён совместно с owner-only PATCH |
| D-15 | Note-only guardrail | Наличие `reconciliation_note` **само по себе** не удовлетворяет MCP at-least-one. Note-only вне scope (FIN-215). Note — только доп. поле к owner-only либо type+category |
| D-16 | Pass-through owner | `has_owner` = наличие ключа независимо от значения; MCP **не** trim/normalize `expense_owner`; clear-семантика — backend FIN-232 D-05 |

## Non-goals / guardrails

* Не валидировать member id на стороне MCP (нет дублирования FIN-36 lookup).
* Не добавлять фильтр `expense_owner=` в `query_transactions` v1.
* Не открывать note-only / category-only / type-only через ослабление at-least-one.
* Не smoke на prod без явной команды.
* Не смешивать с `put_transaction_overrides` (reconciliation map).

## Чеклист тестов

* **T1:** Owner-only set (`expense_owner=<active>`) → PATCH body только owner; `ok`; ответ `expense_owner` = id.
* **T2:** Clear `expense_owner: null` → body null; ответ `expense_owner: null`.
* **T3:** Clear `expense_owner: ""` → проброс as-is; mock API → `expense_owner: null`.
* **T3a:** `expense_owner: "   "` → значение передано API **без** локальной нормализации; mock API → `expense_owner: null`.
* **T4:** Type+category **без** `expense_owner` → body как FIN-211; ключ owner отсутствует (regression).
* **T5:** Type+category **с** `expense_owner` → все три в body; успех.
* **T6:** API 422 `unknown_member` / `inactive_member` / `no_active_household` → tool error с code в сообщении.
* **T7:** Type без category → error до HTTP.
* **T8:** Ни type+category, ни ключ owner → error до HTTP.
* **T9:** `category_source` → error (FIN-211 D-04 regression).
* **T10:** `allow_closed` + `period_closed` — как FIN-211 T7/T8.
* **T11:** Schema: `required` = `["transaction_id"]`; свойство `expense_owner` type string\|null.
* **T12:** `query_transactions` non-aggregated row содержит `expense_owner`; API без ключа → row `null`; `group_by=month` без регрессии; старые поля на месте.
* **T13:** owner-only + `reconciliation_note` → оба в body (D-14).
* **T13a:** `expense_owner: null` + `reconciliation_note` → body с обоими ключами (`has_owner` по наличию ключа).
* **T14:** только `reconciliation_note`, без type/category и без ключа `expense_owner` → `ValueError` до HTTP; FIN-215 surface не открыт (D-15).
* **T15:** type+category + `reconciliation_note`, без `expense_owner` → все три поля в body; ключ owner отсутствует; поведение FIN-211 для note не регрессирует.

## Приёмочная проверка

### Предусловия

* MCP `finance_api_connect` → `data_profile` = **`test`** или **`cand`** (не prod).
* FIN-232 deployed на том же API (колонка + PATCH/GET).
* Active household + ≥1 active member (seed FIN-36 / FIN-240).
* Известен `transaction_id` personal expense через `query_transactions`.

### A1 — Set + read

**Действие:**

```json
put_transaction_category({
  "profile": "cand",
  "transaction_id": "<uuid>",
  "expense_owner": "nikolai"
})
```

затем `query_transactions` по периоду строки.

**Ожидаемый результат:** `ok: true`; `transaction.expense_owner = "nikolai"`; в row query то же значение.

### A2 — Clear

**Действие:** `put_transaction_category` с `expense_owner: null` на ту же строку.

**Ожидаемый результат:** `expense_owner: null` в ответе и в `query_transactions`.

### A3 — Invalid member

**Действие:** `expense_owner: "no-such-member"`.

**Ожидаемый результат:** tool error с `unknown_member` (или эквивалент из API); строка не изменена.

## Связь с другими FIN

| FIN | Роль |
| --- | ---- |
| FIN-232 | Backend contract; **Blocks** FIN-241 |
| FIN-211 | Базовый MCP tool; эта спека расширяет surface |
| FIN-215 | Полный FIN-87 MCP surface (в т.ч. note-only) — out of scope; D-15 |
| FOPS-9 | Ops assign July — потребитель после Done |
| FIN-242 / FIN-243 / FIN-153 | Follow-ups cutover; вне этой задачи |

## История ревизий

| Дата | Rev | Изменение |
| ---- | --- | --------- |
| 2026-07-26 | rev.1 | Intake: extend put_transaction_category + query_transactions; O-01/O-02 |
| 2026-07-26 | rev.2 | O-01/O-02 → D-13/D-14; D-15 note-only guardrail; D-16 pass-through; T3a/T13a/T14; уточнения D-05/D-07 |
| 2026-07-26 | rev.3 | T15: type+category+note regression (FIN-211); Approved |

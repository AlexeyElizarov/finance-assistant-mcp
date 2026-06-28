# MCP tools `put_transaction_overrides` и `upsert_expense_project`

**Связь:** [FIN-107](https://alexeielizarov.atlassian.net/browse/FIN-107) (Canonical: **MCP-21**); родитель [FIN-101](https://alexeielizarov.atlassian.net/browse/FIN-101) (BLG-093 household ops); wave-5; **Relates** [FIN-103](https://alexeielizarov.atlassian.net/browse/FIN-103).

**Домен:** reconciliation overrides и effective fact — [plan-fact-period-closing.md](../../../PycharmProjects/FinancePlanningProject/.specs/planning/plan-fact-period-closing.md) (§`transaction_key`, **D29**); проекты расходов — `GET/POST/PUT /api/v1/projects`; ops-runbook шаг 3.3–3.4 — [monthly-close-api-runbook.md](../../../PycharmProjects/FinancePlanningProject/.specs/operations/monthly-close-api-runbook.md).

**Статус:** Утверждено (2026-06-28, rev.2).

## Назначение

Переклассификация операций в plan-fact (например, отель Booking.com, ошибочно попавший в «Командировки», а не в личный/проектный расход) требует `PUT /api/v1/budget/reconciliation` с `transaction_overrides` и часто правки `projects.json` через `POST`/`PUT /api/v1/projects`. Сегодня агенты обходят политику [mcp-only.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/mcp-only.md) ad-hoc Python-скриптами к `monthly_close_lib`.

**Пример (2026-05):** предоплаты Canal Pride Amsterdam (353,06 € отель + 159,96 € DB) оказались в профессиональных/контурных строках до ручного override после `reopen_periods`.

**Критерий приёмки:** ops применяет overrides и upsert проекта только через MCP `finance-assistant`; после записи — опциональный derive; без прямых REST/shell-обходов.

## Объём и границы

### Входит в объём

* MCP tool **`put_transaction_overrides`**: запись map `transaction_key` → `budget_item_id` за месяц через `PUT /api/v1/budget/reconciliation`.
* MCP tool **`upsert_expense_project`**: create/replace одного проекта через `POST` / `PUT /api/v1/projects`.
* Логика в `monthly_close_lib.py` + handlers в `server.py` (уже есть черновая реализация — см. §«Факт до»).
* Схемы tools в MCP server; строки в `mcp-gaps.md`; ссылка в [monthly-close-api/index.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/index.md) после Done.
* Unit-тесты (mock `ApiClient`): merge, closed period, upsert create/update.

### Не входит в объём

* Изменения backend API (`FinancePlanningProject`) — контракты уже есть.
* UI plan-fact / Fiori.
* `DELETE /api/v1/projects` — отдельный tool при необходимости (follow-up).
* Bulk overrides за несколько месяцев одним вызовом.
* Автоматический подбор `transaction_key` / `budget_item_id` по описанию операции — ops сначала `query_transactions` / grouped plan-fact.
* `apply_keywords` — отдельный tool; эта спека не дублирует keyword-цикл (runbook §3.4).

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| Backend `PUT /budget/reconciliation` | Полная замена map overrides (**D29**) | Данные и валидация есть |
| Backend `POST/PUT /projects` | CRUD `projects.json` | Данные есть |
| `monthly_close_lib` | `put_transaction_overrides`, `upsert_expense_project` | Реализовано, **без тестов** |
| `server.py` | Оба tool зарегистрированы | **Не** в `mcp-gaps.md`; label `mcp-gap` на FIN-107 |
| Ops / агенты | Ad-hoc scripts | Нарушение mcp-only |

## Целевое поведение

### Домен: `transaction_key` и overrides (справочно)

Источник правды — [plan-fact-period-closing.md](../../../PycharmProjects/FinancePlanningProject/.specs/planning/plan-fact-period-closing.md):

* **`transaction_key`** — стабильный SHA-256 hex; ops получает из `query_transactions` или drill-down API.
* **`transaction_overrides`** — map `transaction_key` → `budget_item_id` (UUID статьи ACT в выбранной версии).
* Backend **PUT** принимает **полную** map для `(budget_version_id, period)` — не patch (**D29**).
* Период **`closed`** → **422** `period_closed`.
* Невалидная статья / flow mismatch → **422** `validation_error`.
* Успешный PUT → `status` **`draft`** (если был `open`).

**Reopen:** `POST .../reconciliation/reopen` удаляет запись reconciliation → overrides сбрасываются; типичный сценарий: `reopen_periods` → overrides → derive → close.

### MCP: `put_transaction_overrides`

#### Вход

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из сессии | URL API |
| `period` | string | **да** | — | `YYYY-MM` |
| `overrides` | object | **да** | — | Непустой map `transaction_key` → `budget_item_id` |
| `merge` | bool | нет | `true` | Слить с существующими overrides перед PUT. **`false` — destructive:** заменяет всю persisted map периода содержимым `overrides`; все ключи, не переданные в аргументе, удаляются |
| `derive` | bool | нет | `true` | После **успешного** PUT (`2xx`) — `POST /api/v1/transactions/derive` scope `period` |

#### Алгоритм

1. `finance_api_connect` / сессия → `ApiClient`, resolve ACT `budget_version_id` для `period` (`resolve_budget_version_id`).
2. `GET /api/v1/budget/reconciliation?budget_version_id=…&period={YYYY-MM-01}` — текущая map.
3. Нормализовать existing map: `existing_overrides = existing.get("transaction_overrides") or {}` — если поле **отсутствует** или **`null`**, трактовать как `{}`.
4. Построить `current`:
   * `merge=true` → `existing_overrides` + `overrides` (ключи из аргумента перезаписывают).
   * `merge=false` → только `overrides` из аргумента (**destructive:** полная замена persisted map; ключи только в existing, но не в arg — удаляются).
5. `PUT /api/v1/budget/reconciliation` с `budget_version_id`, `period`, `transaction_overrides: current`.
6. **Только после успешного PUT** (`2xx`): если `derive=true` → `POST /api/v1/transactions/derive` с `{"scope":"period","accounting_period":"YYYYMM"}`. При ошибке PUT derive **не** вызывается (запрещён `try/finally` с derive).
7. Вернуть JSON-ответ tool (см. ниже).

**Конкурентность:** MCP merge **не** обеспечивает optimistic locking; семантика **last-write-wins** (как backend **D29**). Два параллельных вызова с `merge=true` могут перезаписать overrides друг друга — приемлемо для одиночного ops-оператора; не patch API.

**Снятие override:** при `merge=true` удалить ключ **нельзя**. Ops: `merge=false` и передать **полную** желаемую map (после шага 2 — скопировать `existing_overrides` и убрать ключи). Отдельный параметр `remove_keys` — **вне scope** (follow-up F-02).

#### Выход (корень)

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `ok` | bool | `true` при успехе |
| `profile` | string | data profile |
| `base` | string | API base URL |
| `period` | string | `YYYY-MM` |
| `budget_version_id` | string | UUID ACT-версии |
| `overrides_applied` | object | map из аргумента (не полная persisted map) |
| `merge` | bool | фактический флаг |
| `reconciliation` | object | тело ответа PUT (status, transaction_overrides, methodology_status, …) |
| `derive` | object \| omitted | тело derive; **отсутствует**, если `derive=false` |

#### Ошибки

| Ситуация | Поведение |
| -------- | --------- |
| `overrides` пустой / не object | Tool error (`ValueError`) |
| `period` не `YYYY-MM` | Tool error |
| Период closed | Tool error (RuntimeError из HTTP 422) |
| Неизвестный `budget_item_id` / flow mismatch | Tool error (422) |
| API 5xx / timeout | Tool error; частичный ответ **не** возвращается |
| PUT не `2xx` | Tool error; derive **не** вызывается |
| `derive=false` | PUT выполняется; derive **не** вызывается |

### MCP: `upsert_expense_project`

#### Вход

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `profile` | string | нет | default `prod` |
| `base` | string | нет | URL API |
| `project` | object | **да** | Тело проекта (см. схему) |

#### Схема `project` (backend `ProjectBody`)

| Поле | Тип | Правила |
| ---- | --- | ------- |
| `id` | string | `PR` + три цифры (напр. `PR005`) |
| `description` | string | непустое |
| `keywords` | array | непустой; элементы — string или `{ "keyword": "…", "valid_to": "YYYYMMDD" }` |
| `valid_from` | string | `YYYYMMDD` |
| `valid_to` | string | `YYYYMMDD`, `>= valid_from` |

#### Алгоритм

1. `GET /api/v1/projects` → множество существующих `id`.
2. Если `project.id` есть → `PUT /api/v1/projects/{id}` (**full replace**).
3. Иначе → `POST /api/v1/projects` (**201**).
4. Вернуть action + project.

**Семантика replace:** все поля проекта (`description`, `keywords`, `valid_from`, `valid_to`) **полностью** заменяются содержимым аргумента `project`; **partial update не поддерживается** — переданный объект должен быть полным (как backend `ProjectBody`).

**Инвариант:** `project.id` в теле **должен** совпадать с path при PUT; MCP передаёт один объект — id из тела.

#### Выход

```json
{
  "ok": true,
  "profile": "prod",
  "base": "http://127.0.0.1:8000",
  "action": "created",
  "project": { "id": "PR005", "description": "…", "keywords": ["BOOKING"], "valid_from": "20260501", "valid_to": "20260531" }
}
```

`action`: `"created"` | `"updated"`.

#### Ошибки

| Ситуация | Поведение |
| -------- | --------- |
| `project` не object | Tool error |
| Дубликат id на POST | Tool error (409 conflict) |
| Валидация полей | Tool error (422) |
| API 5xx | Tool error |

**Примечание:** upsert проекта **не** запускает derive автоматически. После keywords — ops вызывает `apply_keywords` и/или `put_transaction_overrides` + derive по runbook §3.4.

### Типовой ops-сценарий (Canal Pride Amsterdam)

Предусловия: период был closed; нужна переклассификация.

```
1. reopen_periods({ "periods": ["2026-05"] })
2. query_transactions({ "date_from": "2026-05-01", "date_to": "2026-05-31", "contains": ["BOOKING"] })
   → transaction_key для отеля и DB
3. query_plan_fact / GET items → budget_item_id целевой статьи (личный фонд / проект)
4. (опц.) upsert_expense_project({ "project": { … keywords для Booking … } })
5. put_transaction_overrides({
     "period": "2026-05",
     "overrides": { "<tx_key_hotel>": "<item_uuid>", "<tx_key_db>": "<item_uuid>" }
   })
6. verify_month({ "period": "2026-05" })
7. process_month close — только по явной команде пользователя
```

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | API PUT semantics | Backend — полная map (**D29**); MCP при `merge=true` эмулирует patch на клиенте |
| D-02 | Default merge | `true` — типичный ops добавляет 1–N overrides без чтения полной map |
| D-03 | Удаление override | Только `merge=false` + полная желаемая map; отдельный `remove_keys` — follow-up |
| D-04 | Default derive | `true` после PUT overrides — согласовано с runbook §3.4 |
| D-05 | derive в upsert | **Не** вызывать автоматически |
| D-06 | Закрытый период | Проброс 422; ops сначала `reopen_periods` |
| D-07 | Имена tools | `put_transaction_overrides`, `upsert_expense_project` (как в FIN-107) |
| D-08 | `budget_version_id` | Не в аргументах tool — resolve ACT через `resolve_budget_version_id` |
| D-09 | Ответ reconciliation | Полный body PUT в поле `reconciliation` (passthrough) |
| D-10 | Projects DELETE | Вне scope; при необходимости — отдельная FIN-задача |
| D-11 | Конкурентность merge | **Last-write-wins**; optimistic locking **не** поддерживается |
| D-12 | `transaction_overrides` null/absent | Нормализация в `{}` перед merge |
| D-13 | Derive после PUT | Только при успешном PUT (`2xx`); при ошибке PUT derive **запрещён** |
| D-14 | `merge=false` | **Destructive** — полная замена persisted map периода |
| D-15 | Upsert project | Full replace; partial update **не** поддерживается |

## Non-goals / guardrails

* Не менять backend reconciliation / projects API.
* Не писать overrides в `transactions.csv`.
* Не подбирать статью по ML/эвристике без явного `budget_item_id` от ops.
* Не обходить closed-period guard.
* Не дублировать `apply_keywords` внутри этих tools.

## Чеклист тестов

* **T1:** `merge=true` — existing `{a:1}` + arg `{b:2}` → PUT map `{a:1,b:2}`.
* **T2:** `merge=true` — arg `{a:9}` перезаписывает existing `a`.
* **T3:** `merge=false` — PUT только arg map; existing ключи не в arg — удалены из persisted.
* **T4:** Пустой `overrides` → tool error до HTTP.
* **T5:** PUT → 422 `period_closed` → tool error.
* **T6:** `derive=false` — derive endpoint не вызывается; поле `derive` отсутствует.
* **T7:** `derive=true` — один POST derive с `accounting_period=YYYYMM`.
* **T8:** upsert — новый id → POST, `action=created`.
* **T9:** upsert — существующий id → PUT, `action=updated`.
* **T10:** upsert — невалидный `id` (`PR1`) → tool error.
* **T11:** invalid `period` → tool error.
* **T12:** `merge=false`, `existing={}`, arg `{a:1}` → PUT `{a:1}`.
* **T13:** upsert — `keywords=[]` → validation error (tool error).

**Файл:** `scripts/test_transaction_overrides_and_projects.py` (`unittest` + mock `ApiClient`).

**Команда:**

```bash
cd mcp-servers/finance-assistant/scripts && python -m unittest test_transaction_overrides_and_projects -v
```

## Приёмочная проверка

### Предусловия

* API `prod` запущен; `finance_api_connect` → `data_profile == prod`.
* Тестовый месяц **open** или переоткрыт через `reopen_periods`.

### A1 — override одной операции

**Действие:** найти `transaction_key` через `query_transactions`; `put_transaction_overrides` с одним ключом, `merge=true`.

**Ожидаемый результат:**

* `reconciliation.status` → `draft`.
* `reconciliation.transaction_overrides` содержит ключ.
* `derive` присутствует при default `derive=true`.
* grouped plan-fact / `query_plan_fact` отражает новую статью после derive.

### A2 — upsert проекта

**Действие:** `upsert_expense_project` с новым `PRxxx` и keyword.

**Ожидаемый результат:** `action: created`; повтор с тем же `id` → `updated`; `GET /projects` через API содержит проект.

### A3 — mcp-only compliance

**Действие:** сценарий §Canal Pride только MCP tools.

**Ожидаемый результат:** без `monthly_close_lib` из shell и без curl к prod API.

### Документация (DoD FIN-107)

* `mcp-gaps.md` — оба tool в таблице «Доступные tools».
* FIN-107 References → путь к этой спеке; label `mcp-gap` снят при Done.
* (опц.) абзац в `monthly-close-api/index.md` — overrides / projects.

## E2E

| Сценарий | Изменение | Обновление |
| -------- | --------- | ---------- |
| FinancePlanningProject E2E | Нет | Нет |
| Ops reclassify closed month | MCP-only path | Пример в спеке + runbook cross-link |

## Follow-ups / Out of scope

| ID | Тема | Решение |
| -- | ---- | ------- |
| F-01 | `delete_expense_project` MCP | Отдельная задача при необходимости |
| F-02 | `remove_keys` в `put_transaction_overrides` | Упростить снятие override без full map |
| F-03 | [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104) money check | Использует plan-fact после overrides |
| F-04 | Push `Spec:` в Jira | ✓ при утверждении rev.2 |

## Утверждение

* **Статус:** Утверждено
* **Дата:** 2026-06-28 (rev.2; ревью PO: last-write-wins, destructive `merge=false`, derive-after-PUT, null-map, full-replace upsert)
* **Следующий шаг:** unit-тесты T1–T13 → обновить `mcp-gaps.md` → Done FIN-107

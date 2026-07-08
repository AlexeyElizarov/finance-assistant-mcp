# MCP tool `create_budget_item` — новая статья и REG plan-item в ACT-версии

**Связь:** [FIN-109](https://alexeielizarov.atlassian.net/browse/FIN-109); родитель [FIN-96](https://alexeielizarov.atlassian.net/browse/FIN-96); **Relates** [FIN-108](https://alexeielizarov.atlassian.net/browse/FIN-108) (follow-up F-01).

**Домен:** ops-пример — [household-base-share.md](../../../assistant/35-finance-assistant/working/2026-07/household-base-share.md) (Hue Sync TV 3 €/мес с 2026-05).

**Статус:** Утверждено (2026-06-28, rev.4).

## Назначение

Добавление новой статьи бюджета с месячным REG-планом требует `POST /api/v1/budget/items`, `POST /api/v1/budget/plan-items` в ACT-версии и `POST /api/v1/budget/projections/recalculate`. MCP `update_plan_item` (FIN-108) меняет только суммы существующих plan-items.

**Критерий приёмки:** ops создаёт статью + REG plan-item только через MCP `finance-assistant`; после записи `query_plan_fact` отражает новый план (при default `recalculate=true`).

## Объём и границы

### Входит в объём

* MCP tool **`create_budget_item`**: POST item + POST REG plan-item в ACT-версии + optional recalculate.
* Guards: ARC / `can_mutate`; дубликат имени статьи; валидация amount / periods.
* Semantics частичного успеха (POST items OK, POST plan-items fail) — см. **D-04**.
* Логика в `monthly_close_lib.py` + handler в `server.py`.
* Unit-тесты (mock `ApiClient`): happy path и validation errors.
* Строка в `mcp-gaps.md`; ссылка в [monthly-close-api/index.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/index.md) после Done.

### Не входит в объём

* Backend API — контракты уже есть.
* IRR plan-items — отдельный tool при необходимости.
* Bulk-создание статей.
* `delete_budget_item` / `delete_plan_item` — follow-up (**D-04**: rollback через DELETE не выполняется).
* Автоподбор категории / flow_type — ops передаёт явные значения.

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| Backend `POST /budget/items`, `POST /budget/plan-items` | Создание статьи и REG plan-item | Нет MCP path |
| Backend `POST /budget/projections/recalculate` | Пересбор проекции | Нужен после create для plan-fact |
| MCP `update_plan_item` (FIN-108) | Изменение суммы существующей записи | Не создаёт новые строки |
| Ops / агенты | UI / curl / ad-hoc скрипты | Нарушение mcp-only; блокер Hue (FIN-109) |

## Целевое поведение

### MCP: `create_budget_item`

#### Вход

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из сессии | URL API |
| `name` | string | **да** | — | Имя статьи (trim перед использованием) |
| `flow_type` | string | **да** | — | `EXP` или `INC` |
| `operation_category_id` | string | **да** | — | Код категории (напр. `C0006`); см. **D-06** |
| `amount` | string \| number | **да** | — | Сумма REG plan-item: **≥ 0** (`0` допустим) |
| `start_period` | string | **да** | — | `YYYY-MM` — первый активный месяц REG |
| `planning_type` | string | нет | `REG` | Только `REG` в v1 |
| `keywords` | list[string] | нет | `[]` | Keywords статьи; пустой список допустим (**D-15**) |
| `item_status` | string | нет | `ACT` | **`budget_item.status`**; MCP не валидирует enum (**D-16**) |
| `currency` | string | нет | `EUR` | Валюта plan-item |
| `periodicity` | string | нет | `M` | REG periodicity; MCP не ограничивает дополнительно (**D-14**) |
| `end_period` | string | нет | — | `YYYY-MM` — последний активный месяц REG (опц.) |
| `recalculate` | bool | нет | `true` | После успешных POST — `POST /budget/projections/recalculate` |

**Разделение status (**D-07**):** аргумент tool — `item_status` → поле `status` тела `POST /budget/items`. Plan-item всегда получает `status: "ACTIVE"` в `POST /budget/plan-items` (не параметризуется в v1).

#### Преобразование периодов → даты (**D-05**)

MCP парсит `start_period` / `end_period` через `parse_period` (`YYYY-MM` или `YYYYMM`).

| Аргумент | Поле plan-item | Правило |
| -------- | -------------- | ------- |
| `start_period` `2026-05` | `start_date` | **Первый день месяца:** `2026-05-01` |
| `end_period` `2026-07` | `end_date` | **Последний день месяца:** `2026-07-31` |
| `end_period` опущен | `end_date` | `null` (REG без верхней границы) |

**Валидация:** если `end_period` задан и **строго раньше** `start_period` (сравнение `(year, month)` после parse) → tool error **до HTTP**.

#### Алгоритм

1. `finance_api_connect` / сессия → `ApiClient`.
2. Нормализовать `name` → `name.strip()`; пустое имя → tool error.
3. Нормализовать `amount` → decimal string (напр. `"3.00"`); отрицательные → tool error до HTTP; **`amount == 0` допустим** (**D-08**).
4. `parse_period(start_period)`; при `end_period` — parse + проверка `end >= start`.
5. Если `planning_type != "REG"` → tool error (**D-03**).
6. `resolve_act_version_id` → `GET /api/v1/budget/versions/{id}` → `assert_version_mutable(version=...)`.
7. **Duplicate guard (**D-02**):** `GET /api/v1/budget/items` — если существует статья с тем же именем: сравнение **`strip()` + `casefold()`** на новом имени и на `item.name` (case-insensitive exact); Unicode NFC/NFD **не** нормализуется отдельно → tool error **до POST**.
8. `POST /api/v1/budget/items` с телом:

   ```json
   {
     "name": "<trimmed>",
     "flow_type": "<flow_type>",
     "operation_category_id": "<operation_category_id>",
     "planning_type": "REG",
     "keywords": [...],
     "status": "<item_status>"
   }
   ```

9. **Только после успешного POST items** (`201`): `POST /api/v1/budget/plan-items` с REG-телом:

   ```json
   {
     "budget_version_id": "<ACT uuid>",
     "budget_item_id": "<из шага 8>",
     "planning_type": "REG",
     "amount": "<normalized>",
     "currency": "<currency>",
     "status": "ACTIVE",
     "periodicity": "<periodicity>",
     "start_date": "<start_date>",
     "end_date": "<end_date or null>",
     "forecast_method": null
   }
   ```

10. **Только после успешных POST items и POST plan-items** (`201`): если `recalculate=true` → `POST /api/v1/budget/projections/recalculate` с `{"budget_version_id": "<ACT uuid>"}`.
11. Вернуть JSON (см. ниже).

**Частичный успех (**D-04**):** если шаг 8 успешен, шаг 9 не `201` — MCP **не** выполняет rollback (DELETE item вне scope). Tool error; payload **обязан** включать **create context** успешного POST items — `budget_item_id`, `budget_version_id`, `name`, `amount`, `budget_item` — чтобы ops мог вручную создать plan-item или удалить статью в UI. Recalculate **не** вызывается.

#### Выход (корень)

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `ok` | bool | `true` при успехе |
| `profile` | string | data profile |
| `base` | string | API base URL |
| `budget_item_id` | string | UUID новой статьи |
| `plan_item_id` | string | UUID plan-item |
| `budget_version_id` | string | ACT version UUID |
| `name` | string | Имя статьи (trimmed) |
| `amount` | string | Нормализованная сумма |
| `start_period` | string | `YYYY-MM` из аргументов |
| `end_period` | string \| omitted | `YYYY-MM`, если задан в аргументах |
| `budget_item` | object | Тело ответа POST items |
| `plan_item` | object | Тело ответа POST plan-items |
| `recalculate` | object \| omitted | Краткий итог recalculate; **отсутствует**, если `recalculate=false` |

Поле `recalculate` в ответе (если вызывался):

```json
{
  "budget_version_id": "<uuid>",
  "projection_rows": 42
}
```

`projection_rows` — число пересчитанных projection-строк; MCP извлекает count **независимо от внутреннего формата backend** (сегодня: `len(budget_projections)`; допускается `updated_count`) — как FIN-108 **D-15**.

#### Ошибки

| Ситуация | Поведение |
| -------- | --------- |
| Отсутствуют обязательные поля (`name`, `flow_type`, `operation_category_id`, `amount`, `start_period`) | Tool error |
| `name` пустой после trim | Tool error |
| `planning_type != REG` | Tool error |
| `amount` отсутствует / не число / < 0 | Tool error |
| `start_period` / `end_period` не `YYYY-MM` | Tool error |
| `end_period` < `start_period` | Tool error **до HTTP** |
| Статья с тем же именем уже есть (**D-02**) | Tool error **до POST** |
| ACT ARC / `assert_version_mutable` fail | Tool error **до POST** |
| `POST /budget/items` не `201` | Tool error; plan-items и recalculate **не** вызываются |
| `POST /budget/items` OK, `POST /budget/plan-items` не `201` | Tool error (**D-04**): payload с create context items; recalculate **не** вызывается |
| Backend validation (`operation_category_id`, `item_status`, `periodicity`, planning_type mismatch и т.д.) | Tool error (422 passthrough) — **D-06**, **D-14**, **D-16** |
| Recalculate 422/5xx после успешных POST | Tool error (**D-09**): payload с полным create context — `budget_item_id`, `plan_item_id`, `budget_version_id`, `amount`, `budget_item`, `plan_item` — ops повторяет recalculate без повторного POST |

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Имя tool | `create_budget_item` (FIN-109) |
| D-02 | Duplicate name | `strip()` + `casefold()` exact match на новом имени и `item.name` из GET; error до POST; NFC/NFD не нормализуется |
| D-03 | `planning_type` | Только `REG` в v1; IRR → tool error |
| D-04 | Partial create | POST items OK + POST plan-items fail → **нет rollback**; error payload с `budget_item_id`, `budget_item`, …; DELETE вне scope |
| D-05 | Period → date | `start_period` → `YYYY-MM-01`; `end_period` → последний день месяца (`YYYY-MM-DD`); `end < start` → error |
| D-06 | `operation_category_id` | MCP **не** валидирует существование категории заранее; backend 422 пробрасывается как tool error |
| D-07 | Status fields | `item_status` (default `ACT`) → budget item; plan-item всегда `ACTIVE` |
| D-08 | `amount == 0` | Допустим (неотрицательная сумма) |
| D-09 | Recalculate failure | Exception/payload включает create context: `budget_item_id`, `plan_item_id`, `budget_version_id`, `amount`, `budget_item`, `plan_item` |
| D-10 | Default recalculate | `true` — иначе plan-fact не обновится |
| D-11 | `budget_version_id` | Не в аргументах — всегда ACT из `resolve_act_version_id`; в **ответе** присутствует |
| D-12 | ARC guard | `assert_version_mutable(version=...)` до любого POST |
| D-13 | Derive | **Не** вызывать — create items/plan-items не затрагивают транзакции |
| D-14 | `periodicity` | MCP **не** ограничивает дополнительно (в т.ч. `D`/`W`/`B` допустимы как аргумент); backend validation — source of truth |
| D-15 | `keywords=[]` | Пустой список keywords допустим; не является ошибкой |
| D-16 | `item_status` enum | MCP **не** валидирует допустимые значения заранее; backend 422 пробрасывается как tool error |

## Non-goals / guardrails

* Не менять backend budget API.
* Не выполнять compensating DELETE при partial create.
* Не валидировать `operation_category_id`, `item_status`, `periodicity` отдельным GET / enum-check (backend — source of truth).
* Не создавать IRR plan-items в v1.
* Не обходить ARC / validation guards.

## Чеклист тестов

* **T1:** happy path → POST items + POST plan-items + recalculate; `start_date` / `end_date` по **D-05**.
* **T2:** duplicate name (exact case) → tool error до POST.
* **T3:** ARC version → tool error до POST.
* **T4:** `amount=-1` → tool error до HTTP.
* **T5:** `recalculate=false` → POST recalculate не вызывается; поле `recalculate` отсутствует.
* **T6:** POST items OK + POST plan-items fail → tool error (**D-04**); recalculate не вызывается; payload содержит `budget_item_id`, `budget_item`.
* **T7:** recalculate fail после успешных POST → error payload содержит полный create context (**D-09**).
* **T8:** `planning_type=IRR` → tool error.
* **T9:** invalid `start_period` → tool error.
* **T10:** `end_period` < `start_period` → tool error до HTTP.
* **T11:** duplicate name — другой case / лишние пробелы (`" Hue "`) → error (**D-02**, `casefold`).
* **T12:** `recalculate=true` → `projection_rows == len(budget_projections)` (или `updated_count`).
* **T13:** `amount=0` → POST с `"0.00"`; успех.
* **T14:** `keywords=[]` (или опущено) → успех; POST items с `"keywords": []` (**D-15**).

**Файл:** `scripts/test_create_budget_item.py` (`unittest` + mock `ApiClient`).

**Команда:**

```bash
cd mcp-servers/finance-assistant/scripts && python -m unittest test_create_budget_item -v
```

## Проверка (в scope FIN-109)

**Prod-мутации и ops-приёмка (Hue на prod) — вне scope этой задачи.** Отдельно: household ops после merge tool.

### Unit (обязательно для Done)

```bash
cd mcp-servers/finance-assistant/scripts && python -m unittest test_create_budget_item -v
```

* Все **T1–T14** зелёные (mock `ApiClient`).

### Ручная smoke (опционально, не prod)

* MCP tool `create_budget_item` виден после restart сервера.
* Schema: `item_status`, `start_period`, `end_period`.

## Типовой ops-сценарий (Hue) — **не в scope FIN-109**

Пример для будущего household ops (prod, отдельная процедура):

```
1. create_budget_item({ ... Hue ... })
2. query_plan_fact({ "period": "2026-07", "article": "Hue" })
3. household_base_share({ "period": "2026-07" })
```

## Follow-ups / Out of scope

| ID | Тема | Решение |
| -- | ---- | ------- |
| F-01 | Compensating DELETE при partial create | Вне scope; ops / UI |
| F-02 | `delete_plan_item` MCP | Отдельная задача (FIN-108 F-02) |
| F-03 | IRR create | Отдельный tool при необходимости |
| F-04 | Backend ARC guard на POST plan-items | Вне FIN-109; MCP guard достаточен |
| F-05 | Prod Hue / household ops приёмка | Отдельная ops-процедура; не блокирует Done FIN-109 |

## Утверждение

* **Статус:** Утверждено (rev.4)
* **Дата:** 2026-06-28
* **Workflow Jira:** `In Progress` → **`To Test`** (unit T1–T14) → `Done`; prod ops вне задачи
* **Следующий шаг:** перевести FIN-109 в **To Test** после добавления статуса в workflow FIN; Done после зелёных unit-тестов + merge

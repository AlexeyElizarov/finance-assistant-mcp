# MCP tools `create_plan_item` + bounded `update_plan_item` — разовые и ограниченные REG plan-lines

**Связь:** [FIN-110](https://alexeielizarov.atlassian.net/browse/FIN-110); родитель [FIN-96](https://alexeielizarov.atlassian.net/browse/FIN-96); **Relates** [FIN-108](https://alexeielizarov.atlassian.net/browse/FIN-108), [FIN-109](https://alexeielizarov.atlassian.net/browse/FIN-109), [FIN-103](https://alexeielizarov.atlassian.net/browse/FIN-103).

**Домен:** ops-пример — BahnCard 25 (62.90 € в 2026-06); [household-base-share.md](../../../assistant/35-finance-assistant/working/2026-07/household-base-share.md).

**Статус:** Утверждено (2026-06-28, rev.2).

## Назначение

Годовые разовые взносы, например BahnCard 25 — **62.90 € в месяц оплаты/продления**, требуют REG plan-item с **ограниченным** горизонтом (`start_period` = `end_period` = месяц взноса). Это **не** ежемесячный платёж: сумма начисляется один раз в год, в календарном месяце оплаты.

`create_budget_item` (FIN-109) создаёт новую статью; `update_plan_item` (FIN-108) менял только `amount`.

**Критерий приёмки:** ops задаёт bounded plan-line на **существующей** статье и исправляет неверную amortization только через MCP; `household_base_share` за 2026-07 отражает professional **335.00 €** (без BahnCard в июле).

## Объём и границы

### Входит в объём

* MCP tool **`create_plan_item`**: POST REG plan-item на **существующую** статью (`article` или `budget_item_id`) + optional recalculate.
* Расширение **`update_plan_item`**: optional `start_period`, `end_period` в PUT (исправление существующей записи, напр. BahnCard 5.24 → 62.90 в 2026-06).
* Периоды → даты по FIN-109 **D-05**; `planning_type` только `REG` в v1.
* Guards: ARC / `can_mutate`; валидация amount / periods.
* Логика в `monthly_close_lib.py` + handlers в `server.py`.
* Unit-тесты (mock `ApiClient`).
* Строка в `mcp-gaps.md`.

### Не входит в объём

* Backend API — контракты уже есть.
* IRR plan-items — follow-up FIN-109 F-03.
* `delete_plan_item` — follow-up FIN-108 F-02.
* Bulk-создание bounded lines.

## Целевое поведение

### MCP: `create_plan_item`

#### Вход

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из сессии | URL API |
| `article` | string | * | — | Подстрока имени статьи |
| `budget_item_id` | string | * | — | UUID статьи |
| `amount` | string \| number | **да** | — | Сумма REG: **≥ 0** (см. **D-08**) |
| `start_period` | string | **да** | — | `YYYY-MM` — первый активный месяц |
| `end_period` | string | нет | — | `YYYY-MM` — последний активный месяц (см. **D-09**) |
| `planning_type` | string | нет | `REG` | Только `REG` в v1 |
| `currency` | string | нет | `EUR` | Валюта |
| `periodicity` | string | нет | `M` | REG periodicity |
| `recalculate` | bool | нет | `true` | POST recalculate после POST plan-item |

\* **Resolve статьи:** `article` или `budget_item_id` (обязательно одно).

**Ops-рекомендация (one-off annual fee):** для годового разового платежа передавать **`start_period` = `end_period`** (месяц оплаты/продления). Поле `end_period` формально опционально (open-ended REG допустим), но для bounded-case без `end_period` plan-item действует до горизонта версии — это **не** one-off.

#### Алгоритм

1. Сессия → `ApiClient`.
2. Нормализовать `amount`; `planning_type != REG` → error.
3. `parse_period(start_period)`; при `end_period` — parse + `assert_period_range`.
4. `resolve_act_version_id` → `assert_version_mutable`.
5. `resolve_budget_item_id_for_plan(article, budget_item_id)`.
6. `POST /api/v1/budget/plan-items` (тело через `build_reg_plan_item_body`).
7. **Только после успешного POST** (`201`): при `recalculate=true` → `POST /budget/projections/recalculate`.
8. JSON-ответ (см. ниже).

**Recalculate fail (**D-07b**):** plan-item **уже создан** в шаге 6; compensating DELETE **не** выполняется. Tool error; payload **обязан** включать create context — `plan_item_id`, `budget_item_id`, `budget_version_id`, `amount`, `plan_item` — чтобы ops повторил recalculate без повторного POST.

#### Выход

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `ok` | bool | `true` |
| `plan_item_id` | string | UUID новой записи |
| `budget_item_id` | string | UUID статьи |
| `budget_version_id` | string | ACT version |
| `article` | string | Имя статьи |
| `amount` | string | Нормализованная сумма |
| `start_period` | string | `YYYY-MM` |
| `end_period` | string \| omitted | Если задан |
| `plan_item` | object | Ответ POST |
| `recalculate` | object \| omitted | Если вызывался |

### Расширение MCP: `update_plan_item`

Добавить optional аргументы:

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `start_period` | string | нет | `YYYY-MM` → `start_date` = первый день месяца |
| `end_period` | string | нет | `YYYY-MM` → `end_date` = последний день месяца |

#### Алгоритм (дополнение к FIN-108)

1. **Resolve + GET** (как FIN-108): `resolve_plan_item_for_update` → полное тело записи (`GET /budget/plan-items/{id}` при `plan_item_id`, иначе строка из `projection-period-page`). **PUT всегда после GET** — existing `start_date` нужен для валидации.
2. Если заданы `start_period` и `end_period` — `assert_period_range`.
3. Если только `end_period` — month из existing `start_date` (prefix `YYYY-MM`) vs `end_period`; `end < start` → tool error **до PUT**.
4. `plan_item_put_body` с optional date overrides + `amount`; `PUT /budget/plan-items/{id}`.
5. Recalculate — как FIN-108.

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Имя нового tool | `create_plan_item` (FIN-110) |
| D-02 | Bounded fix существующей | Расширить `update_plan_item` optional `start_period`/`end_period` |
| D-03 | `planning_type` | Только `REG` в v1 |
| D-04 | Period → date | FIN-109 D-05 |
| D-05 | Default recalculate | `true` |
| D-06 | Multiple plan-items | Backend допускает несколько plan-items на статью; renewal — новый `create_plan_item` |
| D-07 | POST plan-items fail | Plan-item не создан; recalculate не вызывается; rollback не требуется |
| D-07b | Recalculate fail после POST | Plan-item **сохранён**; error payload с `plan_item_id`, `budget_item_id`, `budget_version_id`, `amount`, `plan_item`; ops повторяет recalculate |
| D-08 | `amount == 0` | Допустим (backend принимает; как FIN-108/109). Не ошибка ввода; ops-смысл — обнулить план |
| D-09 | One-off annual fee | Ops **рекомендуется** `start_period = end_period`; `end_period` опционален в API tool, но без него — open-ended REG |
| D-10 | `update_plan_item` only `end_period` | GET existing plan-item (FIN-108 resolve path) → сравнить `end_period` с month(`start_date`); error до PUT если end < start |

## Чеклист тестов

### `create_plan_item` (`test_create_plan_item.py`)

* **T1:** happy path bounded (start=end=2026-06) → POST + recalculate.
* **T2:** open-ended (no end_period) → POST с `end_date=null`.
* **T3:** `end_period` < `start_period` → error до HTTP.
* **T4:** ARC → error до POST.
* **T5:** `planning_type=IRR` → error.
* **T6:** `recalculate=false` → no POST recalculate.
* **T7:** recalculate fail после успешного POST → tool error; payload содержит **`plan_item_id`**, `budget_item_id`, `budget_version_id`, `plan_item` (plan-item уже создан, rollback нет).
* **T8:** `amount=0` → POST с `"0.00"`; успех.

### `update_plan_item` (дополнение `test_update_plan_item.py`)

* **T18:** `start_period` + `end_period` + amount → GET + PUT с новыми датами.
* **T19:** only `end_period` → GET existing → validate vs `start_date` → PUT.
* **T20:** `end_period` < existing start (from GET) → error до PUT.

## Приёмочная проверка (prod)

Доказывает снятие неверной amortization (5.24 €/мес → one-off 62.90 € только в июне):

1. `update_plan_item({ plan_item_id: b8636ce7-..., amount: "62.90", start_period: "2026-06", end_period: "2026-06" })`
2. `query_plan_fact({ period: "2026-06", article: "BahnCard" })` → plan ≈ 62.90
3. `query_plan_fact({ period: "2026-07", article: "BahnCard" })` → plan ≈ 0 или отсутствует
4. `household_base_share({ period: "2026-07" })` → professional **335.00**, base share **1404.65** per partner

## Утверждение

* **Статус:** Утверждено (rev.2)
* **Дата:** 2026-06-28
* **Ревью PO:** one-off wording, D-08/D-09, recalculate-fail context, GET-before-PUT для `end_period`-only

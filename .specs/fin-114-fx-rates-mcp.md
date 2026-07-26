# MCP tools `list_fx_rates` / `upsert_fx_rate` и конвертация в `household_base_share`

**Связь:** [FIN-114](https://alexeielizarov.atlassian.net/browse/FIN-114); родитель [FIN-112](https://alexeielizarov.atlassian.net/browse/FIN-112); **Blocks** [FIN-113](https://alexeielizarov.atlassian.net/browse/FIN-113) (Done); **Relates** [FIN-103](https://alexeielizarov.atlassian.net/browse/FIN-103), [FIN-121](https://alexeielizarov.atlassian.net/browse/FIN-121), [FIN-124](https://alexeielizarov.atlassian.net/browse/FIN-124) (OpenUI5 — вне scope).

**Домен:** [household-budget-model.md](../../../assistant/35-finance-assistant/methodology/budgeting/household-budget-model.md) (доход Николая 20 000 ₽/мес в € по курсу); backend API — [fin-113-fx-rates-api.md](../../../PycharmProjects/FinancePlanningProject/.specs/fin-113-fx-rates-api.md).

**Статус:** Утверждено (2026-07-11, rev.5)

## Назначение

Домашний бюджет смешивает EUR и RUB. MCP `create_budget_item` / `update_plan_item` принимают `currency`, но ops передают EUR с ручным пересчётом (20 000 ₽ ÷ 89,80 → 222,72 €). `household_base_share` читает `plan_amount` через `GET /budget/plan-actual?view=grouped` **без** слоя FX — курсы зашиты в суммы или в working-sheet.

Backend [FIN-113](https://alexeielizarov.atlassian.net/browse/FIN-113) (**Done**) хранит плановые курсы RUB→EUR и конвертирует flat plan-actual при `convert_to_eur=true`. MCP-слой отсутствует: ops не могут задать курс и пересчитать базовую долю одним mcp-only сценарием.

**Критерий приёмки:** ops задаёт курс на `YYYY-MM` через `upsert_fx_rate`, проверяет через `list_fx_rates`; `household_base_share({ period: "2026-07" })` возвращает EUR totals по контурам с учётом RUB plan-lines по API-курсу (20 000 ₽ @ 89,8 → 222,72 € в строке «Взнос Николая»); при отсутствии курса — tool error `fx_rate_missing` с `missing_rates[]` из API.

## Объём и границы

### Входит в объём

* Новый модуль `scripts/fx_rates.py` — `list_fx_rates()`, `upsert_fx_rate()`.
* MCP tools **`list_fx_rates`** и **`upsert_fx_rate`** в `server.py` + schema.
* Расширение `fetch_period_plans()` / `compute_from_mapping()` / `compute_household_base_share()` в `scripts/household_base_share.py`: EUR-эквиваленты plan-lines через `GET /budget/plan-actual?convert_to_eur=true` (flat).
* Опциональный параметр MCP `household_base_share`: `convert_plans_to_eur` (default **`true`**, D-12).
* Поле `amount_detail` на строках контуров при `native_currency ≠ EUR` (см. D-02).
* Проброс ошибок API `fx_rate_missing` / `fx_currency_not_supported` / `fx_pair_not_supported` / `validation_error` как tool error.
* Unit-тесты (mock `ApiClient`): FX tools + household conversion + backward compat + missing rate.
* Обновление `mcp-gaps.md`; краткое уточнение description MCP `create_budget_item` (RUB nominal + workflow с `upsert_fx_rate`).

### Не входит в объём

* Backend API — [FIN-113](https://alexeielizarov.atlassian.net/browse/FIN-113) (**Done**).
* OpenUI5 экран курсов — [FIN-124](https://alexeielizarov.atlassian.net/browse/FIN-124).
* Автозагрузка курсов — [FIN-123](https://alexeielizarov.atlassian.net/browse/FIN-123).
* Валидация наличия курса при `create_budget_item` / `update_plan_item` (FIN-113: курс не обязателен на write).
* Миграция prod plan-items с EUR-эквивалентов на RUB-номинал (ops после релиза).
* `convert_to_eur` для `query_plan_fact` (отдельный follow-up при необходимости).
* Backend household read API [FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102) — FX в MCP path `source: "api"` вне scope v1.
* Пары валют кроме RUB→EUR; DELETE курсов.

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| `GET/PUT /api/v1/fx-rates` | CRUD плановых курсов (FIN-113) | Нет MCP-обёртки |
| `household_base_share` | `fetch_period_plans` → grouped plan-actual, `plan_amount` as-is | RUB строки либо EUR-ручной пересчёт, либо неверный total |
| `create_budget_item` | `currency` default `EUR` | Ops не знают mcp-only workflow для RUB |
| `mcp-gaps.md` | `list_fx_rates` / `upsert_fx_rate` в gaps | Tool не реализован |
| FIN-121 | `plan` в EUR (ожидание) | FX отложен на FIN-114 |

## Обратная совместимость

* Новые tools `list_fx_rates` / `upsert_fx_rate` — additive.
* `household_base_share` **без** `convert_plans_to_eur` или с `convert_plans_to_eur=false` — **идентично** FIN-103 / FIN-121 (grouped fetch, native `plan_amount`).
* `household_base_share` с `convert_plans_to_eur=true` (default **D-12**) на каталоге **только EUR** — totals совпадают с legacy grouped fetch (regression T8; единый pipeline — **D-07**).
* Существующие поля ответа (`plan`, `total`, `free_remainder`, `base_share`, FIN-121 `income_resolution`) **не переименовываются**; `plan` остаётся EUR при включённой конвертации.

## Целевое поведение

### Семантика курса (наследование FIN-113 D-01)

| Поле | Значение |
| ---- | -------- |
| `from_currency` | `RUB` |
| `to_currency` | `EUR` |
| `rate` | RUB за 1 EUR (напр. `"89.8"` → 1 € = 89,8 ₽) |

```
amount_eur = round_half_up(amount_rub / rate, 2)
```

### MCP: `list_fx_rates`

Тонкая обёртка над `GET /api/v1/fx-rates`.

#### Вход

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из сессии | URL API |
| `period` | string | нет | — | Один месяц `YYYY-MM` или `YYYY-MM-DD` |
| `period_from` | string | нет | — | Начало диапазона (пара с `period_to`) |
| `period_to` | string | нет | — | Конец диапазона |
| `from_currency` | string | нет | `RUB` | ISO 4217 from |
| `to_currency` | string | нет | `EUR` | ISO 4217 to |

Правила комбинации `period` / `period_from` / `period_to` — **как FIN-113** (конфликт → API 422 → tool error).

#### Выход

```json
{
  "ok": true,
  "profile": "prod",
  "base": "http://127.0.0.1:8000",
  "fx_rates": [
    {
      "period": "2026-07-01",
      "from_currency": "RUB",
      "to_currency": "EUR",
      "rate": "89.8",
      "updated_at": "2026-07-03T10:15:00"
    }
  ]
}
```

Пустой список → `ok: true`, `fx_rates: []` (не ошибка).

#### Алгоритм

```
1. ApiClient из сессии
2. Собрать query из аргументов (omit null/empty)
3. GET /api/v1/fx-rates?...
4. status 200 → wrap { ok, profile, base, fx_rates }
5. status 422/4xx → tool error с телом API
```

### MCP: `upsert_fx_rate`

Тонкая обёртка над `PUT /api/v1/fx-rates`.

#### Вход

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из сессии | URL API |
| `period` | string | **да** | — | `YYYY-MM` или `YYYY-MM-DD` |
| `rate` | string | **да** | — | Плановый курс (> 0, fixed-point, см. FIN-113 D-14) |
| `from_currency` | string | нет | `RUB` | from |
| `to_currency` | string | нет | `EUR` | to |

#### Выход

```json
{
  "ok": true,
  "profile": "prod",
  "base": "http://127.0.0.1:8000",
  "fx_rate": {
    "period": "2026-07-01",
    "from_currency": "RUB",
    "to_currency": "EUR",
    "rate": "89.8",
    "updated_at": "2026-07-11T05:00:00"
  }
}
```

#### Алгоритм

```
1. validate period format (normalize YYYY-MM internally for error messages)
2. validate rate non-empty string
3. PUT /api/v1/fx-rates body { period, rate, from_currency?, to_currency? }
4. status 200 → wrap single fx_rate
5. 422 fx_pair_not_supported / validation_error → tool error
```

### Расширение `household_base_share`

#### Новый параметр

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `convert_plans_to_eur` | bool | нет | `true` (D-12) | При `true` — единый pipeline plan-actual (**D-07**); при `false` — legacy grouped fetch (FIN-103) |

Существующие параметры FIN-103 / FIN-121 без изменений.

#### Pipeline загрузки plan amounts

```
if convert_plans_to_eur:
    # D-07: единый pipeline — всегда flat + convert_to_eur=true,
    # даже если в месяце нет RUB (backend вернёт plan_amount_eur == plan_amount)
    rows = GET /budget/plan-actual
           ?budget_version_id=…
           &period=YYYY-MM-01
           &convert_to_eur=true
    fx_rate_row = GET /fx-rates?period=YYYY-MM-01   # once per call (D-05); ok if []
    for each row in plan_actual_month_rows:
        plans[item_id] = parse(plan_amount_eur)
        if row.currency != "EUR":
            plan_details[item_id] = {
              native_amount: parse(plan_amount),
              native_currency: row.currency,
              fx_rate: fx_rate_row.rate,          # из API, не derive (D-05)
              fx_period: row.period
            }
else:
    plans = legacy fetch_period_plans (view=grouped)   # FIN-103 unchanged
    plan_details = {}
```

**Запрещено (D-07):** ветвление «если RUB нет → grouped, иначе → flat» — один алгоритм при `convert_plans_to_eur=true`.

**Курс для `amount_detail` (D-05):** только `GET /fx-rates?period=…`; **не** вычислять `native_amount / plan_eur` (накопление ошибок округления).

При `convert_plans_to_eur=true` и HTTP 422 `fx_rate_missing` от plan-actual → tool error; пробросить `error.details.missing_rates` в текст/структуру ошибки MCP (D-06).

#### Строки контуров (расширение FIN-103)

Базовая структура line **без изменений**:

```json
{
  "article_match": "Взнос Николая",
  "budget_item_id": "…",
  "article": "Взнос Николая (20 000 ₽)",
  "plan": 222.72
}
```

При `convert_plans_to_eur=true` и `native_currency != "EUR"` добавляется **`amount_detail`** (optional block):

```json
"amount_detail": {
  "native_amount": 20000.0,
  "native_currency": "RUB",
  "fx_rate": "89.8",
  "fx_period": "2026-07-01"
}
```

При pure EUR catalog поле `amount_detail` **отсутствует** (не `{ native_currency: "EUR" }`).

`plan` и contour `total` — **всегда EUR** (2 dp) при `convert_plans_to_eur=true`.

#### Путь `source: "api"` (FIN-102 probe)

v1: **без изменений**. Если probe вернул 200, payload нормализуется как сегодня; post-filter FIN-121 применяется к EUR `plan` из API body. Конвертация RUB→EUR для `source: "api"` — follow-up [FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102), не FIN-114.

#### Инварианты (после pipeline)

1. При `convert_plans_to_eur=true` все `plan` и `total` в расчётных контурах — EUR, 2 dp.
2. Формула `free_remainder` / `base_share` **не меняется** (FIN-103); меняются только входные plan amounts.
3. FIN-121 income resolution работает на EUR `plan` после конвертации (post-filter до/после — как сейчас: filter использует `plans` dict уже в EUR).
4. `partner_count == len(partners)`; contour uniqueness — без изменений FIN-103.
5. `amount_detail` присутствует **только** если `native_currency != "EUR"` и `convert_plans_to_eur=true`.

### Ошибки и проброс API

| Ситуация | Поведение MCP |
| -------- | ------------- |
| `fx_rate_missing` (plan-actual или fx-rates) | Tool error; включить `missing_rates` из `error.details` |
| `fx_currency_not_supported` | Tool error |
| `fx_pair_not_supported` | Tool error |
| `validation_error` (period conflict, rate ≤ 0) | Tool error с message API |
| GET fx-rates 200, `[]` | `ok: true`, не ошибка |
| Невалидный `period` в MCP args | Tool error до HTTP |

**Missing rate — без fallback** (FIN-113 D-05): silent use of legacy EUR amount **запрещён** при `convert_plans_to_eur=true`.

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-12 | Default `convert_plans_to_eur` | **`true`**; `false` = legacy grouped fetch (FIN-103/121 regression path) |
| D-02 | `amount_detail` | Только при `native_currency != "EUR"` и `convert_plans_to_eur=true`; на EUR-only строках поле **отсутствует** |
| D-03 | Имена MCP tools | `list_fx_rates`, `upsert_fx_rate` (как в Jira Done when) |
| D-04 | Модуль FX | `scripts/fx_rates.py` (отдельно от `monthly_close_lib.py`) |
| D-05 | Lookup rate для display | Один `GET /fx-rates?period=` на вызов `household_base_share` |
| D-06 | Missing rate | Tool error; проброс `details.missing_rates`; **без** fallback |
| D-07 | Plan-actual path при FX | При `convert_plans_to_eur=true` — единый pipeline: всегда `GET /budget/plan-actual?convert_to_eur=true` (flat), независимо от состава валют; grouped **не** используется; без ветвления по наличию RUB |
| D-08 | Семантика rate | Как FIN-113: RUB per 1 EUR |
| D-09 | `create_budget_item` | Только doc/schema hint; **без** validate rate on create |
| D-10 | `source: "api"` | Вне scope v1 |

## Non-goals / guardrails

* Не дублировать формулу конвертации в MCP — **источник правды** backend `convert_to_eur` через plan-actual API.
* Не менять prod plan-items автоматически.
* Smoke приёмки — **`test`** / **`cand`**, не **`prod`** без явной ops-команды.
* Не снимать label `mcp-gap` с FIN-114 до Done.

## Чеклист тестов

### FX tools

* **T1:** `upsert_fx_rate({ period: "2026-07", rate: "89.80" })` → mock PUT 200, canonical `rate: "89.8"`.
* **T2:** `list_fx_rates({ period: "2026-07" })` → mock GET, wrap `fx_rates[]`.
* **T3:** `list_fx_rates()` без period → all rates.
* **T4:** `list_fx_rates({ period: "2026-07", period_from: "2026-01" })` → mock 422 → tool error.
* **T5:** `upsert_fx_rate({ period: "2026-07", rate: "0" })` → mock 422 validation_error.
* **T6:** `upsert_fx_rate` pair USD/EUR → mock 422 `fx_pair_not_supported`.

### household_base_share FX

* **T7:** `convert_plans_to_eur=true`, fixture row 20 000 RUB @ 89.8 → line `plan=222.72`, `amount_detail` present.
* **T8:** `convert_plans_to_eur=true`, all EUR rows → totals match legacy grouped fetch.
* **T9:** `convert_plans_to_eur=false` → identical to pre-FIN-114 grouped behavior (regression FIN-103/121).
* **T10:** missing rate → mock plan-actual 422 `fx_rate_missing` with 2 periods → tool error mentions both.
* **T11:** FIN-121 `income_mode=salary_only` + FX → filtered income uses EUR converted plans.
* **T12:** contour `total` = sum of line `plan` after conversion.

### Backward compat

* **T13:** Existing `test_household_base_share` cases pass with `convert_plans_to_eur=false` or EUR-only fixtures.
* **T14:** MCP schema default для `convert_plans_to_eur` = **`true`** (D-12).
* **T15:** EUR-only catalog + `convert_plans_to_eur=true` → flat pipeline, totals = legacy grouped (D-07).

## Приёмочная проверка

### Предусловия

* MCP `finance_api_connect` → `data_profile` = **`cand`** или **`test`**
* Backend с FIN-113 deployed
* Fixture: plan-item «Nikolay contribution» `currency=RUB`, amount 20000, period 2026-07

### A1 — upsert + list

**Действие:**

1. `upsert_fx_rate({ "period": "2026-07", "rate": "89.80" })`
2. `list_fx_rates({ "period": "2026-07" })`

**Ожидаемый результат:** шаг 1 → `fx_rate.rate == "89.8"`; шаг 2 → одна запись с тем же rate.

### A2 — household_base_share with RUB line

**Действие:** `household_base_share({ "period": "2026-07", "profile": "cand" })`

**Ожидаемый результат:** в `household_income.lines` строка Nikolay `plan ≈ 222.72`, `amount_detail.native_amount == 20000`, `amount_detail.fx_rate == "89.8"`; `free_remainder` / `base_share` согласованы с working-sheet.

### A3 — missing rate (negative)

**Действие:** удалить/не задавать курс для 2026-08; fixture RUB line на 2026-08; `household_base_share({ "period": "2026-08" })`.

**Ожидаемый результат:** tool error `fx_rate_missing`.

### A4 — legacy path

**Действие:** `household_base_share({ "period": "2026-07", "convert_plans_to_eur": false })`

**Ожидаемый результат:** совпадает с pre-FIN-114 behavior (native plan amounts).

**Автоматизация:** `tests/test_fx_rates.py`, расширение `tests/test_household_base_share.py`.

## Связь с другими FIN

| FIN | Связь |
| --- | ----- |
| [FIN-113](https://alexeielizarov.atlassian.net/browse/FIN-113) | **Blocks** (Done) — REST API |
| [FIN-103](https://alexeielizarov.atlassian.net/browse/FIN-103) | Базовый `household_base_share` |
| [FIN-121](https://alexeielizarov.atlassian.net/browse/FIN-121) | Income filter на EUR plans |
| [FIN-124](https://alexeielizarov.atlassian.net/browse/FIN-124) | UI maintenance — не дублировать |
| [FIN-123](https://alexeielizarov.atlassian.net/browse/FIN-123) | Auto-fetch — не в v1 |

## Follow-ups

| ID | Тема | Jira | Когда |
| -- | ---- | ---- | ----- |
| F-01 | После завершения миграции prod RUB plan-items (FIN-113 F-04) — удалить `convert_plans_to_eur` из MCP; FX — единственный режим | [FIN-158](https://alexeielizarov.atlassian.net/browse/FIN-158) (**Blocks:** [FIN-157](https://alexeielizarov.atlassian.net/browse/FIN-157)) | **вне scope FIN-114** |
| F-02 | Ops: prod RUB plan-items → номинал в ₽ (FIN-113 F-04) | [FIN-157](https://alexeielizarov.atlassian.net/browse/FIN-157) | ops после релиза FIN-113/114 |
| F-03 | `query_plan_fact` + FX conversion | [FIN-159](https://alexeielizarov.atlassian.net/browse/FIN-159) | при необходимости ops |

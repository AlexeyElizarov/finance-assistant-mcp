# MCP `query_transactions` — фильтры учётного периода и категории

**Связь:** [FIN-27](https://alexeielizarov.atlassian.net/browse/FIN-27); родитель [FIN-3](https://alexeielizarov.atlassian.net/browse/FIN-3); **Relates** [FIN-17](https://alexeielizarov.atlassian.net/browse/FIN-17) (dedicated `list_c9999`), [FIN-26](https://alexeielizarov.atlassian.net/browse/FIN-26), [FIN-101](https://alexeielizarov.atlassian.net/browse/FIN-101), [FIN-107](https://alexeielizarov.atlassian.net/browse/FIN-107) (lookup перед overrides).

**Домен:** ad-hoc выборки и расследования C9999/OTTO — [monthly-close-api/index.md](../../../assistant/35-finance-assistant/ops/index.md) § «Ad-hoc выборки»; mcp-only — [mcp-only.md](../../../assistant/35-finance-assistant/ops/mcp-only.md).

**Статус:** Утверждено (2026-07-09, rev.3)

## Назначение

При расследовании C9999, OTTO и reconciliation ops нужны выборки транзакций **по учётному месяцу** и **категории операции** без ad-hoc Python к `GET /api/v1/transactions`. Сегодня MCP `query_transactions` принимает только `date_from`/`date_to`, `indicator`, `provider`, `description`/`contains`; фильтр `category` есть, но **нет** `period` / `accounting_period`, поэтому месячные срезы делают обходом date-range или скриптами.

**Критерий приёмки:** типовый запрос «все расходы C9999 за 2026-02» (`period: "2026-02"`) или «OTTO за месяц» выполняется одним вызовом `query_transactions` через MCP; label `mcp-gap` снят при Done.

## Объём и границы

### Входит в объём

* Расширение `build_query_path()` / `fetch_rows()` в `scripts/query-transactions.py`: параметры учётного периода + **`normalize_query_args()`** (CLI и MCP).
* Handler `_handle_query_transactions` в `server.py` + schema tool (описание новых полей).
* CLI `query-transactions.py`: флаги `--period` / `--accounting-period` (parity с MCP).
* Unit-тесты (mock `ApiClient`): period-only, period+category, alias `transaction_category`, invalid period, backward compat `category`.
* Обновление примера в [monthly-close-api/index.md](../../../assistant/35-finance-assistant/ops/index.md) § «Ad-hoc выборки».
* Снятие label `mcp-gap` с FIN-27 при Done.

### Не входит в объём

* Dedicated tool `list_c9999` — [FIN-17](https://alexeielizarov.atlassian.net/browse/FIN-17) (Done); FIN-27 — общий drill-down, не дублирует C9999 proposal flow.
* Диапазон `accounting_period_from` / `accounting_period_to` — **GET** `/api/v1/transactions` поддерживает только exact `accounting_period` (range есть только в delete-by-filter).
* Backend изменения `FinancePlanningProject`.
* Поле `transaction_key` / `id` в ответе — см. **D-05**; override flow по-прежнему через plan-fact drill-down или follow-up.
* Фильтр `transaction_type` — см. **D-06**.
* Новые фильтры REST, отсутствующие в list API (`project`, `budget_period`) — отдельные задачи при необходимости.
* Рефакторинг `group_by month` / `split_internet`.
* Эхо применённого `accounting_period` в корне ответа tool — см. **D-09**.

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| MCP schema `query_transactions` | `date_*`, `indicator`, `category`, `provider`, `description`, `contains`, `group_by` | Нет `period` / `accounting_period` |
| `build_query_path()` | Передаёт `transaction_category` при `args.category` | Нет period в query string |
| `c9999_rows()` | Прямой `GET …?period=YYYYMM&transaction_category=C9999&transaction_type=C` | Только internal helper, не MCP |
| REST `GET /api/v1/transactions` | Query `accounting_period` (alias deprecated `period`), `transaction_category` | Контракт готов |
| Ops / агенты | Ad-hoc scripts / date-range workaround | Нарушение mcp-only |

## Обратная совместимость

Все существующие параметры `query_transactions` (`date_from`, `date_to`, `indicator`, `category`, `provider`, `description`, `contains`, `group_by`, `split_internet`) **сохраняют прежнюю семантику**. Добавление `period`, `accounting_period` и alias `transaction_category` **не меняет** поведение запросов, которые эти поля не используют.

Подтверждение: unit-тест T07 + прогон существующего test suite без регрессий.

## Целевое поведение

### MCP: `query_transactions` (расширение)

#### Термины

После `normalize_query_args()` каждый фильтр находится в одном из двух состояний:

* **active** — значение участвует в формировании query и считается активным фильтром;
* **unset** — отсутствие значения (`None` / не передан аргумент / пустая строка после trim / массив `contains` без ни одного непустого элемента после trim); **не участвует** в query и **не считается** активным фильтром.

Как сегодня: **≥1 active фильтр** обязателен. `period` alone — валидный запрос.

#### `normalize_query_args()` (общее правило)

Единая функция в `query-transactions.py` (используется CLI **и** MCP handler). Порядок pipeline: **normalize_query_args → validation → parse → HTTP mapping**.

Строковые фильтры (`date_*`, `period`, `accounting_period`, `category`, `transaction_category`, `provider`, `description`):

1. **`strip()`** ведущих/концевых пробелов (**D-07**).
2. Пустая строка после trim → **unset**.
3. Значение передаётся в API **без изменения регистра** (**D-08**).

Массив **`contains`**:

1. Каждый элемент — `strip()`.
2. Элементы, ставшие пустыми после trim, **отбрасываются**.
3. Если после отбрасывания массив пуст → **unset** (не active, не `ValueError`).
4. Пример: `contains=["", " OTTO ", ""]` → active `["OTTO"]`; `contains=["", ""]` → unset.

Примеры:

| Вход | После `normalize_query_args()` | Состояние |
| ---- | ------------------------------ | --------- |
| `" 2026-02 "` | `"2026-02"` | active |
| `""` | unset | unset |
| `"   "` | unset | unset |
| `" C9999 "` | `"C9999"` | active |
| `"c9999"` | `"c9999"` | active |

#### Вход — новые поля

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `period` | string | нет* | — | Учётный месяц; **только `YYYY-MM`** (человекочитаемый MCP-интерфейс, как в `list_c9999` / `process_month`) |
| `accounting_period` | string | нет* | — | REST-aligned alias; **`YYYY-MM` или `YYYYMM`**; mutually exclusive с `period` |
| `transaction_category` | string | нет* | — | Синоним `category` (см. алгоритм ниже) |

#### Вход — существующее поле (без изменения семантики)

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `category` | string | нет* | — | → API `transaction_category` (напр. `C9999`) |

\* См. правило «≥1 active фильтр» выше.

#### Период (алгоритм)

> К моменту выполнения алгоритма аргументы уже прошли `normalize_query_args()`; `p` / `ap` ниже — active-значение или unset.

`parse_period()` из `monthly_close_lib` → **`Period`** (`year`, `month`); для query string — **`Period.ymmm`** (`"202602"`).

```
1. p  ← normalized period       (active или unset)
   ap ← normalized accounting_period

2. Если p и ap оба active → ValueError("period and accounting_period are mutually exclusive")

3. raw ← p или ap (какой active)
   Если raw unset → period-фильтр не active; к HTTP mapping не переходим

4. Если источник — p (поле period):
     raw должен соответствовать YYYY-MM (дефис обязателен)
     иначе → ValueError("period must be YYYY-MM")

5. parsed ← parse_period(raw)   # Period; ValueError если месяц/формат невалидны

6. API query param: accounting_period={parsed.ymmm}
```

**Не** передавать deprecated REST alias `period=` — только canonical `accounting_period=YYYYMM`.

#### Категория (алгоритм)

> Аргументы уже прошли `normalize_query_args()`.

```
1. c  ← normalized category            (active или unset)
   tc ← normalized transaction_category

2. Если c и tc оба active и c ≠ tc → ValueError("category and transaction_category conflict")

3. cat ← c или tc (какой active)
   Если cat unset → category-фильтр не active

4. API query param: transaction_category={cat}
```

Категории в учёте — id вида `C9999`; backend выполняет сравнение **без дополнительной нормализации** (регистр, пробелы, Unicode не приводятся). `c9999` ≠ `C9999`.

#### HTTP mapping

| MCP arg | REST query param | Примечание |
| ------- | ---------------- | ---------- |
| `period` / `accounting_period` | `accounting_period` | `Period.ymmm` после parse |
| `category` / `transaction_category` | `transaction_category` | as-is после trim |
| остальные | без изменений | см. текущий tool |

#### Выход

Без изменений структуры v1:

* `group_by != "month"` → `{ base, profile, row_count, rows[] }`
* `group_by == "month"` → `{ base, profile, groups[] }`

Поля строки: `date`, `amount`, `indicator`, `category`, `provider`, `description` (как сегодня). Применённый период **не** эхоится в корне ответа (**D-09**).

#### Ошибки API

* `meta.filter_error` из ответа → `RuntimeError` (как сегодня).
* Пустой результат при валидном фильтре → `row_count: 0`, не ошибка.

### CLI parity

CLI и MCP вызывают **`normalize_query_args()`** и **`build_query_path()`** — поведение идентично.

```text
query-transactions.py --period 2026-02 --category C9999 --indicator D
query-transactions.py --accounting-period 202602 --transaction-category C9999
```

`--period` и `--accounting-period` mutually exclusive (та же логика, что MCP).

### Типовые сценарии (после реализации)

**C9999 drill-down (альтернатива `list_c9999` для ad-hoc):**

```json
query_transactions({
  "profile": "prod",
  "period": "2026-02",
  "category": "C9999",
  "indicator": "D"
})
```

**OTTO за месяц:**

```json
query_transactions({
  "profile": "prod",
  "period": "2026-06",
  "contains": ["OTTO"]
})
```

**REST-aligned (`YYYYMM`):**

```json
query_transactions({
  "profile": "prod",
  "accounting_period": "202602",
  "category": "C9999"
})
```

**REST-aligned (`YYYY-MM` через `accounting_period`):**

```json
query_transactions({
  "profile": "prod",
  "accounting_period": "2026-02",
  "category": "C9999"
})
```

## Ключевые решения (индекс)

| ID | Решение |
| -- | ------- |
| **D-01** | `parse_period()` → `Period`; API param = `Period.ymmm` (`YYYYMM`) |
| **D-02** | `period` — **только `YYYY-MM`**; `accounting_period` — `YYYY-MM` или `YYYYMM`; mutually exclusive |
| **D-03** | `transaction_category` ≡ `category`; оба active и различаются → `ValueError` **до** выбора значения |
| **D-04** | Без range `accounting_period_from/to` в v1 (нет в list API) |
| **D-05** | Поле `id` в rows **не** добавлять в v1 (out of scope Jira) |
| **D-06** | `transaction_type` filter **не** в v1 |
| **D-07** | Строковые фильтры: `strip()`; пустое после trim = unset |
| **D-08** | Значения фильтров передаются в API без изменения регистра |
| **D-09** | Эхо `accounting_period` в ответе tool **не** добавлять |

## Тесты (минимум)

| ID | Сценарий |
| -- | -------- |
| T01 | `period=2026-02` only → GET path содержит `accounting_period=202602` |
| T02 | `period` + `category=C9999` → оба query params |
| T03 | `transaction_category` alias без `category` → API param set |
| T04 | `category=C9999` + `transaction_category=FOOD` → ValueError, no HTTP |
| T05 | `period` + `accounting_period` (оба active) → ValueError, no HTTP |
| T06 | Invalid period `2026-13` → ValueError |
| T07 | Backward compat: только `date_from`/`date_to` — без регрессии |
| T08 | Handler MCP: mock rows → JSON `row_count`, поля строк |
| T09 | `meta.filter_error` от API → RuntimeError |
| T10 | CLI `--period` builds same path as MCP handler |
| T11 | `period=202602` (без дефиса) → ValueError `period must be YYYY-MM` |
| T12 | `period=" 2026-02 "` → trim → `accounting_period=202602` |
| T13 | `period=""` → unset; запрос без других active фильтров → ValueError «≥1 фильтр» |
| T14 | `category=" C9999 "` → API `transaction_category=C9999` |
| T15 | `period=""` + `accounting_period=202602` → valid (period unset после trim) |
| T16 | `contains=["", ""]` → unset; `contains` **не считается** active фильтром; без других active фильтров → ValueError «≥1 фильтр» |
| T17 | `accounting_period=202602` (YYYYMM) → valid; path `accounting_period=202602` |
| T18 | `accounting_period=2026-02` (YYYY-MM) → valid; path `accounting_period=202602` |

## Smoke (test profile, после реализации)

1. `query_transactions({ period: "2026-02", category: "C9999", indicator: "D" })` — `row_count` согласуется с `list_c9999` за тот же месяц (если tool доступен).
2. `query_transactions({ period: "2026-06", contains: ["OTTO"] })` — непустой или осознанно пустой результат без ad-hoc script.
3. Без прямого `curl` / ad-hoc Python к API.

## Definition of Done (синхрон с Jira)

- [ ] MCP handler + **schema tool**: новые параметры `period`, `accounting_period`, `transaction_category` с описаниями
- [ ] CLI и MCP используют **`normalize_query_args()`** и **`build_query_path()`** — поведение идентично
- [ ] Unit-тесты T01–T18
- [ ] Обратная совместимость: существующие сценарии `query_transactions` без регрессий (T07 + test suite)
- [ ] Пример в `monthly-close-api/index.md`
- [ ] Label `mcp-gap` снят; FIN-27 → To Test

## Связанные документы

| Документ | Действие после Done |
| -------- | ------------------- |
| [index.md](../../../assistant/35-finance-assistant/ops/index.md) | пример с `period` + `category` |
| [mcp-gaps.md](../mcp-gaps.md) | уточнить описание `query_transactions` (фильтры period/category) |

# MCP tool `list_c9999` — список неразнесённых расходов C9999

**Связь:** [FIN-17](https://alexeielizarov.atlassian.net/browse/FIN-17); родитель [FIN-4](https://alexeielizarov.atlassian.net/browse/FIN-4); **Relates** [FIN-24](https://alexeielizarov.atlassian.net/browse/FIN-24) (backend suggestions), [FIN-27](https://alexeielizarov.atlassian.net/browse/FIN-27) (period/category в `query_transactions`).

**Домен:** шаг предложения по разнесению — [c9999-proposal-policy.md](../../../assistant/35-finance-assistant/ops/c9999-proposal-policy.md); close — [close-policy.md](../../../assistant/35-finance-assistant/ops/close-policy.md).

**Статус:** Утверждено (2026-07-02, rev.4).

## Назначение

При закрытии месяца агент обязан показать оператору таблицу всех расходов в **C9999** до вызова `apply_keywords` ([c9999-proposal-policy.md](../../../assistant/35-finance-assistant/ops/c9999-proposal-policy.md)). Сегодня MCP `verify_month` и `process_month` отдают только счётчик `expense_c9999_count` в `classification_summary`; строки транзакций недоступны без обхода mcp-only (CLI `monthly-close.py` / `fix-month.py`).

**Критерий приёмки:** один вызов `list_c9999` для `YYYY-MM` возвращает все expense C9999 строки месяца в форме, пригодной для чат-таблицы предложения; агент не использует ad-hoc скрипты и не нарушает [mcp-only.md](../../../assistant/35-finance-assistant/ops/mcp-only.md).

**Сверка с verify:** `list_c9999` возвращает только строки и `row_count`. Сопоставление с `expense_c9999_count` из `verify_month` / `process_month` — обязанность вызывающего сценария, не этого tool.

## Объём и границы

### Входит в объём

* Новый MCP tool **`list_c9999`** в `mcp-servers/finance-assistant/`.
* Reuse / расширение `c9999_rows()` в `scripts/monthly_close_lib.py`: нормализация, сортировка, агрегаты.
* Handler в `server.py`, schema tool, unit-тесты (mock `ApiClient`).
* Обновление `mcp-gaps.md` (перенос из «открытых пробелов» в «доступные tools»); снятие label `mcp-gap` в Jira при Done.
* Ссылка в [c9999-proposal-policy.md](../../../assistant/35-finance-assistant/ops/c9999-proposal-policy.md) на `list_c9999` (после реализации).

### Не входит в объём

* Backend [FIN-24](https://alexeielizarov.atlassian.net/browse/FIN-24) — подсказки категорий/проектов из readiness/classification summary.
* Расширение `query_transactions` фильтрами period/category — [FIN-27](https://alexeielizarov.atlassian.net/browse/FIN-27); `list_c9999` — узкий dedicated tool с фиксированной семантикой C9999 proposal.
* Мутации (`apply_keywords`) — отдельные tools.
* **`put_transaction_overrides` flow:** v1 отдаёт только `id` (store row id), не `transaction_key`. `list_c9999` **не предназначен** для прямого override flow. Для overrides нужен отдельный lookup (`query_transactions` после [FIN-27](https://alexeielizarov.atlassian.net/browse/FIN-27) или backend follow-up с `transaction_key` в list API).
* Вложенный `classification_summary` / дополнительный verify-вызов внутри tool.
* Автоматический derive/import — вызывающий код (`process_month`) не меняется в этой задаче, кроме cross-link в описании tool.
* Добавление `transaction_key` в `GET /api/v1/transactions` — backend follow-up (**D-03**).

## Зафиксированные решения

| ID | Решение |
| -- | ------- |
| **D-01** | `suggestions` — всегда пустой массив `[]` в v1; поле в схеме для forward-compat ([FIN-24](https://alexeielizarov.atlassian.net/browse/FIN-24)). |
| **D-02** | `classification_summary` / `expense_c9999_count` **не** включаются в ответ v1. |
| **D-03** | В строках только `id`; `transaction_key` — backend follow-up. Tool не для override flow (см. границы). |
| **D-04** | Сортировка: transaction date ascending, затем `description` (case-insensitive). |
| **D-05** | `posting_date` не включается в v1. |
| **D-06** | Корневое поле `warnings: string[]` — ops-видимые предупреждения (напр. неразбираемая дата); пустой массив при отсутствии проблем. Одна запись на затронутую строку; дедупликация **не** в v1 (`len(warnings) ≤ row_count`). |
| **D-07** | Суммы: парсинг и агрегация через `Decimal`; в JSON — `number` с 2 знаками (консистентно с другими MCP tools; `52.9` vs `52.90` в JSON эквивалентны). |
| **D-08** | **`c9999_rows(api, period)`** — единственный источник сырых строк; внутренний HTTP-контракт helper **не** входит в спецификацию tool. |

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| `c9999_rows()` в `monthly_close_lib.py` | Возвращает `list[dict]` expense C9999 за месяц | Только CLI; без нормализации, сортировки, MCP |
| MCP `verify_month` | Полный `verify` + `classification_summary.expense_c9999_count` | Нет строк |
| MCP `process_month` | Счётчик в log; блок close при C9999 > 0 | Нет строк в ответе tool |
| MCP `period_status_report` | `c9999_count` per period | Агрегат, не drill-down |
| MCP `query_transactions` | Фильтры date/indicator/category/provider/description | Нет `period` / `accounting_period` |
| `GET /api/v1/transactions` list row | `id`, `date_display`, `amount`, … | Нет `transaction_key` |
| `c9999-proposal-policy` | Таблица € \| Описание \| … в чате | Агент не получает данные через MCP |

## Целевое поведение

### MCP: `list_c9999`

#### Вход

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из сессии | URL API |
| `period` | string | **да** | — | Учётный месяц в формате **`YYYY-MM`** |

#### Период

* Вход tool: **`YYYY-MM`** (напр. `2026-02`).
* `parse_period(period)` → `Period`; дальше handler и `c9999_rows()` работают с `Period` (маппинг на API — внутри helper, **D-08**).
* В ответе tool поле `period` — снова **`YYYY-MM`**.

#### Источник строк

Единственный источник сырых строк — **`c9999_rows(api, period) -> list[dict]`** (**D-08**). Handler **не** парсит HTTP-тело напрямую.

Нормализация вынесена в отдельную функцию, напр. `normalize_c9999_rows(raw_rows) -> tuple[list[dict], list[str]]`, вызываемую после `c9999_rows()`.

#### Алгоритм

1. `finance_api_connect` / сессия → `ApiClient`.
2. `parse_period(period)` → `Period`.
3. `raw_rows = c9999_rows(api, period)`.
4. Инициализировать `warnings: list[str] = []`.
5. Для каждой строки `raw_rows`:
   * распарсить `date_display` во **внутренний** sort key `YYYY-MM-DD` (поддержка `DD.MM.YYYY` и `YYYY-MM-DD`; reuse логики `month_key` / парсер даты из `query-transactions.py` где возможно);
   * при неразбираемой дате: sort key = `9999-12-31`, append в `warnings` строку вида `unparseable_date:id={id}:date_display={value}` (**D-06**);
   * `amount`: парсить как `Decimal` (нормализация `,` → `.`), затем `abs()`; API может отдавать знаковое значение — MCP **всегда** возвращает положительное; сериализация в JSON как `number` с **2** знаками (**D-07**);
   * собрать объект ответа (см. ниже); поле `date` — passthrough `date_display` **только для отображения**, не для сортировки.
6. Отсортировать по внутреннему sort key (date asc), затем `description` (case-insensitive).
7. `row_count = len(rows)`; `total_amount_eur` = сумма `Decimal` amount по строкам, округление до 2 знаков.
8. Вернуть JSON с `warnings`.

**Сортировка:** не по строке `date` / `date_display` (`DD.MM.YYYY` лексически неверна) — только по распарсенному ISO sort key (**D-04**).

#### Выход (корень)

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `ok` | bool | `true` при успешном HTTP |
| `profile` | string | data profile |
| `base` | string | API base URL |
| `period` | string | `YYYY-MM` |
| `row_count` | int | Число строк (= `len(rows)`) |
| `total_amount_eur` | number | Сумма `amount` по строкам (Decimal → 2 знака) |
| `warnings` | array of string | Ops-видимые предупреждения; `[]` если нет (**D-06**) |
| `rows` | array | Нормализованные строки |

Поля **`classification_summary`**, **`expense_c9999_count`** — **отсутствуют** (**D-02**).

#### Элемент `rows[]`

| Поле | Тип | Источник API | Описание |
| ---- | --- | ------------ | -------- |
| `id` | string | `id` | Row id в store; **не** `transaction_key` (**D-03**) |
| `date` | string | `date_display` | Только для отображения (passthrough); сортировка — по внутреннему parsed sort key, не по этому полю |
| `amount` | number | `amount` | `abs(Decimal(amount))`, 2 знака в JSON (**D-07**) |
| `description` | string | `description` | Текст из выписки |
| `provider` | string | `provider` | Bank/source provider code |
| `project` | string | `project` | Проект (может быть пустым) |
| `suggestions` | array | — | Всегда `[]` в v1 (**D-01**) |

#### Ошибки

| Условие | Поведение |
| ------- | --------- |
| Невалидный `period` | tool error до HTTP |
| API 4xx/5xx | tool error с status и телом |
| Пустой результат | `ok: true`, `row_count: 0`, `rows: []`, `warnings: []` — не ошибка |
| Неразбираемый `date_display` | sort key = `9999-12-31`; запись в `warnings[]`; строка не отбрасывается (**D-06**) |

#### Пример ответа

```json
{
  "ok": true,
  "profile": "prod",
  "base": "http://127.0.0.1:8000",
  "period": "2026-02",
  "row_count": 2,
  "total_amount_eur": 87.40,
  "warnings": [],
  "rows": [
    {
      "id": "42",
      "date": "05.02.2026",
      "amount": 52.90,
      "description": "REWE MARKT 12345",
      "provider": "sparkasse",
      "project": "",
      "suggestions": []
    }
  ]
}
```

### Связь с c9999-proposal-policy

Агент после `list_c9999` (или когда `process_month` / `verify_month` показали `expense_c9999_count > 0`) строит чат-таблицу:

| € | Описание | Категория | Keyword | Обоснование |

Поля «Категория / Keyword / Обоснование» заполняет агент (и позже — [FIN-24](https://alexeielizarov.atlassian.net/browse/FIN-24)); tool отдаёт только фактические строки C9999.

Типовой flow:

```
process_month({ period, reopen: true })  → expense_c9999_count > 0 в log
list_c9999({ period })                   → таблица для оператора
# опционально: сверить row_count с expense_c9999_count из verify
# после подтверждения
process_month({ period, apply_keywords: "...", skip_import: true })
```

## Тесты

| ID | Сценарий |
| -- | -------- |
| T1 | Mock API: 2 C9999 rows → `row_count=2`, поля нормализованы |
| T2 | Пустой ответ API → `row_count=0`, `ok=true` |
| T3 | `total_amount_eur` — сумма abs(Decimal); отрицательный amount в API → положительный в ответе; без float-артефактов (напр. `0.1 + 0.2`) |
| T4 | Невалидный period → error без HTTP |
| T5 | Handler зарегистрирован в `server.py` (smoke import / list_tools) |
| T6 | Сортировка: строки с `05.02.2026` и `15.01.2026` → январь перед февралём |
| T7 | Каждая строка: `suggestions == []` |
| T8 | Handler вызывает `c9999_rows(api, parsed_period)` с `Period` из входа `YYYY-MM` |
| T9 | **Regression (обязательный):** неразбираемый `date_display` → `warnings` не пустой, строка в `rows`, sort last (observability > fail-fast) |
| T10 | Успешный ответ: `warnings` присутствует (пустой массив) |

## Done when (Jira)

- [x] MCP tool `list_c9999` в `server.py` с schema и handler
- [x] Unit-тесты T1–T10
- [x] `mcp-gaps.md` — tool в «Доступные»; убран из «Открытых пробелов»
- [x] `c9999-proposal-policy.md` — пример вызова `list_c9999`
- [x] Label `mcp-gap` снят с FIN-17 при закрытии

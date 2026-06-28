# Методологический статус периода в MCP status report

**Связь:** [FIN-106](https://alexeielizarov.atlassian.net/browse/FIN-106); родитель [FIN-3](https://alexeielizarov.atlassian.net/browse/FIN-3); спринт HO S1 prep 28–30 Jun; блокирует [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104) (money check).

**Домен:** журнал закрытия и `methodology_status` — [period-close.md](../../../assistant/35-finance-assistant/methodology/period-close.md); двухфазное закрытие в API — [closed-period-mutation-guard.md](../../../PycharmProjects/FinancePlanningProject/.specs/web/apps/closed-period-mutation-guard.md) **D-14**.

**Статус:** Утверждено (2026-06-28, rev.2).

## Назначение

Ops-процедуры двухфазного закрытия месяца различают **reconciliation** (`open` / `draft` / `closed`) и **методологию** (`open` / `preliminary_closed` / `final_closed`). Backend уже отдаёт оба уровня в `GET /api/v1/budget/reconciliation`, но MCP tools `period_status_report` и `list_period_statuses` читают только поле `status` и не показывают фазу закрытия.

Из-за этого monthly review, money check и tail MC после 16-го требуют отдельных вызовов reconciliation по каждому месяцу или ручного знания, был ли close `preliminary` или `final`.

**Критерий приёмки:** один вызов `period_status_report` (и при необходимости `list_period_statuses`) позволяет ops пометить цифры как предварительные или окончательные без дополнительных API-запросов; поведение согласовано с backend `methodology_status_for`.

## Объём и границы

### Входит в объём

* Расширение **`period_status_report`**: поля `methodology_status`, `close_phase` в каждой строке `periods[]`; агрегаты по фазам на уровне отчёта.
* Расширение **`list_period_statuses`**: те же поля в каждой строке `periods[]` (без агрегатов verify).
* Рефакторинг `monthly_close_lib.reconciliation_status` → чтение полного payload reconciliation (без дополнительных HTTP-вызовов).
* Обновление описаний tools в `server.py` и строки в `mcp-gaps.md` после реализации.
* Unit-тесты на маппинг и сборку отчёта (mock API).

### Не входит в объём

* Изменения backend API (`FinancePlanningProject`) — уже реализовано.
* Новый отдельный MCP tool (расширяем существующие).
* UI plan-fact / Fiori.
* [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104) (`money_check_report`) — отдельная задача; эта спека только поставляет данные в status report.
* Bulk reconciliation API «за один запрос на N месяцев».
* Изменение семантики существующих полей `status`, `reconciliation_status`, `closed_count`, `closed_periods`.

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| Backend `GET /budget/reconciliation` | `status`, `methodology_status`, `close_phase` | Данные есть |
| `reconciliation_status()` | Возвращает только `body["status"]` | `methodology_status` отбрасывается |
| `period_status_report` | Строки: `reconciliation_status`, ready, C9999, MC tail | Нет фазы методологии |
| `list_period_statuses` | Строки: `period`, `status` (reconciliation) | Нет фазы методологии |
| Ops / monthly review | Ассистент не видит preliminary vs final | Ручные вызовы или догадки |

## Целевое поведение

### Два измерения состояния

`status` / `reconciliation_status` и `methodology_status` — **ортогональные измерения**. MCP **не** выводит одно из другого и **не** требует совпадения или префиксной связи между ними.

Типичная пара при черновике сверки:

```json
{
  "status": "draft",
  "methodology_status": "open",
  "close_phase": null
}
```

Это норма: `draft` — состояние workflow reconciliation; `open` — методологический снимок «месяц не закрыт». Запрещено в MCP: `assert methodology_status.startswith(status)` и любая иная «синхронизация» полей.

### Доменная логика (backend, справочно)

Источник правды — ответ `GET /api/v1/budget/reconciliation` (логика `methodology_status_for` в backend). MCP **не** вычисляет фазу самостоятельно — только транслирует поля API.

Типичные сочетания на текущем backend:

| `reconciliation.status` | `methodology_status` (API) | `close_phase` (API) |
| ----------------------- | -------------------------- | ------------------- |
| `open`, `draft` | `open` | `null` |
| `closed` | `preliminary_closed` | `preliminary` |
| `closed` | `final_closed` | `final` |

Примечания (ожидаемое поведение backend, не валидируется MCP):

* `reconciliation_status == "closed"` и отсутствие записи в журнале close → backend обычно отдаёт `methodology_status: "final_closed"`, `close_phase: "final"` (legacy close до журнала `close_phase`).
* После штатного **reopen** между фазами: `status: "open"`, `methodology_status: "open"`, `close_phase: null`.
* `draft` на уровне методологии соответствует `open` (как в plan-fact UI).

`close_phase` — фаза **методологического** закрытия (preliminary / final), не фаза reconciliation workflow. Имя поля совпадает с backend; в MCP **не переименовывается**.

Жизненный цикл методологии — [period-close.md](../../../assistant/35-finance-assistant/methodology/period-close.md); reconciliation lifecycle — [closed-period-mutation-guard.md](../../../PycharmProjects/FinancePlanningProject/.specs/web/apps/closed-period-mutation-guard.md).

### MCP: `period_status_report`

#### Вход

Без изменений: `profile`, `base`, `year` / `period_from` / `period_to`, `anchor_period`, `detail`, `skip_empty`.

#### Строка `periods[]`

Во **всех** режимах `detail` (`status_only`, `summary`, `full`) к существующим полям **добавляются**:

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `methodology_status` | string | Значение из API как есть; известные сегодня: `open`, `preliminary_closed`, `final_closed` |
| `close_phase` | string \| null | Значение из API как есть; типично `preliminary`, `final` или `null` |

Поле `reconciliation_status` **сохраняется** без переименования.

MCP **не валидирует** enum `methodology_status` и **не проверяет** согласованность пары `methodology_status` + `close_phase` — оба поля транслируются из backend без изменений. Ответственность за консистентность — backend.

#### Агрегаты корня отчёта

К существующим полям **добавляются**:

| Поле | Тип | Правило |
| ---- | --- | ------- |
| `preliminary_closed_count` | int | Строгое равенство `methodology_status == "preliminary_closed"` |
| `preliminary_closed_periods` | string[] | `YYYY-MM` таких месяцев, порядок как в `periods` |
| `final_closed_count` | int | Строгое равенство `methodology_status == "final_closed"` |
| `final_closed_periods` | string[] | `YYYY-MM` таких месяцев |

Иные значения `methodology_status` (например будущее `migration_closed`) в эти агрегаты **не входят**; строка `periods[]` всё равно содержит фактическое значение API.

Существующие `closed_count` / `closed_periods` остаются по `reconciliation_status == "closed"` (включая `preliminary_closed`, т.к. reconciliation тоже `closed`).

#### Пример (фрагмент)

Запрос:

```json
{
  "profile": "prod",
  "year": 2026,
  "period_from": "2026-05",
  "period_to": "2026-06",
  "detail": "status_only"
}
```

Ответ (фрагмент):

```json
{
  "detail": "status_only",
  "period_count": 2,
  "closed_count": 2,
  "closed_periods": ["2026-05", "2026-06"],
  "preliminary_closed_count": 1,
  "preliminary_closed_periods": ["2026-06"],
  "final_closed_count": 1,
  "final_closed_periods": ["2026-05"],
  "periods": [
    {
      "period": "2026-05",
      "reconciliation_status": "closed",
      "methodology_status": "final_closed",
      "close_phase": "final"
    },
    {
      "period": "2026-06",
      "reconciliation_status": "closed",
      "methodology_status": "preliminary_closed",
      "close_phase": "preliminary"
    }
  ]
}
```

#### Ошибки и границы

| Ситуация | Поведение MCP |
| -------- | ------------- |
| API reconciliation для месяца недоступен (5xx, timeout) | Tool завершается ошибкой; частичный отчёт **не** возвращается |
| API 404 (версия не найдена) | Как сейчас — ошибка tool |
| Месяц без транзакций (`skip_empty`, `row_count: 0`) | `methodology_status` / `close_phase` всё равно заполняются из reconciliation; **нельзя** пропускать строку по `row_count == 0` |
| Неизвестное `methodology_status` от API | Проброс как есть; tool **не** падает; агрегаты preliminary/final не увеличиваются |
| Несогласованная пара `final_closed` + `close_phase: "preliminary"` | Проброс как есть; валидация — backend, не MCP |
| Горизонт ACT пуст | `period_count: 0`, новые агрегаты = 0, `periods: []` |

Дополнительных HTTP-вызовов на месяц **не** добавляется: те же запросы reconciliation, расширенный разбор ответа.

### MCP: `list_period_statuses`

#### Строка `periods[]`

К существующим полям **добавляются** `methodology_status`, `close_phase` (те же типы и семантика).

Поле `status` **не переименовывается** — по-прежнему reconciliation status (`open` / `closed` / `draft`).

#### Пример (фрагмент)

```json
{
  "periods": [
    {
      "period": "2026-06",
      "status": "closed",
      "methodology_status": "preliminary_closed",
      "close_phase": "preliminary"
    }
  ],
  "closed_count": 1,
  "closed_periods": ["2026-06"]
}
```

Агрегаты `preliminary_closed_*` / `final_closed_*` в `list_period_statuses` **не** добавляются (минимальный scope; полный срез — `period_status_report`).

### Реализация (ориентир для разработчика)

1. Заменить или дополнить `reconciliation_status()` функцией `fetch_reconciliation()` → `dict` с `status`, `methodology_status`, `close_phase`; старый call site мигрировать.
2. Пробросить поля в `compact_period_summary()` и `period_status_report()`; при `skip_empty` и `row_count=0` reconciliation metadata **не** отбрасывать.
3. Обновить `_handle_list_period_statuses` в `server.py`.
4. Обновить `description` tools (упомянуть preliminary / final).
5. **Не** добавлять enum-check / assert согласованности полей в MCP.

Репозиторий: `mcp-servers/finance-assistant/`.

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Откуда брать methodology | Только из `GET /budget/reconciliation`; дублировать доменную логику в MCP запрещено |
| D-02 | Обратная совместимость | Только additive fields; существующие ключи и семантика без изменений |
| D-03 | `list_period_statuses` | В scope: добавить поля в строки; без новых корневых агрегатов |
| D-04 | Агрегаты в `period_status_report` | Добавить `preliminary_closed_*` и `final_closed_*`; `closed_*` не менять |
| D-05 | Режим `status_only` | Включает `methodology_status` / `close_phase` (тот же reconciliation GET) |
| D-06 | `close_phase` при `open` | Backend в текущей реализации отдаёт `null`; MCP не нормализует значение и пробрасывает его как есть |
| D-07 | Отдельный MCP tool | Не создавать; расширение существующих tools ([FIN-106](https://alexeielizarov.atlassian.net/browse/FIN-106)) |
| D-08 | Неизвестный `methodology_status` | Passthrough: значение API без валидации; tool не fail fast |
| D-09 | Согласованность `methodology_status` и `close_phase` | Не проверяется в MCP; трансляция backend как есть |
| D-10 | Агрегаты при неизвестном status | Считаются только `preliminary_closed` и `final_closed` (строгое равенство); прочие — только в строке |
| D-11 | Ортогональность `status` и `methodology_status` | Разные измерения; пары вроде `draft` + `open` допустимы |
| D-12 | Имя `close_phase` | Сохранить как в backend (= фаза методологического close, не reconciliation) |

## Non-goals / guardrails

* Не менять backend и схему БД.
* Не переименовывать `status` / `reconciliation_status` в существующих consumers.
* Не валидировать и не «чинить» пары `methodology_status` / `close_phase` в MCP.
* Не добавлять в отчёт текстовые подписи («предварительно») — только машиночитаемые поля; формулировки для чата — в ops-шаблонах.
* Не реализовывать [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104) в этом инкременте.

## Чеклист тестов

* **T1:** `period_status_report`, `detail=status_only` — строка с `preliminary_closed` содержит `close_phase: "preliminary"`.
* **T2:** `period_status_report` — месяц `open` после reopen: `methodology_status: "open"`, `close_phase: null`.
* **T3:** `period_status_report` — legacy `closed` без журнала: `final_closed` / `final`.
* **T4:** Агрегаты `preliminary_closed_count`, `final_closed_count` согласованы со строками.
* **T5:** `skip_empty=true`, `row_count=0` — methodology поля присутствуют (регрессия: `if row_count == 0: continue` запрещена).
* **T6:** `list_period_statuses` — новые поля в каждой строке; `status` = reconciliation.
* **T7:** Существующие поля отчёта (`ready`, `c9999_count`, …) не регрессируют при `detail=summary`.
* **T8:** API возвращает `methodology_status: "migration_closed"` — строка проброшена; `preliminary_closed_count` и `final_closed_count` не увеличиваются; tool успешен.
* **T9:** `status: "draft"`, `methodology_status: "open"` — оба поля в ответе без нормализации.

## Приёмочная проверка

### Предусловия

* API `prod` запущен; MCP `finance_api_connect` → `data_profile == prod`.
* В данных есть минимум один месяц с `preliminary_closed` и один с `final_closed` (или настроить через close/reopen в тестовом профиле).

### T1 — preliminary в status report

**Действие:** `period_status_report({ "profile": "prod", "period_from": "2026-06", "period_to": "2026-06", "detail": "status_only" })`.

**Ожидаемый результат:**

* В строке `2026-06`: `methodology_status == "preliminary_closed"`, `close_phase == "preliminary"`.
* `preliminary_closed_periods` содержит `"2026-06"`.

### T2 — list без лишних вызовов verify

**Действие:** `list_period_statuses({ "profile": "prod", "anchor_period": "2026-06" })`.

**Ожидаемый результат:** у закрытых месяцев есть `methodology_status`; tool не требует `verify_month`.

### Автоматизация

**Тесты:** `scripts/test_period_status_methodology.py` (новый файл, `unittest` + mock `ApiClient`).

**Команда:**

```bash
cd mcp-servers/finance-assistant/scripts && python -m unittest test_period_status_methodology -v
```

## E2E

| Сценарий | Изменение | Обновление |
| -------- | --------- | ---------- |
| FinancePlanningProject E2E | Нет | Нет |
| Ops monthly close (methodology) | Чтение фазы через MCP | Обновить пример в [monthly-close-api/index.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/index.md) после Done |

## Follow-ups / Out of scope

| ID | Тема | Решение |
| -- | ---- | ------- |
| F-01 | Агрегаты в `list_period_statuses` | Вне [FIN-106](https://alexeielizarov.atlassian.net/browse/FIN-106); при необходимости — отдельная задача в FIN |
| F-02 | [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104) | Использует те же поля из этой спеки |
| F-03 | Ссылка на спеку в Jira | ✓ при утверждении |
| F-04 | Частичный отчёт при ошибке одного месяца (`partial: true`) | Вне [FIN-106](https://alexeielizarov.atlassian.net/browse/FIN-106); сейчас fail-fast (D-01 в ошибках); отдельная задача FIN при необходимости |

## Утверждение

* **Статус:** Утверждено
* **Дата:** 2026-06-28
* **Следующий шаг:** реализация в спринте HO S1; снять label `mcp-gap` с [FIN-106](https://alexeielizarov.atlassian.net/browse/FIN-106) при Done

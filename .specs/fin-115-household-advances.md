# MCP tool `household_advances` — учёт авансов на базовые потребности

**Связь:** [FIN-115](https://alexeielizarov.atlassian.net/browse/FIN-115); родитель [FIN-101](https://alexeielizarov.atlassian.net/browse/FIN-101); **Blocks** [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104), [FIN-105](https://alexeielizarov.atlassian.net/browse/FIN-105); **Relates** [FIN-116](https://alexeielizarov.atlassian.net/browse/FIN-116) (отдельный домен — займы третьим лицам).

**Домен:** аванс на базовые потребности — [household-budget-model.md](../../../assistant/35-finance-assistant/methodology/budgeting/household-budget-model.md) (§ «Аванс на базовые потребности»); partner ids — [household-contour-mapping.{profile}.json](../../../assistant/35-finance-assistant/methodology/household-contour-mapping.prod.json).

**Статус:** Утверждено (2026-07-10, rev.2)

## Назначение

Когда личный фонд партнёра исчерпан, модель разрешает оплату базовых потребностей **из общего фонда** с удержанием из личного фонда **следующего** месяца. Сегодня суммы фиксируются только в чате — нет машиночитаемого журнала для money check ([FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104)) и расчёта переноса ([FIN-105](https://alexeielizarov.atlassian.net/browse/FIN-105)).

**Критерий приёмки:** ops регистрирует аванс одним вызовом `household_advances`; `list` возвращает открытые суммы по партнёрам без ручного переноса из чата; потребители FIN-104/105 читают те же данные из ledger.

## Объём и границы

### Входит в объём

* Новый MCP tool **`household_advances`** в `mcp-servers/finance-assistant/` с действиями `register`, `list`, `void`, **`mark_deducted`** (см. D-02 — нужен для идемпотентного FIN-105).
* Interim JSON **ledger** per profile: `{ASSISTANT_ROOT}/working/household/household-advances.{profile}.json`.
* Модуль `scripts/household_advances.py` — load/save ledger, валидация, агрегации для потребителей.
* Валидация `partner_id` по `partners[].id` из `household-contour-mapping.{profile}.json`.
* Unit-тесты (mock filesystem + fixture ledger): register, list filters, void, totals, mark_deducted, backward compat пустого ledger.
* Запись в `mcp-gaps.md` (секция gaps → available после Done); schema в `server.py` после реализации.

### Не входит в объём

* Backend API / `FinancePlanningProject` — interim JSON до появления BE ledger.
* [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104) (`money_check_report`) — только читает ledger через shared helper / тот же файл.
* [FIN-105](https://alexeielizarov.atlassian.net/browse/FIN-105) (`personal_fund_carryover`) — вызывает `mark_deducted` и суммирует `advance_deduction`; формула carryover — в спеке FIN-105.
* [FIN-116](https://alexeielizarov.atlassian.net/browse/FIN-116) (`household_receivables`) — займы третьим лицам; **не** объединять с авансами.
* Вычисление остатка личного фонда и проверка права на аванс — см. D-08; tool **не** читает plan/fact/carryover.
* Проверка статуса закрытия `issue_period` — см. D-09; orchestration снаружи tool.
* Привязка к банковской операции (`transaction_key`) — follow-up.
* Hard delete записей — только `void` (audit trail).

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| MCP tools | Нет `household_advances` | Label `mcp-gap` на FIN-115 |
| Ledger | Только чат / память | FIN-104, FIN-105 blocked |
| `household_base_share` | Базовая доля без удержания авансов | Удержание — FIN-105 + этот ledger |
| Backend | Нет доменной сущности advance | Interim JSON |

## Обратная совместимость

Новый tool; существующие tools **не меняются**. Пустой / отсутствующий ledger → `list` возвращает `entries: []`, `totals_by_partner: {}` без ошибки.

## Целевое поведение

### Домен (источник — модель)

| Поле | Смысл |
| ---- | ----- |
| `issue_period` | Календарный месяц, когда партнёр взял аванс (`YYYY-MM`) |
| `deduct_in_period` | Месяц удержания из личного фонда = **следующий** календарный месяц после `issue_period` |
| `amount` | Сумма аванса, EUR, > 0, 2 знака |
| `partner_id` | Кто взял аванс (`aleksey` / `nikolay` из contour mapping) |

Несколько авансов за один `issue_period` у одного партнёра **суммируются** при агрегации.

### Interim ledger (JSON)

Путь:

```
{ASSISTANT_ROOT}/working/household/household-advances.{profile}.json
```

`ASSISTANT_ROOT` — как в `monthly_close_lib.ASSISTANT_ROOT`.

#### Схема файла (v1)

```json
{
  "schema_version": 1,
  "profile": "prod",
  "entries": [
    {
      "id": "adv-202607-nikolay-001",
      "partner_id": "nikolay",
      "issue_period": "2026-07",
      "deduct_in_period": "2026-08",
      "amount": 70.0,
      "currency": "EUR",
      "note": "продукты",
      "status": "open",
      "registered_at": "2026-07-15T08:00:00Z",
      "voided_at": null,
      "void_reason": null,
      "deducted_at": null
    }
  ]
}
```

### Pipeline (общий)

```
profile = resolve_profile(arg)
ledger_path = working/household/household-advances.{profile}.json
ledger = load_or_init(ledger_path)          # missing file → empty entries[]
partners = load_partner_ids(profile)        # from household-contour-mapping.{profile}.json
dispatch(action, args, ledger, partners)
atomic_write(ledger_path, ledger)           # только если action мутирует
return normalized_response
```

**Atomic write:** запись через temp file + `replace` (как принято в проекте для JSON logs).

### MCP: общие параметры

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `action` | string | **да** | — | `register` \| `list` \| `void` \| `mark_deducted` |
| `profile` | string | нет | `prod` | data profile (имя файла ledger) |

### Action `register`

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `partner_id` | string | **да** | id из contour mapping |
| `issue_period` | string | **да** | `YYYY-MM` |
| `amount` | number | **да** | > 0, max 2 decimals |
| `note` | string | нет | произвольная пометка ops |

**Алгоритм:**

1. Валидировать `issue_period` (`YYYY-MM`), `amount`, `partner_id ∈ partners`. **Не** проверять остаток личного фонда и **не** проверять статус закрытия месяца (D-08, D-09).
2. `deduct_in_period = next_calendar_month(issue_period)`.
3. Сгенерировать `id`: `adv-{issue_period_compact}-{partner_id}-{seq:03d}` где `seq` = 1 + count existing entries с тем же prefix (не reuse void ids).
4. Append entry со `status: "open"`, `registered_at` = текущее время в **UTC**, ISO8601 с суффиксом `Z` (напр. `2026-07-15T08:00:00Z`). Поля `voided_at` / `deducted_at` — тот же формат.
5. Save ledger; вернуть `{ ok, action, entry }`.

### Action `list`

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `partner_id` | string | нет | фильтр по партнёру |
| `issue_period` | string | нет | фильтр по месяцу выдачи |
| `deduct_in_period` | string | нет | фильтр по месяцу удержания |
| `status` | string | нет | `open` \| `deducted` \| `void` \| omit = все |

**Алгоритм:**

1. Filter `entries[]` по необязательным полям фильтра.
2. Вычислить `totals_by_partner`: по **всем** записям ledger со `status == "open"` — сумма `amount` на каждый `partner_id` (независимо от фильтра `entries[]`).
3. Вычислить `totals_by_issue_period`: по **всем** open-записям ledger — map `issue_period → сумма amount`. Поле **всегда** присутствует в ответе; если open-записей нет — `{}`.
4. Вернуть `{ ok, action, profile, filters, entries, totals_by_partner, totals_by_issue_period }`.

`entries` отражает применённые фильтры; агрегаты (`totals_by_partner`, `totals_by_issue_period`) **всегда** рассчитываются по полному ledger и **не** зависят от фильтрации списка.

**Read-only:** файл не мутируется.

### Action `void`

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `id` | string | **да** | id записи |
| `reason` | string | нет | причина отмены (audit) |

**Алгоритм:**

1. Найти entry по `id`; 0 matches → tool error; 2+ → tool error (ambiguous — не должно случаться).
2. Если `status == "void"` → tool error (`already_void`).
3. Если `status == "deducted"` → tool error (`already_deducted`).
4. Set `status = "void"`, `voided_at` (UTC `Z`), `void_reason`; save; return `{ ok, action, entry }`.

Hard delete **запрещён**.

### Action `mark_deducted`

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `issue_period` | string | **да** | Месяц **выдачи** аванса (`YYYY-MM`); именно он соответствует закрываемому периоду при вызове из FIN-105. **Не** `deduct_in_period`: после close июля вызывается `mark_deducted(issue_period="2026-07")`, хотя удержание происходит в августе |
| `partner_id` | string | нет | если omit — все партнёры |

**Алгоритм:**

1. Select entries where `status == "open"` AND `issue_period` matches AND (`partner_id` omit OR matches).
2. For each: set `status = "deducted"`, `deducted_at` = UTC now (`Z`).
3. Save; return `{ ok, action, marked: [{ id, partner_id, amount, ... }], marked_total }`.

Идempotent: повторный вызов для того же `issue_period` → `marked: []`, `marked_total: 0` (не error).

Предназначено для вызова из FIN-105 после финального close; ops может вызвать вручную.

### Статусы (enum v1)

| `status` | Смысл |
| -------- | ----- |
| `open` | зарегистрирован, ожидает удержания |
| `deducted` | удержание применено (FIN-105 или ops) |
| `void` | отменён по ошибке |

### Резолв / валидация

| Ситуация | Поведение |
| -------- | --------- |
| Неизвестный `action` | Tool error |
| `partner_id` ∉ mapping partners | Tool error |
| `issue_period` / `deduct_in_period` не `YYYY-MM` | Tool error |
| `amount` ≤ 0 или > 2 decimals | Tool error |
| `void`: id не найден | Tool error |
| `void`: уже void / deducted | Tool error |
| Ledger JSON corrupt | Tool error |
| Contour mapping missing for profile | Tool error |

### Инварианты (после pipeline)

1. Каждая запись имеет ровно один `status` из enum v1.
2. `deduct_in_period == next_calendar_month(issue_period)` для всех non-void entries.
3. `amount > 0`; `currency == "EUR"` (**immutable** после register).
4. `id` уникален в `entries[]`.
5. После `void` или `mark_deducted` запись **остаётся** в файле (audit trail).
6. `totals_by_partner` на `list` учитывает **только** `status == "open"`.
7. Сумма нескольких `open` entries одного партнёра за месяц = суммарное удержание для FIN-105.

### Контракт для потребителей (read helpers)

Экспорт из `scripts/household_advances.py` (для FIN-104/105, без дублирования логики):

```python
def load_ledger(profile: str) -> dict[str, Any]:
    """Load ledger JSON; missing file → empty entries[]."""

def sum_open_by_partner(ledger, *, partner_id: str | None = None) -> dict[str, float]:
    """Сумма open-авансов по каждому partner_id; ключ — partner_id, значение — EUR."""

def sum_open_for_issue_period(
    ledger, issue_period: str, *, partner_id: str | None = None
) -> dict[str, float]:
    """Сумма open-авансов с данным issue_period по каждому partner_id; ключ — partner_id."""
```

FIN-104: `open_advances` = `sum_open_by_partner`.
FIN-105: `advance_deduction[partner]` = `sum_open_for_issue_period(ledger, closed_period, partner_id=partner)` **до** `mark_deducted`; после mark — повторный расчёт = 0.

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Имя tool | `household_advances` (как в FIN-115) |
| D-02 | `mark_deducted` | Четвёртое action в v1; идempotent; вызывается FIN-105 после расчёта |
| D-03 | Partner ids | Из `household-contour-mapping.{profile}.json` → `partners[].id` |
| D-04 | `deduct_in_period` | Авто = следующий календарный месяц; **не** передаётся в register |
| D-05 | Отмена | Только `void`; запись сохраняется с `voided_at` / `void_reason` |
| D-06 | Валюта v1 | Только EUR; поле `currency` фиксировано `"EUR"` |
| D-07 | Путь ledger | `{ASSISTANT_ROOT}/working/household/household-advances.{profile}.json` — mutable ops state; `methodology/` только для статической конфигурации (contour mapping, policy) |
| D-08 | Проверка остатка фонда | Tool **не вычисляет** остаток личного фонда и **не проверяет** право на выдачу аванса; не читает plan, fact, balances, carryover. Решение о регистрации принимает вызывающая сторона (ops) |
| D-09 | Register для прошлого периода | Tool **не проверяет** статус закрытия `issue_period` (monthly close). `register` с любым валидным `YYYY-MM` разрешён; корректность периода и согласованность с FIN-105 — ответственность ops / orchestration снаружи |
| D-10 | Timestamps audit | `registered_at`, `voided_at`, `deducted_at` — всегда UTC ISO8601 с суффиксом `Z`; локальный offset **не** используется |

## Non-goals / guardrails

* **Чистый ledger (D-08, D-09):** tool только пишет/читает записи; не финансовый движок, не оркестратор close.
* Не смешивать с FIN-116 (receivables / займы третьим лицам).
* Не писать в prod ledger при smoke — профиль **`test`** или **`cand`** с отдельным файлом.
* Не требовать backend / SQLite / API monthly-close для валидации register.
* Не auto-register из банковских транзакций.

## Чеклист тестов

* **T1:** register — happy path; `deduct_in_period` = next month; entry `open`.
* **T2:** register — invalid partner → error.
* **T3:** register — amount ≤ 0 → error.
* **T4:** list — empty ledger → `entries: []`, `totals_by_partner: {}`, `totals_by_issue_period: {}`.
* **T5:** list — filter by partner / issue_period / status.
* **T6:** list — `totals_by_partner` sums only `open`.
* **T7:** void — open entry → `void`; audit fields set.
* **T8:** void — already void / deducted → error.
* **T9:** mark_deducted — marks all open for issue_period; second call idempotent.
* **T10:** multiple registers same month/partner — totals sum correctly.
* **T11:** corrupt ledger JSON → error on load.
* **T12:** seq in id increments per partner+issue_period prefix.
* **T13:** register с `issue_period` в прошлом (месяц уже closed с точки зрения ops) — **успех**; tool не обращается к period status.

## Приёмочная проверка

### Предусловия

* MCP `finance_api_connect` → `data_profile` = **`test`** или **`cand`**
* Contour mapping для профиля существует
* Ledger file для профиля отсутствует или пуст

### A1 — register (модельный пример)

**Действие:** `household_advances({ "action": "register", "profile": "test", "partner_id": "nikolay", "issue_period": "2026-07", "amount": 70, "note": "продукты" })`.

**Ожидаемый результат:** `entry.deduct_in_period == "2026-08"`, `status == "open"`, файл создан.

### A2 — list totals

**Действие:** `household_advances({ "action": "list", "profile": "test", "status": "open" })`.

**Ожидаемый результат:** `totals_by_partner.nikolay == 70`.

### A3 — void

**Действие:** `void` по id из A1.

**Ожидаемый результат:** `totals_by_partner` пуст; запись в `entries` со `status: void`.

### A4 — mark_deducted (FIN-105 path)

**Действие:** register 70 + 30 same month → `mark_deducted({ issue_period: "2026-07" })` → list open.

**Ожидаемый результат:** `marked_total == 100`; open totals = 0.

## Связь с другими FIN

| FIN | Связь | Использование ledger |
| --- | ----- | -------------------- |
| FIN-104 | blocked by FIN-115 | `open_advances` per partner via `sum_open_by_partner` |
| FIN-105 | blocked by FIN-115 | `advance_deduction` from `issue_period == closed month`; then `mark_deducted` |
| FIN-116 | Relates (не merge) | Отдельный tool / файл receivables |
| FIN-103 | sibling | Partner ids из того же contour mapping |

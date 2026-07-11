# MCP tool `household_receivables` — учёт займов третьим лицам (дебиторка)

**Связь:** [FIN-116](https://alexeielizarov.atlassian.net/browse/FIN-116); родитель [FIN-101](https://alexeielizarov.atlassian.net/browse/FIN-101); **Relates** [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104) (money check — `outstanding_receivables`), [FIN-115](https://alexeielizarov.atlassian.net/browse/FIN-115) (**не merge** — семейные авансы), [FIN-137](https://alexeielizarov.atlassian.net/browse/FIN-137) (concurrent write — тот же паттерн ledger).

**Домен:** займы третьим лицам — [household-budget-model.md](../../../assistant/35-finance-assistant/methodology/household-budget-model.md) (§ «Займы третьим лицам»); возврат наличными — [accounting.md](../../../assistant/35-finance-assistant/methodology/accounting.md); partner ids — [household-contour-mapping.{profile}.json](../../../assistant/35-finance-assistant/methodology/household-contour-mapping.prod.json).

**Статус:** Утверждено (2026-07-10, rev.3)

## Назначение

Деньги, выданные третьему лицу (напр. **300 € Аркадию**, июнь 2026, возврат конец августа), — реальный банковский отток, но **не потребление**: до возврата это **дебиторка**. Без журнала money check завышает «свободную» ликвидность; ops держит суммы только в чате.

В отличие от [FIN-115](https://alexeielizarov.atlassian.net/browse/FIN-115) (аванс партнёру из общего фонда с удержанием следующего месяца), внешний займ: заёмщик вне контура, горизонт до `due_period`, возврат — **доход кредитора** (личный или общий контур по `source`), виден на money check как неликвид.

**Критерий приёмки:** ops регистрирует займ одним вызовом `household_receivables`; `list` показывает открытую дебиторку с остатком; частичный возврат и `write_off`/`mark_gift` закрывают баланс; [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104) читает `outstanding_receivables` через shared helpers без дублирования логики.

## Объём и границы

### Входит в объём

* Новый MCP tool **`household_receivables`** в `mcp-servers/finance-assistant/` с действиями `register`, `record_repayment`, `list`, `extend`, `write_off`, `mark_gift`.
* Interim JSON **ledger** per profile: `{ASSISTANT_ROOT}/working/household/household-receivables.{profile}.json`.
* Модуль `scripts/household_receivables.py` — load/save, валидация, агрегации для FIN-104.
* Поля домена v1: `lender_id`, `borrower_label`, `principal`, `balance`, `source` (`personal` | `shared`), `issue_period`, `due_period`, опционально `note`, `transaction_key`.
* На `list`: агрегаты `totals_by_lender`, `totals_by_due_period`, `totals_shared`; вычисляемый флаг **`is_overdue`** (см. D-03).
* Unit-тесты (mock filesystem + fixture ledger).
* Запись в `mcp-gaps.md` после реализации; schema в `server.py`.

### Не входит в объём

* Backend API / SQLite — interim JSON до BE ledger.
* [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104) (`money_check_report`) — только **читает** helpers; реализация tool — отдельная задача.
* [FIN-115](https://alexeielizarov.atlassian.net/browse/FIN-115) — семейные авансы; **отдельный файл** ledger.
* Автоматическая регистрация из банковских транзакций; проверка остатка личного/общего фонда при `register`.
* Проверка статуса закрытия `issue_period` / orchestration monthly close.
* Hard delete записей — только terminal statuses с audit trail.
* [FIN-137](https://alexeielizarov.atlassian.net/browse/FIN-137) file lock — до реализации FIN-137: atomic replace + **single-operator assumption** (как FIN-115).

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| MCP tools | Нет `household_receivables` | Label `mcp-gap` на FIN-116 |
| Ledger | Только чат / память | FIN-104 не может показать дебиторку |
| `household_advances` | Авансы партнёрам | Другой домен |
| Backend | Нет доменной сущности receivable | Interim JSON |

## Обратная совместимость

Новый tool; существующие tools **не меняются**. Пустой / отсутствующий ledger → `list` возвращает `entries: []`, нулевые totals без ошибки.

## Целевое поведение

### Домен (источник — модель)

| Поле | Смысл |
| ---- | ----- |
| `lender_id` | Партнёр-кредитор / инициатор (`aleksey` / `nikolay` из contour mapping) |
| `borrower_label` | Имя/метка заёмщика (внешнее лицо), произвольная строка |
| `principal` | Исходная сумма займа, EUR, > 0; **immutable** после register |
| `balance` | Непогашенный остаток; **stored derived field** (см. ниже) |
| `currency` | Константа **`"EUR"`** v1; **immutable**; мультивалютность вне scope |
| `source` | `personal` — из личного фонда кредитора; `shared` — из общего фонда семьи |
| `issue_period` | Месяц выдачи `YYYY-MM` |
| `due_period` | Плановый месяц возврата `YYYY-MM` (конец месяца — ops-соглашение) |

| Событие | Учёт (модель; tool **не** двигает plan/fact) |
| ------- | --------------------------------------------- |
| Выдача | Расход контура `source` в `issue_period`; дебиторка +`principal` |
| Возврат | Доход контура в `receipt_period`; дебиторка − сумма возврата |
| Outstanding | `balance > 0`, status terminal не достигнут |

#### Semantics: `balance` (stored derived field)

`balance` **хранится** в ledger для удобства чтения и агрегаций, но является **производным** от `repayments[]`:

* при `status == "open"`: после каждой мутации **обязательно** `balance == principal − Σ repayments.amount` (± 0.01);
* при terminal status: `balance == 0` (см. инварианты); при `write_off` / `mark_gift` **оставшийся остаток считается прощённым** и **не** добавляется в `repayments[]`.

Master-данные по возвратам — только `repayments[]`. Если для open-entry инвариант нарушен — ledger **повреждён** → tool error при load.

### Interim ledger (JSON)

Путь:

```
{ASSISTANT_ROOT}/working/household/household-receivables.{profile}.json
```

#### Схема файла (v1)

```json
{
  "schema_version": 1,
  "profile": "prod",
  "entries": [
    {
      "id": "recv-202606-aleksey-001",
      "lender_id": "aleksey",
      "borrower_label": "Arkady",
      "principal": 300.0,
      "balance": 300.0,
      "currency": "EUR",
      "source": "personal",
      "issue_period": "2026-06",
      "due_period": "2026-08",
      "note": "до конца августа",
      "transaction_key": null,
      "status": "open",
      "repayments": [],
      "extensions": [],
      "registered_at": "2026-06-15T10:00:00Z",
      "closed_at": null,
      "close_reason": null
    }
  ]
}
```

`closed_at` — UTC ISO8601 `Z`; заполняется при **любом** переходе в terminal status (`repaid`, `written_off`, `gift`); для `open` — `null`.

`extensions[]` элемент: `{ "from_due_period", "to_due_period", "extended_at", "note" }`. При append: `from_due_period` = текущий `due_period` **до** обновления; `to_due_period` = новый `due_period` **после** обновления.

`repayments[]` элемент: `{ "amount", "receipt_period", "recorded_at", "note", "transaction_key" }`.

### Pipeline (общий)

```
profile = resolve_profile(arg)
ledger_path = working/household/household-receivables.{profile}.json
ledger = load_or_init(ledger_path)
partners = load_partner_ids(profile)
dispatch(action, args, ledger, partners)
atomic_write(ledger_path, ledger)    # только mutating actions
return normalized_response
```

Reuse утилит из `household_advances.py` где возможно: `normalize_period`, `next_calendar_month`, `validate_amount`, `utc_now_iso`, `save_ledger`, `load_partner_ids` (или общий helper module — **не обязательно** в v1, допустимо copy minimal set).

### MCP: общие параметры

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `action` | string | **да** | — | см. actions ниже |
| `profile` | string | нет | `prod` | data profile |

### Action `register`

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `lender_id` | string | **да** | id из contour mapping |
| `borrower_label` | string | **да** | non-empty trim; **не** уникален (D-08); после register **не изменяется** (D-09) |
| `amount` | number | **да** | = `principal` = начальный `balance` |
| `source` | string | **да** | `personal` \| `shared` |
| `issue_period` | string | **да** | `YYYY-MM` |
| `due_period` | string | **да** | `YYYY-MM`, **≥** `issue_period` (календарно) |
| `note` | string | нет | пометка ops |
| `transaction_key` | string | нет | информационная ссылка на банковскую операцию (D-02); уникальность **не** проверяется |

**Алгоритм:**

1. Валидировать periods, amount, `lender_id ∈ partners`, `source` enum, `borrower_label` non-empty.
2. `id = recv-{issue_period_compact}-{lender_id}-{seq:03d}` (seq как в FIN-115).
3. Append entry: `status: "open"`, `balance = principal = amount`, `currency: "EUR"`, `repayments: []`, `extensions: []`, `registered_at` UTC `Z`, `closed_at: null`.
4. Save; return `{ ok, action, entry }`.

Tool **не** проверяет остаток фонда и статус закрытия месяца (guardrails как у FIN-115). Несколько займов одному `borrower_label` **разрешены** (D-08); каждый register — отдельная запись с уникальным `id`.

### Action `record_repayment`

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `id` | string | **да** | id записи |
| `amount` | number | **да** | > 0, ≤ текущий `balance` |
| `receipt_period` | string | **да** | `YYYY-MM` месяца поступления |
| `note` | string | нет | |
| `transaction_key` | string | нет | информационная ссылка (D-02); уникальность **не** проверяется |

**Алгоритм:**

1. Найти entry; 0 → error; status ∉ `{open}` → error (`not_open`).
2. Append repayment; `balance -= amount` (round 2 dp).
3. If `balance == 0` → `status = "repaid"`, `closed_at` = now.
4. Save; return `{ ok, action, entry, repayment }`.

Partial repayments **разрешены** (D-07): любое количество вызовов на один `id`, в том числе несколько платежей в одном `receipt_period`. **`receipt_period` после `due_period` разрешён** — просрочка не блокирует возврат (D-06).

### Action `extend`

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `id` | string | **да** | |
| `new_due_period` | string | **да** | `YYYY-MM`, **строго >** текущий `due_period` |
| `note` | string | нет | причина продления |

**Алгоритм:**

1. Entry must be `status == "open"` and `balance > 0`.
2. Push `{ from_due_period: old, to_due_period: new, extended_at, note }` в `extensions[]`.
3. Set `due_period = new_due_period`; status остаётся `open`.
4. Save; return `{ ok, action, entry }`.

### Action `write_off`

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `id` | string | **да** | |
| `reason` | string | нет | audit («безнадёжный долг») |

**Алгоритм:**

1. Entry `status == "open"`, `balance > 0`.
2. Set `status = "written_off"`, `balance = 0`, `closed_at` = now, `close_reason = reason or "written_off"`. **Оставшийся остаток считается прощённым** — в `repayments[]` **не** записывается.
3. Запись и история `repayments` **сохраняются** (audit).
4. Save; return `{ ok, action, entry }`.

### Action `mark_gift`

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `id` | string | **да** | |
| `reason` | string | нет | audit («решили подарить») |

**Алгоритм:** как `write_off`, но `status = "gift"`, `close_reason` default `"gift"`. **Оставшийся остаток считается прощённым** (как у `write_off`). Экономически эквивалентно write-off; различие — аналитика (модель).

### Action `list`

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `lender_id` | string | нет | фильтр |
| `borrower_label` | string | нет | case-insensitive substring |
| `issue_period` | string | нет | |
| `due_period` | string | нет | |
| `source` | string | нет | `personal` \| `shared` |
| `status` | string | нет | `open` \| `repaid` \| `written_off` \| `gift` \| omit = все |
| `as_of_period` | string | нет | `YYYY-MM` для расчёта `is_overdue`; omit → текущий календарный месяц (UTC) |

**Алгоритм:**

1. `as_of_period = normalize_period(arg.as_of_period ?? current_calendar_month_utc())`.
2. Filter `entries[]` по необязательным полям (кроме `as_of_period`).
3. Для каждой entry (в filtered и для aggregates по полному ledger):

```
is_overdue =
  status == "open"
  AND balance > 0
  AND due_period < as_of_period
```

`overdue` **не хранится** как status (D-03).

4. `totals_by_lender`: по **всем** open entries с `source == "personal"` — sum `balance` по `lender_id` (независимо от фильтра entries). Shared **не** включается (D-05).
5. `totals_shared`: sum `balance` open entries с `source == "shared"` (D-05).
6. `totals_by_due_period`: по **всем** open entries — map `due_period → sum balance`.
7. `overdue_count`: count open entries где `is_overdue`.
8. Return `{ ok, action, profile, as_of_period, filters, entries, totals_by_lender, totals_shared, totals_by_due_period, overdue_count }`.

Each entry in response includes computed `is_overdue`.

**Read-only:** файл не мутируется.

### Статусы (enum v1)

| `status` | Смысл |
| -------- | ----- |
| `open` | `balance > 0`, ожидается возврат |
| `repaid` | полностью погашен через `record_repayment`; `balance == 0`, `closed_at` set |
| `written_off` | списан как безнадёжный; `balance == 0`, `closed_at` set |
| `gift` | добровольно прощён; `balance == 0`, `closed_at` set |

Terminal = `repaid` | `written_off` | `gift`. Для всех terminal: `closed_at != null`, `balance == 0`.

`is_overdue` — **вычисляемый** флаг на `list`, не значение `status`.

### Резолв / валидация

| Ситуация | Поведение |
| -------- | --------- |
| Неизвестный `action` | Tool error |
| `lender_id` ∉ partners | Tool error |
| period не `YYYY-MM` | Tool error |
| `due_period` < `issue_period` | Tool error |
| `amount` ≤ 0 или > 2 decimals | Tool error |
| `borrower_label` empty | Tool error |
| `source` not in enum | Tool error |
| `record_repayment`: amount > balance | Tool error |
| Mutating action on non-open entry | Tool error |
| `extend`: new_due_period ≤ current due_period | Tool error |
| Изменение `borrower_label` после register | **Не поддерживается** — нет action rename (D-09) |
| id не найден | Tool error |
| Ledger JSON corrupt / balance desync on open entry | Tool error |
| Contour mapping missing | Tool error |

### Инварианты (после pipeline)

1. **`status == "open"`:** `0 < balance ≤ principal` AND `balance == principal − Σ repayments.amount` (± 0.01).
2. **Terminal** (`repaid`, `written_off`, `gift`): `balance == 0`, `closed_at != null`. Формула п.1 **не** применяется — при `write_off`/`mark_gift` **оставшийся остаток считается прощённым**, не попадает в `repayments[]`.
3. `principal > 0`; `currency == "EUR"` (**const**, immutable после register; мультивалютность v1 **нет**).
4. `id` уникален в `entries[]`.
5. Terminal statuses **не** мутируются повторно.
6. `totals_by_lender` учитывает только `status == "open"` AND `source == "personal"`.
7. `totals_shared` — только `status == "open"` AND `source == "shared"`.
8. Запись **никогда не удаляется** из файла после register.
9. `due_period` после `extend` монотонно растёт; для каждого элемента `extensions[]`: `from_due_period` = предыдущее значение `due_period` в цепочке, последний `to_due_period == entry.due_period`.
10. `borrower_label` **immutable** после register (D-09).
11. `transaction_key` — информационная ссылка; duplicate keys **не** error (D-02).
12. При load: open-entry с нарушением п.1 → ledger **повреждён** → tool error.

### Контракт для потребителей (read helpers)

Экспорт из `scripts/household_receivables.py`:

```python
def load_ledger(profile: str) -> dict[str, Any]:
    """Load ledger; missing file → empty entries[]."""

def sum_outstanding_by_lender(ledger, *, lender_id: str | None = None) -> dict[str, float]:
    """Open personal-source balances by lender_id."""

def sum_outstanding_shared(ledger) -> float:
    """Open shared-source balances total."""

def list_overdue_entries(ledger, *, as_of_period: str | None = None) -> list[dict[str, Any]]:
    """Open entries with due_period < as_of_period (default: current month)."""
```

FIN-104: `outstanding_receivables` per lender = `sum_outstanding_by_lender`; добавить `outstanding_shared_total` и optional `overdue_entries` / warning when `overdue_count > 0`.

## Открытые решения

*(нет — все закрыты в D-01…D-13.)*

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Имя tool | `household_receivables` |
| D-02 | `transaction_key` | **Optional** при register/repayment; информационная ссылка — **не** foreign key; уникальность **не** проверяется этим tool |
| D-03 | Просрочка | Вычисляемый `is_overdue` на `list`: `status == "open" AND balance > 0 AND due_period < as_of_period`; `as_of_period` = param или текущий календарный месяц (UTC); stored status `overdue` **нет** |
| D-04 | `extend` + `mark_gift` | Входят в **v1** |
| D-05 | Shared loan в totals | `totals_by_lender` — только `source == "personal"`; `totals_shared` — только `source == "shared"`; split между партнёрами **не** делается |
| D-06 | Repayment после `due_period` | **Разрешён** — штатный сценарий; просрочка не блокирует `record_repayment` |
| D-07 | Количество repayment | **Не ограничено**; несколько платежей в одном `receipt_period` разрешены |
| D-08 | Несколько займов одному заёмщику | **Разрешены**; `borrower_label` не уникален; каждый register — отдельная запись |
| D-09 | Изменение `borrower_label` | После register **не поддерживается** (нет rename action) |
| D-10 | Semantics `balance` | **Stored derived field** — см. § Semantics: `balance`; desync на open-entry → corrupt ledger |
| D-11 | Balance vs terminal | **Open:** `balance == principal − Σ repayments`; **terminal:** `balance == 0`; при write_off/gift **оставшийся остаток считается прощённым** (не в `repayments[]`) |
| D-12 | `closed_at` | Заполняется при **любом** terminal transition: `repaid`, `written_off`, `gift` |
| D-13 | `currency` | Const **`"EUR"`** v1; immutable; мультивалютность out of scope |

## Non-goals / guardrails

* **Чистый ledger:** tool не читает plan/fact, не оркестрирует close, не проверяет согласование партнёров для shared/gift.
* **Не merge с FIN-115** — другой файл, другой tool.
* Smoke на **`test`** / **`cand`**; не писать prod ledger без ops-команды.
* Single-operator assumption для concurrent writes до [FIN-137](https://alexeielizarov.atlassian.net/browse/FIN-137).
* Не auto-detect займов из транзакций.

## Чеклист тестов

* **T1:** register personal — happy path; balance == principal; status open.
* **T2:** register shared source — totals_shared on list.
* **T3:** register — invalid lender / empty borrower / due < issue → error.
* **T4:** list — empty ledger → zero totals.
* **T5:** list — filters by lender, source, status; `is_overdue` true when due_period past.
* **T6:** record_repayment partial — balance decrements; still open.
* **T7:** record_repayment full — status repaid, balance 0.
* **T8:** record_repayment amount > balance → error.
* **T9:** extend — due_period updated; extensions[] audit.
* **T10:** register 500 → repay 100 → `write_off` → `principal == 500`, `len(repayments) == 1`, `Σ repayments == 100`, `balance == 0`, `status == "written_off"`, `closed_at != null`. Аналогично для `mark_gift`.
* **T11:** totals_by_lender excludes shared; totals_shared excludes personal.
* **T12:** multiple loans same lender — totals sum.
* **T13:** corrupt ledger JSON → error.
* **T14:** Arkady example — register Jun/ due Aug → list open, overdue false in Jun, true in Sep.
* **T15:** repayment after due_period — success (D-06).
* **T16:** two register same borrower_label — separate ids; totals sum (D-08).
* **T17:** multiple repayments same receipt_period — success (D-07).
* **T18:** duplicate transaction_key on two entries — no error (D-02).
* **T19:** open ledger with balance ≠ principal − repayments → load error (D-10).
* **T20:** extend — last extensions[].to_due_period == entry.due_period (invariant п.9).

## Приёмочная проверка

### Предусловия

* MCP `finance_api_connect` → `data_profile` = **`test`** или **`cand`**
* Contour mapping для профиля существует
* Ledger file отсутствует или пуст

### A1 — register (модельный пример Аркадий)

**Действие:** `household_receivables({ "action": "register", "profile": "test", "lender_id": "aleksey", "borrower_label": "Arkady", "amount": 300, "source": "personal", "issue_period": "2026-06", "due_period": "2026-08", "note": "до конца августа" })`.

**Ожидаемый результат:** `entry.balance == 300`, `status == "open"`, файл создан.

### A2 — list outstanding

**Действие:** `household_receivables({ "action": "list", "profile": "test", "status": "open" })`.

**Ожидаемый результат:** `totals_by_lender.aleksey == 300`, `totals_shared == 0`.

### A3 — partial repayment

**Действие:** `record_repayment({ id, amount: 100, receipt_period: "2026-08" })` → list.

**Ожидаемый результат:** `balance == 200`, status open; totals 200.

### A4 — full repayment

**Действие:** `record_repayment({ amount: 200, receipt_period: "2026-08" })`.

**Ожидаемый результат:** status repaid; open totals 0.

### A5 — write_off after partial repayment

**Действие:** register 500 → `record_repayment({ amount: 100 })` → `write_off({ id })` → list open.

**Ожидаемый результат:** `principal == 500`, `len(repayments) == 1`, `Σ repayments == 100`, `balance == 0`, `status == "written_off"`, `closed_at != null`; open totals empty. `write_off` **не** создаёт скрытый repayment.

## Связь с другими FIN

| FIN | Связь | Использование ledger |
| --- | ----- | -------------------- |
| FIN-104 | Relates | `outstanding_receivables`, overdue warning |
| FIN-115 | Relates (не merge) | Отдельный домен / файл |
| FIN-137 | Relates | Тот же atomic-write pattern; lock позже |
| FIN-103 | sibling | Partner ids из contour mapping |

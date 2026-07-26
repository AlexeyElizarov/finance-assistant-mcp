# MCP tool `money_check_report` — еженедельный household money check

**Связь:** [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104); родитель [FIN-101](https://alexeielizarov.atlassian.net/browse/FIN-101); **Relates** [FIN-81](https://alexeielizarov.atlassian.net/browse/FIN-81) (in-app UI follow-up), [FIN-103](https://alexeielizarov.atlassian.net/browse/FIN-103), [FIN-105](https://alexeielizarov.atlassian.net/browse/FIN-105), [FIN-106](https://alexeielizarov.atlassian.net/browse/FIN-106), [FIN-115](https://alexeielizarov.atlassian.net/browse/FIN-115), [FIN-116](https://alexeielizarov.atlassian.net/browse/FIN-116), [FIN-132](https://alexeielizarov.atlassian.net/browse/FIN-132) (detect-only warning).

**Домен:** еженедельная сверка 15 мин — [household-budget-model.md](../../../assistant/35-finance-assistant/methodology/budgeting/household-budget-model.md) (§ «Видимость без слежки»); ops июля — [ops-checklist.md](../../../assistant/35-finance-assistant/working/2026-07/ops-checklist.md) (§ money check).

**Статус:** Утверждено (2026-07-11, rev.3)

## Назначение

Еженедельный money check требует остатков **личных фондов** обоих партнёров, статуса закрытия прошлого месяца (`preliminary_closed` / `final_closed`), счётчиков открытых **C9999** и **`?`**, открытых **авансов** и **дебиторки**, заметок по атрибуции C24 — сегодня ops собирает это из 5–8 отдельных MCP-вызовов и чата.

**Критерий приёмки:** один вызов `money_check_report` возвращает структурированный payload для ритуала 15 мин; ops дополнительно открывает только внешний [purchases.md](../../../assistant/00-todo/lists/purchases.md) (3 ближайшие покупки). Перенос остатка и `available_personal_fund` **не дублируют** формулу FIN-105 — только читают carryover log / `personal_fund_carryover` dry_run.

## Объём и границы

### Входит в объём

* Новый MCP tool **`money_check_report`** в `mcp-servers/finance-assistant/`.
* Модуль `scripts/money_check_report.py` — оркестрация read-only helpers.
* Переиспользование:
  * `household_base_share` — `base_share` на `check_period`;
  * `compute_personal_spend` из `personal_fund_carryover.py` — факт личных трат MTD;
  * `load_carryover_log` / `resolve_incoming_carryover` / `detect_late_advance_register_conflict` из `personal_fund_carryover.py`;
  * internal dry_run path `compute_personal_fund_carryover(..., dry_run=true)` когда нет log-run для пары `(M, T)` и prior month `final_closed` (D-13);
  * `household_advances`: `load_ledger`, `sum_open_by_partner`, `totals_by_issue_period`;
  * `household_receivables`: `load_ledger`, `sum_outstanding_by_lender`, `sum_outstanding_shared`, `list_overdue_entries`;
  * `fetch_reconciliation` / `fetch_reconciliation_full` — `methodology_status` prior month;
  * `GET /api/v1/transactions/classification-summary` — C9999 count;
  * client-side count «`?`» (см. D-03).
* Unit-тесты (mock API + fixture ledgers/mapping/log).
* Обновление `mcp-gaps.md` и schema в `server.py` после реализации.

### Не входит в объём

* Чтение или парсинг `purchases.md` — только статическая подсказка ops (D-07).
* Мутации ledger / carryover log / plan / transactions.
* Backend API changes.
* Полная policy FIN-132 (re-run carryover, stale flag) — только warning `late_advance_register_conflict` (detect-only).
* Балансы банковских счетов / forecast (FIN-59, BLG-026) — in-app FIN-81.
* Общая продуктовая карта (вариант F) — до решения по продуктам; non-goal.
* Авто-регистрация авансов / займов.

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| MCP tools | `household_base_share`, `personal_fund_carryover`, `household_advances`, `household_receivables`, `period_status_report`, `query_transactions`, `list_c9999` | Нет единого weekly report |
| Ops ritual | 5–8 вызовов + чат | >15 мин, риск пропуска блока |
| Label | `mcp-gap` на FIN-104 | Tool отсутствует |

## Обратная совместимость

Новый tool; существующие tools **не меняются**. Пустые ledgers / пустой carryover log → нулевые totals без ошибки (как FIN-115 / FIN-116).

## Целевое поведение

### Периоды (terminology)

| Поле | Смысл | Default |
| ---- | ----- | ------- |
| `check_period` | Месяц лимита и факта трат (`YYYY-MM`) | `current_calendar_month_utc()` (D-12) |
| `prior_period` | Месяц для `methodology_status` и carryover source | `prev_calendar_month(check_period)` |
| `as_of_period` | Месяц для overdue receivables / stale advances | `check_period` |

### Формула остатка личного фонда (money check MTD)

Для каждого партнёра **p** на `check_period` **T**:

```
base[p]              = household_base_share(T).partners[p].base_share

# Carryover block — только через FIN-105 contract (не дублировать формулу):
carryover_block      = resolve_carryover_block(prior_period=M, target_period=T)
  # см. pipeline step 4

starting_fund[p]     = carryover_block.available_personal_fund[p]
                       IF carryover_block has target
                       ELSE base[p] + carryover_block.incoming_carryover[p]
                            − open_advances_deduct_in_T[p]

open_advances_deduct_in_T[p] =
    Σ amount open entries where deduct_in_period == T and partner_id == p

actual_spend_mtd[p]  = compute_personal_spend(T)[p]   # import FIN-105 path (D-11)

remaining_balance[p] = starting_fund[p] − actual_spend_mtd[p]
```

`remaining_balance` — **информационный** остаток на дату вызова; не записывается в план.

#### Partner figure flags (display)

Два **независимых** флага на `partners[]` — не объединять в один:

| Флаг | `true` когда | Смысл для ops |
| ---- | ------------ | ------------- |
| `figures_preliminary` | `methodology.methodology_status == "preliminary_closed"` | Цифры за prior month могут измениться (ожидается карточный хвост) |
| `figures_incomplete` | `carryover.source == "none"` **и** prior `methodology_status == "open"` | Carryover ещё не применим; `starting_fund` = base − open advances only |

Оба могут быть `false` одновременно (prior `final_closed`, carryover из log/dry_run).

Если `prior_period` имеет `methodology_status == "preliminary_closed"`:

* `partners[].figures_preliminary == true` (все партнёры)
* warning `figures_preliminary` в корне
* `starting_fund` / `remaining_balance` **всё равно** считаются по доступным данным; ops интерпретирует с пометкой «предварительно»

Если `prior_period` ещё `open`:

* `partners[].figures_incomplete == true` (все партнёры)
* `carryover_block.source == "none"`; `incoming_carryover[p] == 0`
* warning `prior_period_not_closed:{M}`

### Pipeline (порядок фиксирован)

```
profile, check_period?, prior_period?, as_of_period?, mapping_path?, include_advance_breakdown? = parse(args)
T = normalize_period(check_period or current_calendar_month_utc())
M = normalize_period(prior_period or prev_calendar_month(T))
as_of = normalize_period(as_of_period or T)

partners_meta = load_partners(mapping)
partner_ids = frozenset(ids)

# 1 — methodology status prior month + check month (D-14)
reconciliation_M = fetch_reconciliation(api, budget_version_id, M)
reconciliation_T = fetch_reconciliation(api, budget_version_id, T)
methodology_block = build_methodology_block(reconciliation_M)       # prior_period
check_period_methodology = build_methodology_block(reconciliation_T)  # check_period

# 2 — base share current month
base_payload = household_base_share(period=T, profile, mapping_path)

# 3 — classification counts for check month
class_summary = GET classification-summary(T)
c9999_count = class_summary.expense_c9999_count
unresolved_count = count_unresolved_expenses(api, T)   # D-03

# 4 — carryover block (FIN-105 consumer, read-only; D-13)
carryover_log = load_carryover_log(profile)
prior_run = find_carryover_run(carryover_log, closed_period=M, target_period=T)
  # match BOTH periods; log is cache, not source of truth
if prior_run exists:
    carryover_block = materialize_from_log(prior_run)
    carryover_block.source = "log"
elif methodology_block.is_final:
    carryover_block = compute_personal_fund_carryover(
        closed_period=M, target_period=T, dry_run=true, mark_advances_deducted=false
    )
    carryover_block.source = "dry_run"
else:
    carryover_block = empty_carryover_block(M, T)
    carryover_block.source = "none"

# 5 — personal spend MTD (reuse compute_personal_spend)
actual_spend, spend_lines, spend_warnings = compute_personal_spend(api, closed_period=T, ...)

# 6 — merge partner rows (starting_fund, remaining_balance, flags)
partners_out[] = build_partner_rows(...)

# 7 — advances (FIN-115 read-only)
advances_ledger = load_ledger(advances)
open_by_partner = sum_open_by_partner(advances_ledger)
issue_period_breakdown = totals_by_issue_period(advances_ledger)  # if include_advance_breakdown
stale_entries = filter_stale_open_advances(advances_ledger, as_of)  # D-04

# 8 — receivables (FIN-116 read-only)
recv_ledger = load_ledger(receivables)
outstanding_by_lender = sum_outstanding_by_lender(recv_ledger)
outstanding_shared_total = sum_outstanding_shared(recv_ledger)
overdue_entries = list_overdue_entries(recv_ledger, as_of_period=as_of)

# 9 — warnings aggregation
warnings = collect_warnings(
    methodology_block, spend_warnings,
    stale_open_advances, overdue_receivables,
    detect_late_advance_register_conflict(log, advances_ledger, M),
    prior_period_not_closed, missing_account_attribution, ...
)

# 10 — C24 attribution notes (D-05)
c24_notes = build_c24_attribution_notes(mapping, spend_warnings)

return { ok: true, check_period: T, prior_period: M, as_of_period: as_of, ... }
```

**Запрещено:** копировать формулы `carryover`, `advance_deduction`, `available_personal_fund` из FIN-105 в этот модуль — только вызов helpers / чтение log / dry_run orchestrator.

### MCP: входные параметры

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из connect | URL API |
| `check_period` | string | нет | текущий UTC месяц | Месяц лимита и факта трат `YYYY-MM` |
| `prior_period` | string | нет | `prev_month(check_period)` | Месяц для methodology / carryover |
| `as_of_period` | string | нет | `check_period` | Ref month для overdue / stale |
| `mapping_path` | string | нет | default contour mapping | |
| `include_advance_breakdown` | boolean | нет | `true` | Включить `advances.totals_by_issue_period` |
| `budget_version_id` | string | нет | ACT из connect | |

### MCP: ответ (top-level)

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `ok` | bool | `true` при успехе |
| `profile` | string | |
| `check_period` | string | |
| `prior_period` | string | |
| `as_of_period` | string | |
| `computed_at` | string | UTC ISO8601 `Z` |
| `methodology` | object | Статус **prior** month (FIN-106 fields) |
| `check_period_methodology` | object | Статус **check** month — **только display** (D-15); same shape as `methodology` |
| `partners` | array | Per-partner fund rows |
| `classification` | object | C9999 + unresolved counts на `check_period` |
| `advances` | object | Open advances aggregates |
| `receivables` | object | Outstanding + overdue |
| `illiquid_hint` | object | Informational totals (D-06) |
| `carryover` | object | Source metadata + per-partner carryover fields |
| `c24_attribution` | object | Notes + unmapped spend refs |
| `purchases_hint` | string | Static ops pointer (D-07) |
| `warnings` | string[] | Machine-readable codes |

#### `methodology`

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `period` | string | `prior_period` |
| `reconciliation_status` | string | API `status` |
| `methodology_status` | string | `open` \| `preliminary_closed` \| `final_closed` |
| `close_phase` | string \| null | API `close_phase` |
| `label` | string | `open` \| `preliminary` \| `final` — для отображения ops |

#### `check_period_methodology`

Тот же набор полей, что у `methodology`, но `period == check_period`.

**Назначение (D-15):**

* **Только отображение** статуса месяца проверки — в т.ч. исторического (`final_closed`).
* **Не участвует** в pipeline step 4 (carryover): ветка log/dry_run/none опирается **исключительно** на `methodology` (prior month).
* **Не влияет** на `warnings[]` и не меняет partner rows.
* Реализация **не должна** читать `check_period_methodology` в условиях расчёта — только включить в JSON-ответ.

#### `partners[]`

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `id` | string | `aleksey` / `nikolay` |
| `display_name` | string | из mapping |
| `base_share` | number | Плановая доля на `check_period` |
| `incoming_carryover` | number | Из carryover block |
| `advance_deduction` | number | Удержания, влияющие на `starting_fund` |
| `starting_fund` | number | Лимит на месяц (до MTD spend) |
| `actual_spend_mtd` | number | Факт личных трат с 1-го числа `check_period` |
| `remaining_balance` | number | `starting_fund − actual_spend_mtd` |
| `figures_preliminary` | bool | Prior month `preliminary_closed` — см. § Partner figure flags |
| `figures_incomplete` | bool | Prior month still `open`, carryover не применён |

#### `classification`

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `period` | string | `check_period` |
| `expense_c9999_count` | int | из classification-summary |
| `expense_c9999_amount_eur` | string | из classification-summary |
| `unresolved_expense_count` | int | «`?`» count (D-03) |

#### `advances`

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `open_advances_by_partner` | object | `partner_id → EUR` (`sum_open_by_partner`) |
| `totals_by_issue_period` | object | optional; `{}` when `include_advance_breakdown=false` |
| `stale_open_advances` | array | Entries matching D-04 (diagnostic) |

#### `receivables`

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `outstanding_by_lender` | object | personal source only |
| `outstanding_shared_total` | number | shared source |
| `overdue_entries` | array | from `list_overdue_entries` |
| `overdue_count` | int | `len(overdue_entries)` |

#### `illiquid_hint`

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `total_outstanding_eur` | number | Σ personal + shared open balances |
| `note` | string | Constant: informational; does not mutate plan/fact |

#### `carryover`

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `source` | string | `log` \| `dry_run` \| `none` |
| `closed_period` | string | `prior_period` |
| `target_period` | string | `check_period` |
| `log_computed_at` | string \| null | from log run when `source=log` |

#### `c24_attribution`

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `notes` | string[] | Human-readable ops reminders (D-05) |
| `unattributed_spend_refs` | string[] | from `unattributed_spend:*` warnings |

### Резолв / валидация

| Ситуация | Поведение |
| -------- | --------- |
| Invalid `check_period` / `prior_period` format | Tool error |
| `prior_period >= check_period` | Tool error |
| Mapping invalid / empty partners | Tool error (reuse FIN-103) |
| API unreachable | Tool error |
| Corrupt carryover log (>1 run per period) | Tool error (FIN-105 D-16) |
| Corrupt advances/receivables ledger on load | Tool error (FIN-115 / FIN-116) |
| `personal_fund_carryover` dry_run fails | Tool error; partial payload **не** возвращать |
| Empty ledgers | `ok: true`, zero totals |
| `check_period` already `final_closed` (historical call) | **Allowed** (D-14); `ok: true`; обычный расчёт; `check_period_methodology.label == "final"` |

### Warning codes (enum v1)

| Code | Условие |
| ---- | ------- |
| `prior_period_not_closed:{YYYY-MM}` | `methodology_status` prior ∈ `{open}` и carryover source `none` |
| `figures_preliminary` | prior `preliminary_closed` |
| `stale_open_advances` | ≥1 open advance with `deduct_in_period < as_of_period` (D-04) |
| `overdue_receivables` | `overdue_count > 0` |
| `late_advance_register_conflict:{YYYY-MM}` | reuse FIN-105 / FIN-132 detect-only |
| `missing_account_attribution` | mapping без `account_attribution` block |
| `unattributed_spend:{ref}` | from compute_personal_spend |
| `expense_c9999_open:{n}` | `expense_c9999_count > 0` on check_period |
| `unresolved_expenses:{n}` | `unresolved_expense_count > 0` |
| `overrun_discussion_required:{partner_id}` | if prior final carryover log shows flag (informational on check) |

Warnings **не блокируют** `ok: true` (diagnostic ritual).

### Инварианты (после pipeline)

1. `remaining_balance[p] == starting_fund[p] − actual_spend_mtd[p]` (±0.01 per partner).
2. `advances.open_advances_by_partner` == `sum_open_by_partner(ledger)` — без фильтрации entries list.
3. `receivables.outstanding_shared_total` == `sum_outstanding_shared(ledger)`.
4. Tool **не** вызывает mutating actions (`register`, `mark_deducted`, persist carryover).
5. При `carryover.source == "log"` модуль **не выполняет** повторный расчёт carryover. Все значения carryover (`incoming_carryover`, `advance_deduction`, `available_personal_fund` / `starting_fund`) **читаются исключительно** из log-run; вызов `compute_personal_fund_carryover` и пересчёт формулы **запрещены**.
6. `check_period_methodology` **не** используется в условиях pipeline (D-15).
7. `illiquid_hint.total_outstanding_eur` == sum of open receivable balances (±0.01).
8. `figures_preliminary` и `figures_incomplete` **одинаковы** для всех партнёров одного отчёта — флаги определяются состоянием `prior_period` / carryover source, **не** индивидуальными данными партнёра.

## Открытые решения

*(Пусто — все O-* закрыты в D-12…D-14.)*

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Имя tool | `money_check_report` |
| D-02 | Carryover / available fund | **Только** FIN-105 log read или `personal_fund_carryover(dry_run=true)`; формула **не** копируется |
| D-03 | Unresolved «`?`» count | Expense rows (`transaction_type=C`) in `check_period` where `transaction_category` null or `""`; count via transactions API fetch + filter (no backend change v1) |
| D-04 | Stale advances | Open entries where `deduct_in_period < as_of_period`; warning `stale_open_advances`; **не блокирует** |
| D-05 | C24 attribution notes | If mapping has `account_attribution.default_partner_by_provider.c24 == nikolay` (or provider key for C24) → note «C24 spends attributed to Nikolay personal fund»; append `unattributed_spend:*` refs |
| D-06 | Illiquid hint | Informational sum of open receivables; **no** plan/fact mutation |
| D-07 | Purchases | Static `purchases_hint` string with path to `00-todo/lists/purchases.md`; **не читать файл** |
| D-08 | Advance breakdown | `include_advance_breakdown` default `true`; omit `totals_by_issue_period` when `false` |
| D-09 | Prior month default | `prior_period = prev_calendar_month(check_period)` unless overridden |
| D-10 | FIN-132 | Surface `late_advance_register_conflict` via existing `detect_late_advance_register_conflict`; no auto-fix |
| D-11 | `compute_personal_spend` | **Import** from `personal_fund_carryover.py`; do not duplicate FIN-105 spend path |
| D-12 | Default `check_period` | If unset → `current_calendar_month_utc()`; all period math in **UTC**; user local timezone **does not** affect period selection (aligned with FIN-116) |
| D-13 | Carryover log lookup | Log used **only** when run matches **both** `(closed_period, target_period) == (M, T)`; otherwise `personal_fund_carryover(dry_run=true)` if prior `final_closed`; log is cache, not source of truth |
| D-14 | Historical `check_period` | Read-only tool **allows** any valid past `check_period` (incl. `final_closed`); no error, no required warning; expose `check_period_methodology` with `label == "final"` when applicable; normal payload otherwise |
| D-15 | `check_period_methodology` | **Display-only** block; not used in carryover branch, warnings, or partner row math |
| D-16 | Partner figure flags | **`figures_preliminary`** ↔ prior `preliminary_closed`; **`figures_incomplete`** ↔ prior `open` + carryover `none`; mutually independent booleans |

## Non-goals / guardrails

* Не менять FIN-105 / FIN-115 / FIN-116 tool contracts.
* Не читать `purchases.md` / GTD lists programmatically.
* Не prod smoke в спеке без явной ops-команды — приёмка на **`test`** / **`cand`**.
* Не duplicate FIN-132 orchestration.
* Не account balances / forecast (FIN-81 scope).
* Не использовать `check_period_methodology` в ветвлениях pipeline (D-15).

## Чеклист тестов

* **T1:** Happy path — two partners, base_share + spend → correct `remaining_balance`.
* **T2:** Prior `preliminary_closed` → `figures_preliminary=true`, `figures_incomplete=false`, warning `figures_preliminary`.
* **T3:** Prior `open` → `figures_incomplete=true`, `figures_preliminary=false`, carryover source `none`, warning `prior_period_not_closed`.
* **T4:** Carryover log hit — `(M, T)` match → partner rows match logged `available_personal_fund` (mock log).
* **T5:** Log missing + prior `final_closed` → dry_run path invoked once (mock).
* **T5b:** Log exists for `M` but stored `target_period ≠ T` → **dry_run**, not log (D-13).
* **T6:** Open advances — `open_advances_by_partner` matches ledger fixture.
* **T7:** Stale advance (`deduct_in_period` in past) → warning + `stale_open_advances[]` populated.
* **T8:** Receivables overdue → `overdue_receivables` warning + entries.
* **T9:** `late_advance_register_conflict` propagated when detect helper returns code.
* **T10:** C9999 count > 0 → warning `expense_c9999_open`.
* **T11:** Unresolved expense rows → `unresolved_expense_count` + warning.
* **T12:** `include_advance_breakdown=false` → omit `totals_by_issue_period`.
* **T13:** `prior_period >= check_period` → tool error.
* **T14:** Empty ledgers → zeros, `ok: true`.
* **T15:** `missing_account_attribution` when mapping block absent.
* **T16:** Tool never calls persist / mark_deducted (mock assert).
* **T17:** `check_period` historical `final_closed` (e.g. `2026-04`) → `ok: true`, `check_period_methodology.label == "final"`; block **не** влияет на carryover (D-15).
* **T18:** `check_period_methodology.label == "final"` does **not** add warnings and does **not** change `carryover.source` vs current-month call with same `(M, T)`.

## Приёмочная проверка

### Предусловия

* MCP `finance_api_connect` → `data_profile` = **`cand`** (или `test`).
* FIN-103/105/115/116 tools доступны; fixture ledgers при необходимости.

### A1 — один вызов вместо ручной сборки

**Действие:** `money_check_report({ "profile": "cand", "check_period": "2026-07" })`.

**Ожидаемый результат:**

* `partners.length == 2` с `base_share`, `actual_spend_mtd`, `remaining_balance`.
* `methodology.period == "2026-06"`.
* `advances`, `receivables`, `classification` присутствуют.
* `purchases_hint` указывает на purchases list.

### A2 — ops ritual parity

**Действие:** выполнить чеклист money check из [ops-checklist.md](../../../assistant/35-finance-assistant/working/2026-07/ops-checklist.md) § «Еженедельный money check» используя **только** `money_check_report` + ручной purchases.md.

**Ожидаемый результат:** не требуются отдельные вызовы `household_base_share`, `household_advances list`, `household_receivables list`, `fetch reconciliation` для prior month.

### A3 — preliminary label

**Действие:** fixture prior month `preliminary_closed`.

**Ожидаемый результат:** `methodology.label == "preliminary"`; `figures_preliminary == true`; `figures_incomplete == false`.

### A4 — incomplete figures (prior open)

**Действие:** fixture prior month `open`.

**Ожидаемый результат:** `figures_incomplete == true`; `figures_preliminary == false`; warning `prior_period_not_closed`.

## Связь с другими FIN

| FIN | Связь | Примечание |
| --- | ----- | ---------- |
| FIN-105 | Blocks (Done) | Consumer carryover; dry_run fallback |
| FIN-115 | Blocks (Done) | Read ledger helpers |
| FIN-103 | Relates (Done) | `base_share` |
| FIN-106 | Relates (Done) | methodology fields pattern |
| FIN-116 | Relates (Done) | Read receivables helpers |
| FIN-132 | Relates (To Do) | detect-only warning |
| FIN-81 | Relates | In-app UI; MCP interim |
| FIN-156 | Relates | `account_attribution` mapping |

## Утверждение

* **Статус:** Утверждено (2026-07-11, rev.3)
* **PO approval:** 2026-07-11
* **Следующий шаг:** «реализуй FIN-104»

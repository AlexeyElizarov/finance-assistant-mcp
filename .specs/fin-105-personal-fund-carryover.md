# MCP tool `personal_fund_carryover` — перенос остатка личного фонда после FINAL

**Связь:** [FIN-105](https://alexeielizarov.atlassian.net/browse/FIN-105); родитель [FIN-101](https://alexeielizarov.atlassian.net/browse/FIN-101); **Blocks** [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104); **Relates** [FIN-103](https://alexeielizarov.atlassian.net/browse/FIN-103), [FIN-115](https://alexeielizarov.atlassian.net/browse/FIN-115) (ledger + `mark_deducted`), [FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102) / BE-11 (backend read API), [FIN-230](https://alexeielizarov.atlassian.net/browse/FIN-230) (incoming from history in compute), [FIN-132](https://alexeielizarov.atlassian.net/browse/FIN-132) (late register — warning only), [FIN-136](https://alexeielizarov.atlassian.net/browse/FIN-136) (extended audit — follow-up).

**Домен:** формула остатка / перерасхода и удержания авансов — [household-budget-model.md](../../../assistant/35-finance-assistant/methodology/budgeting/household-budget-model.md) (§ «Остаток и перерасход личного фонда», «Аванс на базовые потребности»); ops — [ops-checklist.md](../../../assistant/35-finance-assistant/working/2026-07/ops-checklist.md).

**Статус:** Утверждено (2026-07-26, rev.4) — amend FIN-230 thin-client API path

## Назначение

После **`FINAL_CLOSED`** календарного месяца ops вручную считает остаток / перерасход личного фонда каждого партнёра и перенос на следующий месяц (фаза 4 household ops, напр. июнь → август). С [FIN-115](https://alexeielizarov.atlassian.net/browse/FIN-115) удержание авансов и запись carryover log выполняются в **фиксированном порядке** с документированным восстановлением после частичного успеха (D-04) — **не** как единая DB-транзакция; при отказе `mark_deducted` после записи log допускается промежуточное состояние, ops доводит ledger вручную.

**Критерий приёмки:** один вызов `personal_fund_carryover` после `final_closed` для `closed_period` возвращает таблицу по партнёрам (`carryover`, `advance_deduction`, `available_personal_fund` при `target_period`), флаги перерасхода > 50 €; при полном success — `mark_deducted(issue_period=closed_period)` и запись carryover log; при ошибке **до** persist log — авансы остаются `open`; при ошибке **после** persist log — см. D-04 (partial state).

## Объём и границы

### Входит в объём

* Новый MCP tool **`personal_fund_carryover`** в `mcp-servers/finance-assistant/`.
* Модуль `scripts/personal_fund_carryover.py` — расчёт, probe FIN-102, оркестрация ledger.
* Interim **carryover log** per profile: `{ASSISTANT_ROOT}/working/household/personal-fund-carryover.{profile}.json`.
* Расширение **contour mapping** (schema v1, optional block): `account_attribution` для interim partner attribution (до [FIN-36](https://alexeielizarov.atlassian.net/browse/FIN-36)).
* Интеграция с `household_advances`: `sum_open_for_issue_period`, внутренний вызов `_action_mark_deducted` + `save_ledger` (не MCP roundtrip).
* Переиспользование `household_base_share` / probe [FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102) для `base_share`.
* Проверка `methodology_status == final_closed` через reconciliation API.
* Unit-тесты: happy path, overrun flags, advance deduction + mark success, failure без mark, API probe fallback.
* Обновление `mcp-gaps.md` и schema в `server.py` после реализации.

### Не входит в объём

* [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104) (`money_check_report`) — потребитель carryover log / helpers.
* [FIN-132](https://alexeielizarov.atlassian.net/browse/FIN-132) — полная policy late register; в v1 только **warning** `late_advance_register_conflict` (detect-only).
* [FIN-136](https://alexeielizarov.atlassian.net/browse/FIN-136) — расширенные audit-поля deduction run.
* Авто-мутация plan-items / Finanzplaner (ops вручную).
* Backend persistence advances ([FIN-138](https://alexeielizarov.atlassian.net/browse/FIN-138)).
* Компенсирующие записи ([FIN-134](https://alexeielizarov.atlassian.net/browse/FIN-134)).
* Июнь 2026 «старая схема карманов» — см. **D-13** (без auto legacy mode).

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| MCP tools | `household_base_share`, `household_advances` (Done) | Нет carryover orchestration |
| Carryover | Таблица в markdown ops | Ручной расчёт, риск рассинхрона с ledger |
| FIN-115 | `mark_deducted` есть | Нет вызывающего orchestrator |
| FIN-102 / BE-11 | To Do | Interim path обязателен в v1 |
| Partner attribution | Только ops-заметки (C24 Николая) | Нет machine-readable правил |

## Обратная совместимость

Новый tool; существующие tools **не меняются**. Пустой carryover log → `incoming_carryover` для первого месяца = `0` per partner. Пустой advances ledger → `advance_deduction = 0`.

## Целевое поведение

### Формула (источник — модель)

Для закрытого месяца **M** (`closed_period`) и каждого партнёра **p**:

```
incoming_carryover[p]     = carryover log entry for (M−1)[p]  OR override param  OR 0
prior_advance_deduction[p]= Σ open advances issue_period=(M−1) for p  (typically 0 if already marked)
starting_fund[p]          = base_share(M)[p] + incoming_carryover[p] − prior_advance_deduction[p]

actual_spend[p]           = Σ attributed personal expenses in M  (interim: see D-12)
balance[p]                = starting_fund[p] − actual_spend[p]   # signed EUR, 2 dp

carryover[p]              = balance[p]   # >0 remainder, <0 overrun
overrun_amount[p]         = max(0, −balance[p])
overrun_requires_discussion[p] = overrun_amount[p] > 50.00

advance_deduction[p]      = sum_open_for_issue_period(ledger, M, partner_id=p)  # BEFORE mark
```

При переданном **`target_period`** **T** (ops указывает явно; напр. июнь → август, не обязательно следующий календарный месяц):

```
available_personal_fund[p] = base_share(T)[p] + carryover[p] − advance_deduction[p]
```

Если **`target_period` не передан** — tool рассчитывает только carryover закрытого месяца M; в корне ответа `target_period: null`; в `partners[]` поля `base_share_target` и `available_personal_fund` **отсутствуют** (D-17).

`base_share` — из `household_base_share` (или FIN-102 API). Tool **не** записывает `available_personal_fund` в план.

### Pipeline (порядок фиксирован)

```
profile, closed_period, target_period?, dry_run?, mark_advances_deducted? = parse(args)
validate_period_format(closed_period, target_period?)

status = fetch_reconciliation(closed_period)
if status.methodology_status != "final_closed" and not allow_non_final:
    → tool error

partners = load_partners(mapping)
ledger = load_ledger(advances)
carryover_log = load_or_init(carryover_log_path)

# --- compute path ---
source, api_body = probe_household_carryover_api(closed_period, target_period?)
if source == "api":
    # FIN-102 считает формулу на backend; ответ — готовые partner rows
    partners_payload = normalize_api_response(api_body)
else:
    base_M = household_base_share(period=closed_period)
    base_T = household_base_share(period=target_period) if target_period else None
    incoming = resolve_incoming_carryover(carryover_log, closed_period, override?)
    prior_adv = sum_open_for_issue_period(ledger, prev_month(closed_period))  # informational
    spend = compute_personal_spend(closed_period, partners, mapping, api)
    partners_payload = build_partner_rows(...)

advance_deduction = sum_open_for_issue_period(ledger, closed_period) per partner
merge advance_deduction + overrun flags into partners_payload
warnings = collect_warnings(late_advance_register_conflict, unmapped_spend, ...)

if dry_run:
    return { ok: true, dry_run: true, ..., advances_marked: false, log_persisted: false }

# --- persist path (only if not dry_run) ---
append_carryover_log(carryover_log, closed_period, partners_payload, audit_fields)
save_carryover_log(carryover_log_path, carryover_log)     # step A

if mark_advances_deducted and any(advance_deduction.values()):
    mark_result = mark_deducted_internal(ledger, issue_period=closed_period)  # step B
    save_ledger(advances_path, ledger)

return { ok: true, source, closed_period, target_period, partners: [...], warnings, advances_marked, log_persisted }
```

**Persist order (D-04):** операции **не атомарны** между двумя JSON-файлами. Порядок: `save_carryover_log` (**step A**) → `mark_deducted` + `save_ledger` (**step B**). Ошибка на A → B **не** вызывается, авансы `open`. Ошибка на B после A → tool error, `advances_marked: false`, `log_persisted: true` — ops повторяет `household_advances mark_deducted` вручную (runbook).

### MCP: входные параметры

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `closed_period` | string | **да** | — | Закрытый учётный месяц `YYYY-MM` |
| `target_period` | string | нет | — | Месяц для `available_personal_fund`; omit → `target_period: null` в ответе (D-17) |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из connect | URL API |
| `mapping_path` | string | нет | default contour mapping | |
| `dry_run` | boolean | нет | `false` | Только расчёт; **без** persist log и **без** `mark_deducted` |
| `mark_advances_deducted` | boolean | нет | `true` | При `dry_run=true` игнорируется (всегда false) |
| `allow_non_final` | boolean | нет | `false` | Разрешить расчёт если статус ≠ `final_closed` (warning `non_final_period`) |
| `incoming_carryover_override` | object | нет | — | Map `partner_id → EUR`; заменяет log для **starting_fund** только |

### MCP: ответ

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `ok` | bool | `true` при успехе |
| `profile` | string | |
| `base` | string | |
| `closed_period` | string | |
| `target_period` | string \| null | значение запроса или `null`, если параметр не передан (D-17) |
| `source` | string | `"api"` \| `"mapping"` |
| `methodology_status` | string | из reconciliation |
| `dry_run` | bool | |
| `advances_marked` | bool | `true` если `mark_deducted` выполнен в этом вызове |
| `log_persisted` | bool | |
| `formula` | string | каноническое уравнение для ops-чата |
| `partners` | array | см. ниже |
| `warnings` | string[] | коды предупреждений |
| `marked_advances` | object \| null | `{ issue_period, marked: [...], marked_total }` если mark выполнен |

#### Элемент `partners[]`

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `id` | string | `aleksey` / `nikolay` |
| `display_name` | string | из mapping |
| `base_share_closed` | number | `base_share` месяца M |
| `incoming_carryover` | number | из log / override / 0 |
| `starting_fund` | number | лимит на начало M (см. формулу) |
| `actual_spend` | number | факт личных трат M |
| `balance` | number | signed: +остаток / −перерасход |
| `carryover` | number | = `balance` (alias для ops) |
| `overrun_amount` | number | ≥ 0 |
| `overrun_requires_discussion` | bool | `overrun_amount > 50` |
| `advance_deduction` | number | open advances `issue_period=M` |
| `base_share_target` | number | только если `target_period` задан; иначе поле **отсутствует** |
| `available_personal_fund` | number | только если `target_period` задан; иначе поле **отсутствует** |
| `spend_lines` | array | optional breakdown `{ article, amount, source }` |

### Interim: personal spend и attribution

#### Personal expense articles (interim)

Множество статей **личных расходов** (D-14) = union:

* `legacy_irr_sanity[]`
* `personal_subscriptions_sanity[]`

Explicit block `personal_expense[]` — **вне scope** v1; расширение списков — отдельная FIN при необходимости.

**Исключения** (не личный фонд партнёра): `professional.*`, `shared_fund`, `savings`, `household_income`, `household_income.exclude`.

#### Partner attribution (interim, до FIN-36)

Optional block in `household-contour-mapping.{profile}.json`:

```json
"account_attribution": {
  "default_partner_by_provider": {
    "c24": "aleksey",
    "sparkasse-giro": "aleksey",
    "sparkasse-mastercard": "aleksey"
  },
  "description_overrides": [
    {
      "provider": "c24",
      "contains": "NIKOLAY",
      "partner_id": "nikolay",
      "reason": "c24_card_lent_to_nikolay"
    }
  ]
}
```

**Алгоритм attribution** для expense transaction **t** в M:

1. Если API вернёт `expense_owner` (post FIN-36) — использовать его.
2. Иначе: `description_overrides` (first match by provider + case-insensitive substring).
3. Иначе: `default_partner_by_provider[t.provider]`.
4. Иначе: **warning** `unattributed_spend:{transaction_key}`; сумма **не** входит в `actual_spend` (не silent assign).

**Сумма:** только `transaction_type` expense / indicator расход; учётный период = M; статья ∈ personal expense set.

### Interim carryover log (JSON v1)

Путь: `{ASSISTANT_ROOT}/working/household/personal-fund-carryover.{profile}.json`

```json
{
  "schema_version": 1,
  "profile": "prod",
  "runs": [
    {
      "closed_period": "2026-07",
      "target_period": "2026-08",
      "computed_at": "2026-08-18T10:00:00Z",
      "source": "mapping",
      "partners": {
        "aleksey": { "carryover": 120.0, "advance_deduction": 0.0, "overrun_amount": 0.0 },
        "nikolay": { "carryover": -30.0, "advance_deduction": 70.0, "overrun_amount": 30.0 }
      },
      "advances_marked": true
    }
  ]
}
```

`incoming_carryover` для M = единственная запись `runs[]` с `closed_period == prev_month(M)` → `partners[p].carryover`.

**Load validation (D-16):** при загрузке log, если для одного `closed_period` найдено **>1** записи в `runs[]` → **tool error** (`corrupt carryover log: duplicate closed_period`). Ops исправляет файл вручную; auto-merge **запрещён**.

### Probe FIN-102 / BE-11

```
GET /api/v1/household/personal-fund-carryover?closed_period=…&target_period=…
```

| HTTP | Поведение |
| ---- | --------- |
| 200 | `source: "api"`. **Backend ([FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102) + [FIN-230](https://alexeielizarov.atlassian.net/browse/FIN-230)) выполняет расчёт формулы** (включая incoming из persisted prior run) и возвращает готовые `partners[]`. MCP **нормализует** payload, **дополняет** `advance_deduction` из локального advances ledger и выполняет persist + `mark_deducted`. Query `incoming_carryover` передаётся **только** при explicit `incoming_carryover_override` (thin client; иначе omit) |
| 404 | `source: "mapping"` — полный interim pipeline; MCP резолвит incoming через cutover (API history + JSON fallback, FIN-163) и считает формулу локально |
| 5xx / timeout | tool error (без silent fallback) |

### Резолв / валидация

| Ситуация | Поведение |
| -------- | --------- |
| `closed_period` / `target_period` не `YYYY-MM` | Tool error |
| `methodology_status != final_closed` и `allow_non_final=false` | Tool error |
| `methodology_status != final_closed` и `allow_non_final=true` | Warning `non_final_period`; продолжить |
| Unknown `partner_id` in override | Tool error |
| Contour mapping missing | Tool error |
| Reconciliation API 5xx | Tool error |
| `incoming_carryover_override` + missing partner | Tool error |
| mark_deducted fails after log saved | Tool error; payload includes `log_persisted: true`, `advances_marked: false` |
| Duplicate `closed_period` in carryover log | Tool error (`corrupt carryover log`) — D-16 |
| `closed_period == "2026-06"` без `incoming_carryover_override` | Tool error — D-13; ops использует override или runbook |

### Инварианты (после успешного non-dry_run pipeline)

1. После успешного non-dry_run run carryover log содержит **ровно одну** запись `runs[]` для `closed_period` (re-run заменяет существующую).
2. После success: open advances с `issue_period=closed_period` имеют `status=deducted` (или их не было).
3. `carryover[p]` = `starting_fund[p] − actual_spend[p]` (±0.01 rounding per partner).
4. `available_personal_fund[p]` = `base_share_target[p] + carryover[p] − advance_deduction[p]` когда `target_period` задан.
5. Повторный вызов для того же `closed_period` **всегда полностью пересчитывает** carryover из текущих данных, затем upsert в log; `mark_deducted` идempotent (D-05).
6. При **load** log: если для одного `closed_period` найдено >1 записи → tool error до расчёта (D-16).

### Warning codes (enum v1)

| Code | Условие |
| ---- | ------- |
| `non_final_period` | расчёт при не-final статусе с `allow_non_final` |
| `overrun_discussion_required:{partner_id}` | `overrun_amount > 50` |
| `unattributed_spend:{transaction_id}` | транзакция не приписана партнёру |
| `late_advance_register_conflict:{issue_period}` | deducted entries exist for M, новые open за M после prior run (FIN-132 detect-only) |
| `missing_account_attribution` | block отсутствует в mapping; используются только API `expense_owner` если есть |

## Зафиксированные решения

Открытых решений нет. Все решения — D-01…D-17 (без D-03, D-15; см. rev.3).

### Pipeline

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Имя tool | `personal_fund_carryover` |
| D-02 | Advance integration | `advance_deduction` из `sum_open_for_issue_period(ledger, closed_period)` **до** mark; `mark_deducted(issue_period=closed_period)` после persist log |
| D-04 | Persist order | **Не атомарно** между log и ledger. Порядок: `save_carryover_log` → `mark_deducted` → `save_ledger`; ошибка после log — partial state, ops доводит ledger вручную |
| D-05 | Повторный run | **Полный пересчёт** из текущих данных → upsert log → идempotent `mark_deducted`; сохранённые суммы log **не** используются как результат |
| D-07 | `dry_run` | Skips log + mark; default `false` |
| D-08 | `mark_advances_deducted` default | `true`; false для preview/compare |

### Formula

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-06 | Overrun threshold | 50 EUR per model; `overrun_requires_discussion` boolean |
| D-17 | `target_period` | **Optional**; без auto next month. Omit → корень `target_period: null`; `available_personal_fund` / `base_share_target` **отсутствуют** в `partners[]` |

### Validation

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-16 | Corrupt carryover log | >1 run на `closed_period` при load → **tool error**; ops чинит файл вручную |

### Data source

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-12 | Источник `actual_spend` interim | **Transactions + `account_attribution`**; plan-fact **не** используется; совместимо с FIN-36 / FIN-104 |
| D-14 | Personal expense articles | Union `legacy_irr_sanity` + `personal_subscriptions_sanity` only; `personal_expense[]` — вне v1 |
| D-13 | Июнь 2026 legacy pockets | **Не auto**; пересчёт — `incoming_carryover_override` или runbook |

### API

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-09 | FIN-102 probe | Backend вычисляет формулу; MCP нормализует ответ и выполняет локальную обработку ledger (детали — § Probe) |

### Прочее

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-10 | Late register (FIN-132) | Warning only in v1; no auto-fix |
| D-11 | Finanzplaner | Tool read-only; no plan mutations |

## Non-goals / guardrails

* Не менять backend в FIN-105.
* Не auto-write plan-items с carryover.
* Не reopen периодов.
* Smoke — **`test`** / **`cand`**; **prod** только по явной ops-команде.
* Не дублировать FIN-115 register/list/void в этом tool.

## Чеклист тестов

* **T1:** Happy path — remainder; `carryover > 0`; log persisted; no advances → `advances_marked: false`.
* **T2:** Overrun 30 € — `overrun_amount=30`, `overrun_requires_discussion=false`.
* **T3:** Overrun 80 € — warning `overrun_discussion_required:{partner}`.
* **T4:** Open advances 70 € — `advance_deduction=70`; after success entries `deducted`.
* **T5:** Failure before log save — advances stay `open` (mock save error).
* **T6:** Log saved, mark fails — `log_persisted=true`, `advances_marked=false`.
* **T7:** `dry_run=true` — no file mutations (mock open count).
* **T8:** `target_period` — `available_personal_fund` matches formula.
* **T9:** `incoming_carryover_override` — affects `starting_fund` only.
* **T10:** `methodology_status=preliminary_closed`, `allow_non_final=false` → error.
* **T11:** API 404 — `source=mapping`; API 200 — `source=api`, backend-computed partner rows, MCP skips interim spend calc (D-09).
* **T12:** Unattributed transaction — warning, excluded from spend.
* **T13:** Idempotent second `mark_deducted` — `marked: []`.
* **T14:** Re-run keeps single run entry per `closed_period`.
* **T15:** Duplicate `closed_period` in log file → tool error on load (D-16).
* **T16:** Re-run after data change — amounts recalculated from current transactions, not prior log values (D-05).
* **T17:** Omit `target_period` — root `target_period: null`; no `available_personal_fund` / `base_share_target` in partner rows (D-17).
* **T18:** `closed_period=2026-06` without override → tool error (D-13).
* **T19:** `closed_period=2026-06` **with** `incoming_carryover_override` → success; override applied to `starting_fund` (D-13).

## Приёмочная проверка

### Предусловия

* MCP `finance_api_connect` → `data_profile` = **`test`** или **`cand`**
* Месяц в fixture с `final_closed` (или mock reconciliation)
* Contour mapping + optional `account_attribution` для профиля
* Fixture advances ledger + transactions

### A1 — carryover with advance mark

**Действие:** register advance 70 for `nikolay` issue_period=`2026-07` → `personal_fund_carryover({ closed_period: "2026-07", target_period: "2026-08", profile: "test" })`.

**Ожидаемый результат:** `advance_deduction.nikolay == 70`; `advances_marked == true`; list open = 0.

### A2 — dry run leaves ledger open

**Действие:** same with `dry_run: true`.

**Ожидаемый результат:** `advances_marked == false`; open advance unchanged.

### A3 — overrun flag

**Действие:** fixture spend exceeding fund by 55 €.

**Ожидаемый результат:** `overrun_requires_discussion == true`; warning present.

## Связь с другими FIN

| FIN | Связь | Примечание |
| --- | ----- | ---------- |
| FIN-115 | Blocks (Done) | Ledger + `mark_deducted` |
| FIN-103 | Relates | `base_share` |
| FIN-102 | Relates (BE-11) | API probe when Done |
| FIN-104 | Blocks outward | После стабилизации FIN-105 |
| FIN-132 | Relates | Warning detect-only v1 |
| FIN-136 | Relates | Audit extension follow-up |

## Утверждение

* **Статус:** Утверждено (rev.4 — FIN-230 thin-client amend)
* **Дата:** 2026-07-11 (rev.3); amend 2026-07-26 (rev.4)
* **Следующий шаг:** Done (FIN-105); API-path incoming ownership — [FIN-230](https://alexeielizarov.atlassian.net/browse/FIN-230)

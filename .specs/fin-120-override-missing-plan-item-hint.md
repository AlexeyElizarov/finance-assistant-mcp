# MCP `put_transaction_overrides` — подсказка при отсутствии plan-item в ACT

**Связь:** [FIN-120](https://alexeielizarov.atlassian.net/browse/FIN-120); родитель [FIN-92](https://alexeielizarov.atlassian.net/browse/FIN-92); **Relates** [FIN-107](https://alexeielizarov.atlassian.net/browse/FIN-107) (базовый tool), [FIN-119](https://alexeielizarov.atlassian.net/browse/FIN-119) (`create_plan_item` для IRR).

**Домен:** reconciliation overrides — [monthly-close-api/index.md](../../../assistant/35-finance-assistant/ops/index.md); prod-триггер — июнь 2026 «Прочие доходы» (IRR, `9c5a12d0-…`).

**Статус:** Утверждено (2026-07-12, rev.3)

## Назначение

При `put_transaction_overrides` на `budget_item`, который **существует**, но **не имеет plan-item** в ACT-версии, backend возвращает **422 budget item validation** с общим текстом «Укажите существующую статью бюджета.» — неясно, отсутствует ли `budget_item`, неверна версия или нет plan-row (REG/IRR). После FIN-119 ops может создать plan-item через `create_plan_item`, но текущая MCP-ошибка не подсказывает **когда** это делать.

**Критерий приёмки:** override на `budget_item` без plan-item в ACT → tool error с именем, `budget_item_id`, `planning_type` и примером `create_plan_item` (IRR без `start_period`; REG с `start_period`); успешный override без изменений.

## Объём и границы

### Входит в объём

* Расширение **`put_transaction_overrides`** в `monthly_close_lib.py`: обогащение **422 budget item validation** (см. **D-05**, helper `is_budget_item_validation_failure`).
* Helper диагностики целевых `budget_item_id` из map overrides (приоритет кандидатов — **D-03**; приоритет типов ошибок — **D-06**).
* Текст tool error на **русском** (parity API tone); примеры `create_plan_item` для `planning_type` **REG** и **IRR** (см. **D-09**).
* Unit-тесты: missing plan-item → enriched error; happy path и прочие 422 **не регрессируют**.
* Обновление description tool в `server.py` (упоминание подсказки при missing plan-item).

### Не входит в объём

* Автоматическое создание plan-item внутри override flow.
* Изменение логики merge / derive / reopen (FIN-107 без изменений).
* Обогащение других 422 (`period_closed`, flow mismatch «Статья не подходит к типу операции.») — follow-up (см. §Follow-up).
* Backend distinct 422 code — follow-up Task под FIN-92 (**D-10**); v1 — MCP-only enrichment.

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| Backend `_validate_override_items` | Три разных причины → один текст 422 | Нельзя различить без client-side lookup |
| `put_transaction_overrides()` | `RuntimeError(f"PUT reconciliation -> {status}: {body}")` | Сырой JSON без ops-подсказки |
| FIN-119 `create_plan_item` | IRR plan-item доступен | Ops не знает, что вызвать после generic 422 |
| Unit-тесты FIN-107 T5 | Любой 422 → `RuntimeError` | Нет сценария missing plan-item |

## Обратная совместимость

Успешный PUT (`2xx`) и семантика merge/derive **идентичны** FIN-107. Меняется только текст/структура **tool error** при diagnosable 422 missing plan-item. Прочие ошибки (unknown `budget_item`, inactive `budget_item`, `period_closed`, flow mismatch) — прежний `RuntimeError` с телом API или уточнённый текст без смены HTTP-семантики.

## Целевое поведение

### Pipeline / формула

```
1. session → ApiClient; resolve ACT budget_version_id (как FIN-107)
2. GET reconciliation → merge overrides → PUT (как FIN-107)
3. if PUT status == 200:
     optional derive → return ok payload (без изменений)
4. if PUT status == 422 and is_budget_item_validation_failure(put_body):  # D-05
     enriched = diagnose_put_reconciliation_budget_item_error(
       api, vid, period, overrides_arg, current_map
     )  # str | None; diagnostic failures → None (D-07), не raise
     if enriched is not None:
       raise RuntimeError(enriched)
     raise RuntimeError(f"PUT reconciliation -> {put_status}: {put_body}")
5. else:
     raise RuntimeError(f"PUT reconciliation -> {status}: {body}")
```

Псевдокод **иллюстративный**: enriched error поднимается **вне** общего `except`; сбои диагностики обрабатываются **внутри** `diagnose_*` (return `None`).

### Helper `is_budget_item_validation_failure` (D-05)

Нормализует тело PUT-ответа (`body` или `body["error"]`) → `code`, `message`.

| Условие | Результат |
| ------- | --------- |
| `code == "budget_item_not_in_version"` | `True` (future backend, **D-10**) |
| `code == "validation_error"` AND `message == "Укажите существующую статью бюджета."` | `True` (v1 prod) |
| Иное (`period_closed`, flow mismatch, прочие `validation_error`) | `False` |

Поля `details` / `loc` / error path **не** используются для match в v1.

### Диагностика причины 422

`diagnose_put_reconciliation_budget_item_error` → `str | None` (псевдокод):

```
candidate_ids = ordered_unique_budget_item_ids(
  overrides_arg, current_map, priority=D-03
)

plan_rows = GET /api/v1/budget/plan-items?budget_version_id={vid}  # once — D-08
if plan_rows GET fails → return None  # D-07

version_item_ids = {row.budget_item_id for row in plan_rows.budget_plan_items}
findings = []  # (priority_rank, candidate_index, kind, details)
best_rank = None

for index, item_id in enumerate(candidate_ids):
  try:
    item = GET /api/v1/budget/items/{item_id}
  except not_found:
    findings.append((1, index, unknown_budget_item, {}))
  except api_error:
    return None  # D-07
  else:
    if item.status != "ACT":
      findings.append((2, index, inactive_budget_item, {name, status}))
    elif item_id not in version_item_ids:
      findings.append((3, index, missing_plan_item_in_version, {name, planning_type}))

  if findings:
    best_rank = min(f[0] for f in findings)
    if best_rank == 1:
      break  # D-08: rank 1 не улучшится — досрочный выход

if not findings:
  return None

best = min(findings, key=(priority_rank, candidate_index))
return format_enriched_error(best, period)
```

**Приоритет типов ошибок (D-06):**

| Rank | `kind` | Когда |
| ---- | ------ | ----- |
| 1 | `unknown_budget_item` | `GET /budget/items/{id}` → 404 |
| 2 | `inactive_budget_item` | `budget_item.status` ≠ `ACT` |
| 3 | `missing_plan_item_in_version` | `budget_item` ACT, но `budget_item_id` ∉ plan-items версии |

Пример: unknown **A** + missing plan-item **B** → ошибка про **A** (rank 1).

**Дополнительные API-запросы (D-08):**

* `GET /budget/plan-items?budget_version_id={vid}` — **ровно один** на сессию диагностики.
* `GET /budget/items/{id}` — по кандидатам до досрочного выхода или исчерпания списка.
* **Запрещено:** повторный `GET plan-items` на каждого кандидата.
* **Досрочный выход:** если `best_rank == 1`, дальнейший обход не меняет выбранную ошибку (напр. три unknown подряд — достаточно первого GET).

**Сбой диагностики (D-07):**

Любая ошибка diagnostic GET (5xx, timeout, network, неожиданный формат) → `diagnose_*` возвращает `None`; caller отдаёт исходный `RuntimeError(f"PUT reconciliation -> {put_status}: {put_body}")`. Diagnostic exception **не** пробрасывается в tool error.

### MCP: входные параметры

Без изменений относительно FIN-107 (`period`, `overrides`, `merge`, `derive`, `profile`, `base`).

### MCP: ответ / side effects

Успешный ответ — без изменений (FIN-107).

**Шаблоны tool error** (`RuntimeError`):

**missing_plan_item_in_version** (`planning_type` = REG или IRR):

```
Статья «{name}» (budget_item_id={budget_item_id}, planning_type={planning_type})
не имеет plan-item в ACT-версии {budget_version_id}.

Создайте plan-item через create_plan_item и повторите override.

Пример (IRR):
  create_plan_item(budget_item_id="{budget_item_id}", amount=0, planning_type="IRR", forecast_method="MAN")

Пример (REG):
  create_plan_item(budget_item_id="{budget_item_id}", amount=0, start_period="{period_yyyy_mm}")
```

**missing_plan_item_in_version** (`planning_type` ∉ {REG, IRR}) — **D-09**: блок «Пример» **опускается**; hint остаётся: «Создайте plan-item через create_plan_item…».

**unknown_budget_item:**

```
budget_item не найден: budget_item_id={budget_item_id}
```

**inactive_budget_item:**

```
budget_item «{name}» не ACTIVE (budget_item_id={budget_item_id}, status={status})
```

`period_yyyy_mm` — из аргумента `period`. `amount=0` — placeholder (FIN-110 D-08).

### Резолв / валидация

| Ситуация | Поведение |
| -------- | --------- |
| PUT 422 budget item validation + missing plan-item (diagnosed) | Tool error с hint `create_plan_item` |
| PUT 422 + unknown `budget_item_id` | Tool error без hint `create_plan_item` |
| PUT 422 + inactive `budget_item` | Tool error без hint `create_plan_item` |
| PUT 422 + diagnostic API failure | Fallback: исходный PUT `RuntimeError` |
| PUT 422 + flow mismatch / period_closed / иное | Fallback: `RuntimeError` с телом API |
| PUT 200 | Success payload (FIN-107) |

Enrichment **не** применяется к 422 вне budget item validation (напр. `period_closed`, flow mismatch).

### Конфликты

Не применимо — tool не меняет map semantics.

### Инварианты (после pipeline)

1. При **200** persisted map и derive-semantics совпадают с FIN-107.
2. При diagnosable missing plan-item tool error содержит имя, `budget_item_id`, `planning_type` и hint (если REG/IRR).
3. **Не** создаётся plan-item автоматически.
4. Derive **не** вызывается при любом PUT ≠ 2xx (FIN-107).
5. При сбое диагностики ops видит **тот же** PUT error, что и до FIN-120.
6. `GET plan-items` для версии — не более **одного** вызова на enrichment.

## Открытые решения

*(Пусто.)*

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Язык tool error | Русский, parity API tone |
| D-02 | Примеры create_plan_item | IRR: без `start_period`; REG: с `start_period` = period override |
| D-03 | Priority candidate ids | Сначала `budget_item_id` из аргумента `overrides`; merged map просматривается **только если** среди явно изменённых id причина не найдена |
| D-04 | Точка enrichment | Только после failed PUT 422 (не pre-check на happy path) |
| D-05 | Match 422 для enrichment | Helper `is_budget_item_validation_failure`: `code==budget_item_not_in_version` OR (`code==validation_error` AND `message` exact match prod-текста); иначе `False` |
| D-06 | Приоритет типов при нескольких причинах | `unknown_budget_item` (1) → `inactive_budget_item` (2) → `missing_plan_item_in_version` (3); одна ошибка на вызов |
| D-07 | Сбой diagnostic GET | `diagnose_*` → `None`; caller — исходный PUT `RuntimeError`; diagnostic exception не пробрасывается |
| D-08 | Стратегия API-запросов | Один `GET plan-items`; `GET items/{id}` per candidate; досрочный выход при `best_rank==1` |
| D-09 | `planning_type` ∉ {REG, IRR} | Hint «создайте через create_plan_item» без примера вызова |
| D-10 | Backend distinct 422 code | **Вне scope v1**; follow-up Task под FIN-92 после MCP Done |

## Non-goals / guardrails

* Нет auto-create plan-item.
* Нет prod smoke в спеке — только unit + optional cand вручную ops.
* Не менять backend в v1 (**D-10**).

## Чеклист тестов

* **T1:** Mock PUT 422 budget item validation + ACT `budget_item` без plan-item, `planning_type=IRR` → enriched error с `create_plan_item`, IRR example без `start_period`.
* **T2:** Same, `planning_type=REG` → hint содержит `start_period`.
* **T3:** PUT 422 + unknown `budget_item_id` → «не найден», **без** `create_plan_item` hint.
* **T4:** PUT 200 — regression FIN-107 T1.
* **T5:** PUT 422 `period_closed` — fallback raw error; `is_budget_item_validation_failure` → `False`.
* **T6:** Handler: enriched error → derive **не** вызывается.
* **T7:** Два ids: первый missing plan-item, второй unknown → ошибка про **unknown** (D-06).
* **T7b:** Два ids: первый inactive, второй missing plan-item → ошибка про **inactive** (D-06).
* **T8:** Diagnostic `GET items` → 500 → fallback исходный PUT error (D-07).
* **T9:** Три unknown candidates → один `GET plan-items`, один `GET items` (досрочный выход, D-08).
* **T10:** `planning_type=ONCE` (или иной ∉ REG/IRR) → hint без блока «Пример» (D-09).
* **T11:** `is_budget_item_validation_failure`: flow-mismatch message → `False`; prod message → `True`.

## Приёмочная проверка

### Предусловия

* MCP `finance_api_connect` → `data_profile` = **`cand`** или **`test`**
* `budget_item` с `planning_type=IRR` **без** plan-item в ACT

### A1 — missing plan-item

**Действие:** `put_transaction_overrides({ period, overrides: { "<tx_key>": "<irr_budget_item_id>" } })`.

**Ожидаемый результат:** tool error с именем, `budget_item_id`, `planning_type=IRR`, пример `create_plan_item` без `start_period`.

### A2 — после create_plan_item

**Действие:** `create_plan_item` по hint → повтор override.

**Ожидаемый результат:** `ok: true`.

### A3 — unknown budget_item

**Действие:** `put_transaction_overrides` с несуществующим `budget_item_id`.

**Ожидаемый результат:** tool error «budget_item не найден»; **нет** substring `create_plan_item`.

## Связь с другими FIN

* **FIN-119** — prerequisite (`create_plan_item` IRR).
* **FIN-107** — базовая семантика tool.

## Follow-up (после FIN-120 Done)

1. **Backend distinct error code** (`budget_item_not_in_version`) — снять зависимость MCP от `message` fallback (**D-10**).
2. **Обогащение прочих 422** — `period_closed`, flow mismatch, inactive profile и др.
3. **Общий diagnostic helper MCP** — переиспользование enrichment pattern в других tools (не только `put_transaction_overrides`).

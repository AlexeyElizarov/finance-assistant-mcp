# MCP `create_plan_item` — поддержка IRR plan-items

**Связь:** [FIN-119](https://alexeielizarov.atlassian.net/browse/FIN-119); родитель [FIN-96](https://alexeielizarov.atlassian.net/browse/FIN-96); **Relates** [FIN-110](https://alexeielizarov.atlassian.net/browse/FIN-110) (REG bounded plan-item), [FIN-109](https://alexeielizarov.atlassian.net/browse/FIN-109) (F-03 follow-up), [FIN-120](https://alexeielizarov.atlassian.net/browse/FIN-120) (clearer override error).

**Домен:** ops-сценарий — «Прочие доходы» (`planning_type=IRR`) без plan-item в ACT → `put_transaction_overrides` 422; обход через прямой REST (июнь 2026 prod).

**Статус:** Утверждено (2026-07-10, rev.3)

## Назначение

`create_plan_item` (FIN-110) создаёт только **REG** plan-items. Статьи с `planning_type=IRR` (например «Прочие доходы») нельзя добавить в ACT-версию через MCP. Без plan-item override транзакции на IRR-статью отклоняется backend (**422**, «Укажите существующую статью бюджета»).

**Критерий приёмки:** ops создаёт IRR plan-item на **существующей** IRR-статье одним вызовом `create_plan_item` (без REST bypass); после recalculate `put_transaction_overrides` на эту статью принимается.

## Объём и границы

### Входит в объём

* Расширение MCP tool **`create_plan_item`**: POST **IRR** plan-item на существующую статью (`article` или `budget_item_id`).
* Новый helper **`build_irr_plan_item_body`** в `monthly_close_lib.py`; ветвление REG/IRR в **`create_plan_item()`**.
* Параметры MCP: `planning_type=IRR`, `forecast_method` (`MAN` / `AVG`); условная обязательность `start_period`.
* Валидация: `planning_type` совпадает со статьёй; IRR отклоняет REG-only поля; REG отклоняет `forecast_method`.
* Обновление schema/description tool в `server.py`; handler — условные required fields.
* Unit-тесты IRR (T1–T8); регрессия FIN-110 REG.
* Строка в `mcp-gaps.md`.

### Не входит в объём

* Backend API — контракт `POST /api/v1/budget/plan-items` уже поддерживает IRR.
* `create_budget_item` для IRR (FIN-109 v1 — только REG).
* `update_plan_item` для IRR (`forecast_method`) — отдельная задача при необходимости.
* `delete_plan_item` — FIN-108 F-02.
* FIN-120 (текст ошибки override) — отдельная задача.

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| `create_plan_item()` | `planning_type != REG` → `ValueError` | IRR недоступен |
| MCP schema | `start_period` required; только REG-поля | Нельзя описать IRR POST |
| `build_reg_plan_item_body` | REG body с dates/periodicity | Нет IRR builder |
| FIN-110 T5 | `planning_type=IRR` → error | Заменяется happy path FIN-119 |
| Ops июнь 2026 | Прямой `POST /api/v1/budget/plan-items` | Нарушение mcp-only |

## Обратная совместимость

Вызов на **REG-статье** без `planning_type` с `amount` + `start_period` **идентичен** FIN-110 (effective type = REG из статьи). Существующие REG-тесты FIN-110 (**T1–T4, T6–T8**) **не регрессируют**.

Изменение FIN-110 **T5** (`planning_type=IRR` → error): заменяется на IRR happy path в FIN-119 T1.

## Целевое поведение

### Pipeline / формула

```
1. session → ApiClient
2. normalize amount (≥ 0; zero allowed — FIN-110 D-08)
3. resolve article → (budget_item_id, article_name, article_planning_type)
   if both article and budget_item_id set:
     must resolve to the same budget_item_id, else tool error (D-13)
4. effective_planning_type (D-11 — BEFORE any type-specific validation):
   if user passed planning_type:
     must equal article_planning_type, else tool error
   else:
     effective = article_planning_type (D-02, D-14)
5. branch effective_planning_type:
   REG:
     require start_period; parse; optional end_period + assert_period_range
     reject forecast_method if present in arguments (D-12)
     body = build_reg_plan_item_body(...)
   IRR:
     reject start_period, end_period, periodicity if any set (D-04)
     forecast_method = arg or default MAN; must be MAN|AVG
     body = build_irr_plan_item_body(...)
6. resolve_act_version_id → assert_version_mutable
7. POST /api/v1/budget/plan-items
8. if recalculate: POST projections/recalculate (FIN-110 D-07b on fail)
9. return JSON result
```

При наличии нескольких потенциальных ошибок возвращается **первая** ошибка согласно порядку pipeline (**D-15**).

### MCP: входные параметры

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из сессии | URL API |
| `article` | string | * | — | Подстрока имени статьи |
| `budget_item_id` | string | * | — | UUID статьи |
| `amount` | string \| number | **да** | — | Сумма plan-item (≥ 0) |
| `planning_type` | string | нет | из статьи | `REG` или `IRR`; см. **D-02**, **D-14** |
| `start_period` | string | **REG: да** | — | `YYYY-MM` — начало REG |
| `end_period` | string | нет | — | `YYYY-MM` — конец REG |
| `periodicity` | string | нет | `M` | REG periodicity |
| `forecast_method` | string | нет | `MAN` | `MAN` или `AVG` (только IRR; см. **D-12**) |
| `currency` | string | нет | `EUR` | Валюта |
| `recalculate` | bool | нет | `true` | POST recalculate после plan-item |

\* **Resolve статьи:** `article` или `budget_item_id` (обязательно одно). Если переданы **оба** — см. **D-13**.

**Условная обязательность в handler:** `start_period` required только когда effective type = **REG**. Для **IRR** `start_period` **не** передаётся и **не** должно быть в arguments (иначе tool error — **D-04**).

### MCP: ответ

Как FIN-110, плюс для IRR:

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `planning_type` | string | `IRR` |
| `forecast_method` | string | `MAN` или `AVG` |
| `start_period` | omitted | Нет для IRR |
| `end_period` | omitted | Нет для IRR |

Остальные поля (`plan_item_id`, `budget_item_id`, `budget_version_id`, `article`, `amount`, `plan_item`, `recalculate`) — как FIN-110.

### POST body (IRR)

Через `build_irr_plan_item_body`:

```json
{
  "budget_version_id": "<act>",
  "budget_item_id": "<uuid>",
  "planning_type": "IRR",
  "amount": "0.00",
  "currency": "EUR",
  "status": "ACTIVE",
  "periodicity": null,
  "start_date": null,
  "end_date": null,
  "forecast_method": "MAN"
}
```

Семантика — parity UI ([period-details-screen.md](../../../PycharmProjects/FinancePlanningProject/.specs/planning/period-details-screen.md) §IRR create).

### Резолв / валидация

**Приоритет ошибок (**D-15**):** при нескольких нарушениях возвращается **первая** ошибка по порядку pipeline (шаги 2→9). Пример: `planning_type=IRR`, `start_period=2026-01`, статья REG → сначала **mismatch `planning_type`** (шаг 4), проверка IRR + `start_period` (шаг 5) **не** выполняется.

| Ситуация | Поведение | Шаг pipeline |
| -------- | --------- | ------------ |
| 0 совпадений по `article` | Tool error (как FIN-110) | 3 |
| 2+ совпадений | Tool error ambiguous | 3 |
| `article` + `budget_item_id` — разные UUID | Tool error до POST (**D-13**) | 3 |
| `planning_type` arg ≠ `article.planning_type` | Tool error до POST | 4 |
| IRR + `start_period` / `end_period` / `periodicity` | Tool error до POST (**D-04**) | 5 |
| REG + `forecast_method` (любое значение) | Tool error до POST (**D-12**) | 5 |
| REG без `start_period` | Tool error | 5 |
| `forecast_method` not in {MAN, AVG} (IRR) | Tool error | 5 |
| ARC / `can_mutate=false` | Tool error до POST | 6 |
| POST 422 (type mismatch и др.) | Tool error с телом API | 7 |

### Recalculate fail

Наследуется **FIN-110 D-07b**: plan-item **сохранён**; error payload с `plan_item_id`, `budget_item_id`, `budget_version_id`, `plan_item`.

### Инварианты (после pipeline)

1. `plan_item.planning_type` = `article.planning_type` = effective type.
2. **REG:** `periodicity`, `start_date` заданы; `forecast_method` = null.
3. **IRR:** `forecast_method` ∈ {MAN, AVG}; `periodicity`, `start_date`, `end_date` = null.
4. При `recalculate=true` и успешном POST — projection recalculate вызван ровно один раз.
5. Default REG path на REG-статье без новых args — byte-for-byte parity FIN-110 POST body (REG).

## Открытые решения

*(Пусто — все O-* закрыты в D-*.)*

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Scope tool | Расширить **`create_plan_item`** (не companion tool; закрывает O-02) |
| D-02 | `planning_type` | После resolve статьи определяется её `planning_type`. Если пользователь **не** передал `planning_type`, используется тип статьи. Если пользователь **передал** `planning_type`, он **обязан совпадать** с типом статьи, иначе tool error до POST (закрывает O-01) |
| D-03 | `forecast_method` default | **`MAN`** (parity UI) |
| D-04 | IRR + REG-only args | **Tool error** если переданы `start_period`, `end_period` или `periodicity` |
| D-05 | Period → date | REG: FIN-109 **D-05** / FIN-110 без изменений |
| D-06 | Default recalculate | `true` (FIN-110 D-05) |
| D-07 | `amount == 0` | Допустим (ops: placeholder plan-item для override) |
| D-08 | Multiple plan-items | Backend допускает несколько plan-items на статью (FIN-110 D-06) |
| D-09 | Recalculate fail | FIN-110 **D-07b** — без изменений |
| D-10 | Resolve расширение | `resolve_budget_item_id_for_plan` → tuple `(id, name, planning_type)` |
| D-11 | Порядок определения type | `effective_planning_type` определяется **до любых** type-specific валидаций (в т.ч. до проверки `start_period`) |
| D-12 | `forecast_method` при REG | Поле **запрещено** для REG **независимо от значения** — даже `forecast_method="MAN"` → tool error до POST |
| D-13 | `article` + `budget_item_id` | Если переданы оба — должны **резолвиться в один и тот же `budget_item_id`** (сравнение UUID); иначе tool error до POST |
| D-14 | `budget_item_id` без `planning_type` | Тип всё равно читается из статьи (GET item); infer по **D-02** |
| D-15 | Приоритет ошибок | При нескольких нарушениях — **первая** ошибка по порядку pipeline (шаги 2→9) |

## Non-goals / guardrails

* Не менять backend validation / domain models.
* Smoke приёмки — **`test`** или **`cand`**; prod только по явной ops-команде.
* Не автоматизировать создание IRR plan-item при override (FIN-120 — UX ошибки, не auto-fix).

## Чеклист тестов

Файл: `tests/test_create_plan_item.py` (IRR block + REG regression).

* **T1:** IRR happy path — article IRR, `amount=0`, **`forecast_method` omitted** → POST IRR body с `MAN` + recalculate (проверка **D-03**).
* **T2:** IRR `forecast_method=AVG` → POST с `AVG`.
* **T3:** Explicit `planning_type=REG` на IRR-статье → error до POST.
* **T4:** IRR + `start_period` → error до POST (**D-04**).
* **T5:** REG + `forecast_method=MAN` → error до POST (**D-12**).
* **T6:** IRR `recalculate=false` → no recalculate POST.
* **T7:** IRR recalculate fail после успешного POST → error + context (**D-09**).
* **T8:** REG bounded (FIN-110 T1) без изменений аргументов → regression.

Дополнительно:

* **T9:** `article` + `budget_item_id` — разные UUID → error (**D-13**).
* **T10:** только `budget_item_id`, IRR-статья, `planning_type` omitted → infer IRR, POST OK (**D-14**).
* **T11:** `planning_type=IRR`, `start_period` задан, статья REG → error **mismatch type** (шаг 4), не IRR+start_period (**D-15**).

Handler tests (`server._handle_create_plan_item`): IRR omit `start_period` OK; REG missing `start_period` → error.

## Приёмочная проверка

### Предусловия

* MCP `finance_api_connect` → `data_profile` = **`cand`** (или `test`).
* В каталоге есть IRR-статья дохода (например substring «Прочие доходы» или аналог на cand).
* ACT-версия mutable; у статьи **нет** ACTIVE plan-item (или тест на новую строку допустим).

### A1 — создать IRR plan-item (default `forecast_method`)

**Действие:**

```json
create_plan_item({
  "article": "<irr_income_substring>",
  "amount": "0",
  "recalculate": true
})
```

**Ожидаемый результат:** `ok: true`, `planning_type: IRR`, **`forecast_method: MAN`** (default **D-03**, поле не передавалось), `plan_item_id` present; `start_period` absent.

### A2 — override без REST bypass (cand)

**Предусловие:** A1 выполнен; есть транзакция на ту же статью, требующая override.

**Действие:** `put_transaction_overrides({ ... })` на IRR-статью.

**Ожидаемый результат:** `put_transaction_overrides` завершился успешно (`ok: true`); backend **не** вернул **422**.

### Ops reference (prod, post-deploy)

Июнь 2026 «Прочие доходы»: `create_plan_item` → `put_transaction_overrides` на P9999 return — **без** прямого REST. Выполняется ops вручную после релиза MCP; **не** gate спеки.

## Связь с другими FIN

| FIN | Связь |
| --- | ----- |
| FIN-110 | Базовый REG tool; D-07b, recalculate, resolve |
| FIN-109 | F-03 — IRR create отложен; закрывается FIN-119 для **existing** article |
| FIN-120 | Relates — улучшение текста 422 при отсутствии plan-item |

## Утверждение

* **Статус:** Утверждено (rev.3)
* **Дата:** 2026-07-10
* **Ревью PO:** rev.2 + D-15 error priority; D-13 UUID wording; A2 `ok=true`; renumber D-*

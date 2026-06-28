# MCP tool `update_plan_item` — изменение суммы плановой записи

**Связь:** [FIN-108](https://alexeielizarov.atlassian.net/browse/FIN-108) (Canonical: **MCP-22**); родитель [FIN-96](https://alexeielizarov.atlassian.net/browse/FIN-96) (EPIC-budgeting); **Relates** [FIN-103](https://alexeielizarov.atlassian.net/browse/FIN-103) (household ops — правка плана перед base share).

**Домен:** plan-items и проекция — [budget-implementation-plan.md](../../../PycharmProjects/FinancePlanningProject/.specs/planning/budget-implementation-plan.md) (фазы 4–5); plan-fact читает **`amount`** из проекции, не напрямую из plan-items; ops-пример — [2026-07-household-ops.md](../../../assistant/35-finance-assistant/methodology/2026-07-household-ops.md) (Командировки 300 € → 335 €).

**Статус:** Утверждено (2026-06-28, rev.3).

## Назначение

Изменение плановой суммы статьи (например, «Командировки (отель и проезд)» с 300 на 335 €/мес) требует `PUT /api/v1/budget/plan-items/{plan_item_id}` с **полным** телом записи и последующего пересчёта проекции. Сегодня агенты обходят [mcp-only.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/mcp-only.md) прямым REST или ad-hoc скриптами.

**Критерий приёмки:** ops меняет сумму plan-item только через MCP `finance-assistant`; после записи plan-fact / `query_plan_fact` отражает новый план (при default `recalculate=true`).

## Объём и границы

### Входит в объём

* MCP tool **`update_plan_item`**: изменить поле `amount` одной плановой записи через `PUT /api/v1/budget/plan-items/{id}` (full replace тела — MCP читает текущую запись, подставляет новый `amount`, PUT).
* Разрешение записи по **`plan_item_id`** или по **`article`** / **`budget_item_id`** + **`period`** (через `GET /api/v1/budget/projection-period-page`).
* После успешного PUT — опциональный `POST /api/v1/budget/projections/recalculate` для ACT-версии (default `true`).
* Логика в `monthly_close_lib.py` + handler в `server.py`.
* Unit-тесты (mock `ApiClient`): resolve, PUT, recalculate, guards.
* Строка в `mcp-gaps.md`; ссылка в [monthly-close-api/index.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/index.md) после Done.

### Не входит в объём

* Backend API — контракты уже есть.
* `POST` / `DELETE` plan-items — отдельные tools при необходимости (follow-up).
* Bulk-обновление нескольких plan-items или нескольких месяцев одним вызовом.
* Ручная правка ячеек проекции (`PUT /budget/projections/{id}`) — другой сценарий (manual override).
* UI Fiori / projection period page.
* Автоподбор суммы — ops передаёт явный `amount`.

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| Backend `PUT /budget/plan-items/{id}` | Full replace plan item; **нет** проверки `version.status == ARC` | MCP обязан guard сам (**D-12**) |
| Backend `POST /budget/projections/recalculate` | Пересбор проекции из plan-items | Нужен после PUT для plan-fact |
| `GET /budget/projection-period-page` | Plan items месяца + `item_name`; `can_mutate` | Есть с wave 13 |
| MCP | `query_plan_fact` — **чтение** плана | Нет мутации plan-items |
| Ops / агенты | curl / скрипты | Нарушение mcp-only |

## Целевое поведение

### Справочно: plan-item → plan-fact

* **Plan-items** (`budget_plan_items`) — исходные REG/IRR записи с `amount`, `start_date`, `end_date`.
* **Проекция** (`budget_projections`) — помесячная сетка; **plan-fact** берёт `plan_amount` из проекции ([budget_plan_actual.py](../../../PycharmProjects/FinancePlanningProject/financeplanning/budget_plan_actual.py)).
* Изменение plan-item **без** recalculate не обновляет plan-fact до следующего ручного пересчёта.

### MCP: `update_plan_item`

#### Вход

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из сессии | URL API |
| `plan_item_id` | string | * | — | UUID plan-item |
| `article` | string | * | — | Подстрока имени статьи (case-insensitive), как `resolve_budget_item_id` |
| `budget_item_id` | string | * | — | UUID статьи (альтернатива `article`) |
| `period` | string | * | — | `YYYY-MM` — месяц для resolve по `projection-period-page` |
| `amount` | string \| number | **да** | — | Новая сумма: **≥ 0** (`0` допустим); в PUT как decimal string |
| `recalculate` | bool | нет | `true` | После успешного PUT — `POST /budget/projections/recalculate` для версии записи |

\* **Resolve:** либо `plan_item_id`, либо (`period` + (`article` или `budget_item_id`)). Без `plan_item_id` поле `period` **обязательно**.

**Приоритет `plan_item_id` (**D-11**):** если `plan_item_id` задан, resolve идёт **только** по нему (`GET /budget/plan-items/{id}`). Поля `article`, `budget_item_id`, `period` **не участвуют** в resolve (допускаются как необязательная диагностика в логе/ответе, но игнорируются алгоритмом).

#### Алгоритм

1. `finance_api_connect` / сессия → `ApiClient`.
2. Нормализовать `amount` → decimal string (напр. `"335.00"`); отрицательные → tool error до HTTP; **`amount == 0` допустим**.
3. **Resolve plan-item:**
   * **`plan_item_id` задан:** `GET /api/v1/budget/plan-items/{id}` → текущее тело.
   * **Иначе:** `parse_period(period)` → `GET /api/v1/budget/projection-period-page?budget_version_id={ACT}&period={YYYY-MM-01}`.
     * Если `can_mutate == false` (версия ARC) → tool error.
     * Отфильтровать `plan_items` по `budget_item_id` (из `budget_item_id` или `resolve_budget_item_id(article)`).
     * 0 совпадений → tool error; >1 → tool error (ambiguous — ops уточняет `plan_item_id`).
4. **ARC guard (**D-12**):** признаки **`can_mutate == false`** и **`version.status == "ARC"`** — **независимые** сигналы немутируемой версии; **любой** достаточен для отказа **до PUT**. На resolve-path `can_mutate` приходит из `projection-period-page`; на обоих путях после resolve — `GET /api/v1/budget/versions/{budget_version_id}`. *(Сегодня backend вычисляет `can_mutate` как `status != "ARC"`, но MCP не синхронизирует их — проверяет каждый доступный сигнал.)*
5. Построить PUT body через `plan_item_put_body`: только поля `BudgetPlanItemOut`; **отбросить** enrichments projection-page (`item_name`, `item_flow_type`).
6. `PUT /api/v1/budget/plan-items/{id}`.
7. **Только после успешного PUT** (`2xx`): если `recalculate=true` → `POST /api/v1/budget/projections/recalculate` с `{"budget_version_id": "<из записи>"}`. При ошибке PUT recalculate **не** вызывается.
8. Вернуть JSON (см. ниже).

**REG vs IRR:** одна REG-запись с горизонтом версии меняет план **во всех** месяцах, где запись активна, после recalculate. IRR — только месяцы, покрываемые `start_date`/`end_date` записи. Tool **не** создаёт новые plan-items.

**Конкурентность:** last-write-wins (как backend plan-items JSON store); optimistic locking **не** поддерживается.

#### Выход (корень)

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `ok` | bool | `true` при успехе |
| `profile` | string | data profile |
| `base` | string | API base URL |
| `plan_item_id` | string | UUID обновлённой записи |
| `budget_item_id` | string | UUID статьи |
| `budget_version_id` | string | UUID версии записи |
| `article` | string | Имя статьи (из projection-page или GET items) |
| `amount_before` | string | Сумма до изменения |
| `amount_after` | string | Сумма после PUT |
| `plan_item` | object | Тело ответа PUT (passthrough) |
| `recalculate` | object \| omitted | Краткий итог recalculate; **отсутствует**, если `recalculate=false` |

Поле `recalculate` в ответе (если вызывался):

```json
{
  "budget_version_id": "<uuid>",
  "projection_rows": 42
}
```

`projection_rows` — число пересчитанных projection-строк в ответе recalculate; MCP извлекает count **независимо от внутреннего формата backend** (сегодня: `len(budget_projections)`; допускается будущее поле `updated_count`).

#### Ошибки

| Ситуация | Поведение |
| -------- | --------- |
| Нет `plan_item_id` и нет пары `period` + (`article` \| `budget_item_id`) | Tool error |
| `amount` отсутствует / не число / < 0 | Tool error |
| `period` не `YYYY-MM` | Tool error |
| Plan-item / статья не найдены | Tool error (404) |
| >1 plan-item на статью в месяце | Tool error (ambiguous) |
| Версия ARC (`can_mutate=false` или `version.status == ARC`) | Tool error **до PUT** |
| Валидация backend (planning_type mismatch и т.д.) | Tool error (422) |
| PUT не `2xx` | Tool error; recalculate **не** вызывается |
| Recalculate 422/5xx после успешного PUT | Tool error (**D-13**): payload **обязан** включать контекст успешного PUT — `plan_item_id`, `budget_version_id`, `amount_after`, `plan_item` — чтобы ops мог повторить recalculate без повторного PUT |

### Типовой ops-сценарий (Командировки 300 → 335 €)

Предусловия: ACT-версия; REG-запись «Командировки» на горизонте 2026.

```
1. update_plan_item({
     "period": "2026-07",
     "article": "Командировки",
     "amount": "335.00"
   })
2. query_plan_fact({ "period": "2026-07", "article": "Командировки" })
   → plan ≈ 335
3. household_base_share({ "period": "2026-07" })  — professional_total обновлён
```

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Имя tool | `update_plan_item` (MCP-22 / FIN-108) |
| D-02 | PUT semantics | Backend full replace; MCP меняет только `amount`, остальные поля из GET |
| D-03 | Resolve без id | `projection-period-page` + filter по `budget_item_id`; `period` обязателен |
| D-04 | Ambiguous match | >1 plan-item на статью в месяце → error; ops передаёт `plan_item_id` |
| D-05 | Default recalculate | `true` — иначе plan-fact не обновится |
| D-06 | Recalculate scope | Вся ACT-версия (`POST .../recalculate`), не один месяц — как backend |
| D-07 | Derive | **Не** вызывать — plan-items не затрагивают транзакции |
| D-08 | `budget_version_id` | Не в аргументах — из resolved plan-item; в **ответе** присутствует |
| D-09 | ARC guard (resolve path) | `can_mutate` на projection-period-page |
| D-09b | Немутируемая версия | `can_mutate=false` **или** `status==ARC` — независимые OR-сигналы; любой блокирует |
| D-10 | Bulk / patch line | Вне scope; REG покрывает «все месяцы строки» одним PUT |
| D-11 | `plan_item_id` precedence | Если задан — resolve **только** по id; `article`/`budget_item_id`/`period` игнорируются |
| D-12 | ARC guard (`plan_item_id` path) | Backend PUT **не** блокирует ARC; MCP после resolve проверяет `version.status` и error до PUT |
| D-13 | Recalculate failure | Exception/payload включает успешный PUT context: `plan_item_id`, `budget_version_id`, `amount_after`, `plan_item` |
| D-14 | `amount == 0` | Допустим (неотрицательная сумма) |
| D-15 | `projection_rows` | Count из ответа recalculate; `len(budget_projections)` или `updated_count` |

## Non-goals / guardrails

* Не менять backend plan-items / projections API.
* Не трогать `is_manually_adjusted` ячеек проекции напрямую.
* Не подбирать сумму эвристически.
* Не обходить ARC / validation guards.

## Чеклист тестов

* **T1:** `plan_item_id` + amount → GET + PUT с новым amount.
* **T2:** `article` + `period` → resolve один plan-item → PUT.
* **T3:** `budget_item_id` + `period` → resolve → PUT.
* **T4:** >1 plan-item на статью в месяце → tool error до PUT.
* **T5:** 0 plan-items → tool error.
* **T6:** `amount=-1` → tool error до HTTP.
* **T7:** `amount=0` → PUT с `"0"` / `"0.00"`; успех.
* **T8:** `recalculate=false` → POST recalculate не вызывается; поле `recalculate` отсутствует.
* **T9:** `recalculate=true` → один POST recalculate; `projection_rows == len(budget_projections)`.
* **T10:** PUT 422 → tool error; recalculate не вызывается.
* **T11:** `can_mutate=false` (resolve path) → tool error до PUT.
* **T12:** `plan_item_id` + версия ARC → tool error до PUT (guard по version.status).
* **T13:** invalid `period` → tool error.
* **T14:** missing resolve args → tool error.
* **T15:** `plan_item_id` + `article`/`period` → resolve только по id; PUT без projection-page.
* **T16:** recalculate fails after PUT → error payload содержит `plan_item_id`, `amount_after`, `budget_version_id`, `plan_item`.
* **T17:** `plan_item_id` valid, GET version → 404 → tool error до PUT.

**Файл:** `scripts/test_update_plan_item.py` (`unittest` + mock `ApiClient`).

**Команда:**

```bash
cd mcp-servers/finance-assistant/scripts && python -m unittest test_update_plan_item -v
```

## Приёмочная проверка

### Предусловия

* API `prod`; `finance_api_connect` → `data_profile == prod`.
* Известна статья с REG plan-item (напр. Командировки).

### A1 — по article

**Действие:** `update_plan_item` с `period`, `article`, новым `amount`.

**Ожидаемый результат:** `amount_after` совпадает; `query_plan_fact` за тот месяц — новый plan.

### A2 — по plan_item_id

**Действие:** id из `projection-period-page`; update без `article`.

**Ожидаемый результат:** PUT успешен; plan-fact обновлён после recalculate.

### A3 — mcp-only

**Действие:** сценарий §Командировки только MCP tools.

**Ожидаемый результат:** без curl и ad-hoc Python к prod API.

### A4 — REG side effect

**Действие:** обновить REG plan-item (напр. Командировки) на одном месяце; `query_plan_fact` за **другой** месяц в горизонте активности той же записи.

**Ожидаемый результат:** plan изменился **во всех** месяцах, где REG-запись активна после recalculate (не только в `period` из аргумента resolve).

### Документация (DoD FIN-108)

* `mcp-gaps.md` — tool в «Доступные»; убрать из «Открытые пробелы» при Done.
* FIN-108 References → путь к этой спеке; label `mcp-gap` снят при Done.

## E2E

| Сценарий | Изменение | Обновление |
| -------- | --------- | ---------- |
| FinancePlanningProject E2E | Нет | Нет |
| Household ops июль 2026 | MCP path для правки плана | Cross-link в 2026-07-household-ops |

## Follow-ups / Out of scope

| ID | Тема | Решение |
| -- | ---- | ------- |
| F-01 | `create_plan_item` MCP | Отдельная задача |
| F-02 | `delete_plan_item` MCP | Отдельная задача |
| F-03 | Bulk update по списку `{period, amount}` | При необходимости — новая FIN |
| F-04 | Backend ARC guard на PUT plan-items | Вне FIN-108; MCP guard достаточен для ops |
| F-05 | Push `Spec:` в Jira | ✓ при утверждении rev.2 |

## Утверждение

* **Статус:** Утверждено (реализация готова)
* **Дата:** 2026-06-28 (rev.3)
* **Следующий шаг:** приёмка A1–A4 на prod → Done FIN-108 (снять `mcp-gap`)

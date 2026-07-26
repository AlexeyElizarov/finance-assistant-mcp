# MCP tool `update_budget_item` — правка полей статьи бюджета (в т.ч. `planning_type`)

**Связь:** [FIN-227](https://alexeielizarov.atlassian.net/browse/FIN-227); родитель [FIN-96](https://alexeielizarov.atlassian.net/browse/FIN-96); **Relates** [FIN-109](https://alexeielizarov.atlassian.net/browse/FIN-109) (`create_budget_item`), [FIN-108](https://alexeielizarov.atlassian.net/browse/FIN-108) / [FIN-110](https://alexeielizarov.atlassian.net/browse/FIN-110) (`update_plan_item`), [FIN-111](https://alexeielizarov.atlassian.net/browse/FIN-111) (`delete_plan_item`), [FIN-146](https://alexeielizarov.atlassian.net/browse/FIN-146) (IRR `update_plan_item`).

**Домен:** ops-триггер 2026-07-19 — статья «Перевод карманных денег супругу» (`f40a1f06-5da1-4bb3-9de1-fec7e3140bef`) seeded as **IRR**, должна быть **REG** с bounded horizon; IRR plan-item не принимает `end_date`.

**Статус:** Утверждено (2026-07-20, rev.3)

## Назначение

Ops не может изменить `planning_type` (и другие master-поля) существующей статьи бюджета через MCP. Backend уже поддерживает `PUT /api/v1/budget/items/{id}`; MCP имеет `create_budget_item` и `update_plan_item`, но **нет** `update_budget_item`. Под mcp-only конвертация IRR→REG (статья → plan-item → bounded REG → recalculate) заблокирована.

**Критерий приёмки:** ops на профиле `cand`/`test` одним MCP-сценарием (без REST bypass) переводит статью IRR→REG и выставляет bounded REG-горизонт plan-item; `query_plan_fact` отражает новый план после recalculate.

## Объём и границы

### Входит в объём

* Новый MCP tool **`update_budget_item`**: partial update всех master-полей статьи через `PUT /api/v1/budget/items/{id}` (full replace тела — MCP читает текущую статью, мержит поля, PUT) — **D-02**.
* Optional **`convert_plan_item`**: при смене `planning_type` конвертировать ровно один ACT plan-item тем же `plan_item_id` (PUT) — **D-12**.
* Compensating **rollback** статьи при fail convert PUT — **D-04** / **D-14**.
* Guards: ARC / `assert_version_mutable`; duplicate name при rename; валидация enum `planning_type`.
* Документированный ops-путь IRR→REG для триггерной статьи (в т.ч. bounded `end_period`).
* Логика в `monthly_close_lib.py` + handler/schema в `server.py`.
* Unit-тесты (mock `ApiClient`); строка в `mcp-gaps.md` (уже есть planned — обновить после Done).

### Не входит в объём

* Backend API — контракт `PUT /budget/items/{id}` уже есть; **atomic convert endpoint не добавляем** (MCP orchestration + compensating rollback).
* `delete_plan_item` / `delete_budget_item` — **FIN-111**.
* Расширение `update_plan_item` для IRR amount/`forecast_method` — **FIN-146** (отдельно от convert `planning_type`).
* Bulk-update статей; mute/unmute версии.
* Автоподбор категории / keywords.
* Smoke / мутации на **prod** без явного ops-ok.

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| Backend `PUT /budget/items/{id}` | Full replace master-полей; **не** проверяет существующие plan-items на match `planning_type` | Нет MCP path; при convert возможен временный mismatch |
| Backend `_validate_plan_item_refs` | `planning_type` plan-item **обязан** совпадать со статьёй (на PUT/POST plan-item) | После смены типа статьи старый plan-item нельзя PUT без смены его `planning_type` |
| Backend `BudgetItem.operation_category_id` | Обязателен, non-empty; pattern `C/P/S/I` + 4 digits | Очистить категорию через `null` **нельзя** |
| MCP `update_plan_item` (FIN-108/110) | Меняет `amount` / REG horizon; **не** меняет `planning_type` plan-item | Не конвертирует IRR↔REG |
| MCP `create_plan_item` (FIN-119) | POST REG/IRR на статье с matching type | Не заменяет существующий IRR-row; ambiguous при 2 rows |
| MCP `create_budget_item` (FIN-109) | Только create REG | Не правит существующую статью |
| `apply_keywords` | PUT items только для `keywords` | Не трогает `planning_type` |
| Ops 2026-07-19 | Zero amount / REST bypass | Нарушение mcp-only; bounded retire IRR невозможен |

## Обратная совместимость

Новый tool — существующих MCP-вызовов **нет**. `create_budget_item` / `update_plan_item` / `apply_keywords` **не меняются**. Существующие unit-тесты этих tools **не регрессируют**.

## Целевое поведение

### Pipeline / формула

```
1. session → ApiClient (profile/base как у create_budget_item)
2. resolve article:
   - article и/или budget_item_id (оба → тот же id; FIN-119 D-13)
   - GET /budget/items/{id} → current_item
3. validate patch:
   - хотя бы одно master patch-поле задано
     (planning_type | name | flow_type | operation_category_id | keywords | item_status)
   - planning_type если задан: только REG|IRR
   - name если задан: strip; non-empty; duplicate guard (casefold) excl. self
   - operation_category_id если задан: non-empty string (null/"" → tool error; D-15)
   - convert_plan_item=true без фактической смены planning_type → tool error (D-12)
4. resolve_act_version_id → GET version → assert_version_mutable  # до любого PUT
5. if planning_type меняется (new != current_item.planning_type):
     GET /budget/plan-items?budget_version_id={ACT}
     candidates = plan-items where budget_item_id == id
       # только ACT version; ARC / другие версии игнорируются (D-13)
     if len(candidates) >= 1 and convert_plan_item=false:
       tool error + conflicting_plan_item_ids  # до любого PUT
     if len(candidates) > 1 and convert_plan_item=true:
       tool error (ambiguous)  # до любого PUT
     # len 0 → article-only; len 1 + convert=true → convert path
6. if convert path: GET /budget/plan-items/{id} → current_plan (для rollback context / amount default)
7. build article PUT body = current_item merged with patch; id == path
8. PUT /api/v1/budget/items/{id}  → expect 200 → updated_item
9. if convert path:
     build target plan-item body for NEW planning_type (same plan_item_id, ACT)
     PUT /api/v1/budget/plan-items/{plan_item_id}
     if PUT plan-item fail (D-14):
       PUT /budget/items/{id} с pre-convert article body (rollback)
       if rollback OK → tool error "conversion failed, changes rolled back"
         + contexts: article_before, attempted_article_after, plan_item_error
       if rollback fail → critical tool error
         + contexts: article_before, article_after (mismatch), plan_item_error, rollback_error
       recalculate НЕ вызывать в обоих случаях
     if PUT plan-item OK and effective_recalculate:
       POST /budget/projections/recalculate
10. else if effective_recalculate (explicit true only; default false without convert):
     POST recalculate
11. return JSON
```

**Факт backend (зафиксировано):** `PUT /budget/items` **не** валидирует match с plan-items → между шагами 8 и 9 возможен краткий mismatch `BudgetItem≠PlanItem`. Контракт MCP: mismatch **не** допускается как устойчивое состояние после ошибки — compensating rollback (**D-14**).

**Известное ограничение (concurrency):** compensating rollback PUT-ит сохранённый pre-convert article body. Backend budget items **не** дают optimistic locking / version token; rollback **не** защищает от конкурентной мутации той же статьи между первым PUT и rollback (last-write-wins). В scope FIN-227 не расширяем.

### MCP: входные параметры

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из сессии | URL API |
| `budget_item_id` | string | * | — | UUID статьи |
| `article` | string | * | — | Подстрока имени (resolve как `create_plan_item`) |
| `planning_type` | string | нет | — | `REG` или `IRR` |
| `name` | string | нет | — | Новое имя (trim) |
| `flow_type` | string | нет | — | `EXP` / `INC` (backend validates) |
| `operation_category_id` | string | нет | — | Код категории; **не** nullable (**D-15**) |
| `keywords` | list[string] | нет | — | Полная замена списка (как PUT body) |
| `item_status` | string | нет | — | → `status` статьи (`ACT` / `INA`); MCP не enum-check (**D-06**) |
| `convert_plan_item` | bool | нет | `false` | Только вместе с фактической сменой `planning_type` (**D-12**) |
| `amount` | string \| number | **convert→REG: да**; convert→IRR: нет | из текущего plan-item при convert→IRR | Сумма после convert (**D-16**) |
| `start_period` | string | **при convert→REG** | — | `YYYY-MM` |
| `end_period` | string | нет | — | `YYYY-MM` bounded REG (триггерный сценарий) |
| `periodicity` | string | нет | `M` | REG periodicity при convert→REG |
| `forecast_method` | string | нет | `MAN` | `MAN` \| `AVG` при convert→IRR |
| `currency` | string | нет | из текущего plan-item | Валюта при convert |
| `recalculate` | bool | нет | см. **D-05** | После полного успеха convert / явный override |

\* Resolve: `article` или `budget_item_id` (хотя бы одно). Если оба — должны резолвиться в один id (parity FIN-119 **D-13**).

**Patch semantics:** опущенное master-поле = оставить текущее значение из GET. Явная передача `keywords: []` — очистить список. `operation_category_id: null` / `""` — tool error (**D-15**).

### Convert plan-item (**D-12**)

Учитываются **только** plan-items текущей ACT budget version (`GET …/plan-items?budget_version_id={ACT}`). Plan-items ARC и любых других версий **игнорируются** (**D-13**).

| `convert_plan_item` | Смена `planning_type`? | ACT plan-items на статью | Поведение |
| ------------------- | ---------------------- | ------------------------- | --------- |
| `true` | нет | любое | Tool error (**D-12**) |
| `false` / omitted | да | ≥1 | Tool error + `conflicting_plan_item_ids` (до PUT) |
| `true` | да | 0 | Article PUT only; `converted=false` |
| `true` | да | 1 | Convert path (PUT plan-item) |
| `true` | да | >1 | Tool error ambiguous (до PUT) |
| `false` / omitted | да | 0 | Article PUT only |
| любое | нет | — | Только master patch; convert не применяется |

При convert, ровно один ACT plan-item:

| Направление | Тело PUT plan-item |
| ----------- | ------------------ |
| IRR → REG | `planning_type=REG`, `periodicity`, `start_date`/`end_date` из periods (FIN-109 period→date), `forecast_method=null`, **`amount` обязателен**, `currency`, `status` сохранить |
| REG → IRR | `planning_type=IRR`, `forecast_method` (default `MAN`), IRR shape по FIN-119 builders; **`amount`** = аргумент или текущий plan-item (**D-16**); `currency` |

Тот же `plan_item_id` (не DELETE+POST) — **не** зависит от FIN-111 (**D-09**).

### MCP: ответ / side effects

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `ok` | bool | `true` |
| `profile` | string | data profile |
| `base` | string | API base |
| `budget_item_id` | string | UUID |
| `budget_version_id` | string | ACT uuid |
| `article` | string | Имя после update |
| `planning_type_before` | string | До |
| `planning_type_after` | string | После |
| `budget_item` | object | Тело ответа PUT items |
| `plan_item_id` | string \| omitted | Если был convert |
| `plan_item` | object \| omitted | Тело PUT plan-items при convert |
| `converted` | bool | `true` если выполнен успешный convert |
| `recalculate` | object \| omitted | как FIN-108/109 |

### Резолв / валидация

| Ситуация | Поведение |
| -------- | --------- |
| Нет `article` и нет `budget_item_id` | Tool error |
| 0 match по `article` | Tool error |
| 2+ match по `article` | Tool error (ambiguous) |
| `article` + `budget_item_id` → разные id | Tool error |
| Ни одно master patch-поле не задано | Tool error |
| `planning_type` не `REG`/`IRR` | Tool error до HTTP |
| `name` пустой после trim | Tool error |
| `operation_category_id` null / empty | Tool error (**D-15**) |
| Duplicate name (другое id, casefold) | Tool error до PUT |
| ACT ARC / `can_mutate=false` | Tool error до PUT |
| `convert_plan_item=true` без смены `planning_type` | Tool error (**D-12**) |
| Смена `planning_type`, ≥1 ACT plan-item, `convert_plan_item=false` | Tool error до PUT; `conflicting_plan_item_ids[]` |
| `convert_plan_item=true`, >1 ACT plan-items | Tool error (ambiguous) до PUT |
| Convert→REG без `amount`/`start_period` | Tool error до HTTP |
| Convert→IRR с `start_period`/`end_period`/`periodicity` | Tool error (REG-only fields) |
| PUT items не 200 | Tool error; convert/recalculate не вызываются |
| PUT items OK, PUT plan-items fail, rollback OK | Tool error (**D-14**); статья = pre-convert; recalculate не вызывается |
| PUT items OK, PUT plan-items fail, rollback fail | Critical tool error (**D-14**); mismatch может остаться; оба контекста + `rollback_error` |
| Recalculate fail после полного успеха convert | Tool error с convert context (parity FIN-108 D-13); **без** rollback convert (данные уже согласованы) |

### Инварианты (после успешного pipeline)

1. `budget_item.planning_type == planning_type_after` (если patch задавал тип).
2. Если `converted=true`: `plan_item.planning_type == budget_item.planning_type` и `plan_item.budget_item_id == budget_item_id`.
3. При `converted=false`: ACT plan-items **не** изменялись этим вызовом.
4. После tool error с успешным rollback: статья снова в pre-convert состоянии; plan-item не изменён.
5. Derive **не** вызывается.
6. Recalculate — только после полного успеха мутаций и только если `effective_recalculate=true`.

## Открытые решения

*(пусто — все перенесены в D-.)*

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Backend contract | PUT body собирается из полей ответа `GET/PUT` budget item (`id`, `name`, `flow_type`, `operation_category_id`, `planning_type`, `keywords`, `status`) — тот же набор, что у Pydantic `BudgetItemOut` / `BudgetItemFields` + `id`. MCP не сериализует response DTO «вслепую» и не добавляет enrichments |
| D-02 | Patchable fields (ex O-01) | Все master-поля `BudgetItem`: `planning_type`, `name`, `flow_type`, `operation_category_id`, `keywords`, `item_status`. MCP GET → merge → full PUT |
| D-03 | ARC guard | `assert_version_mutable` до любого PUT (parity FIN-109 D-12) |
| D-04 | Partial failure policy (ex O-05) | При fail convert PUT — **compensating rollback** статьи к pre-convert body; см. **D-14**. Persistent mismatch **не** является нормальным контрактом |
| D-05 | Default `recalculate` (ex O-03) | Если `recalculate` явно передан — использовать его. Если опущен — `true` **только** при успешном `convert_plan_item`; иначе `false` (включая смену `planning_type` при 0 plan-items) |
| D-06 | `item_status` / `flow_type` enum | MCP не дублирует backend enum-check; 422 passthrough |
| D-07 | Derive | Не вызывать |
| D-08 | `budget_version_id` | Не аргумент; всегда ACT; в ответе присутствует |
| D-09 | Convert без FIN-111 | Тот же `plan_item_id` + PUT с новым `planning_type` (не DELETE) |
| D-10 | Триггерный ops-путь | См. § Приёмочная проверка A1 |
| D-11 | Имя tool (ex O-04) | `update_budget_item` |
| D-12 | Convert policy (ex O-02) | `convert_plan_item` только при фактической смене `planning_type`; 0 ACT rows → article-only; 1 → convert; >1 → ambiguous error; `convert_plan_item=true` без смены типа → error |
| D-13 | Scope plan-items | Только plan-items **текущей ACT** version; ARC и прочие версии игнорируются |
| D-14 | Rollback algorithm (ex O-05) | См. pipeline §9: GET before → PUT article → PUT plan → on plan fail: PUT article rollback; dual-error если rollback fail; recalculate только после полного успеха |
| D-15 | `operation_category_id` clear | Backend запрещает empty; MCP: `null` / `""` → tool error; смена только на другой валидный код |
| D-16 | `amount` при convert | IRR→REG: **обязателен**. REG→IRR: аргумент или текущий `plan_item.amount` |

## Non-goals / guardrails

* Не оставлять silent / «нормальный» persistent mismatch article↔plan-item (rollback обязателен при convert fail).
* Не добавлять backend atomic convert endpoint в FIN-227.
* Не добавлять optimistic locking для budget mutations (известное ограничение rollback — см. pipeline).
* Не реализовывать FIN-111 / FIN-146 в этой задаче.
* Не мутировать prod в приёмке.
* Не менять поведение `apply_keywords` / `update_plan_item`.

## Чеклист тестов

* **T1:** happy path — patch `planning_type` IRR→REG, 0 ACT plan-items → PUT items only; `converted=false`; default recalculate **не** вызывается.
* **T2:** IRR→REG, 1 ACT IRR plan-item, `convert_plan_item=false` → tool error + `conflicting_plan_item_ids`; **нет** PUT.
* **T3:** IRR→REG, 1 plan-item, `convert_plan_item=true` + `amount` + `start_period` + `end_period` → PUT items + PUT plan-items REG + recalculate (default); `converted=true`.
* **T4:** REG→IRR convert с `forecast_method=MAN`, без `amount` → amount из текущего plan-item; REG-only fields rejected.
* **T5:** ARC version → tool error до PUT.
* **T6:** rename duplicate (casefold) → error до PUT.
* **T7:** ambiguous article substring → tool error.
* **T8:** `convert_plan_item=true`, 2 ACT plan-items → tool error до PUT.
* **T9a:** PUT items OK, PUT plan-items fail, rollback OK → article restored; error message указывает rollback; recalculate не вызывается.
* **T9b:** PUT items OK, PUT plan-items fail, rollback fail → critical error с `rollback_error` + оба article context.
* **T10:** patch только `keywords` / `name` → PUT items; recalculate не вызывается (default).
* **T11:** `convert_plan_item=true` без смены `planning_type` → tool error.
* **T12:** plan-item только в ARC version, ACT пуст, смена типа без convert → article PUT OK (ARC rows ignored).
* **T13:** `operation_category_id=""` или отсутствие значения при явной передаче null-эквивалента → tool error.
* **T14:** schema/handler: tool registered; required resolve args.

**Файл:** `tests/test_update_budget_item.py` (`unittest` + mock `ApiClient`).

**Команда:**

```bash
cd mcp-servers/finance-assistant && python -m unittest tests.test_update_budget_item -v
```

## Приёмочная проверка

### Предусловия

* MCP `finance_api_connect` → `data_profile` = **`cand`** или **`test`** (не prod).
* На профиле есть IRR-статья с одним ACT IRR plan-item (или создать фикстуру через `create_plan_item` на IRR-статье).
* ACT-версия mutable.

### A1 — IRR→REG + bounded horizon (mirror prod trigger)

**Действие:**

```
update_budget_item({
  "budget_item_id": "<fixture-uuid>",
  "planning_type": "REG",
  "convert_plan_item": true,
  "amount": "<current-or-desired>",
  "start_period": "2026-01",
  "end_period": "2026-06"
})
```

**Ожидаемый результат:** `ok=true`, `planning_type_after=REG`, `converted=true`, plan-item с `end_date` = последний день 2026-06; default recalculate выполнен; `query_plan_fact` за 2026-07 → plan 0 / вне горизонта; за месяц внутри горизонта — ожидаемая сумма.

### A2 — conflict without convert

**Действие:** тот же patch без `convert_plan_item` (или `false`) при существующем ACT plan-item.

**Ожидаемый результат:** tool error; в тексте/payload есть id conflict plan-item; статья **не** изменена.

## Связь с другими FIN

| FIN | Роль |
| --- | ---- |
| FIN-109 | Create path; naming / ARC / recalculate parity |
| FIN-108 / FIN-110 | Bounded REG после convert; **не** заменяет convert `planning_type` |
| FIN-111 | Delete path — альтернатива convert при >1 rows; **не** блокер v1 при D-12 |
| FIN-146 | IRR field updates без смены типа — ортогонально |
| FIN-119 | IRR create builders reuse для convert→IRR |

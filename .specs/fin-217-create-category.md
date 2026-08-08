# MCP `create_category` — создание категории в справочнике профиля

**Связь:** [FIN-217](https://alexeielizarov.atlassian.net/browse/FIN-217); родитель [FIN-98](https://alexeielizarov.atlassian.net/browse/FIN-98); backend [FIN-214](https://alexeielizarov.atlassian.net/browse/FIN-214) Done — [fin-214-create-category-api.md](../../../PycharmProjects/FinancePlanningProject/.specs/fin/fin-214-create-category-api.md).

**Домен:** [classification.md](../../../PycharmProjects/FinancePlanningProject/.specs/transactions/classification.md); mcp-only — [mcp-only.md](../../../assistant/35-finance-assistant/ops/mcp-only.md).

**Статус:** Утверждено (2026-07-19, rev.4)

## Назначение

Backend [FIN-214](https://alexeielizarov.atlassian.net/browse/FIN-214) даёт `POST /api/v1/categories`, но MCP `finance-assistant` этого пути не экспонирует: `apply_keywords` мутирует keywords только у **существующих** id. Агенты не могут завести новую категорию в рамках mcp-only.

**Критерий приёмки (код):** одним вызовом MCP `create_category` на `cand`/`test` создаётся категория; повторный вызов с тем же id — clear tool error; `mcp-gaps.md` содержит tool в available.

**DoD (Jira):** при Done [FIN-217](https://alexeielizarov.atlassian.net/browse/FIN-217) снять label `mcp-gap` (process, не часть A2).

## Объём и границы

### Входит в объём

* Новый MCP tool **`create_category`**: тонкая обёртка над `POST /api/v1/categories`.
* Модуль/функция в `scripts/` + handler / schema в `server.py`.
* Прокидка `profile` / `base` (как у других create-tools).
* Минимальная pre-HTTP валидация (пустые обязательные поля, типы) — остальное делегировать API (**D-02**).
* Unit-тесты (mock `ApiClient`): happy path, 422 duplicate / validation, transport error.
* Обновление `mcp-gaps.md` (tool в available после ship).

### Не входит в объём

* Backend API — [FIN-214](https://alexeielizarov.atlassian.net/browse/FIN-214) / [fin-214-create-category-api.md](../../../PycharmProjects/FinancePlanningProject/.specs/fin/fin-214-create-category-api.md) (уже Done).
* Расширение `apply_keywords` для create — **нет** (**D-01**).
* `list_categories` MCP tool — вне v1 (достаточно ответа create + ops через API GET при необходимости).
* Bulk create; delete category.
* Автовыбор следующего id.
* Дублирование domain-правил id/type/default в MCP (regex, `type`↔`id[0]`, `default:true`, element types keywords) — источник правды backend (**D-02**).
* Prod smoke без явной ops-команды.
* Заведение конкретных catalog ids (`P0004`, …) — ops после ship, не код.

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| Backend `POST /categories` | FIN-214 Done | MCP не вызывает |
| MCP `apply_keywords` | Keywords add/remove на существующих id | Не создаёт категорию |
| `create_budget_item` / `create_plan_item` | Есть create-path для budget | Категории — gap |
| `mcp-gaps.md` | Нет `create_category` | Label `mcp-gap` на FIN-217 |

## Обратная совместимость

* Новый tool — additive; существующие tools **не меняют** семантику.
* `apply_keywords` без изменений.

## Целевое поведение

### Выбор инструмента (D-01)

| Вариант | Решение |
| ------- | ------- |
| Расширить `apply_keywords` секцией create | **Нет** — другой контракт (keywords ops); путает unified payload |
| Dedicated MCP tool `create_category` | **Да** |

### Pipeline

```
# create_category — FIN-217 v1

1. finance_api_connect / get_session(profile, base)
2. validate MCP args (до HTTP) → ValueError при нарушении:
   - id: present, strip → non-empty
   - type: present, strip → non-empty
   - description: present, strip → non-empty
   - keywords: если ключ есть — должен быть list (элементы не проверять); иначе []
   - default: если ключ есть — bool; иначе false
3. body := {
     "id": <stripped>,
     "type": <stripped>,
     "description": <stripped>,
     "keywords": <list as-is>,
     "default": <bool>
   }
4. POST /api/v1/categories  with body
5. success iff status == 201 AND body is dict → return JSON envelope (ниже)
6. иначе (любой status ≠ 201, в т.ч. 200/422/5xx; или body не dict)
   → RuntimeError (см. контракт ошибок)
```

Pattern id / type↔id / `default:true` / keywords element types — **не дублировать** в MCP сверх strip/presence/`isinstance(list|bool)` (**D-02**); источник правды — backend FIN-214 D-02…D-10.

### Контракт ошибок (D-06) — parity `create_budget_item`

| Класс | Когда | Тип исключения | Минимальный состав сообщения |
| ----- | ----- | -------------- | ---------------------------- |
| Pre-HTTP | пустые обязательные после strip; `keywords` не `list`; `default` не `bool` | **`ValueError`** | человекочитаемый текст поля (напр. `description is required`) |
| HTTP / unexpected body | `status != 201` **или** body не `dict` (включая 200, 422, 5xx) | **`RuntimeError`** | `POST /api/v1/categories -> {status}: {body}` — как `create_budget_item` (`POST budget/items -> …`) |
| Transport / network | `api.request` бросает (напр. `URLError`, `TimeoutError`, `OSError`) | **тот же exception** | без wrap; пробрасывать как есть |

Текст/язык тела API (часто RU) входит в `{body}` as returned — MCP **не** переводит и не вырезает `message` отдельно. Handler в `server.py` не ловит эти исключения в soft `{ok:false}` envelope (в отличие от partial-failure create_budget_item); они становятся MCP tool error.

**Успех (D-07):** только HTTP **201** и `isinstance(body, dict)`. Любой другой успешный с точки зрения HTTP status (в т.ч. **200**) — **ошибка** (`RuntimeError` по таблице выше).

### MCP: входные параметры

| Поле | Тип (schema) | Обяз. | По умолч. | Описание |
| ---- | ------------ | ----- | --------- | -------- |
| `profile` | string | нет | `prod` (**D-05**) | data profile |
| `base` | string | нет | из сессии | URL API |
| `id` | string | **да** | — | напр. `P0004` |
| `type` | string | **да** | — | `C`\|`P`\|`S`\|`I` |
| `description` | string | **да** | — | человекочитаемое имя |
| `keywords` | list[string] | нет | `[]` | schema декларирует `list[string]`; **runtime-wrapper** проверяет только `isinstance(..., list)`, элементы — schema/backend (**D-02**) |
| `default` | bool | нет | `false` | флаг default-категории; create с `true` backend отклоняет **422** (FIN-214 D-04); в FIN-217 не поддерживается |

### MCP: ответ / side effects

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `ok` | bool | `true` только при успехе **D-07** |
| `profile` | string | data profile |
| `base` | string | API base URL |
| `category` | object | тело 201 (`id`, `type`, `description`, `keywords`, `default`) |

Side effect: новая строка в справочнике профиля на сервере API.

### Резолв / валидация / ошибки

| Ситуация | Поведение |
| -------- | --------- |
| Пустой `id` / `type` / `description` после strip | `ValueError` до HTTP |
| `keywords` не list | `ValueError` до HTTP |
| `default` передан и не bool | `ValueError` до HTTP |
| HTTP ≠ 201 (422 duplicate/pattern/`default:true`/…, 5xx, **200**, …) | `RuntimeError` по **D-06** |
| 201, body не dict | `RuntimeError` по **D-06** |
| Transport / network | exception из `api.request` без wrap |
| 201 + dict | `ok: true` + `category` |

### Инварианты (после pipeline)

1. При соблюдении контракта FIN-214 успешный ответ содержит `category.id`, равный переданному stripped `id` (гарантия backend; MCP **не** сверяет id повторно).
2. Повтор с тем же id на том же profile → `RuntimeError` (API 422 в сообщении).
3. Без новых параметров у других tools поведение неизменно.
4. Успех возможен только при HTTP 201 (**D-07**).

## Открытые решения

*(пусто — все перенесены в D-.)*

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Tool shape | Dedicated `create_category`, не extend `apply_keywords` |
| D-02 | Validation split | MCP: presence/strip/`isinstance(list\|bool)`; domain (id pattern, type↔id, `default:true`, keywords elements) — backend FIN-214. Schema может декларировать `list[string]`; wrapper **не** итерирует элементы |
| D-03 | Response | `{ok, profile, base, category}` |
| D-04 | Build order | FIN-214 Done; smoke на `cand`/`test` при живом POST |
| D-05 | Default `profile` | `prod` — parity `create_budget_item` / session tools; smoke явно на `cand` |
| D-06 | Tool error contract | Pre-HTTP → `ValueError`; HTTP/body fail → `RuntimeError("POST /api/v1/categories -> {status}: {body}")`; transport → propagate |
| D-07 | Success status | Только **201** + dict body; 200 и прочие ≠ 201 — ошибка |

## Non-goals / guardrails

* Не ходить в SQLite / CLI в обход MCP после ship.
* Не создавать категории на **prod** в приёмке без явного ops-ok.
* Не тикетить в FIN заведение конкретных master-data ids после capability.

## Чеклист тестов

* **T1:** mock 201 + dict → `ok` + `category.id`.
* **T2:** empty description → `ValueError` до HTTP (POST не вызван).
* **T3:** mock 422 duplicate → `RuntimeError`; message содержит `POST /api/v1/categories -> 422` и `{body}` as returned (**D-06**).
* **T4:** keywords omit → body с `"keywords": []`.
* **T5:** default omit → body `"default": false`.
* **T6:** schema tool зарегистрирован в `server.py` list.
* **T7:** mock 422 `default:true` → `RuntimeError` (pass-through; без pre-HTTP reject кроме type).
* **T8:** `default` = `"true"` (string) → `ValueError` до HTTP.
* **T9:** `id=" C8901 "`, `type=" C "`, `description=" Test "` → POST body со stripped значениями.
* **T10:** `keywords="foo"` → `ValueError` до HTTP (POST не вызван).
* **T11:** mock `api.request` raises `URLError` (или эквивалент) → тот же exception propagates (transport).
* **T12:** mock HTTP 200 + dict → `RuntimeError` (не success; **D-07**). Отдельный 5xx не обязателен — тот же путь, что T3/T12.

## Приёмочная проверка

### Предусловия

* Backend [FIN-214](https://alexeielizarov.atlassian.net/browse/FIN-214) API доступен на целевом профиле (Done).
* MCP `finance_api_connect` → `data_profile` = **`cand`** или **`test`**.

### A1 — create end-to-end

**Повторяемость:** delete вне scope — на каждом прогоне выбирать **уникальный** свободный id (напр. `C89` + две цифры, не занятые в профиле). Фиксированный `C8901` — только пример; повтор A1 с тем же id без очистки даст duplicate и не считается регрессией smoke.

**Действие:** `create_category({ profile: "cand", id: "<unique>", type: "C", description: "FIN-217 smoke", keywords: [] })`.

**Ожидаемый результат:** `ok: true`; опционально сразу повтор с тем же id → `RuntimeError` (duplicate).

### A2 — gaps (реализация)

**Действие:** проверить `mcp-gaps.md` после ship.

**Ожидаемый результат:** строка `create_category` в available.

Снятие Jira label `mcp-gap` — **DoD при Done**, не часть A2.

## Связь с другими FIN

* [FIN-214](https://alexeielizarov.atlassian.net/browse/FIN-214) — backend POST (Done); ранее Blocks FIN-217.
* [FIN-203](https://alexeielizarov.atlassian.net/browse/FIN-203) blocked by FIN-214 (category id via API); MCP FIN-217 не обязателен для разблокировки FIN-203.

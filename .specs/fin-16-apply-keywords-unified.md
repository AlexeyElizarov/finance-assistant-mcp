# MCP `apply_keywords` — единый JSON для категорий, статей и проектов

**Связь:** [FIN-16](https://alexeielizarov.atlassian.net/browse/FIN-16); родитель [FIN-4](https://alexeielizarov.atlassian.net/browse/FIN-4); **Blocks** [FIN-14](https://alexeielizarov.atlassian.net/browse/FIN-14) (backend unified endpoint); **Relates** [FIN-34](https://alexeielizarov.atlassian.net/browse/FIN-34), [FIN-62](https://alexeielizarov.atlassian.net/browse/FIN-62).

**Домен:** C9999 proposal → keywords → derive — [c9999-proposal-policy.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/c9999-proposal-policy.md); plan-fact resolver / `ambiguous_fallback` — [FIN-34](https://alexeielizarov.atlassian.net/browse/FIN-34); runbook §3.4 — [monthly-close-api/index.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/index.md).

**Статус:** Утверждено (2026-07-08, rev.5)

## Назначение

При закрытии месяца ops добавляет keywords в **категории операций**, **статьи бюджета** и **проекты**. Сегодня `apply_keywords_file()` обновляет **только категории** (add); статьи и проекты — прямой REST, нарушение [mcp-only.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/mcp-only.md); standalone MCP tool отсутствует.

**Prod 2026-06 (триггеры):**

- **P9999 / ambiguous_fallback:** «Прочие доходы» — добавлены `Zinszahlung`, `nicht erreichbar`, чтобы отделить от fallback-статьи «Взнос Николая» ([FIN-34](https://alexeielizarov.atlassian.net/browse/FIN-34)).
- **C0011 / plan-fact conflict:** «BahnCard 25 (office week)» — удалён слишком широкий keyword `DB Vertrieb`, конфликтовавший со строкой «Deutsche Bahn (Abo …)».

**Критерий приёмки:** один JSON-файл (или inline payload) применяется через MCP без REST-обхода; `process_month.apply_keywords` и standalone tool `apply_keywords` используют **одну** библиотечную функцию; smoke на **`cand`** (паттерны P9999, C0011 transport) проходит; label `mcp-gap` снят при Done.

## Объём и границы

### Входит в объём

* Расширение **`apply_keywords_payload()`** / рефакторинг `apply_keywords_file()` в `monthly_close_lib.py`: merge keywords для `categories`, `budget_items`, `projects`.
* Standalone MCP tool **`apply_keywords`** в `server.py` (сейчас в `mcp-gaps.md указан, но **не зарегистрирован**).
* Обновление **`keywords_payload_effective()`** / `keywords_file_effective()` под unified format (см. §`effective`, `changes` и derive).
* Параметр `apply_keywords` в `process_month` — тот же parser (без дублирования логики).
* Документированный JSON-формат + пример в `methodology/monthly-close-api/examples/`.
* Unit-тесты (mock `ApiClient`): categories add; budget_items add+remove; projects add; legacy flat file; effective guard; standalone derive; partial success; idempotency; empty payload.
* `mcp-gaps.md` — уточнение описания tool; снятие `mcp-gap` при Done.

### Не входит в объём

* Backend unified endpoint — [FIN-14](https://alexeielizarov.atlassian.net/browse/FIN-14) (**Blocks**). v1 MCP — последовательные PUT; адаптер на FIN-14 — follow-up **после** Done FIN-14 (см. §Интеграция с FIN-14).
* PATCH одной категории / incremental edit API — [FIN-62](https://alexeielizarov.atlassian.net/browse/FIN-62).
* Продуктовое правило «одна fallback-статья на категорию» — [FIN-34](https://alexeielizarov.atlassian.net/browse/FIN-34).
* `put_transaction_overrides` — [FIN-107](https://alexeielizarov.atlassian.net/browse/FIN-107).
* Автоподбор статей по описанию операции.
* Bulk keywords за несколько месяцев.

## Ключевые решения (индекс)

Спорные или неочевидные пункты; детали — в соответствующих разделах ниже.

| ID | Решение | Раздел |
| -- | ------- | ------ |
| **D-01** | Legacy flat `{C0001: […]}` — бессрочно; unified — для новых файлов | §Единый JSON-формат |
| **D-06** | FIN-14: capability только через `GET /api/v1/meta` | §Интеграция с FIN-14 |
| **D-07** | Не транзакционно; partial success без rollback | §Атомарность |
| **D-12** | `effective` ≠ `changes`; derive по payload, не по факту изменений | §`effective`, `changes` и derive |

## Целевое поведение

### Единый JSON-формат (v1)

Корневой объект — **одна из двух форм** (auto-detect при parse):

#### 1. Legacy (обратная совместимость)

Плоский объект `{ "C0001": ["kw1", …], … }` — все ключи верхнего уровня = id категорий (`C…` / `P…` / `S…` / `I…`). Семантика: **только add** keywords к категориям (как сегодня).

Пример: [2026-05-keywords.json](../../../assistant/35-finance-assistant/methodology/monthly-close-api/examples/2026-05-keywords.json).

#### 2. Unified (рекомендуемый)

```json
{
  "categories": {
    "C9999": { "add": ["BRITISHWAY"] }
  },
  "budget_items": {
    "Прочие доходы": {
      "add": ["Zinszahlung", "nicht erreichbar"]
    },
    "BahnCard 25 (office week)": {
      "remove": ["DB Vertrieb"]
    }
  },
  "projects": {
    "PR001": { "add": ["Booking.com"] }
  }
}
```

**Shorthand unified** (эквивалент `{ "add": [...] }` на сущности):

```json
{
  "categories": {
    "C0001": ["LUDWIG 130301"]
  }
}
```

Оба синтаксиса на одной сущности **не смешивать** в одном файле не требуется — но оба валидны.

**Правила секций:**

| Секция | Ключ записи | Значение | HTTP |
| ------ | ----------- | -------- | ---- |
| `categories` | category id (`C0005`, …) | `string[]` (shorthand add) **или** `{ "add": string[], "remove": string[] }` — только эти ключи в object-форме | Один `PUT /api/v1/categories` (full snapshot) |
| `budget_items` | UUID **или** имя статьи (name match, см. §Payload normalization) | то же | `PUT /api/v1/budget/items/{id}` per changed item |
| `projects` | project id (`PR…`) | то же | `PUT /api/v1/projects/{id}` per changed project |

* Пустые массивы `add` / `remove` (`[]`) **допустимы**; секция может отсутствовать.
* На одной сущности операции **не схлопываются** на уровне payload: сначала все **add**, затем все **remove**; итог зависит от исходного состояния API (см. §Payload normalization).
* Неизвестный category id → `ApplyKeywordsValidationError` до PUT.
* Неизвестное / неоднозначное имя статьи → `ApplyKeywordsValidationError` с кандидатами.
* Неизвестный project id → `ApplyKeywordsValidationError`.

**Определение формата:**

| Корневые ключи | Формат |
| -------------- | ------ |
| Только `categories` / `budget_items` / `projects` (любое подмножество) | Unified |
| Только id категорий (`C…` / `P…` / `S…` / `I…`) | Legacy |
| Unified-секция **и** legacy id категории в одном корне | `ApplyKeywordsValidationError` |
| Любой другой ключ (`foo`, `accounts`, …) | `ApplyKeywordsValidationError` |

### Validation rules

Корень payload — **object** (не array). Иначе → `ApplyKeywordsValidationError`.

**Legacy entry:** значение = `string[]`; каждый элемент — `string` (non-array → ошибка).

**Unified entry** (на сущность):

* `string[]` — **shorthand add** (см. пример выше) **или**
* object с **только** ключами `add` и/или `remove` (**дополнительные поля запрещены**); каждый — `string[]` (не `null`); элементы — `string`. Пустые массивы `[]` допустимы.

| Невалидный пример | Результат |
| ----------------- | --------- |
| `"add": "abc"` | `ApplyKeywordsValidationError` |
| `"add": null` / `"remove": null` | `ApplyKeywordsValidationError` |
| `"add": [1, 2]` | `ApplyKeywordsValidationError` |
| `{ "add": [], "comment": "..." }` | `ApplyKeywordsValidationError` |
| `{ "add": [], "foo": [] }` | `ApplyKeywordsValidationError` |
| `{ "foo": [] }` на сущности | `ApplyKeywordsValidationError` |
| `{ "accounts": {} }` в корне | `ApplyKeywordsValidationError` |
| `{"categories":{}, "C0001":[]}` | `ApplyKeywordsValidationError` |

**Blank keywords** (`""`, `" "`, `"\t"`) в payload — **допустимы** (не ValidationError). Обработка — §Payload normalization.

### Атомарность и частичный успех (**D-07**)

Операция **не атомарна** между секциями и между отдельными `budget_items` / `projects`.

| Гранулярность | Поведение при ошибке |
| ------------- | -------------------- |
| `categories` | Один PUT на весь справочник — либо успех целиком, либо ошибка без изменений categories |
| `budget_items` | PUT per item — успешные items сохранены |
| `projects` | PUT per project — успешные projects сохранены |

При первой ошибке: **stop**, `ok=false`, `error` с контекстом (секция, ключ, HTTP status), `changes` — журнал **уже выполненных** реальных изменений. Rollback **не** выполняется. Ops при partial success: повторный вызов с оставшимся payload или ручной откат через UI/API.

Standalone: при `ok=false` **derive не вызывается**, даже если `derive=true`.

### Payload normalization

Применяется **до** merge с API и **до** проверки `effective`.

| Правило | Поведение |
| ------- | --------- |
| **Blank** | После validation: `""`, `" "`, … **удаляются** из списков; в API не попадают; не участвуют в `effective` ([FIN-2](https://alexeielizarov.atlassian.net/browse/FIN-2) D-03) |
| **Дедуп** в одном `add`/`remove` list | `["A","A"]` → один `A`; порядок первого вхождения |
| **Порядок на сущности** | Все `add` (после дедуп), затем все `remove` (после дедуп); **не** схлопываются заранее |
| **add + remove одного keyword** | Допустимо; итог = последовательное применение к **текущему** списку API. Пример: API `["A"]`, payload add A + remove A → `[]` → запись в `*_removed` |
| **Name match** (`budget_items` ключ) | Ключ payload и `item.name`: `strip()` + `casefold()`; exact на нормализованных строках |
| **Keyword match** | Строки keyword (payload и API) — **exact**, без normalize. `" DB "` ≠ `"DB"` |

**Идемпотентность** (после normalize, при merge с API):

| Ситуация | Результат |
| -------- | --------- |
| `add`, keyword уже в API | OK, **не** в журнале |
| `remove`, keyword нет в API | OK, **не** в журнале |

Журнал `changes` — **только реальные** persisted delta (**D-12**).

### Порядок обработки и HTTP

```
1. categories   (merge in memory → один PUT)
2. budget_items (по одному PUT на изменённую статью)
3. projects     (по одному PUT на изменённый проект)
```

Порядок секций **гарантирован**; внутри секции — порядок элементов во **входном JSON**.

**Эффективность HTTP:** не более **одного** `PUT /categories` за вызов; не более **одного** `PUT /budget/items/{id}` на статью; не более **одного** `PUT /projects/{id}` на проект — даже если в payload несколько операций на одну сущность (merge in memory, затем один PUT).

### `effective`, `changes` и derive (**D-12**)

Три поля **независимы**:

| Поле | Семантика |
| ---- | --------- |
| `effective` | Есть non-blank **add** в payload после normalize (C9999 guard, [FIN-2](https://alexeielizarov.atlassian.net/browse/FIN-2) D-03) |
| `changes` | Реально изменённые данные в API |
| derive (standalone) | По **`effective`**, не по `changes` |

**Пример (допустимо):** payload `{"categories":{"C0001":{"add":["existing"]}}}` — keyword уже в API:

```json
{
  "ok": true,
  "effective": true,
  "changes": {
    "categories_added": [],
    "categories_removed": [],
    "budget_items_added": [],
    "budget_items_removed": [],
    "projects_added": [],
    "projects_removed": []
  },
  "derive": { "...": "derive response" }
}
```

Не оптимизировать derive по пустому `changes` — derive по **payload** (`effective`), не по факту изменений (**D-12**).

### Библиотека: `apply_keywords_payload(api, payload) -> dict`

Единая точка для `apply_keywords_file`, standalone tool и `process_month`.

Возвращает журнал **реальных изменений**:

```json
{
  "categories_added": [{"category": "C0005", "keyword": "x"}],
  "categories_removed": [],
  "budget_items_added": [{"budget_item_id": "…", "name": "…", "keyword": "…"}],
  "budget_items_removed": [{"budget_item_id": "…", "name": "…", "keyword": "…"}],
  "projects_added": [],
  "projects_removed": []
}
```

`apply_keywords_file(api, path)` → `json.load` + `apply_keywords_payload`.

**Исключения** (`monthly_close_lib.py`):

```
ApplyKeywordsError
├── ApplyKeywordsValidationError   # невалидный payload, unknown id/name
└── ApplyKeywordsPartialError      # HTTP failure mid-apply; attr partial_changes: dict
```

Handler standalone / `process_month`: validation → `ok=false`, `error`; partial → `ok=false`, `changes=partial_changes`, derive не вызывается.

### Пустой payload

`{}`, `{"categories":{}}`, unified с пустыми секциями:

* HTTP PUT **не** вызываются
* `changes` — все списки `[]`
* `effective=false`
* Standalone: `ok=true`, derive **не** вызывается (даже при `derive=true`)

`process_month`: шаг keywords выполняется, `keywords_effective=false`, journal пустой. **Orchestrator derive/verify после keywords — intentionally unchanged** (всегда derive в pipeline, независимо от `effective`; guard C9999 использует `keywords_effective` отдельно).

### MCP: standalone `apply_keywords`

Standalone принимает inline `payload` (альтернатива `keywords_file`); default `derive=true`.

#### Вход

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из сессии | URL API |
| `period` | string | **да** | — | `YYYY-MM` — для derive |
| `keywords_file` | string | один из двух | — | Путь к JSON |
| `payload` | object | один из двух | — | Inline JSON |
| `derive` | bool | нет | `true` | derive после успешного apply (**D-07**) |

Ровно один из `keywords_file` / `payload` обязателен.

#### Алгоритм

1. Сессия → `ApiClient`.
2. Parse payload (file или inline).
3. `apply_keywords_payload(api, payload)` — при `ApplyKeywordsPartialError` → `ok=false`, partial `changes`, без derive.
4. Если успех и `derive=true` и `effective=true` (**D-12**) → `run_derive(api, period)`.
5. Вернуть JSON.

#### Выход (корень)

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `ok` | bool | `true` при полном успехе |
| `effective` | bool | `keywords_payload_effective` |
| `changes` | object | журнал реальных изменений |
| `error` | string | при `ok=false` |
| `derive` | object \| omitted | тело derive; нет при ошибке, `derive=false` или пустом payload |

### `process_month` (без новой бизнес-логики)

Существующий шаг `apply_keywords` вызывает тот же parser / `apply_keywords_payload`. Guard [FIN-2](https://alexeielizarov.atlassian.net/browse/FIN-2) использует `keywords_effective`. Порядок orchestrator (import → keywords → **derive** → verify → close) **не меняется** в FIN-16 — в отличие от standalone, где derive зависит от `effective`.

Типовой сценарий после C9999 proposal:

```json
process_month({
  "period": "2026-06",
  "skip_import": true,
  "apply_keywords": "working/monthly-close-api/2026-06-keywords-unified.json"
})
```

### Интеграция с FIN-14 (после backend Done)

При наличии capability в `GET /api/v1/meta` (**D-06**) — `apply_keywords_payload` использует unified ops endpoint FIN-14. Иначе — v1 multi-PUT. Derive: один раз из ответа backend на ops-path; MCP не дублирует.

Детали контракта — спека FIN-14; amend этой спеки после утверждения FIN-14.

## Тесты (минимум)

| ID | Сценарий |
| -- | -------- |
| T01 | Legacy flat `{C0001: ["x"]}` → categories_added, PUT categories |
| T02 | Unified budget_items add by name → PUT item |
| T03 | Unified budget_items remove only → changes.removed, `effective=false` |
| T04 | Ambiguous budget item name → `ApplyKeywordsValidationError` |
| T05 | Standalone + `derive=false` → no derive call |
| T06 | `process_month` + unified file → journal in log.steps |
| T07 | Unified categories add + budget_items add → `effective=true` |
| T08 | Partial success: categories OK, second budget_item PUT fails → `ok=false`, first item in changes, no derive |
| T09 | Idempotent add: keyword exists → not in `categories_added` |
| T10 | Idempotent remove: keyword missing → not in `*_removed` |
| T11 | Empty `{}` → `ok=true`, `effective=false`, no PUT, no derive |
| T12 | Call order: categories PUT before budget_items PUTs |
| T13 | Invalid payload (`add` not array) → `ApplyKeywordsValidationError` |
| T14 | Root `{"foo":{}}` → `ApplyKeywordsValidationError` |
| T15 | Mixed `{"categories":{},"C0001":[]}` → `ApplyKeywordsValidationError` |
| T16 | Duplicate `add:["A","A"]` → single apply |
| T17 | API `["A"]`, add A + remove A → `categories_removed`, not added |
| T18 | Idempotent add: `effective=true`, empty `changes`, derive called |
| T19 | `"add": null` → `ApplyKeywordsValidationError` |
| T20 | Blank `" "` in add → ignored, not in API, not effective alone |
| T21 | Unified shorthand `categories.C0001: ["x"]` → same as `{add:["x"]}` |
| T22 | Extra field `{add:[], comment:"x"}` → `ApplyKeywordsValidationError` |

## Smoke (`cand`, после реализации)

Норма: [index.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/index.md) § «Проверка реализации» — **не `prod`**.

1. На **`profile: cand`**: keywords на «Прочие доходы» (P9999 pattern) → derive → plan-fact: `ambiguous_fallback` снят для Zinszahlung-операций.
2. На **`profile: cand`**: remove `DB Vertrieb` с BahnCard → derive → plan-fact conflict снят.
3. Без прямого `curl` / ad-hoc Python к API.

## Definition of Done (синхрон с Jira)

- [x] Standalone MCP `apply_keywords` зарегистрирован и описан в `mcp-gaps.md`
- [x] Unified JSON с `budget_items` документирован + example
- [x] `process_month.apply_keywords` использует общий parser
- [x] Unit-тесты T01–T22
- [x] Smoke на `cand` (механика P9999/C0011; plan-fact effects — cand без транзакций/BahnCard, 2026-07-09)
- [x] Label `mcp-gap` снят; FIN-16 → Done (2026-07-09)

## Связанные документы

| Документ | Действие после Done |
| -------- | ------------------- |
| [c9999-proposal-policy.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/c9999-proposal-policy.md) | ссылка на standalone `apply_keywords` + unified example |
| [index.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/index.md) | пример unified JSON |
| [examples/](../../../assistant/35-finance-assistant/methodology/monthly-close-api/examples/) | `2026-06-keywords-unified.json` (illustrative) |

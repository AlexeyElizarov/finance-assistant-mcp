# MCP `put_transaction_category` — коррекция `transaction_type` + категории

**Связь:** [FIN-211](https://alexeielizarov.atlassian.net/browse/FIN-211); родитель [FIN-26](https://alexeielizarov.atlassian.net/browse/FIN-26); **Relates** [FIN-202](https://alexeielizarov.atlassian.net/browse/FIN-202) (Done — backend `PATCH …/category`); контракт D-01…D-11 — [fin-202-patch-transaction-type.md](../../../PycharmProjects/FinancePlanningProject/.specs/fin/fin-202-patch-transaction-type.md); lookup — [fin-27-query-transactions-filters.md](fin-27-query-transactions-filters.md), [fin-17-list-c9999.md](fin-17-list-c9999.md).

**Домен:** ops classification — [classification.md](../../../PycharmProjects/FinancePlanningProject/.specs/transactions/classification.md); mcp-only — [mcp-only.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/mcp-only.md).

**Статус:** Утверждено (2026-07-19, rev.2)

> **Amend (FIN-241):** surface tool расширен опциональным `expense_owner` (set/clear, owner-only) и additive `expense_owner` в `query_transactions` rows. Контракт — [fin-241-expense-owner-mcp.md](fin-241-expense-owner-mcp.md). FIN-211 Done не пересматривается.

## Назначение

[FIN-202](https://alexeielizarov.atlassian.net/browse/FIN-202) (**Done**) позволяет одним `PATCH /api/v1/transactions/{id}/category` задать `transaction_type` вместе с совместимой непустой `transaction_category` (`category_source=manual`). MCP `finance-assistant` этого пути не экспонирует: `put_transaction_overrides` пишет map `transaction_key` → `budget_item_id` в reconciliation (**другой домен**); агенты вынуждены raw HTTP или оставлять mis-typed строки.

**Критерий приёмки:** одним вызовом MCP на `test`/`cand` ops задаёт `transaction_type` + совместимую непустую категорию на строке; несовместимая пара / type-only / derived / clear→pending → clear tool error **без** частичной записи; поведение совпадает с FIN-202 D-01…D-11; `mcp-gaps.md` обновлён.

## Объём и границы

### Входит в объём

* Новый MCP tool **`put_transaction_category`**: тонкая обёртка над `PATCH /api/v1/transactions/{transaction_id}/category` для сценария FIN-202 (type + category).
* Модуль/функция в `scripts/` (напр. `put_transaction_category` в новом файле или рядом с transaction helpers) + handler / schema в `server.py`.
* Проброс `allow_closed` и опционального `reconciliation_note` (FIN-202 D-06, D-10).
* Unit-тесты (mock `ApiClient`): happy path (FIN-202 D-09), mismatch 422, type-only, closed period, note atomic, lowercase type; T11 lookup fields.
* Обновление `mcp-gaps.md` (tool в available после Done).
* Минимальный lookup для mcp-only: additive `id` и `transaction_type` в неагрегированных rows `query_transactions` (**D-09**).

### Не входит в объём

* Backend API — FIN-202 (**Done**); без новых endpoint’ов.
* Расширение `put_transaction_overrides` (reconciliation map) — **не** подходит по контракту (**D-01**).
* Полный surface FIN-87/FIN-74 через MCP (category-only, clear→pending, `category_source=derived`, note-only) — вне FIN-211 (**D-10**); v1 **требует** type + non-empty category.
* Поле `transaction_key` в ответе `query_transactions` — **не** в FIN-211 (**D-09**).
* Resolve по `transaction_key` без UUID — вне scope (**D-11**).
* Bulk коррекция — [FIN-213](https://alexeielizarov.atlassian.net/browse/FIN-213).
* UI — [FIN-212](https://alexeielizarov.atlassian.net/browse/FIN-212).
* Prod smoke без явной ops-команды.

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| Backend `PATCH …/category` | FIN-202: optional `transaction_type` + compatible category | Нет MCP-обёртки |
| `put_transaction_overrides` | `PUT …/reconciliation` overrides | Другой домен; type/category не трогает |
| `query_transactions` rows | `date`, `amount`, `indicator`, `category`, `provider`, `description` | Нет `id` (FIN-27 **D-05** out of scope) — сложно найти UUID для PATCH |
| `list_c9999` | Отдаёт `id` | Только C9999 expense; mis-typed `I`/`P`/`S` не покрывает |
| `mcp-gaps.md` | Нет tool для type+category | Label `mcp-gap` на FIN-211 |

## Обратная совместимость

* Новый tool — additive; существующие tools **не меняют** семантику при отсутствии новых полей.
* `query_transactions` получает **additive** поля `id` и `transaction_type` в неагрегированных rows (**D-09**); существующие поля и фильтры **без изменений**; вызовы без опоры на новые ключи не ломаются. Поле `transaction_key` **не** добавляется.
* Полный FIN-87 surface через этот tool **не** открывается в v1 (**D-10**) — нет регрессии «тихого» clear/derived.

## Целевое поведение

### Выбор инструмента (D-01)

| Вариант | Решение |
| ------- | ------- |
| Расширить `put_transaction_overrides` | **Нет** — reconciliation `budget_item_id`, не classification |
| Dedicated MCP tool | **Да** — `put_transaction_category` |

### Pipeline

```
# put_transaction_category — FIN-211 v1 (FIN-202 happy path)

1. finance_api_connect / get_session(profile, base)
2. validate MCP args:
   - transaction_id: non-empty strip
   - transaction_type: present (нормализация/enum — backend FIN-202 D-01; MCP может
     передать as-is после strip; пустой после strip → tool ValueError до HTTP)
   - transaction_category: non-empty after strip (иначе ValueError до HTTP)
   - category_source: **не принимать** в v1 (ключ запрещён / ignored → см. D-04)
3. body := {
     "transaction_type": <stripped arg>,
     "transaction_category": <stripped arg>
   }
   if reconciliation_note in args (ключ присутствует):
     body["reconciliation_note"] = <value as passed; null/"" семантика FIN-74 через API>
4. path := /api/v1/transactions/{transaction_id}/category?allow_closed={true|false}
5. status, resp := PATCH path body
6. if status == 200 → ok:true + transaction row fields from resp
   else → ok:false / raise tool error с телом API (422 validation_error / period_closed);
          запись не применена (атомарность API FIN-202 / FIN-74 D-14)
```

Клиентская pre-validation (шаг 2) отклоняет type-only / empty category **до** HTTP; остальные семантические 422 (mismatch, derived если когда-либо пройдёт, invalid type) — от API без дублирования всех D-01…D-11 сообщений в MCP.

### MCP: вход

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из сессии | URL API |
| `transaction_id` | string | **да** | — | UUID строки (`GET /transactions` → `id`) |
| `transaction_type` | string | **да** | — | `C`/`P`/`S`/`I` (strip; регистр — FIN-202 D-01) |
| `transaction_category` | string | **да** | — | Непустой id категории, совместимый с типом |
| `reconciliation_note` | string \| null | нет | — | Если ключ передан — в body (FIN-202 D-10) |
| `allow_closed` | bool | нет | `false` | Query `allow_closed` (FIN-202 D-06) |

**Не принимать в v1:** `category_source` (FIN-202 D-09: отсутствие ключа → implicit `manual` на backend).

### MCP: ответ (успех)

```json
{
  "ok": true,
  "profile": "cand",
  "base": "http://127.0.0.1:8000",
  "transaction": {
    "id": "<uuid>",
    "transaction_type": "P",
    "transaction_category": "P0002",
    "category_source": "manual",
    "classification_status": "classified",
    "reconciliation_note": ""
  }
}
```

Минимальный набор полей `transaction` — перечисленные выше; прочие поля `TransactionRowApi` **можно** пробросить as-is из ответа API (не контракт v1, кроме перечисленных).

### Ошибки

| Ситуация | Результат |
| -------- | --------- |
| Нет / пустой `transaction_id` / type / category после strip | `ValueError` / tool error **до** HTTP |
| `category_source` передан | `ValueError` (v1 forbid) — **D-04** |
| API 422 mismatch / invalid type / type-only edge | Tool error; тело/сообщение API; строка не изменена |
| API 422 `period_closed` (`allow_closed=false`) | Tool error; без записи |
| API 404 unknown id | Tool error |
| Неизвестный `profile` / нет сессии | Как у других tools |

### Инварианты

1. Успешный вызов меняет **и** `transaction_type`, **и** `transaction_category`; `category_source=manual`; `classification_status=classified`.
2. При любой ошибке валидации/API — **нет** частичной записи (type без category и наоборот).
3. v1 **не** открывает derived / clear→pending / type-only через MCP.
4. `allow_closed` default `false` — как API.
5. Семантика type/category — **только** FIN-202; MCP не ослабляет match `category.type == effective_type`.

### Lookup: `query_transactions` (D-09)

Чтобы mcp-only сценарий «найти строку → исправить тип» не требовал raw HTTP:

| Поле row (additive) | Источник API | В FIN-211 |
| ------------------- | ------------ | --------- |
| `id` | `TransactionRowApi.id` | **да** |
| `transaction_type` | `TransactionRowApi.transaction_type` | **да** |
| `transaction_key` | `TransactionRowApi.transaction_key` | **не входит** |

Существующие поля row **сохраняются**. `group_by=month` — без изменений (агрегаты без id).

`list_c9999` уже отдаёт `id` — для C9999 path отдельное изменение не нужно.

## Открытые решения

Нет.

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Extends overrides vs dedicated | Dedicated tool `put_transaction_category`; **не** трогать `put_transaction_overrides` |
| D-02 | Контракт | FIN-202 D-01…D-11: type только с non-empty compatible category; implicit manual; `allow_closed`; atomic note |
| D-03 | Обязательные MCP args | `transaction_id`, `transaction_type`, `transaction_category` |
| D-04 | `category_source` в MCP v1 | Не принимать (forbid); backend FIN-202 D-09 |
| D-05 | Pre-validation | Пустые id/type/category после strip → error до HTTP; семантика mismatch/enum — API |
| D-06 | Имя tool | `put_transaction_category` |
| D-07 | Backend | Без изменений (FIN-202 Done) |
| D-08 | mcp-gaps | Добавить tool в available при Done; снять `mcp-gap` с FIN-211 |
| D-09 | Lookup rows | `query_transactions` возвращает additive `id` и `transaction_type` для неагрегированных rows; существующие поля и фильтры без изменений; `group_by=month` без изменений; `transaction_key` **не** добавляется |
| D-10 | Surface v1 | `put_transaction_category` v1 — только совместная установка `transaction_type` + непустой `transaction_category`; полный FIN-87 surface вне FIN-211 |
| D-11 | Identity | Идентификация транзакции в v1 только через `transaction_id`; resolve по `transaction_key` вне scope |

## Non-goals / guardrails

* Не менять семантику FIN-202 / FIN-87 на backend.
* Не смешивать classification PATCH с reconciliation overrides.
* Не делать bulk / UI в этом тикете.
* Не smoke на prod без явной команды.

## Чеклист тестов

* **T1:** Happy path (FIN-202 D-09) — `transaction_type=P` + `P0002` без `category_source` → `ok`; row `P` / `P0002` / `manual` / `classified`.
* **T2:** Lowercase `transaction_type="p"` + совместимая `P*` → успех, тип `P` (API).
* **T3:** Mismatch `P` + `C0001` → tool error; mock API не считает успешный persist (или 422 body).
* **T4:** Пустой / whitespace `transaction_category` → error **до** HTTP.
* **T5:** Отсутствует `transaction_type` → error до HTTP.
* **T6:** `category_source` в args → error (D-04).
* **T7:** `allow_closed=false` + API `period_closed` → tool error.
* **T8:** `allow_closed=true` + closed period → PATCH с `allow_closed=true`, успех (mock).
* **T9 (FIN-202 D-10):** type + category + `reconciliation_note` → все три в body; успех.
* **T10:** Handler schema: required fields; default `allow_closed=false`.
* **T11 (D-09):** `query_transactions` row содержит `id` и `transaction_type`; **без** `transaction_key`; старые поля на месте; `group_by=month` без регрессии.

## Приёмочная проверка

### Предусловия

* MCP `finance_api_connect` → `data_profile` = **`test`** или **`cand`** (не prod).
* Есть строка с заведомо неверным типом (или тестовая `I` + целевая `P*`).
* Известен `transaction_id` (через `query_transactions` **D-09**, или `list_c9999`).

### Метаданные прогона

* Дата: ______
* Исполнитель: ______
* Commit: ______

### A1 — Коррекция типа + категории

**Действие:**

```json
put_transaction_category({
  "profile": "cand",
  "transaction_id": "<uuid>",
  "transaction_type": "P",
  "transaction_category": "P0002"
})
```

**Ожидаемый результат:** `ok: true`; `transaction_type=P`; `transaction_category=P0002`; `category_source=manual`; `classification_status=classified`.

### A2 — Mismatch

**Действие:** `transaction_type=P` + категория типа `C`.

**Ожидаемый результат:** tool error; строка в БД не изменена.

## Связь с другими FIN

| FIN | Роль |
| --- | ---- |
| FIN-202 | Backend контракт (**Done**) |
| FIN-27 | `query_transactions`; FIN-27 D-05 откладывал `id` — закрыто **D-09** в FIN-211 |
| FIN-17 | `list_c9999` уже отдаёт `id` |
| FIN-107 | Overrides — другой домен; не расширяем |
| FIN-212 | UI type+category — out of scope |
| FIN-213 | Bulk type+category — out of scope |
| [FIN-215](https://alexeielizarov.atlassian.net/browse/FIN-215) | Follow-up: полный FIN-87/FIN-74 surface (**D-10**) |
| [FIN-216](https://alexeielizarov.atlassian.net/browse/FIN-216) | Follow-up: identity по `transaction_key` + expose в `query_transactions` (**D-09** / **D-11**) |

## История ревизий

| Дата | Rev | Изменение |
| ---- | --- | --------- |
| 2026-07-19 | rev.1 | Intake: dedicated tool; FIN-202 Done; O-01…O-03 |
| 2026-07-19 | rev.2 | O-01…O-03 → D-09…D-11; `transaction_key` явно вне scope; Approved |

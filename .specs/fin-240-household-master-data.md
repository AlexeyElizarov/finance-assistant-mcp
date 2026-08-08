# MCP household master data — list/upsert household, members, bank accounts

**Связь:** [FIN-240](https://alexeielizarov.atlassian.net/browse/FIN-240); родитель [FIN-101](https://alexeielizarov.atlassian.net/browse/FIN-101); **Blocks** [FOPS-8](https://alexeielizarov.atlassian.net/browse/FOPS-8); **Relates** [FIN-36](https://alexeielizarov.atlassian.net/browse/FIN-36), [FIN-231](https://alexeielizarov.atlassian.net/browse/FIN-231).

**Домен:** backend — [fin-36-master-data-contour.md](../../../PycharmProjects/FinancePlanningProject/.specs/fin/fin-36-master-data-contour.md); coverage — [fin-231-import-coverage-from-bank-accounts.md](../../../PycharmProjects/FinancePlanningProject/.specs/fin/fin-231-import-coverage-from-bank-accounts.md); mcp-only — [mcp-only.md](../../../assistant/35-finance-assistant/ops/mcp-only.md).

**Статус:** Утверждено (2026-07-26, rev.2)

## Назначение

FOPS ops — MCP-only. После FIN-36 / FIN-231 prod нужен seeded household contour (active household, members, bank accounts с `statement_expected` / `final_close_only`), иначе close/readiness небезопасны. REST FIN-36 уже есть; MCP-обёртки нет → [FOPS-8](https://alexeielizarov.atlassian.net/browse/FOPS-8) блокирован без нарушения mcp-only.

**Критерий приёмки (FIN-240):** на `cand`/`test` через MCP: upsert household + members + bank accounts → REST persistence → MCP list round-trip совпадает с записанным; без CLI/SQL.

**Условный integration smoke (не Done FIN-240):** если FIN-231 deployed на том же профиле — дополнительно подтвердить, что readiness/coverage видит созданный contour. Падение/отсутствие FIN-231 **не** блокирует Done FIN-240.

## Объём и границы

### Входит в объём

* Шесть MCP tools (тонкие обёртки над FIN-36 REST) — **D-01**:
  * `list_households` — `GET /api/v1/households`
  * `upsert_household` — `PUT /api/v1/households/{id}`
  * `list_household_members` — `GET /api/v1/households/{id}/members`
  * `upsert_household_member` — `PUT /api/v1/households/{id}/members/{member_id}`
  * `list_bank_accounts` — `GET /api/v1/households/{id}/bank-accounts`
  * `upsert_bank_account` — `PUT /api/v1/households/{id}/bank-accounts/{account_id}`
* Модуль `scripts/households.py` + handlers / schema в `server.py` (**D-02**).
* Проброс `profile` / `base` (как у `list_fx_rates` / `upsert_fx_rate`).
* Минимальная pre-HTTP валидация (presence/strip path ids и required body fields; типы bool где нужно) — domain-инварианты FIN-36 **не дублировать** (**D-03**).
* Семантика omit vs null для optional body — как FIN-36 D-16a: ключ отсутствует → omit из JSON; `null` → отправить `null` (**D-04**); технически через сохранение presence (**D-09**).
* Unit-тесты (mock `ApiClient`): happy path list/upsert + 422/404 passthrough + omit/null **через MCP handler/schema path** (**D-09**).
* Обновление `mcp-gaps.md` (tools в available после ship).

### Не входит в объём

* Backend REST / миграция / инварианты D-03a–D-07 — [FIN-36](https://alexeielizarov.atlassian.net/browse/FIN-36) (уже реализовано).
* Изменение Close / `import_coverage` — [FIN-231](https://alexeielizarov.atlassian.net/browse/FIN-231).
* Prod seed данных (конкретные id/имена счетов) — [FOPS-8](https://alexeielizarov.atlassian.net/browse/FOPS-8); эта задача только capability.
* Seed CLI / SQL / прямой доступ к `finance.db`.
* `DELETE` lifecycle / BankStatement — FIN-233.
* OpenUI5 master-data screens.
* Atomic switch-active-household.
* Один mega-tool с `action` enum (**отклонён** — см. O-01 → D-01).
* Prod smoke без явной ops-команды.
* Обязательная зависимость Done от FIN-231 readiness behavior.

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| REST FIN-36 | GET/PUT households, members, bank-accounts | Нет MCP |
| MCP `finance-assistant` | Нет tools contour master data | FOPS-8 невозможен mcp-only |
| `mcp-gaps.md` | Нет list/upsert household* | Label `mcp-gap` на FIN-240 |
| Seed prod | Нельзя без CLI/SQL или UI | Нарушает mcp-only |

## Обратная совместимость

* Шесть новых tools — **additive**; существующие MCP tools **не меняют** семантику.
* Backend FIN-36 контракт не меняется; MCP не изобретает новые поля/пути.

## Целевое поведение

### Pipeline (общий для всех шести tools)

```
1. finance_api_connect / get_session(profile, base)
2. validate MCP args (до HTTP) → ValueError при нарушении (см. таблицы)
3. собрать path + body (**D-09**):
   - path ids из аргументов (stripped)
   - body: только ключи, присутствующие в исходном validated argument model
     (`model_fields_set` / `exclude_unset=True` или эквивалент)
   - значение null для optional nullable → JSON null
   - запрещено: Python default None как одновременно «не передано» и «explicit null»
4. HTTP GET или PUT /api/v1/...
5. success (thin client — **без** нормализации/догадок о форме):
   - MCP ожидает REST response shape FIN-36 as-is
   - GET: status == 200 AND body is dict с ожидаемой коллекцией → wrap { ok, profile, base, <collection> }
   - PUT: status == 200 AND body is dict (entity) → wrap { ok, profile, base, <entity> }
6. иначе → RuntimeError с телом API (parity FIN-114 format_api_error)
7. transport/network → проброс exception без wrap
```

### MCP: `list_households`

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из сессии | URL API |

**Выход:**

```json
{
  "ok": true,
  "profile": "cand",
  "base": "http://127.0.0.1:8000",
  "households": [ { "id": "…", "name": "…", "is_active": true, "created_at": "…", "updated_at": "…" } ]
}
```

Пустой список → `ok: true`, `households: []`.

### MCP: `upsert_household`

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` | string | нет | `prod` | data profile |
| `base` | string | нет | из сессии | URL API |
| `id` | string | **да** | — | path `{id}` |
| `name` | string | **да** | — | FIN-36 required |
| `is_active` | bool | нет | omit | optional; omit = keep/default API |

Pre-HTTP: `id`, `name` — present, strip → non-empty; `is_active` если ключ есть — `bool`.

**Выход:** `{ ok, profile, base, household: <HouseholdOut> }`.

### MCP: `list_household_members`

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` / `base` | string | нет | как выше | |
| `household_id` | string | **да** | — | path household |

**Выход:** `{ ok, profile, base, members: [...] }`.

### MCP: `upsert_household_member`

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` / `base` | string | нет | как выше | |
| `household_id` | string | **да** | — | path |
| `member_id` | string | **да** | — | path `{member_id}` |
| `display_name` | string | **да** | — | FIN-36 required |
| `is_active` | bool | нет | omit | optional |

Pre-HTTP: path ids + `display_name` non-empty after strip; `is_active` → bool if present.

**Выход:** `{ ok, profile, base, member: <MemberOut> }`.

### MCP: `list_bank_accounts`

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` / `base` | string | нет | как выше | |
| `household_id` | string | **да** | — | path |

**Выход:** `{ ok, profile, base, bank_accounts: [...] }`.

### MCP: `upsert_bank_account`

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `profile` / `base` | string | нет | как выше | |
| `household_id` | string | **да** | — | path |
| `account_id` | string | **да** | — | path `{account_id}` |
| `provider` | string | **да** | — | FIN-36 required |
| `display_name` | string | **да** | — | FIN-36 required |
| `valid_from` | string | **да** | — | `YYYY-MM` (формат — backend D-07) |
| `holder_member_id` | string\|null | нет | omit | optional nullable; omit = keep, `null` = clear |
| `statement_expected` | bool | нет | omit | optional |
| `final_close_only` | bool | нет | omit | optional |
| `valid_to` | string\|null | нет | omit | optional nullable; omit = keep, `null` = clear |

Pre-HTTP: path ids + required strings non-empty after strip; bool fields if present → `bool`. Формат `YYYY-MM` / D-04a / D-05 / D-25 — **только backend** (**D-03**).

**Выход:** `{ ok, profile, base, bank_account: <BankAccountOut> }`.

### Резолв / валидация / ошибки

| Ситуация | Поведение |
| -------- | --------- |
| Пустой required path/body string после strip | `ValueError` до HTTP |
| `is_active` / `statement_expected` / `final_close_only` переданы и не bool | `ValueError` до HTTP |
| HTTP ≠ 200 или body не dict / не FIN-36 shape | `RuntimeError` — `METHOD path -> HTTP {status} …` (как FIN-114); **без** «починки» ответа |
| API 422 `validation_error` (D-03a, D-04a, D-05, D-07, …) | `RuntimeError` с телом API |
| API 404 `not_found` (чужой parent / missing id) | `RuntimeError` с телом API |
| Transport / network | exception из `api.request` без wrap |
| GET 200, пустая коллекция | `ok: true`, пустой list |

### Инварианты (после pipeline)

1. MCP не меняет и не ослабляет инварианты FIN-36; любой 422/404 API → tool error.
2. Успешный PUT возвращает сущность с `id`, равным path id (гарантия backend; MCP не пересчитывает).
3. Body upsert содержит **только** ключи, явно присутствующие в MCP arguments; `unset ≠ null` сохраняется (**D-04**, **D-09**).
4. Существующие tools без изменений.
5. Ответ REST не нормализуется и не «чинитcя» — только wrap envelope `ok`/`profile`/`base` + ключ сущности/коллекции (**D-10**).

## Открытые решения

*(пусто — все перенесены в D-.)*

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Шесть tools vs один `action` | **Шесть dedicated tools** (имена как в Jira Goal / Done when). Один mega-tool отклонён: хуже discoverability, смешивает схемы args. |
| D-02 | Модуль | `scripts/households.py` (как `fx_rates.py`); handlers в `server.py` |
| D-03 | Domain validation | Pre-HTTP только presence/strip/bool; D-03a…D-07 / overlap / holder — backend |
| D-04 | omit vs null (семантика) | absent key → omit JSON; explicit `null` → JSON null (FIN-36 D-16a); поля: `valid_to`, `holder_member_id`, optional bools (`is_active`, …) |
| D-05 | Успех HTTP | GET/PUT success = **200** + dict body (не 201; upsert FIN-36) |
| D-06 | Имена response keys | `households` / `household` / `members` / `member` / `bank_accounts` / `bank_account` |
| D-07 | Ошибки | Parity FIN-114 `format_api_error` → `RuntimeError`; без soft `{ok:false}` |
| D-08 | Default `profile` | `prod` (как остальные MCP tools); smoke — `cand`/`test` |
| D-09 | Preservation of argument presence | MCP layer **обязан** различать отсутствующий аргумент и явно переданный `null`. Body строится из исходного validated argument model / `model_fields_set` (`exclude_unset=True`) либо эквивалента. Запрещено: обычный Python `None` одновременно как default параметра и marker explicit-null (напр. `if x is not None: body[…] = x`). Тесты T8–T9b / T13 — через **тот же** handler/schema path, что MCP, не только helper. |
| D-10 | Response shape | Thin client: ожидает REST shape FIN-36 as-is; wrap envelope only; без нормализации/догадок |
| D-11 | Acceptance vs FIN-231 | Done FIN-240 = MCP↔REST round-trip (A1 + A3). Integration smoke readiness (A2) — **условный**, только если FIN-231 deployed; не блокирует Done |

## Non-goals / guardrails

* Не seed'ить prod в рамках FIN-240 (это FOPS-8).
* Не дублировать FIN-36 request tables / overlap math в MCP.
* Не менять Close / import_coverage.
* Smoke приёмки — **`test`** / **`cand`**, не **`prod`** без явной ops-команды.
* Label `mcp-gap` снимать при Done FIN-240.
* Done не зависит от deployment/version FIN-231.

## Чеклист тестов

* **T1:** `upsert_household` → mock PUT 200 → wrap `household`.
* **T2:** `list_households` → mock GET 200 → `households[]`.
* **T3:** `upsert_household_member` + `list_household_members` happy path.
* **T4:** `upsert_bank_account` с flags + `valid_from` → 200 wrap `bank_account`.
* **T5:** `list_bank_accounts` → `bank_accounts[]`.
* **T6:** mock PUT 422 (второй active / D-04a / overlap) → `RuntimeError` содержит status/body.
* **T7:** mock GET/PUT 404 (member под чужим household) → `RuntimeError`.
* **T8:** omit optional `valid_to` (ключ отсутствует в MCP args) → HTTP body **без** ключа `valid_to` — через MCP handler/schema path (**D-09**).
* **T9:** MCP args содержат `"valid_to": null` → HTTP body содержит `"valid_to": null` — через MCP handler/schema path (**D-09**).
* **T9a:** omit `holder_member_id` → HTTP body **без** ключа `holder_member_id` (handler path).
* **T9b:** `"holder_member_id": null` → HTTP body содержит `"holder_member_id": null` (handler path).
* **T10:** пустой `id` / `name` → `ValueError` до HTTP (mock request не вызывается).
* **T11:** `statement_expected` не bool → `ValueError`.
* **T12:** schema: шесть tool names зарегистрированы.
* **T13:** `upsert_household` без `is_active` → HTTP body **без** ключа `is_active` (не `"is_active": false`); handler path (**D-09**).

## Приёмочная проверка

### Предусловия

* MCP `finance_api_connect` → `data_profile` = **`cand`** или **`test`**
* Backend с FIN-36 deployed (households API)
* Не использовать **prod**

### A1 — round-trip contour (**обязательный** Done)

**Действие:**

1. `upsert_household({ id, name, is_active: true })`
2. `upsert_household_member` × N
3. `upsert_bank_account` × 3 (разные `provider`, `statement_expected`/`final_close_only` по сценарию)
4. `list_households` / `list_household_members` / `list_bank_accounts`

**Ожидаемый результат:** list совпадает с upsert; ids/path стабильны. Это полный критерий приёмки FIN-240.

### A2 — readiness sees expected (**условный** integration smoke)

**Условие:** FIN-231 deployed на том же профиле. Иначе A2 **пропустить**; отсутствие/падение A2 **не** блокирует Done FIN-240.

**Действие:** после A1 вызвать существующий readiness/verify path, который читает expected из bank accounts.

**Ожидаемый результат:** expected accounts отражают contour (не hardcoded FIN-82), без CLI/SQL.

### A3 — negative API passthrough (**обязательный** Done)

**Действие:** `upsert_bank_account` с `final_close_only: true` и `statement_expected: false`.

**Ожидаемый результат:** tool error (API 422), запись не создана.

**Автоматизация:** `tests/test_households.py` — mock + вызовы через MCP handler/schema path для T8–T9b / T13.

## Связь с другими FIN

| FIN / FOPS | Связь |
| ---------- | ----- |
| [FIN-36](https://alexeielizarov.atlassian.net/browse/FIN-36) | Backend REST SoT; MCP thin client |
| [FIN-231](https://alexeielizarov.atlassian.net/browse/FIN-231) | Consumers `statement_expected` / `final_close_only`; A2 conditional only |
| [FOPS-8](https://alexeielizarov.atlassian.net/browse/FOPS-8) | **Blocked by** FIN-240 — prod seed через MCP |
| [FIN-101](https://alexeielizarov.atlassian.net/browse/FIN-101) | Parent ops epic |

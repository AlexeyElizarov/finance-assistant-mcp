# MCP tool `household_base_share` — базовая доля личных фондов

**Связь:** [FIN-103](https://alexeielizarov.atlassian.net/browse/FIN-103); родитель [FIN-101](https://alexeielizarov.atlassian.net/browse/FIN-101); спринт wave-4; блокирует [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104) (money check — лимиты из base share).

**Домен:** формула шагов 1–5 — [household-budget-model.md](../../../assistant/35-finance-assistant/methodology/household-budget-model.md); ops-процедура июля — [2026-07-household-ops.md](../../../assistant/35-finance-assistant/methodology/2026-07-household-ops.md).

**Статус:** Утверждено (2026-06-28, rev.3).

## Назначение

Фаза 1 household ops (до 3-го числа планируемого месяца) требует расчёта **базовой доли** личных фондов по формуле модели: домашние доходы минус профессиональные расходы, общий фонд и накопления, деление пополам. Сегодня ops собирает таблицу вручную через десятки вызовов `query_plan_fact`.

**Критерий приёмки:** один вызов `household_base_share` для целевого `YYYY-MM` возвращает разбивку по контурам, `free_remainder`, `base_share` на каждого партнёра и sanity-check против legacy IRR-строк плана — без ручного перебора статей.

## Объём и границы

### Входит в объём

* Новый MCP tool **`household_base_share`** в `mcp-servers/finance-assistant/`.
* Interim JSON **contour mapping** per profile: `35-finance-assistant/methodology/household-contour-mapping.{profile}.json` (первая версия — `prod`).
* Загрузка **плановых** сумм статей через существующий `GET /api/v1/budget/plan-actual` (reuse `fetch_month_row` / `resolve_budget_item_id`).
* Расчёт формулы модели; ответ с line items по каждому контуру.
* Блок **`sanity_check`**: сумма legacy IRR-строк личных подлимитов vs `Σ base_share` партнёров (информационно).
* Предупреждения **`warnings[]`**: не включённые INC-статьи с ненулевым планом; отрицательный `free_remainder`.
* Unit-тесты (mock API + fixture mapping).
* Обновление `mcp-gaps.md` и schema tool в `server.py` после реализации.

### Не входит в объём

* Backend read API [FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102) — отдельная задача; эта спека задаёт interim-путь через JSON mapping и совместимый контракт ответа для последующей миграции на API.
* [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104) (`money_check_report`) — факт трат, остатки, C9999.
* [FIN-105](https://alexeielizarov.atlassian.net/browse/FIN-105) (`personal_fund_carryover`) — перенос после `FINAL_CLOSED`.
* Перенос остатка прошлого месяца в базовую долю (шаг 9 модели).
* Мутации плана / Finanzplaner / in-app UI [FIN-78](https://alexeielizarov.atlassian.net/browse/FIN-78).
* Автоматическое обнаружение «других INC» без опоры на mapping (только warn по правилам ниже).

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| MCP tools | `query_plan_fact` — одна статья за вызов | N вызовов на месяц |
| Contour mapping | Только в markdown ops ([2026-07-household-ops.md](../../../assistant/35-finance-assistant/methodology/2026-07-household-ops.md)) | Нет машиночитаемого источника |
| Backend [FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102) | Нет | — |
| Июль 2026 deadline | Ручной расчёт до 3.07 | Риск ошибки и задержки |

## Целевое поведение

### Формула (источник — модель)

Для целевого календарного месяца **M** используются **плановые** суммы (`plan_amount`) из ACT-версии бюджета:

```
household_income  = Σ plan(include household_income)
professional_total = Σ plan(professional, all partners)
shared_fund_total  = Σ plan(shared_fund)
savings_total      = Σ plan(savings)

free_remainder = household_income − professional_total − shared_fund_total − savings_total

partner_count = len(partners)   # из mapping; сегодня 2

base_share(each partner) = round(free_remainder / partner_count, 2)
```

Сегодня `partner_count == 2` — это частный случай «деления пополам» из модели; формула **не** захардкожена как `/ 2`.

Исключения из `household_income` (не входят в сумму, но отображаются в `excluded_income[]`):

* статьи из `household_income.exclude` mapping (поддержка родителей Николая).

**Unmapped INC (warning):** после разрешения mapping tool сканирует все статьи с `flow_type == "INC"` из **`GET /api/v1/budget/items`**. Если у статьи `plan > 0` в месяце M и она **не** попала ни в `include`, ни в `exclude` — warning `unmapped_income:{article}`; в `household_income.total` **не** включается.

**Не использовать факт** (`actual_amount`) для base share — только план месяца M.

### Interim contour mapping (JSON)

Путь по умолчанию:

```
{FINANCE_ASSISTANT_ROOT}/methodology/household-contour-mapping.{profile}.json
```

`FINANCE_ASSISTANT_ROOT` — как в `monthly_close_lib.ASSISTANT_ROOT` (default `C:\Users\haake\assistant\35-finance-assistant`).

#### Схема файла (v1)

```json
{
  "schema_version": 1,
  "profile": "prod",
  "partners": [
    { "id": "aleksey", "display_name": "Алексей" },
    { "id": "nikolay", "display_name": "Николай" }
  ],
  "household_income": {
    "include": [
      { "article_match": "Заработная плата" }
    ],
    "exclude": [
      {
        "article_match": "Переводы в Россию",
        "reason": "nikolay_parent_support"
      }
    ]
  },
  "professional": {
    "aleksey": [
      { "article_match": "Командировки" }
    ],
    "nikolay": []
  },
  "shared_fund": [
    { "article_match": "Арендная плата (Ulf Veit" },
    { "article_match": "Интернет (NetCologne)" },
    { "article_match": "Мобильная связь" },
    { "article_match": "Отопление (RheinEnergie)" },
    { "article_match": "Электроэнергия" },
    { "article_match": "Barmenia" },
    { "article_match": "ARAG" },
    { "article_match": "YouTube Premium" },
    { "article_match": "DB Vertrieb" },
    { "article_match": "Abo 259857844" },
    { "article_match": "Банковское обслуживание" }
  ],
  "savings": [
    { "article_match": "Сбережения" },
    { "article_match": "Прочие сбережения" }
  ],
  "legacy_irr_sanity": [
    { "article_match": "Продукты питания" },
    { "article_match": "Кафе и рестораны" },
    { "article_match": "Онлайн покупки" },
    { "article_match": "Оффлайн покупки" },
    { "article_match": "Снятие наличных" },
    { "article_match": "Аптеки" },
    { "article_match": "Перевод карманных" }
  ],
  "personal_subscriptions_sanity": [
    { "article_match": "XTRAFIT" },
    { "article_match": "ChatGPT" },
    { "article_match": "Cursor" }
  ]
}
```

Правила сопоставления статей:

| Правило | Поведение |
| ------- | --------- |
| `article_match` | Подстрока имени статьи, case-insensitive (как `resolve_budget_item_id`) |
| 0 совпадений | **Ошибка tool** для обязательных контуров (`include`, `shared_fund`, `savings`, `professional` entries); для `legacy_irr_sanity` — пропуск строки + warning |
| >1 совпадение | **Ошибка tool** (ambiguous) |
| DB Abo (2×) | Две строки mapping с **разными** уточняющими `article_match` (напр. `"DB Vertrieb"`, `"Abo 259857844"`); общий `"Deutsche Bahn"` без уточнения **запрещён** (ambiguous) |

При реализации mapping для prod уточнить имена двух DB-статей по `GET /api/v1/budget/items` и зафиксировать в JSON (ожидаемые подстроки: `"Deutsche Bahn (DB Vertrieb)"`, `"Deutsche Bahn (Abo 259857844)"`).

#### Валидация mapping (до расчёта)

| Правило | Поведение |
| ------- | --------- |
| `len(partners) >= 1` | Иначе **tool error** (`invalid mapping: empty partners`) |
| `schema.profile == request.profile` | При несовпадении — **tool error** (в т.ч. при override `mapping_path`) |
| Одна статья в `include` и `exclude` (один `budget_item_id`) | **tool error** (`mapping validation: include/exclude overlap`) |
| Один `budget_item_id` в двух **расчётных** контурах | **tool error** (`mapping validation: duplicate contour assignment`) |

**Расчётные контуры** (участвуют в формуле): `household_income.include`, `household_income.exclude`, `professional.*`, `shared_fund`, `savings`.

**Не расчётные** (sanity-only, пересечения с расчётными **разрешены**): `legacy_irr_sanity`, `personal_subscriptions_sanity`.

**Предметная модель:** уникальность статьи между расчётными контурами — инвариант домена, не только правило interim-mapping. После [FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102) backend **обязан** сохранять тот же инвариант; MCP при `source: "api"` **не** принимает ответ, нарушающий его (tool error).

Проверка пересечений выполняется **после** разрешения всех `article_match` → `budget_item_id` через `GET /api/v1/budget/items` (путь `source: "mapping"`) или валидации payload API (путь `source: "api"`).

### MCP: `household_base_share`

#### Вход

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `profile` | string | нет | default `prod` |
| `base` | string | нет | URL API |
| `period` | string | **да** | `YYYY-MM` — месяц базовой доли |
| `budget_version_id` | string | нет | default ACT из `GET /budget/versions` |
| `mapping_path` | string | нет | override пути к JSON mapping |

#### Выход (корень)

| Поле | Тип | Описание |
| ---- | --- | -------- |
| `ok` | bool | `true` при успехе |
| `profile` | string | data profile |
| `base` | string | API base URL |
| `period` | string | `YYYY-MM` |
| `budget_version_id` | string | UUID версии |
| `mapping_path` | string | использованный файл |
| `mapping_schema_version` | int | из JSON |
| `source` | string | `"mapping"` (interim); `"api"` — зарезервировано для [FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102) |
| `formula` | string | каноническое уравнение (для ops-чата) |
| `household_income` | object | см. ниже |
| `professional` | object | см. ниже |
| `shared_fund` | object | см. ниже |
| `savings` | object | см. ниже |
| `free_remainder` | number | €, 2 знака |
| `partner_count` | int | `len(partners)`; **инвариант:** `partner_count == len(partners)` всегда |
| `partners` | array | `{ id, display_name, base_share }` |
| `sanity_check` | object | legacy IRR vs новая модель |
| `warnings` | string[] | ops-предупреждения |

#### Структура контуров

Каждый контур (`household_income`, `shared_fund`, `savings`) и `professional` (per partner):

```json
{
  "total": 1592.89,
  "lines": [
    {
      "article_match": "Арендная плата (Ulf Veit",
      "budget_item_id": "…",
      "article": "Арендная плата (Ulf Veit, Kirchhoffstraße)",
      "plan": 1100.0
    }
  ]
}
```

`household_income` дополнительно:

```json
{
  "total": 4740.18,
  "lines": [ … ],
  "excluded_income": [
    {
      "article": "Переводы в Россию на оплату услуг",
      "plan": 250.0,
      "reason": "nikolay_parent_support"
    }
  ]
}
```

`professional`:

```json
{
  "total": 300.0,
  "by_partner": {
    "aleksey": { "total": 300.0, "lines": [ … ] },
    "nikolay": { "total": 0.0, "lines": [] }
  }
}
```

#### Sanity-check

```json
{
  "legacy_irr_total": 2485.0,
  "personal_subscriptions_total": 117.0,
  "combined_legacy_personal": 2602.0,
  "two_base_shares": 2847.30,
  "rounding_delta": 0.01,
  "delta_vs_two_base_shares": -245.30,
  "note": "Legacy IRR-подлимиты не равны Σ base_share — ожидаемо при новой модели; операционный контроль — остаток личного фонда, не строки IRR."
}
```

Числа — из плана месяца M.

| Поле | Правило |
| ---- | ------- |
| `two_base_shares` | `partner_count × base_share` (каждый `base_share` уже округлён) |
| `rounding_delta` | `two_base_shares − free_remainder` |

**Округление:** `sum(partners[].base_share)` может отличаться от `free_remainder` на **±0,01 × (partner_count − 1)** € из‑за `round(..., 2)` (при двух партнёрах — **±0,01 €**, как в эталоне июля: `2847,29` → два × `1423,65` = `2847,30`). **Не** распределять «лишний цент» неравномерно между партнёрами — у всех одинаковый `base_share` (D-06).

#### Пример (фрагмент, июль 2026 prod — эталон ops)

Запрос:

```json
{
  "profile": "prod",
  "period": "2026-07"
}
```

Ожидаемые ключевые значения (план ACT на дату утверждения mapping; сверка с [2026-07-household-ops.md](../../../assistant/35-finance-assistant/methodology/2026-07-household-ops.md)):

| Поле | Ожидание |
| ---- | -------- |
| `household_income.total` | 4 740,18 |
| `professional.total` | 300,00 |
| `shared_fund.total` | 1 592,89 |
| `savings.total` | 0,00 |
| `free_remainder` | 2 847,29 |
| `partners[].base_share` | 1 423,65 |

#### Ошибки и границы

| Ситуация | Поведение |
| -------- | --------- |
| Mapping file not found | Tool error |
| Invalid JSON / unsupported `schema_version` | Tool error |
| `partners` пустой | Tool error (`invalid mapping`) |
| `schema.profile ≠ request.profile` | Tool error |
| Include/exclude overlap или duplicate contour assignment | Tool error (`mapping validation`) |
| Ambiguous / missing required article | Tool error с именем `article_match` |
| API 5xx / timeout | Tool error; частичный ответ **не** возвращается |
| `free_remainder < 0` | `ok: true`, warning в `warnings[]` |
| INC с `plan > 0`, не в include/exclude | warning `unmapped_income:{article}` (см. D-13) |
| `period` не `YYYY-MM` | Tool error |
| Household API `200` + передан `mapping_path` | `source: "api"`; mapping не читается |
| Household API `404` | `source: "mapping"`; расчёт по JSON |
| Household API `5xx` / timeout | Tool error (без fallback на mapping) |

HTTP-вызовы: один `GET /budget/items` (кэш на время вызова) + по одному `GET /budget/plan-actual` на **уникальную** статью в mapping (не дублировать запросы для повторяющихся id).

### Миграция на backend API ([FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102))

Порядок выбора источника при каждом вызове tool:

1. Probe `GET /api/v1/household/base-share?period=…` (или эквивалент из [FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102)).
2. **HTTP 200** → `source: "api"`. Локальный mapping **полностью игнорируется**: параметр `mapping_path` не читается, файл mapping не открывается. Ответ нормализуется MCP в тот же JSON shape.
3. **HTTP 404** / endpoint отсутствует → `source: "mapping"`. Расчёт по JSON mapping (текущее поведение FIN-103).
4. **5xx / timeout** на household endpoint → tool error (без silent fallback на mapping).

При `source: "api"`:

* `partner_count` **всегда** вычисляется MCP как `len(partners)` из нормализованного ответа; отдельное поле `partner_count` от backend **игнорируется**, если присутствует.
* Инвариант: `partner_count == len(partners)`; при `partners: []` после нормализации — tool error.

Реализация probe + fallback в FIN-103; переключение на API-only (без mapping fallback) — отдельный инкремент после стабилизации [FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102).

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | План vs факт | Только **plan** месяца M для base share |
| D-02 | Источник контуров (interim) | JSON mapping в `35-finance-assistant/methodology/` |
| D-03 | Источник контуров (long-term) | API [FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102); mapping — fallback |
| D-04 | Поддержка родителей | `exclude` в mapping; в `excluded_income[]`, не в `household_income.total` |
| D-05 | Деление base share | `round(free_remainder / len(partners), 2)`; одинаковое значение каждому партнёру; сегодня `len(partners)==2` |
| D-06 | Округление | `round(x, 2)` half-up; **не** выравнивать центы между партнёрами; `rounding_delta` в sanity_check |
| D-07 | Перенос остатка | **Вне scope** FIN-103 |
| D-08 | Sanity-check | Информационный блок; **не** fail tool при расхождении с IRR |
| D-09 | Unmapped INC | Warning, не auto-include |
| D-10 | Имя tool | `household_base_share` (как в [FIN-103](https://alexeielizarov.atlassian.net/browse/FIN-103)) |
| D-11 | DB Abo 2× | Две статьи в mapping с уникальными `article_match` |
| D-12 | Личные подписки | Только в `personal_subscriptions_sanity`, не в shared_fund |
| D-13 | Источник `flow_type` | Поле `flow_type` из `GET /api/v1/budget/items` (metadata статьи); не из plan-actual row |
| D-14 | Include ∩ exclude | **Mapping validation error** — ошибка конфигурации |
| D-15 | Статья в двух расчётных контурах | **Tool error**; sanity-блоки исключены; **инвариант домена** — сохраняется и при `source: "api"` ([FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102)) |
| D-16 | `mapping_path` override | Разрешён; `schema.profile` в файле **обязан** совпадать с `request.profile` |
| D-17 | Пустой `partners[]` | **Tool error** (`invalid mapping`) |
| D-18 | API vs `mapping_path` | При HTTP 200 household API — mapping и `mapping_path` **игнорируются**; API имеет абсолютный приоритет |
| D-19 | `partner_count` при API | Всегда `len(partners)` в MCP; не доверять отдельному полю backend |

## Non-goals / guardrails

* Не менять backend в FIN-103.
* Не писать в план / Finanzplaner.
* Не включать факт трат и carryover.
* Не удалять legacy IRR-строки из бюджета.
* Не дублировать доменную логику [FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102) в mapping после появления API — только fallback.

## Чеклист тестов

* **T1:** Июль 2026 fixture — `free_remainder == 2847.29`, `base_share == 1423.65`, `rounding_delta == 0.01`.
* **T2:** Excluded parent-support — в `excluded_income`, не в `household_income.total`.
* **T3:** Professional only Aleksey — `professional.by_partner.nikolay.total == 0`.
* **T4:** Missing required article — tool error.
* **T5:** Ambiguous `article_match` — tool error.
* **T6:** Unmapped INC with plan > 0 — warning, расчёт без этой статьи.
* **T7:** Negative `free_remainder` — `ok: true`, warning.
* **T8:** `sanity_check.legacy_irr_total` — сумма legacy lines only.
* **T9:** Один `budget_item_id` в `shared_fund` и `professional.aleksey` — tool error.
* **T10:** Одна статья в `include` и `exclude` — tool error.
* **T11:** `partners: []` — tool error.
* **T12:** `profile=prod`, mapping с `"profile": "test"` — tool error.
* **T13:** Три партнёра в fixture — `base_share == round(free_remainder / 3, 2)` для каждого.
* **T14:** Уникальный `budget_item_id` — один HTTP plan-actual call (mock call count).
* **T15:** Household endpoint отсутствует (`404`) — `source == "mapping"`, tool успешен.
* **T16:** Household endpoint `200` — `source == "api"`, mapping-файл не читается (mock: `open` не вызывается); при переданном `mapping_path` — тот же результат.

## Приёмочная проверка

### Предусловия

* API `prod` запущен; `finance_api_connect` → `data_profile == prod`.
* Файл `household-contour-mapping.prod.json` создан и согласован с ops-документом июля.

### A1 — один вызов вместо таблицы

**Действие:** `household_base_share({ "profile": "prod", "period": "2026-07" })`.

**Ожидаемый результат:**

* `free_remainder` и `partners[].base_share` совпадают с [2026-07-household-ops.md](../../../assistant/35-finance-assistant/methodology/2026-07-household-ops.md) (±0,01 €).
* `excluded_income` содержит поддержку родителей.
* `sanity_check.note` присутствует.

### A2 — ops ritual

**Действие:** выполнить шаг «Фаза 1» из ops-документа только через MCP.

**Ожидаемый результат:** не требуется ручной перебор `query_plan_fact` по списку статей.

### Автоматизация

**Тесты:** `scripts/test_household_base_share.py` (`unittest` + mock `ApiClient` + fixture JSON).

**Команда:**

```bash
cd mcp-servers/finance-assistant/scripts && python -m unittest test_household_base_share -v
```

## E2E

| Сценарий | Изменение | Обновление |
| -------- | --------- | ---------- |
| FinancePlanningProject E2E | Нет | Нет |
| Ops июль 2026 | Фаза 1 через MCP | Ссылка на tool в [2026-07-household-ops.md](../../../assistant/35-finance-assistant/methodology/2026-07-household-ops.md) после Done |
| [FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102) | Fallback → API | После Done FIN-102 |

## Follow-ups / Out of scope

| ID | Тема | Решение |
| -- | ---- | ------- |
| F-01 | [FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102) | Канонический API; mapping deprecated |
| F-02 | [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104) | Использует `base_share` из этого tool |
| F-03 | [FIN-105](https://alexeielizarov.atlassian.net/browse/FIN-105) | Carryover после FINAL |
| F-04 | Ссылка на спеку в Jira | ✓ при утверждении rev.3 |
| F-05 | Mapping для `test` / `cand` | по необходимости; FIN-103 — prod first |

## Утверждение

* **Статус:** Утверждено
* **Дата:** 2026-06-28 (rev.3)
* **Следующий шаг:** реализация в wave-4; снять label `mcp-gap` с [FIN-103](https://alexeielizarov.atlassian.net/browse/FIN-103) при Done

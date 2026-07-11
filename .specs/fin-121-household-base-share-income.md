# MCP `household_base_share` — настраиваемый состав домашних доходов

**Связь:** [FIN-121](https://alexeielizarov.atlassian.net/browse/FIN-121); родитель [FIN-101](https://alexeielizarov.atlassian.net/browse/FIN-101); **Relates** [FIN-103](https://alexeielizarov.atlassian.net/browse/FIN-103) (базовый tool), [FIN-114](https://alexeielizarov.atlassian.net/browse/FIN-114) (FX API — отдельная задача).

**Домен:** формула шагов 1–5 — [household-budget-model.md](../../../assistant/35-finance-assistant/methodology/household-budget-model.md); ops июля — [household-base-share.md](../../../assistant/35-finance-assistant/working/2026-07/household-base-share.md).

**Статус:** Утверждено (2026-07-09, rev.4)

## Назначение

При подготовке базовой доли (фаза 1 household ops) оператору нужны **разные срезы домашних доходов** в одном и том же месяце: только зарплата P0001, зарплата + взнос Николая, полный набор из mapping. Сегодня `household_base_share` всегда берёт `household_income.include` / `exclude` из `household-contour-mapping.{profile}.json` без параметров вызова. При смене состава доходов ops пересчитывал вручную в working-sheet ([household-base-share.md](../../../assistant/35-finance-assistant/working/2026-07/household-base-share.md)) — результат MCP нельзя было воспроизвести одним вызовом.

**Критерий приёмки:** ops выбирает preset `income_mode` или точечные overrides и получает тот же `household_income.total`, `free_remainder`, `base_share`, что и в working-sheet, **без** правки mapping JSON между прогонами.

## Объём и границы

### Входит в объём

* Расширение `compute_from_mapping()` / `compute_household_base_share()` в `scripts/household_base_share.py`: разрешение эффективного набора income-статей.
* Новые параметры MCP tool `household_base_share` в `server.py` + schema.
* Блок `household_income.income_resolution` в ответе (применённый режим, overrides, источник).
* `household_income.excluded_income[]` — все строки, рассматриваемые при разрешении effective include и **не вошедшие** в него, с полем `reason`.
* Unit-тесты (mock API + fixture mapping): сценарии июля 2026 — salary only и salary + partner contribution.
* Обновление `mcp-gaps.md` (описание новых параметров); снятие label `mcp-gap` с FIN-121 при Done.

### Не входит в объём

* FX API / конвертация RUB→EUR — [FIN-114](https://alexeielizarov.atlassian.net/browse/FIN-114). FIN-121 читает **plan_amount в EUR** из `GET /budget/plan-actual` (см. D-02).
* Изменение `household-contour-mapping.prod.json` ради сценариев (mapping остаётся superset; сужение — через параметры вызова).
* Backend [FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102) household API.
* Мутации плана / Finanzplaner.
* Новые контуры (professional, shared_fund, savings) — только `household_income`.

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| MCP schema `household_base_share` | `period`, `profile`, `mapping_path`, `budget_version_id` | Нет `income_mode` / overrides |
| `compute_from_mapping()` | `include` + `exclude` строго из mapping | Нельзя «только P0001» без правки JSON |
| `excluded_income[]` | Только mapping `exclude` | Нет reason для строк, выкинутых preset'ом |
| Ops июль 2026 | Ручной пересчёт в working-sheet | MCP ≠ sheet при другом составе доходов |
| Mapping prod | `include`: зарплата + взнос Николая | Superset; нужен runtime-фильтр |

## Обратная совместимость

Вызов **без** новых параметров (`income_mode` unset, overrides пусты) **идентичен** FIN-103: эффективный набор = mapping `include` / `exclude`. Существующие тесты FIN-103 (T1–T16) **не регрессируют**.

## Целевое поведение

### Пайплайн разрешения income

#### Формула effective include

```
mapping_include_ids  = resolve_all(mapping.household_income.include)
mapping_exclude_ids  = resolve_all(mapping.household_income.exclude)

preset_ids           = apply_income_mode(income_mode, mapping_include_ids)
                       # unset / mapping_default → mapping_include_ids без изменений

include_override_ids = resolve_overrides(include_income_matches[])
exclude_override_ids = resolve_overrides(exclude_income_matches[])

if include_override_ids ∩ exclude_override_ids ≠ ∅:
    tool error "income override conflict"

effective_include_ids =
    (preset_ids ∪ include_override_ids) \ exclude_override_ids
```

`include_override_ids` **имеет приоритет** над `mapping_exclude_ids` (D-08): статья из mapping `exclude` может вернуться в effective include, если явно указана в `include_income_matches[]`.

`exclude_override_ids` **имеет наивысший приоритет** (D-15): всегда исключает статью из effective include, даже если она была возвращена через `include_income_matches[]` или входит в preset.

После resolve override-массивов дубликаты `budget_item_id` **удаляются** (множества id); повтор одного и того же `article_match` в массиве — не ошибка (D-16).

После вычисления `effective_include_ids`:

* `household_income.lines` — статьи из `effective_include_ids` с plan amounts.
* `household_income.excluded_income` — все остальные строки из `mapping_include_ids ∪ mapping_exclude_ids ∪ include_override_ids`, не попавшие в effective include; **ровно одна** запись и **ровно один** `reason` на `budget_item_id` (приоритет D-18).
* `household_income.total = Σ lines[].plan`.

#### Порядок шагов (для реализации)

```
1. Загрузить mapping; resolve mapping.include / mapping.exclude → id sets
2. Применить income_mode preset к mapping_include_ids
3. Resolve include_income_matches[] → include_override_ids
4. Resolve exclude_income_matches[] → exclude_override_ids
5. Проверить конфликт include ∩ exclude overrides
6. Вычислить effective_include_ids по формуле выше
7. Собрать lines / excluded_income / total; пересчитать free_remainder
```

### Резолв `article_match`

Переиспользуется `resolve_article_match()` из FIN-103 с `required=True` для **каждой** строки в `include_income_matches[]` и `exclude_income_matches[]`, а также для mapping entries.

| Ситуация | Поведение |
| -------- | --------- |
| 0 совпадений | Tool error: `Статья бюджета не найдена по article_match {match!r}` |
| 2+ совпадений (ambiguous substring) | Tool error: `Неоднозначно article_match {match!r}: …` |
| Ровно 1 совпадение, `flow_type ≠ "INC"` | Tool error: `income match not inc: {article}` |
| Ровно 1 совпадение, `flow_type == "INC"` | OK |

**Preset `income_mode`** использует **другую** семантику: фильтрует уже разрешённые строки mapping-include по substring (case-insensitive `needle in article`); **допускает несколько** статей на один preset-needle. Ambiguous resolution по одной строке match — только для overrides и mapping entries.

### MCP: новые входные параметры

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `income_mode` | string | нет | Preset состава доходов. **Unset** = поведение FIN-103. Alias `mapping_default` эквивалентен unset |
| `include_income_matches` | string[] | нет | Доп. `article_match` для включения (см. резолв выше) |
| `exclude_income_matches` | string[] | нет | `article_match` для принудительного исключения из effective include |

Существующие поля FIN-103 без изменений.

#### Preset `income_mode`

| Значение | Эффективный include | Примечание |
| -------- | ------------------- | ---------- |
| *(unset)* / `mapping_default` | все `mapping_include_ids` | Текущее FIN-103 |
| `salary_only` | mapping-include, где имя содержит `"Заработная плата"` | Остальные mapping-include → `excluded_income`, reason `income_mode:salary_only` |
| `salary_plus_partner_contribution` | имена содержат `"Заработная плата"` **или** `"Взнос Николая"` | Остальные → `excluded_income`, reason `income_mode:salary_plus_partner_contribution` |

Preset **сужает** `mapping_include_ids`; не добавляет статьи вне mapping-include (кроме явного `include_income_matches[]`).

#### Overrides

* `include_income_matches[]` — каждый match: `resolve_article_match(required=True)` + проверка `flow_type == "INC"`; результат добавляется в `include_override_ids`. Может вернуть статью из `mapping_exclude_ids` (D-08).
* `exclude_income_matches[]` — resolve + INC-check; id добавляется в `exclude_override_ids`; строка уходит в `excluded_income` с `reason: "override:exclude"`.
* Пустые строки после `strip()` — отбрасываются.

#### Конфликты

Конфликт include/exclude overrides определяется по **`budget_item_id` после resolve**, а не по тексту `article_match` (разные match-строки могут сойтись в один id).

| Ситуация | Поведение |
| -------- | --------- |
| Один `budget_item_id` в `include_override_ids` и `exclude_override_ids` | Tool error (`income override conflict`) |
| `exclude_income_matches` на статью уже в mapping `exclude` | Допустимо; одна запись в `excluded_income`, reason `override:exclude` |
| Неизвестный `income_mode` | Tool error |
| `include_income_matches: ["foobar"]` — нет совпадений | Tool error (0 matches) |
| `include_income_matches` — ambiguous | Tool error (как FIN-103 T5) |

### Инварианты (после разрешения)

После шага 7 пайплайна **всегда** выполняются:

1. `household_income.lines` и `household_income.excluded_income` — **дизъюнктны** по `budget_item_id` (одна статья не может быть и в lines, и в excluded).
2. `household_income.total == round(Σ lines[].plan, 2)`.
3. Суммы из `excluded_income[].plan` **не входят** в `household_income.total`.
4. Каждая строка из `mapping_include_ids ∪ mapping_exclude_ids ∪ include_override_ids`, не попавшая в effective include, присутствует в `excluded_income` **ровно один раз** с **ровно одним** `reason`.
5. `income_resolution.effective_include_count == len(lines)`.
6. `free_remainder = household_income.total − professional.total − shared_fund.total − savings.total` (пересчитывается после фильтра income).

### MCP: расширение ответа

Добавить в `household_income`:

```json
{
  "total": 4966.86,
  "lines": [ … ],
  "excluded_income": [
    {
      "article_match": "Взнос Николая",
      "budget_item_id": "…",
      "article": "Взнос Николая (20 000 ₽)",
      "plan": 226.68,
      "reason": "income_mode:salary_only"
    }
  ],
  "income_resolution": {
    "income_mode": "salary_only",
    "include_income_matches": [],
    "exclude_income_matches": [],
    "mapping_include_count": 2,
    "effective_include_count": 1
  }
}
```

`reason` — machine-readable строка из **фиксированного** списка v1:

| Значение | Когда |
| -------- | ----- |
| `mapping:exclude` | Статья из mapping `exclude`, не возвращённая include override |
| `income_mode:salary_only` | Mapping-include отфильтрован preset `salary_only` |
| `income_mode:salary_plus_partner_contribution` | Mapping-include отфильтрован preset `salary_plus_partner_contribution` |
| `override:exclude` | Исключена через `exclude_income_matches[]` |

Другие значения `reason` в v1 **запрещены**.

Если статья исключена несколькими механизмами одновременно, выбирается **один** `reason` с наивысшим приоритетом (D-18):

```
override:exclude  >  mapping:exclude  >  income_mode:*
```

Пример: статья в `mapping.exclude` при `salary_only` → `reason: mapping:exclude` (не `income_mode:salary_only`).

Поля `professional`, `shared_fund`, `savings`, `sanity_check` — **без изменений** (зависят только от mapping, не от income_mode).

### Путь `source: "api"` ([FIN-102](https://alexeielizarov.atlassian.net/browse/FIN-102))

При HTTP 200 household API:

1. MCP нормализует ответ (как FIN-103).
2. Если заданы `income_mode` / overrides — **post-filter** на нормализованном `household_income` (пересчёт `total`, `lines`, `excluded_income`, `free_remainder`, `partners[].base_share`).
3. Если post-filter невозможен (в ответе API нет `household_income.lines` / пустой income-блок) — **tool error**, не silent ignore (D-09).
4. `income_resolution` заполняется так же, как для mapping path.
5. Professional / shared / savings **не** пересчитываются — только income-часть формулы.

Если post-filter даёт отрицательный `free_remainder` — warning `negative_free_remainder` (как FIN-103).

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Default без параметров | **Unset** `income_mode` = FIN-103; `mapping_default` — допустимый alias |
| D-02 | Источник plan amounts | `GET /budget/plan-actual`; FX — FIN-114, вне scope |
| D-03 | Preset article_match | Substring-фильтр по имени: `"Заработная плата"`, `"Взнос Николая"`; несколько статей на needle допустимо |
| D-04 | Preset vs mapping | Preset сужает `mapping_include_ids`; расширение — только `include_income_matches[]` |
| D-05 | Reason в excluded_income | Обязателен для каждой excluded строки |
| D-06 | Unmapped INC warning | Без изменений FIN-103: сканирует **полный** mapping include∪exclude, **независимо** от `income_mode` / overrides. Warning сигнализирует «в mapping настроена статья INC с plan>0, но она не классифицирована в include/exclude» — это проверка **конфигурации mapping**, а не выбранного runtime-среза. При `salary_only` warning про статью вне preset остаётся намеренно |
| D-07 | API path | Post-filter на нормализованном ответе; backend params — вне scope |
| D-08 | Include override vs mapping.exclude | **`include_income_matches` имеет приоритет**: может вернуть статью из mapping `exclude` в effective include |
| D-09 | API post-filter без income data | Tool error |
| D-10 | Override match not found | Tool error (0 matches), как mapping required entries |
| D-11 | Override ambiguous match | Tool error (2+ matches), как FIN-103 |
| D-12 | Override non-INC item | Tool error `income match not inc` |
| D-13 | Custom exclude reason | Только `override:exclude` в v1; объекты `{match, reason}` — вне scope |
| D-14 | `income_resolution` placement | Только внутри `household_income`, не дублировать в корне |
| D-15 | Приоритет exclude override | `exclude_income_matches` побеждает include override и preset |
| D-16 | Дубликаты в override-массивах | После resolve — дедупликация по `budget_item_id`; не tool error |
| D-17 | Конфликт overrides | По `budget_item_id` после resolve, не по тексту match |
| D-18 | Приоритет `reason` | `override:exclude` > `mapping:exclude` > `income_mode:*`; ровно один reason на excluded строку |

## Non-goals / guardrails

* Не менять формулу professional / shared / savings.
* Не добавлять FX-конвертацию (FIN-114).
* Не требовать правки mapping для ops-сценариев июля.
* Не дублировать FIN-103 тесты — только новые T17+.

## Чеклист тестов

* **T17:** `income_mode: "salary_only"` — `household_income.total == 4740.18`; взнос Николая в `excluded_income` с `reason: income_mode:salary_only` (fixture: mapping include = зарплата + взнос).
* **T18:** `income_mode: "salary_plus_partner_contribution"` — `household_income.total == 4966.86` (4740.18 + 226.68); только эти две строки в `lines`.
* **T19:** unset `income_mode` — идентично FIN-103 T1/T2 для того же fixture.
* **T20:** `exclude_income_matches: ["Взнос Николая"]` без preset — total 4740.18; reason `override:exclude`.
* **T21:** `include_income_matches` + `exclude_income_matches` на одну статью — tool error.
* **T22:** unknown `income_mode` — tool error.
* **T23:** `income_resolution` присутствует; `effective_include_count == len(lines)`.
* **T24:** `salary_only` + negative free_remainder path — `ok: true`, warning сохраняется.
* **T25:** API 200 + post-filter `salary_only` — пересчитанные `free_remainder` / `base_share` (mock body с двумя income lines).
* **T26:** mapping include = только зарплата; `include_income_matches: ["Взнос Николая"]` — partner contribution в `lines`; `effective_include_count` увеличился; строка не в `excluded_income`.
* **T27:** `include_income_matches: ["Unknown"]` — tool error (0 matches).
* **T28:** ambiguous `include_income_matches` (две INC-статьи с общим substring) — tool error.
* **T29:** `include_income_matches` на статью из mapping `exclude` — статья в `lines`, не в `excluded_income` (D-08).
* **T30:** `include_income_matches` с одним и тем же match дважды — `effective_include_count` не увеличивается дважды (D-16).
* **T31:** mapping `exclude` + `include_income_matches` + `exclude_income_matches` на одну статью — не в `lines`; `reason: override:exclude` (цепочка приоритетов D-08 → D-15 → D-18).

## Приёмочная проверка

### Предусловия

* API `prod`; `finance_api_connect` → `data_profile == prod`.
* План июля 2026: зарплата **4740.18 €**, взнос Николая **226.68 €**.

### A1 — salary only

**Действие:** `household_base_share({ "period": "2026-07", "income_mode": "salary_only" })`.

**Ожидаемый результат:** `household_income.total == 4740.18`; взнос в `excluded_income`.

### A2 — salary + partner contribution

**Действие:** `household_base_share({ "period": "2026-07", "income_mode": "salary_plus_partner_contribution" })`.

**Ожидаемый результат:** `household_income.total == 4966.86`; совпадает с [household-base-share.md](../../../assistant/35-finance-assistant/working/2026-07/household-base-share.md) шаг 2.

### A3 — default unchanged

**Действие:** `household_base_share({ "period": "2026-07" })` без новых параметров.

**Ожидаемый результат:** тот же ответ, что до FIN-121 (mapping superset).

## Связь с FIN-114

[FIN-114](https://alexeielizarov.atlassian.net/browse/FIN-114) не блокирует FIN-121. После FIN-114 post-filter income по-прежнему работает на EUR totals из API.

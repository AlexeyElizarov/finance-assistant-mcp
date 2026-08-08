# MCP `query_plan_fact` — подсказки при not-found и ambiguous article

**Связь:** [FIN-122](https://alexeielizarov.atlassian.net/browse/FIN-122); родитель [FIN-26](https://alexeielizarov.atlassian.net/browse/FIN-26); **Relates** [FIN-29](https://alexeielizarov.atlassian.net/browse/FIN-29) (combined plan-fact query), [FIN-120](https://alexeielizarov.atlassian.net/browse/FIN-120) (паттерн enriched tool error).

**Домен:** ops plan-fact — [monthly-close-api/index.md](../../../assistant/35-finance-assistant/ops/index.md); prod-триггер — июль 2026 (`P001` вместо `P0001` Заработная плата).

**Статус:** Утверждено (2026-07-12, rev.3)

## Назначение

`query_plan_fact` резолвит `article` как case-insensitive **подстроку** имени статьи (`GET /api/v1/budget/items`). При 0 или 2+ совпадениях tool error содержит только краткий текст (`Статья бюджета не найдена…`, `Неоднозначно --article '…': имя1, имя2`) — без `budget_item_id`, `operation_category_id` и без подсказок по уточнению. Ops вынужден перебирать MCP-вызовы вручную (напр. `P001` не матчится, каноническая зарплата — `P0001` / «Заработная плата»).

**Критерий приёмки:** not-found или ambiguous `article` → tool error на **русском** с перечнем кандидатов (`budget_item_id`, имя, `operation_category_id`) и явными шагами уточнения (`budget_item_id`, более длинная подстрока); при успешном resolve структура JSON-ответа **без изменений**; единственное намеренное изменение success-path — приоритет exact match (см. **D-06**).

## Объём и границы

### Входит в объём

* Расширение `resolve_budget_item_id()` в `scripts/query-plan-fact.py`: enriched errors при 0 и 2+ совпадениях; exact match перед substring (**D-06**).
* Helper(ы) ранжирования кандидатов: match по `operation_category_id`, category-id alias hint (см. **D-04**), fuzzy по имени.
* Обновление description tool `query_plan_fact` в `server.py` (упоминание подсказок при not-found/ambiguous).
* Unit-тесты: not-found с candidates, ambiguous с ids, category-id alias hint (`P001` → `P0001`), happy path + backward compat + exact-match priority.
* Обновление `mcp-gaps.md`; снятие label `mcp-gap` при Done.

### Не входит в объём

* Silent auto-fix (`P001` не превращается в `P0001` автоматически).
* Единый **ArticleResolver** для других tools — follow-up (**D-07**).
* Новый MCP tool «list budget articles».
* Backend изменения API / новые поля в `GET /budget/items`.
* Fuzzy match по транзакциям или plan-items (только каталог `budget_items`).

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| `resolve_budget_item_id()` | Substring по `name`; 0 → однотипный `RuntimeError` | Нет id, category, candidates |
| Ambiguous | Имена через запятую | Нет `budget_item_id`; нет hint по disambiguation |
| Category id в `article` | Не используется | `P001` ≠ подстрока «Заработная плата» |
| `server.py` handler | Пробрасывает `resolve_budget_item_id` exception | Сырой текст |
| Unit-тесты | Отсутствуют для `query-plan-fact.py` | Нет регрессии resolve |

## Обратная совместимость

**Структура JSON-ответа** `query_plan_fact` при успехе **без изменений**.

**Успешный resolve:**

* `budget_item_id` (UUID) — **без изменений**.
* Ровно одна substring — **без изменений** (тот же `(budget_item_id, name)`).
* **Намеренное изменение (D-06):** exact match по полному имени имеет приоритет над substring. Если ровно одна ACT-статья с `normalize_match_text(name) == needle`, resolve успешен **даже когда** имя одновременно является подстрокой других статей. Пример: `article: "Заработная плата"` при двух substring-совпадениях («Заработная плата», «Заработная плата Николай») → success на exact, тогда как раньше был ambiguous.

**Tool error:** меняется только **текст** при not-found и ambiguous (enriched hints). CLI `query-plan-fact.py` получает те же enriched errors (parity MCP).

## Целевое поведение

### Нормализация `normalize_match_text` (D-08)

Перед сравнением **имён** и `article`:

```
strip(article)
схлопнуть последовательности пробелов до одного пробела
casefold()
```

Применяется к `article` (→ `needle`) и к `name` каждой статьи. Примеры: `"  заработная плата"`, `"Заработная   плата"`, `"Заработная плата "` → одинаковый `needle`.

**Не применяется** к `operation_category_id` — для category-id сравнения только `strip()` (**D-04**).

### Pipeline / формула

```
catalog = load_budget_items_catalog(api)          # один GET /budget/items; D-03
if budget_item_id:
    return resolve_by_uuid(api, budget_item_id)   # без изменений

if not article:
    raise ValueError("Укажите article или budget_item_id")

needle = normalize_match_text(article)
active_catalog = [row for row in catalog if row.status == "ACT"]  # D-02

exact_matches = [
    row for row in active_catalog
    if normalize_match_text(row.name) == needle
]
if len(exact_matches) == 1:
    return (id, name)                               # D-06 — success
if len(exact_matches) > 1:
    sorted_matches = sort_ambiguous_rows(exact_matches)         # D-09
    raise format_ambiguous_article_error(article, sorted_matches)

substring_matches = [
    row for row in active_catalog
    if needle in normalize_match_text(row.name)
]
if len(substring_matches) == 1:
    return (id, name)                               # success — как раньше

if len(substring_matches) > 1:
    sorted_matches = sort_ambiguous_rows(substring_matches)     # D-09
    raise format_ambiguous_article_error(article, sorted_matches)

# 0 substring matches → not-found
candidates = rank_article_candidates(article, active_catalog)   # D-01, D-04
raise format_not_found_article_error(article, candidates)
```

`load_budget_items_catalog` — кэш на один вызов `resolve_budget_item_id` (не глобальный).

Ветки exact и substring **взаимоисключающие**: при `len(exact_matches) > 1` substring не вычисляется.

### Сортировка строк в ошибках

**Ambiguous** — `sort_ambiguous_rows` (**D-09**):

```
sort key = (name case-insensitive ASC, budget_item_id ASC)
```

**Not-found candidates** — `sort_candidate_rows` (**D-09**):

```
sort key = (score DESC, name case-insensitive ASC, budget_item_id ASC)
```

Строки в теле tool error **выводятся в этом порядке** (стабильные unit-тесты). Отбор top 5: после `sort_candidate_rows`, взять первые 5 (**D-01**).

### Ранжирование кандидатов `rank_article_candidates`

Вход: исходный `article` (как передан), `active_catalog`.

Для каждой строки вычислить **максимальный** score (одна строка — один score):

| Rank | Условие | score |
| ---- | ------- | ----- |
| 1 | `operation_category_id.strip() == article.strip()` (case-sensitive; **без** `casefold`, **D-04**) | 100 |
| 2 | `operation_category_id` и `article` — category-id shaped (**D-04**) AND Levenshtein distance ≤ 1 (insert / delete / replace; **без** transposition) | 90 |
| 3 | `normalize_match_text(name) == needle` | 80 |
| 4 | `needle in normalize_match_text(name)` | 70 |
| 5 | token overlap: любое слово `name` (split по пробелам после `normalize_match_text`) начинается с `needle` (len≥3) | 60 |
| 6 | `SequenceMatcher(None, normalize_match_text(article), normalize_match_text(name)).ratio() ≥ 0.6` | 50 + ratio×10 |

Строка попадает в candidates, если score > 0.

**Отбор top 5 (D-01):** уникальные по `budget_item_id`, `sort_candidate_rows`, взять **первые 5**.

Если `article` — category-id shaped и rank-1 пуст, rank-2 hits **всё равно** включаются (alias hint `P001` → `P0001`).

### MCP: входные параметры

Без изменений (`profile`, `base`, `article`, `budget_item_id`, `budget_version_id`, `date_from`, `date_to`, `transactions`).

### MCP: ответ / side effects

Успешный ответ — без изменений.

**Шаблоны tool error** (`RuntimeError`, русский текст):

**Not-found** (`kind: article_not_found`):

```
Статья бюджета не найдена по article {article!r}.

Возможные статьи (до 5):
  - {budget_item_id} | {name} | категория {operation_category_id}
  ...

Уточните: budget_item_id=<uuid> или более точная подстрока имени.
```

Если candidates пуст — блок «Возможные статьи» опускается; остаётся первая и последняя строки. Порядок строк кандидатов = **D-09** (`sort_candidate_rows`).

**Ambiguous** (`kind: article_ambiguous`):

```
Неоднозначно article {article!r} — найдено {n} статей:

  - {budget_item_id} | {name} | категория {operation_category_id}
  ...

Уточните: budget_item_id=<uuid> одного из вариантов или более длинная подстрока (напр. {longest_shared_prefix_example}).
```

Порядок строк matches = **D-09** (`sort_ambiguous_rows`: `name`, затем `budget_item_id`).

`longest_shared_prefix_example` — **общий префикс всех** имён из ambiguous matches (case-insensitive, после `normalize_match_text`); min length 4; если < 4 — первые 20 символов самого длинного имени + `…`.

### Резолв / валидация

| Ситуация | Поведение |
| -------- | --------- |
| 1 exact match (D-06) | Success |
| 2+ exact matches | Tool error ambiguous + **все** exact matches с id |
| 0 substring matches | Tool error not-found + candidates (может быть пусто) |
| 2+ substring matches | Tool error ambiguous + **все** substring matches с id |
| 1 substring match | Success (как сейчас) |
| `budget_item_id` задан | Success по UUID (как сейчас) |
| Category alias (`P001`) | **Не** auto-resolve; только candidate/hint в not-found |

### Конфликты

Не применимо (нет merge нескольких resolve-путей в одном вызове).

### Инварианты (после pipeline)

1. При **неизменённых** условиях success (UUID, единственная substring без competing exact) — тот же `(budget_item_id, name)`, что и до FIN-122.
2. Not-found и ambiguous **всегда** tool error — никогда warning/partial success.
3. Каждая строка кандидата/match содержит **все три** поля: `budget_item_id`, `name`, `operation_category_id`.
4. Candidates — только `status == "ACT"` (D-02).
5. Порядок строк в error body детерминирован (**D-09**).

## Открытые решения

Открытых решений нет.

## Зафиксированные решения

| ID | Вопрос | Решение |
| -- | ------ | ------- |
| D-01 | Лимит кандидатов при not-found | Не более **5** кандидатов; `sort_candidate_rows` (D-09); взять первые 5 |
| D-02 | Фильтр каталога | Только `status == "ACT"` для match и candidates; INA не предлагать |
| D-03 | Один GET каталога | Ровно один `GET /api/v1/budget/items` на вызов `resolve_budget_item_id`; ranking in-memory |
| D-04 | Category-id alias | Hint-only. Category-id shaped: regex `^[PCSI]\d{3,5}$`. Сравнение `operation_category_id` — только `strip()`, **без** `casefold`. Levenshtein ≤ 1 (insert/delete/replace; без transposition). **Без** silent resolve |
| D-05 | Язык ошибок | Русский (parity существующих сообщений `query_plan_fact`) |
| D-06 | Приоритет exact match | До substring: `normalize_match_text(name) == needle`. Ровно одно совпадение → success; 2+ exact → ambiguous |
| D-07 | Общий resolver | В FIN-122 общий модуль **не** создаётся. Унификация через единый **ArticleResolver** — отдельный follow-up после стабилизации FIN-122 |
| D-08 | Нормализация имён | `normalize_match_text`: strip + схлопывание пробелов + casefold — только для `name` и `article` |
| D-09 | Порядок строк в error | Ambiguous: `name` case-insensitive ASC, `budget_item_id ASC`. Candidates: `score DESC`, `name` case-insensitive ASC, `budget_item_id ASC` |

## Non-goals / guardrails

* Не создавать **ArticleResolver** и не менять `resolve_budget_item_id_for_plan`, `resolve_article_match`, `update_plan_item` в v1.
* Не добавлять параметр `suggest_only` / `fuzzy=true` — подсказки всегда в error path.
* Smoke и unit — **`test`** / mock; **не prod** без явного ops-ok.

## Чеклист тестов

* **T1:** Happy path — одна substring → `(id, name)` как до изменения.
* **T2:** `budget_item_id` UUID → success без влияния list resolve.
* **T3:** Not-found — 0 matches; error содержит «не найдена» и ≥1 candidate с тремя полями.
* **T4:** Not-found `P001` — candidate с `operation_category_id P0001` в top-5 (alias hint, Levenshtein insertion).
* **T5:** Ambiguous — 2+ substring; error перечисляет **все** matches с id и category.
* **T6:** Ambiguous error предлагает `budget_item_id=` и substring hint (общий префикс всех matches).
* **T7:** Exact name (D-06): два substring, одно exact → success на exact, не ambiguous.
* **T8:** Whitespace: `"Заработная   плата"` exact-match → success (D-08).
* **T9:** INA статьи не попадают в matches/candidates.
* **T10:** Tie-break candidates: одинаковый score и name → порядок по `budget_item_id ASC` (D-09).
* **T11:** Top-5: 8 кандидатов score=70 → в error ровно 5 первых по D-09.
* **T12:** Diagnostic failure GET items → проброс исходного `RuntimeError` / API error (не swallow).
* **T13:** Две ACT-статьи с одинаковым `normalize_match_text(name)` → ambiguous (exact branch), substring не вычисляется.

## Приёмочная проверка

### Предусловия

* MCP `finance_api_connect` → `data_profile` = **`test`** или **`cand`**
* Известная статья с plan в периоде (напр. зарплата)

### A1 — Not-found с alias hint

**Действие:** `query_plan_fact({ "date_from": "2026-07", "date_to": "2026-07", "article": "P001", "profile": "test" })`.

**Ожидаемый результат:** `ok: false`; error перечисляет кандидата с `operation_category_id` `P0001` и именем зарплатной статьи; запрос **не** возвращает plan-fact rows.

### A2 — Ambiguous disambiguation

**Действие:** `query_plan_fact` с `article`, дающим 2+ ACT substring (напр. «Сбережения» на fixture/test).

**Ожидаемый результат:** error со списком всех matches с `budget_item_id`; текст «Уточните: budget_item_id=…»; порядок строк стабилен (D-09).

### A3 — Success regression (substring)

**Действие:** `query_plan_fact` с `article: "Заработная"` (единственный substring match) за июль 2026.

**Ожидаемый результат:** JSON с `months[]`, plan/fact как до изменения.

### A4 — Exact match priority (D-06)

**Действие:** `query_plan_fact` с `article: "Заработная плата"` при каталоге, где exact одна, substring — несколько.

**Ожидаемый результат:** success на exact-статье; не ambiguous.

## Связь с другими FIN

| FIN | Связь |
| --- | ----- |
| FIN-29 | Combined query может reuse ArticleResolver позже (follow-up) |
| FIN-120 | Тот же паттерн enriched `RuntimeError`; разный домен (override 422 vs article resolve) |
| FIN-108 / FIN-109 | Parity hints через **ArticleResolver** follow-up |

### Follow-up (вне FIN-122) — Jira FIN-188

| FIN | Summary | Приоритет |
| --- | ------- | --------- |
| [FIN-188](https://alexeielizarov.atlassian.net/browse/FIN-188) | Epic: ArticleResolver unification | High |
| [FIN-189](https://alexeielizarov.atlassian.net/browse/FIN-189) | Shared core + `query_plan_fact` refactor | High (blocks 190–192, 195, FIN-29) |
| [FIN-190](https://alexeielizarov.atlassian.net/browse/FIN-190) | `update_plan_item` parity | High |
| [FIN-191](https://alexeielizarov.atlassian.net/browse/FIN-191) | `household_base_share` parity | Medium |
| [FIN-29](https://alexeielizarov.atlassian.net/browse/FIN-29) | Combined plan-fact query (updated) | Medium |
| [FIN-192](https://alexeielizarov.atlassian.net/browse/FIN-192) | Structured JSON error payload | Medium |
| [FIN-194](https://alexeielizarov.atlassian.net/browse/FIN-194) | Backend duplicate ACT name policy (FIN-96) | Medium |
| [FIN-193](https://alexeielizarov.atlassian.net/browse/FIN-193) | `list_budget_articles` MCP tool (FIN-26) | Low |
| [FIN-195](https://alexeielizarov.atlassian.net/browse/FIN-195) | Resolve metrics / diagnostics | Low |

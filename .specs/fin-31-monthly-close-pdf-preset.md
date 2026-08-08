# MCP preset `monthly_close_prepare` — подготовка месяца с PDF

**Связь:** [FIN-31](https://alexeielizarov.atlassian.net/browse/FIN-31); родитель [FIN-26](https://alexeielizarov.atlassian.net/browse/FIN-26); **Relates** [FIN-101](https://alexeielizarov.atlassian.net/browse/FIN-101).

**Домен:** runbook — [monthly-close-api/index.md](../../../assistant/35-finance-assistant/ops/index.md); skill `finance-monthly-close`; close — [close-policy.md](../../../assistant/35-finance-assistant/ops/close-policy.md).

**Статус:** Утверждено (2026-07-02, rev.4)

## Назначение

Legacy CLI `monthly-close.py` **всегда** генерирует PDF после derive/verify (без флага `--reports`). MCP `process_month` требует явный `reports: true`; в runbook пример подготовки месяца указан с `reports: false`, что расходится со skill `finance-monthly-close`. Оператор и агент должны помнить набор флагов вместо одного пресета.

**Что такое preset (главный ответ на ревью):** это **рекомендуемый MCP-workflow подготовки месяца** (skill `finance-monthly-close`, шаг 1), а **не** побайтовый parity с default-флагами `monthly-close.py`. Из legacy CLI наследуется только одно свойство: **PDF на prepare-пути без отдельного «вспомнить reports»**. Reopen/reopen_neighbors — типовой MC-workflow, не default CLI.

**Критерий приёмки:** один вызов MCP с preset воспроизводит канонический prepare-workflow **с PDF**, без `close`; документирован в runbook; пробел FIN-31 закрыт (label `mcp-gap` снят при Done).

## Архитектура preset (guardrails)

> **Preset — исключительно UX-слой** над существующими аргументами `process_month`. Не добавляет шагов, веток и бизнес-логики в handler; только подставляет defaults до merge.

> **v1 — ровно один preset:** `monthly_close_prepare`. Любой новый preset (`close_month`, `keyword_pass`, …) — **отдельная FIN** с расширением enum и таблицы defaults; не добавлять «заодно» в FIN-31.

> **Merge:** `effective = {**preset_defaults, **explicit_overrides}` — обычный dict update. Относится ко всем типам полей (bool сейчас; string/enum в будущем). Override применяется **только если ключ явно присутствует** в `arguments` tool call (`key in arguments`), иначе значение из preset сохраняется. Это важно: absent key ≠ `false` при активном preset.

> **Имя preset не гарантирует итоговую конфигурацию:** после explicit override сценарий может перестать соответствовать названию preset (напр. `monthly_close_prepare` + `reports: false` → prepare без PDF). Это допустимо; ответственность на вызывающем.

> **`verify_only=true` и preset:** merge формирует полный `effective` config (включая reopen/reports из preset), но handler при `verify_only=true` делает **early exit** — остальные orchestrator flags **не участвуют** в pipeline. Это существующая семантика handler, не новая ветка preset.

## Объём и границы

### Входит в объём

* Параметр **`preset`** на MCP `process_month` (enum из одного значения в v1).
* Значение **`monthly_close_prepare`**: defaults рекомендуемого prepare-workflow (таблица ниже).
* Helper `resolve_process_month_arguments(arguments) -> dict | None` — preset expand + explicit merge; **`None`** если preset отсутствует (**D-11**); unit-тесты.
* Обновление schema/description `process_month` в `server.py`.
* Runbook [index.md](../../../assistant/35-finance-assistant/ops/index.md): канонический вызов с preset; исправление примера «Подготовка месяца».
* Обновление `mcp-gaps.md`; снятие `mcp-gap` при Done.

### Не входит в объём

* Изменение default `reports` для вызовов **без** preset.
* Дополнительные preset (`close_month`, `keyword_pass`, `reopen_and_verify`, `dry_verify`, …).
* C9999 proposal table — [FIN-17](https://alexeielizarov.atlassian.net/browse/FIN-17) / `list_c9999`.
* Новый backend endpoint или изменение `generate_reports()`.
* Deprecation CLI `monthly-close.py` / `fix-month.py`.
* Отдельный tool `prepare_month`.

## Зафиксированные решения

| ID | Решение |
| -- | ------- |
| **D-03** | Имя preset: **`monthly_close_prepare`** (рекомендуемый MCP-workflow, не CLI parity) |
| **D-01** | Механизм: **`preset` на `process_month`**, не отдельный tool |
| **D-02** | Explicit args **override** preset (dict update). После override конфигурация может не соответствовать имени preset — **ожидаемо** |
| **D-04** | Default `reports=false` для вызовов без preset **не менять** |
| **D-05** | Закрытие gap только документацией — **нет**; нужен UX-слой в коде |
| **D-06** | Merge: **`{**preset_defaults, **explicit}`**; override только при **`key in arguments`** |
| **D-07** | v1: **один** preset; новые — отдельная FIN |
| **D-08** | Preset **не добавляет** бизнес-логики; только defaults |
| **D-09** | **`close_phase` без `close=true` в effective config** → tool error до HTTP (не молча игнорировать) |
| **D-10** | При **`verify_only=true`**: flags в effective config допустимы, но pipeline steps handler'ом **не выполняются** (early exit) |
| **D-11** | Helper **не дублирует** handler-defaults: без preset handler читает `arguments` как сейчас; с preset — merge → `effective` |

## Семантика preset `monthly_close_prepare`

### Не parity с CLI defaults

| Аспект | `monthly-close.py` (CLI) | `monthly_close_prepare` (MCP preset) |
| ------ | ------------------------ | ------------------------------------ |
| PDF на prepare-пути | **всегда** (без `--reports`) | **`reports: true`** ← единственный «legacy»-наследник |
| `--reopen-neighbors` | опционально, default off | **`true`** — типовой MC tail (skill, runbook) |
| `--reopen` | опционально, default off | **`true`** — типовой prepare-workflow |
| `--close` | opt-in | **`false`** (close-policy) |
| `--skip-import` | opt-in | **`false`** |
| `--verify-only` | opt-in | **`false`** |

Preset = **«рекомендуемый workflow подготовки месяца в MCP»**, не зеркало CLI default flags.

### Defaults preset `monthly_close_prepare`

| Поле | Значение |
| ---- | -------- |
| `reopen_neighbors` | `true` |
| `reopen` | `true` |
| `reports` | `true` |
| `close` | `false` |
| `skip_import` | `false` |
| `verify_only` | `false` |

Pipeline после merge: тот же handler `_handle_process_month` (reopen → import → derive → verify → PDF → optional close).

### Допустимые explicit override

Любое поле orchestrator-а `process_month`, **кроме** `preset`, `period`, `profile`, `base`:

| Поле | Override | Примечание |
| ---- | -------- | ---------- |
| `reopen_neighbors` | да | |
| `reopen` | да | |
| `skip_import` | да | prepare без import — допустимо, имя preset уже не буквально |
| `reports` | да | prepare без PDF — допустимо |
| `close` | да | close-policy: только явная команда пользователя |
| `verify_only` | да | см. **D-10**; имя preset не буквально |
| `close_phase` | да | только при effective `close: true`; иначе **D-09** error |
| `apply_keywords` | да | string; отдельный проход после C9999 |

Запрещено: неизвестный `preset`; `close_phase` в arguments при effective `close: false`.

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| CLI `monthly-close.py` | import → derive → verify → **всегда PDF** → optional `--close` | PDF — эталон для gap |
| CLI `fix-month.py` | PDF только с `--reports` | Другая семантика |
| MCP `process_month` | `reports` default **false** (absent key) | Нужно помнить флаг |
| Runbook `index.md` | Пример: `reports: false` | Противоречит skill |
| Skill `finance-monthly-close` | reopen_neighbors + reopen + reports | Нет machine-readable preset |
| `mcp-gaps.md` | FIN-31 с label `mcp-gap` | Gap открыт в Jira |

## Целевое поведение

### Вход (дополнение)

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `preset` | string | нет | v1 enum: **`monthly_close_prepare`** |

### Алгоритм merge (**D-11**)

**Без preset** — helper **не вызывается** (или возвращает `None`); handler читает `arguments` с существующей семантикой `bool(arguments.get(...))`. Helper **не знает** handler-defaults.

**С preset:**

1. `preset` присутствует, но не `monthly_close_prepare` → tool error до HTTP.
2. `base = PRESET_MONTHLY_CLOSE_PREPARE` (таблица defaults).
3. `explicit = {k: arguments[k] for k in OVERRIDABLE_KEYS if k in arguments}`.
4. `effective = {**base, **explicit}`.
5. Handler читает orchestrator flags из `effective` (не из `arguments`).

**Validation (оба пути, до HTTP):**

* Если `"close_phase" in arguments` и effective/actual `close` is falsy → tool error (**D-09**).
  * С preset: `close` из `effective`.
  * Без preset: `close` из `bool(arguments.get("close"))`.

### Канонический вызов (runbook + skill)

```json
process_month({
  "profile": "prod",
  "period": "2026-02",
  "preset": "monthly_close_prepare"
})
```

Эквивалент **без** preset (для сверки тестов и документации):

```json
process_month({
  "profile": "prod",
  "period": "2026-02",
  "reopen_neighbors": true,
  "reopen": true,
  "reports": true
})
```

### Примеры override

**Prepare без PDF** (имя preset не буквально; допустимо):

```json
{ "preset": "monthly_close_prepare", "period": "2026-02", "reports": false }
```

**Prepare без import** (fix-pass после keywords):

```json
{ "preset": "monthly_close_prepare", "period": "2026-02", "skip_import": true }
```

**Множественный override:**

```json
{
  "preset": "monthly_close_prepare",
  "period": "2026-02",
  "reports": false,
  "reopen": false,
  "skip_import": true
}
```

**Verify-only поверх preset** (merge OK; pipeline = только verify, **D-10**):

```json
{ "preset": "monthly_close_prepare", "period": "2026-02", "verify_only": true }
```

### Выход

Без изменений контракта `process_month`. При effective `reports: true` в log — `log.reports` с путями PDF:

```
33-financial-reports/prod-reports/ГГГГ-ММ/{slug}.pdf
```

### Ошибки

| Условие | Поведение |
| ------- | --------- |
| Неизвестный `preset` | tool error |
| `close_phase` в arguments, effective `close: false` | tool error (**D-09**) |
| Import fail / readiness / C9999+close | как сейчас |

## Тесты

| ID | Сценарий |
| -- | -------- |
| T1 | `resolve_process_month_arguments({preset, period})` → effective flags = preset defaults |
| T2 | Unknown preset → ValueError |
| T3 | preset + explicit `reports: false` → effective `reports` false; остальные из preset |
| T4 | preset + explicit `verify_only: true` → effective `verify_only` true (merge, не error) |
| T5 | preset без explicit bool keys → `reports` true (**regression:** `key in arguments`, не absent=false) |
| T6 | Handler mock: preset → `generate_reports` вызван |
| T7 | Без preset — поведение unchanged (`reports` false при absent key) |
| T8 | **Multiple overrides:** preset + `reports: false` + `reopen: false` + `skip_import: true` → корректный merge |
| T9 | Schema: `preset` enum `["monthly_close_prepare"]` |
| T10 | `close_phase` в arguments без effective `close: true` → validation error (**D-09**) |
| T11 | preset + `close: true` + `close_phase: "preliminary"` → validation passes; effective `close_phase` preliminary |

## Done when (Jira)

- [x] `preset: monthly_close_prepare` на `process_month`
- [x] Helper merge (**D-11**: без preset → handler unchanged) + validation **D-09**
- [x] Unit-тесты T1–T11
- [x] Runbook `index.md` — канонический вызов; семантика «workflow, не CLI parity»
- [x] `mcp-gaps.md` — FIN-31 закрыт
- [x] Label `mcp-gap` снят с FIN-31 при Done

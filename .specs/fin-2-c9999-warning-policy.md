# MCP `process_month` — C9999 как предупреждение, не hard stop

**Связь:** [FIN-2](https://alexeielizarov.atlassian.net/browse/FIN-2); родитель [FIN-1](https://alexeielizarov.atlassian.net/browse/FIN-1); **Blocks** [FIN-7](https://alexeielizarov.atlassian.net/browse/FIN-7) (range import/close).

**Домен:** предложение по разнесению — [c9999-proposal-policy.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/c9999-proposal-policy.md); close — [close-policy.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/close-policy.md); двухфазное закрытие — [period-close.md](../../../assistant/35-finance-assistant/methodology/period-close.md).

**Статус:** Утверждено (2026-07-08)

## Назначение

Backend readiness трактует C9999 как **warn** (non-blocking): `classification_c9999` не мешает `ready: true` при прочих blocking checks pass. MCP `verify_period` и `process_month` сегодня **смешивают** C9999 с blocking issues → `verify.ok: false` и `process_month` с `ok: false` / exit 1 даже без `close: true`. При `close: true` guard отклоняет close при `expense_c9999_count > 0` **без учёта** `close_phase`, что ломает штатный **preliminary close** с осознанно оставленными misc-расходами (prod 2026-06: Solmecke, Ministerium, BRITISHWAY; workaround — пустой `apply_keywords`).

**Критерий приёмки:** C9999 — предупреждение в prepare/import/batch; **final** close допускается только при `verify.readiness.ready == true` (backend) и `expense_c9999_count == 0` после import/derive и любых keywords; **preliminary** close допускается при `ready: true` и явном `c9999_acknowledged: true` после review по [c9999-proposal-policy.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/c9999-proposal-policy.md).

## Объём и границы

### Входит в объём

* Разделение blocking **issues** и non-blocking **warnings** в `verify_period()` (`monthly_close_lib.py`).
* Семантика top-level `ok` в MCP `process_month` и `verify_month` (через `verify.ok` для non-close; close — отдельно, см. ниже).
* Новый флаг `c9999_acknowledged` в `process_month` (schema + orchestrator flags).
* Семантика **effective** `apply_keywords` (непустой файл с правилами).
* Guard close: `close_phase` × C9999 × effective keywords × `c9999_acknowledged`.
* Unit-тесты preliminary vs final vs non-close.
* Обновление `mcp-gaps.md`, [close-policy.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/close-policy.md) (секция C9999 / preliminary).
* Выравнивание CLI `scripts/fix-month.py` и `scripts/monthly-close.py` с той же политикой (тот же repo, те же helpers).

### Не входит в объём

* Backend изменения readiness / close API ([FIN-24](https://alexeielizarov.atlassian.net/browse/FIN-24) и др.).
* `list_c9999` — [FIN-17](https://alexeielizarov.atlassian.net/browse/FIN-17) (уже реализован).
* Range tool `process_month` по диапазону — [FIN-7](https://alexeielizarov.atlassian.net/browse/FIN-7) (заблокирован этой задачей).
* Автоматическая проверка «оператор видел таблицу» — только явный флаг ack; enforcement чата остаётся в [c9999-proposal-policy.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/c9999-proposal-policy.md).
* `c9999_acknowledged` как bypass для **final** close (v1 и v2 — только preliminary).

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| Backend readiness | `classification_c9999` → **warn**, non-blocking | Корректно |
| `verify_period()` | C9999 > 0 → `issues[]` → `ok: false` | Расхождение с readiness |
| `process_month` без `close` | `ok: verify["ok"]` | Batch re-import падает на C9999 warn |
| `process_month` с `close: true` | Блок при C9999 > 0 без `apply_keywords` | Не различает preliminary / final |
| `apply_keywords` | Путь к файлу считается «заданным» даже для `{}` | Пустой JSON обходит guard |
| `fix-month.py` / `monthly-close.py` | exit 1 при C9999 + verify.ok false; close guard без phase | То же для CLI |
| Workaround prod 2026-06 | Пустой `apply_keywords` для bypass guard | Нужен явный ack |

## Зафиксированные решения

| ID | Решение |
| -- | ------- |
| **D-01** | `c9999_acknowledged` — имя флага (positive ack). |
| **D-02** | Close guard по **backend readiness**: `verify["readiness"]["ready"]` из ответа `GET …/readiness`; **не** подменять на `verify.ok`; значение **не кэшируется** и **не вычисляется локально** из `issues`/`warnings`. |
| **D-03** | `apply_keywords` **effective** только если JSON содержит хотя бы одну категорию (ключ объекта), у которой список keywords содержит хотя бы одну строку с `len(s.strip()) > 0`; `{}`, `[]`, `[""]`, `[" "]`, `["\t"]` и т.п. — **не** effective. |
| **D-04** | `apply_keywords` **не** bypass для final close; даёт только шанс снизить C9999 до повторной проверки. |
| **D-05** | `c9999_acknowledged: true` при `close: false` → `ValueError`. |
| **D-06** | `c9999_acknowledged: true` при `close: true` и `close_phase: "final"` → `ValueError` (ack не для final). |
| **D-07** | Обязательный pipeline перед guard: `import` (пропускается при `skip_import`) → `apply_keywords` (если путь передан) → `derive` → `verify_period` → guard. `n` = `verify.classification_summary.expense_c9999_count` из **последнего** `verify`; verify **не** вызывается до derive. |
| **D-08** | CLI `fix-month.py` / `monthly-close.py` — те же правила (**O-05** закрыт). |
| **D-09** | Текст `error` при блокировке final: `"C9999 > 0 — resolve C9999 before final close"`; preliminary без ack/kw: `"C9999 > 0 — apply_keywords or c9999_acknowledged before preliminary close"`. |

## Целевое поведение

### `verify_period()` — issues vs warnings

| Поле | Семантика |
| ---- | --------- |
| `issues` | Только **blocking** для ops-решения «месяц сломан» (MC tail, balances, T13 fail, readiness blocking checks) |
| `warnings` | **Non-blocking**, включая C9999 > 0 |
| `ok` | `len(issues) == 0` (C9999 **не** влияет на `ok`) |
| `readiness` | Проброс backend `GET …/readiness` как есть; `readiness.ready` — **источник правды** для close; не кэшируется и не выводится из `issues`/`warnings` |

C9999 по-прежнему в `classification_summary.expense_c9999_count`; дублирование в `warnings` — человекочитаемая строка, напр. `"C9999: N расходов"`.

**Важно:** `verify.ok` и `readiness.ready` **могут расходиться** (напр. MC tail в `issues` при `readiness.ready: true`). `verify.ok` — индикатор качества периода для оператора и non-close pipeline; решение о возможности close принимается **исключительно** по `readiness.ready` (**D-02**).

### MCP `verify_month`

Проброс `verify` с новыми полями; отдельный top-level `ok` **не** добавлять (вызывающий смотрит `verify.ok`).

### MCP `process_month` — порядок шагов и источник `n`

```text
import          — пропускается при skip_import
→ apply_keywords — только если путь передан; иначе шаг пропущен
→ derive         — всегда после import/keywords
→ verify_period  — всегда после derive; до guard verify не вызывается
→ n = verify.classification_summary.expense_c9999_count
→ C9999 close guard (if close)
→ reports (if requested)
→ close (if close && readiness.ready)
```

Обязательная последовательность (**D-07**). Сценарий `apply_keywords` + `skip_import: true`: keywords → derive → verify → guard по актуальному `n`.

### Effective `apply_keywords` (**D-03**, **D-04**)

| Условие | `keywords_effective` |
| ------- | -------------------- |
| Путь не передан | `false` |
| Файл `{}` или все значения `[]` / строки с `len(s.strip()) == 0` | `false` |
| ≥1 категория с ≥1 keyword, у которой после `strip()` длина > 0 | `true` |

Пустой `apply_keywords` **не** обходит C9999 guard (ни preliminary, ни final). Workaround prod 2026-06 после реализации **недопустим**.

`keywords_effective` логируется в `log.steps` (напр. `keywords_effective: true/false`, `keywords_added: N`).

### MCP `process_month` — top-level `ok`

| Сценарий | `ok` |
| -------- | ---- |
| Import failed | `false` |
| `close=false`, pipeline завершён | `true` если нет blocking `verify.issues` (C9999 — только в `warnings`) |
| `close=true`, `readiness.ready == false` | `false` (**backend readiness**, не `verify.ok`) |
| `close=true`, C9999 guard сработал | `false` + `error` |
| `close=true`, close HTTP ≠ 200 | `false` |
| Иначе | `true` |

В `log.steps.verify` — полный объект verify (с `warnings`).

### Guard close (C9999)

Пусть `n = expense_c9999_count` (из последнего verify), `kw_eff = keywords_effective`, `ack = c9999_acknowledged`.

| `close` | `close_phase` | Условие блокировки |
| ------- | ------------- | ------------------ |
| `false` | — | Нет C9999 guard |
| `true` | `preliminary` | `n > 0` и не `kw_eff` и не `ack` |
| `true` | `final` | `n > 0` |

Final close: `apply_keywords` не снимает guard сам по себе — только если после derive `n == 0`. Если keywords переданы, но `n > 0` остался → блокировка как для final без keywords.

При блокировке — `ok: false`, `error` (**D-09**).

При успешном preliminary close с `ack` и `n > 0` — в `log.steps`: `c9999_acknowledged: true`, `c9999_count: n`.

### Новый параметр `process_month`

| Поле | Тип | Обяз. | По умолч. | Описание |
| ---- | --- | ----- | --------- | -------- |
| `c9999_acknowledged` | boolean | нет | `false` | Оператор подтвердил осознанное оставление C9999 (после review по c9999-proposal-policy). Только `close: true` + `close_phase: "preliminary"`. |

**Валидация:**

| Сценарий | Поведение |
| -------- | --------- |
| `c9999_acknowledged: true`, `close: false` | `ValueError` (**D-05**) |
| `c9999_acknowledged: true`, `close: true`, `close_phase: "final"` | `ValueError` (**D-06**) |

### Связь с c9999-proposal-policy

1. Агент показывает таблицу (`list_c9999` или счётчик + строки).
2. Оператор подтверждает misc / правки → keywords **или** ack для preliminary.
3. Preliminary close: `process_month({ close: true, close_phase: "preliminary", c9999_acknowledged: true })`.
4. Final close: после `apply_keywords` (effective) + `skip_import` + derive — повторный `process_month` с `close: true`, `close_phase: "final"` **только если** `expense_c9999_count == 0`.

## Открытые решения

| ID | Вопрос | Предложение |
| -- | ------ | ----------- |
| **O-01** | Отдельный top-level `warnings` в ответе `process_month`? | Нет; достаточно `log.steps.verify.warnings`. |
| **O-03** | MC tail missing в `issues` — оставить blocking? | **Да** (не менять семантику non-C9999 checks). |
| **O-04** | `verify_month` — менять контракт ответа? | Только вложенный `verify`; без breaking rename полей. |

## Тесты

| ID | Сценарий |
| -- | -------- |
| T1 | `verify_period`: C9999 > 0, readiness ready → `ok: true`, C9999 в `warnings`, не в `issues` |
| T2 | `verify_period`: balances blocking → `ok: false`, issue в `issues` |
| T3 | `process_month` mock: `close=false`, C9999 > 0, import ok → top-level `ok: true` |
| T4 | `process_month`: `close=true`, `close_phase=preliminary`, `n > 0`, `c9999_acknowledged=true`, `readiness.ready=true` → close вызван, `ok: true` |
| T5 | `process_month`: `close=true`, `close_phase=preliminary`, `n > 0`, без ack и без `kw_eff` → `ok: false`, `error` |
| T6 | `process_month`: `close=true`, `close_phase=final`, `n > 0` → `ok: false`, close не вызван |
| T7a | `close=true`, `close_phase=final`: до keywords `n > 0`, после effective keywords + derive `n == 0`, `readiness.ready=true` → close вызван |
| T7b | `close=true`, `close_phase=final`: effective keywords передан, после derive `n > 0` → `ok: false`, close не вызван |
| T8 | `c9999_acknowledged=true` без `close=true` → `ValueError` (**D-05**) |
| T9 | `c9999_acknowledged=true`, `close=true`, `close_phase=final` → `ValueError` (**D-06**) |
| T10 | Пустой `{}` `apply_keywords` при `n > 0`, preliminary без ack → guard блокирует (`kw_eff=false`) |
| T11 | Regression: `n == 0`, `close=true`, `close_phase=final`, `readiness.ready=true` → close без C9999 guard |
| T12 | `readiness.ready=false` при `verify.ok=true` (mock) → close не вызван, `ok: false` |
| T13 | `readiness.ready=true` при `verify.ok=false` (mock, напр. MC tail в `issues`), `close=true`, C9999 guard pass → **close вызван**, `ok: true` |

## Done when (Jira)

- [x] `verify.ok` определяется только `verify.issues`; close decision — только `readiness.ready` (backend, без локального вывода)
- [x] `process_month`: effective `apply_keywords`; guard по `close_phase`; `c9999_acknowledged`; close по `readiness.ready`
- [x] CLI `fix-month.py` / `monthly-close.py` выровнены
- [x] Unit-тесты T1–T13 (`tests/test_fin2_c9999_warning_policy.py`; mock, без cand/prod)
- [x] `mcp-gaps.md` и `close-policy.md` обновлены
- [x] Норма проверки: smoke только `test`; cand/prod не трогать ([index.md](../../../assistant/35-finance-assistant/methodology/monthly-close-api/index.md))
- [ ] FIN-7 разблокирован для range work после merge

## Проверка реализации

| Уровень | Где | Профиль API |
| ------- | --- | ----------- |
| Unit | `mcp-servers/finance-assistant/tests/` | mock, без HTTP |
| Smoke MCP (если нужен вручную) | `process_month` / `verify_month` | **`test` only** |
| Smoke script (FIN-2) | `tests/smoke_fin2_test_profile.py` | **`test`** @ live API |
| Ops close | по явной команде | **`prod`** |

**Запрещено** для закрытия задачи FIN-2: import / derive / close на **`cand`** и **`prod`** «для проверки».

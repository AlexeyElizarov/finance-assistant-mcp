# MCP-инструменты истории валюты бюджета домохозяйства

**Статус:** Утверждено (2026-08-23, rev.2).

**Jira:** [FIN-332](https://alexeielizarov.atlassian.net/browse/FIN-332)

## Назначение

- История валюты бюджета домохозяйства доступна по HTTP, но операторы не могут вести её через MCP без прямого REST.
- Нужны тонкие MCP-обёртки списка и добавления записей истории поверх утверждённого HTTP API.

## Критерий приёмки

- История валюты бюджета домохозяйства доступна через MCP для чтения списка и добавления записи согласно целевому поведению.
- Доменные ошибки HTTP API пробрасываются в ошибку инструмента без потери кода ошибки.
- Поведение покрыто функциональными тестами.
- Справочник доступных инструментов MCP обновлён.

## Объём и границы

### Входит в объём

| Входит | Не входит |
| ------ | --------- |
| MCP-инструмент списка записей истории валюты бюджета домохозяйства | Модель, миграция и слой сохранения (FIN-331) |
| MCP-инструмент добавления одной записи истории | HTTP API истории (FIN-334) |
| Проброс `profile` / `base` как у прочих master-data tools | Upsert, изменение и удаление записи (insert-only, FIN-334) |
| Pre-HTTP проверка presence/strip обязательных строковых аргументов | Пакетное добавление, чтение одной записи, HTTP-резолвер периода |
| Проброс кодов ошибок HTTP API в ошибку инструмента | Начальное заполнение на постоянных профилях (FOPS-38) |
| Модуль тонких обёрток, регистрация в MCP-сервере, unit-тесты с mock API | Выравнивание плановых курсов (FIN-333) |
| Обновление `mcp-gaps.md` | OpenUI5; канон переноса остатков (KNOW-43) |
| | Один инструмент с перечислением действия |

## Факт реализации (до)

| Область | Текущее поведение | Проблема |
| ------- | ----------------- | -------- |
| HTTP | FIN-334: `GET`/`POST` коллекции `/api/v1/households/{household_id}/budget-currencies` | Нет MCP-поверхности |
| MCP | FIN-240: households / members / bank accounts; истории валюты бюджета нет | Ops не ведут историю mcp-only |
| `mcp-gaps.md` | Инструментов истории нет | Label `mcp-gap` на FIN-332 |

## Целевое поведение

### Паритет поверхности (D-01)

| Инструмент MCP | Операция HTTP | Смысл |
| -------------- | ------------- | ----- |
| `list_household_budget_currencies` | `GET /api/v1/households/{household_id}/budget-currencies` | Список |
| `create_household_budget_currency` | `POST /api/v1/households/{household_id}/budget-currencies` | Добавление одной записи |

- Публичной HTTP-поверхности FIN-334 соответствуют ровно два MCP-инструмента с той же функциональной семантикой.
- Изменение, удаление, upsert, batch, get-by-`valid_from` и резолвер периода в MCP не вводятся: их нет в HTTP FIN-334.
- Один инструмент с перечислением действия не используется.
- Доменные правила нормализации и инвариантов наследуются из FIN-331 / FIN-334 без дублирования в MCP.

### Общий конвейер (D-02)

```
1. получить сессию API (profile, base)
2. проверить аргументы MCP до HTTP (D-04)
3. собрать путь и тело
4. выполнить GET или POST
5. при ожидаемом коде HTTP (list: только 200; create: только 201) и теле-объекте —
   обернуть коллекцию или сущность в конверт ok / profile / base
6. иначе — RuntimeError с телом ответа API (формат format_api_error как у FIN-240 / FIN-293)
7. сетевые сбои пробросить без дополнительной обёртки
```

- Порядок шагов фиксирован.
- Успех определяется только ожидаемым кодом HTTP операции и тем, что тело — объект.
- Любой HTTP status, отличный от ожидаемого `200` для list и `201` для create, включая другие `2xx`, считается нарушением контракта и даёт `RuntimeError`.
- Инварианты FIN-331 / FIN-334 в MCP не дублируются.

### Модуль и регистрация (D-03)

- Логика обёрток размещается в `scripts/household_budget_currencies.py`.
- Обработчики и схемы инструментов регистрируются в `server.py`.
- После реализации инструменты перечисляются в `mcp-gaps.md` как доступные; label `mcp-gap` снимается с FIN-332.

### Валидация до HTTP (D-04)

- Схема MCP проверяет типы и обязательность объявленных аргументов; незаявленные аргументы отклоняются схемой.
- Отсутствие обязательного ключа (`household_id`; для create также `valid_from` и `currency`) отклоняется схемой MCP до обработчика; HTTP не вызывается. Это не `ValueError` обработчика.
- Обработчик для полей ниже проверяет непустоту через `strip()` только если ключ уже прошёл схему.
- `strip()` используется только для проверки непустоты; в путь и в JSON тела уходит исходное значение аргумента без замены на результат `strip()`.
- Для обоих инструментов: если `household_id` после `strip` пуст — `ValueError` без вызова API.
- Для `create_household_budget_currency`: если `valid_from` или `currency` после `strip` пусты — `ValueError` без вызова API.
- Типы обработчик не дублирует.
- Формат `valid_from`, синтаксис валюты, уникальность, соседние валюты и прочие доменные инварианты проверяет только backend (FIN-331 / FIN-334).

### Тело создания (D-05)

- Тело создания всегда содержит ровно `valid_from` и `currency`.
- Оба поля обязательны схемой MCP.
- Поля `household_id`, `created_at`, `profile_id` в аргументы создания не входят и в тело не передаются.
- Пустая строка (после проверки `strip`) на обязательных строках D-04 даёт `ValueError` без вызова API; пустая строка не преобразуется в `null`.

### Инварианты (D-06)

- MCP не ослабляет инварианты HTTP API истории валюты бюджета.
- MCP не нормализует значения аргументов перед HTTP (в том числе не подменяет их результатом `strip()`); доменную нормализацию выполняет backend.
- MCP не нормализует значения и структуру REST-ответа, кроме конверта `ok` / `profile` / `base` и ключа коллекции или сущности.
- Существующие MCP-инструменты не меняют семантику.

### Список (D-07)

#### Вход

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `profile` | string | нет | Профиль данных; по умолчанию `prod` |
| `base` | string | нет | Базовый URL API; по умолчанию из сессии |
| `household_id` | string | да | Идентификатор домохозяйства в пути |

#### Результат

- При успехе (HTTP 200) ответ инструмента: `ok: true`, `profile`, `base`, коллекция `budget_currencies`.
- Пустой список допустим и сохраняет `ok: true`, если домохозяйство существует.
- Элементы коллекции — объекты ответа FIN-334 as-is (порядок и поля задаёт API).
- Несуществующее домохозяйство даёт ошибку инструмента по ответу API (HTTP 404, `household_not_found`).

#### Пример успеха

```json
{
  "ok": true,
  "profile": "cand",
  "base": "http://127.0.0.1:8000",
  "budget_currencies": [
    {
      "household_id": "default",
      "valid_from": "2025-01-01",
      "currency": "EUR",
      "created_at": "2026-08-23T09:00:00Z"
    }
  ]
}
```

### Добавление записи (D-08)

#### Вход

| Поле | Тип | Обяз. | Описание |
| ---- | --- | ----- | -------- |
| `profile` | string | нет | Профиль данных; по умолчанию `prod` |
| `base` | string | нет | Базовый URL API; по умолчанию из сессии |
| `household_id` | string | да | Идентификатор домохозяйства в пути |
| `valid_from` | string | да | Дата вступления в силу (`YYYY-MM` или `YYYY-MM-DD` с днём `01` — норма FIN-334); обязателен схемой MCP |
| `currency` | string | да | Код валюты (нормализация — backend); обязателен схемой MCP |

#### Результат

- Успешное создание соответствует только HTTP 201 (иной status, включая `200`, — `RuntimeError` по D-02 / D-09).
- При успехе ответ инструмента: `ok: true`, `profile`, `base`, объект `budget_currency` (тело ответа FIN-334 as-is).
- Тело запроса всегда ровно `{ "valid_from", "currency" }` (D-05).
- Повтор той же нормализованной `valid_from` даёт ошибку инструмента по API (HTTP 409, `budget_currency_duplicate_valid_from`).
- Совпадение валюты с непосредственным предшественником или последователем даёт ошибку инструмента по API (HTTP 409, `budget_currency_unchanged`).
- `valid_from` с днём, отличным от `01`, даёт ошибку инструмента по API (HTTP 422, `budget_currency_valid_from_invalid`).
- Несуществующее домохозяйство даёт ошибку инструмента по API (HTTP 404, `household_not_found`).
- Прочие ошибки валидации тела/формата — по API (HTTP 422, `validation_error`).

#### Пример успеха

```json
{
  "ok": true,
  "profile": "cand",
  "base": "http://127.0.0.1:8000",
  "budget_currency": {
    "household_id": "default",
    "valid_from": "2025-01-01",
    "currency": "EUR",
    "created_at": "2026-08-23T09:00:00Z"
  }
}
```

### Ошибки (D-09)

| Ситуация | Поведение |
| -------- | --------- |
| Отсутствует обязательный ключ аргумента | ошибка валидации схемы MCP; HTTP не вызывается |
| Ключ есть, но строка после `strip` пуста | `ValueError` до HTTP |
| HTTP status не равен ожидаемому для операции (`200` list / `201` create), включая прочие `2xx` | `RuntimeError` — `METHOD path -> HTTP {status} …` с кодом/`message` API |
| Ожидаемый HTTP status, но тело не объект | `RuntimeError` с телом ответа API |
| HTTP 404 `household_not_found` | `RuntimeError` с телом API |
| HTTP 409 `budget_currency_duplicate_valid_from` / `budget_currency_unchanged` | `RuntimeError` с телом API |
| HTTP 422 `validation_error` / `budget_currency_valid_from_invalid` | `RuntimeError` с телом API |
| Transport / network | exception из клиента без wrap |

- Код `budget_currency_undefined` на этой поверхности не возникает: резолвер периода по HTTP не экспонируется (FIN-334 D-08); MCP его не добавляет.
- Мягкий ответ вида `{ "ok": false }` без исключения не используется.

### Обратная совместимость (D-10)

- Два новых инструмента — additive; семантика существующих MCP-инструментов не меняется.
- Контракт HTTP FIN-334 этой задачей не меняется.

## Зафиксированные решения

| ID | Решение |
| -- | ------- |
| D-01 | Паритет: ровно `list_household_budget_currencies` и `create_household_budget_currency`; без upsert/batch/get/resolver |
| D-02 | Конвейер: list только HTTP 200; create только HTTP 201; иное status (в т.ч. прочие 2xx) → `RuntimeError`; тело успеха — объект |
| D-03 | Модуль `scripts/household_budget_currencies.py` + регистрация в `server.py` + `mcp-gaps.md` |
| D-04 | Schema = обязательность/типы; `ValueError` = пустая строка после strip; `strip` только для проверки, значение в HTTP без замены |
| D-05 | Тело create всегда ровно `valid_from` + `currency`; оба обязательны схемой |
| D-06 | Без нормализации аргументов и REST-ответа (кроме конверта); без ослабления инвариантов |
| D-07 | Список → ключ `budget_currencies`; пустой список — успех |
| D-08 | Create → ключ `budget_currency`; ошибки 404/409/422 as-is |
| D-09 | Schema vs `ValueError` vs `RuntimeError` разведены; без soft-fail |
| D-10 | Additive; HTTP не меняется |

## Функциональное тестирование

### Предусловия

- Профиль данных `cand`.
- HTTP API FIN-334 доступен на том же профиле (постоянная продуктовая БД).
- Перед T1–T3 создаётся отдельное временное домохозяйство приёмки через существующие MCP household tools (FIN-240) с уникальным `household_id` на прогон; у него нет записей истории валюты бюджета.
- Идентификатор временного домохозяйства приёмки имеет префикс `fin332-acceptance-` (например `fin332-acceptance-20260823-1`); cleanup из этой задачи не входит — при необходимости удаление/архивация тестовых household на `cand` остаётся инфраструктурной практикой вне FIN-332.
- Сессия MCP через `finance_api_connect` на `cand`.
- Unit-тесты с mock API не заменяют приёмку T1–T3 на `cand`.
- Повторный прогон не использует то же временное домохозяйство с уже заполненной историей: создаётся новое либо используется новый уникальный `household_id` с тем же префиксом.

### Метаданные прогона

- Дата: 23.08.2026
- Исполнитель: agent (MCP cand acceptance)
- Сборка / commit: рабочее дерево FIN-332
- БД / профиль: `%LOCALAPPDATA%\finance-planning\finance.db` (`cand`), API `http://127.0.0.1:8002`
- `household_id` приёмки: `fin332-acceptance-20260823-2`

### Список (T1)

| Шаг | Действие | Ожидаемое | Фактическое | Результат |
| --- | -------- | --------- | ----------- | --------- |
| 1 | `list_household_budget_currencies` для временного домохозяйства приёмки | `ok: true`; `budget_currencies: []` | ok; `budget_currencies: []` | PASSED |
| 2 | `list_household_budget_currencies` с несуществующим `household_id` | ошибка инструмента; в тексте HTTP 404 и `household_not_found` | HTTP 404 `household_not_found` | PASSED |
| 3 | `list_household_budget_currencies` с `household_id` из пробелов | `ValueError` до HTTP | `household_id is required` | PASSED |

### Добавление и повторное чтение (T2)

| Шаг | Действие | Ожидаемое | Фактическое | Результат |
| --- | -------- | --------- | ----------- | --------- |
| 1 | `create_household_budget_currency` с `valid_from=2025-01`, `currency=eur` для временного домохозяйства приёмки | `ok: true`; в `budget_currency`: `valid_from=2025-01-01`, `currency=EUR` | ok; `2025-01-01` / `EUR` | PASSED |
| 2 | `list_household_budget_currencies` того же домохозяйства | одна запись с EUR | 1× EUR | PASSED |
| 3 | `create_household_budget_currency` с `valid_from=2030-07-01`, `currency=USD` | `ok: true`; вторая запись | ok; USD | PASSED |
| 4 | `list_household_budget_currencies` | две записи: сначала EUR, затем USD | EUR, USD | PASSED |

### Инварианты добавления (T3)

| Шаг | Действие | Ожидаемое | Фактическое | Результат |
| --- | -------- | --------- | ----------- | --------- |
| 1 | Повтор create с той же `valid_from=2025-01-01` и иной валютой | ошибка инструмента; HTTP 409; `budget_currency_duplicate_valid_from` | HTTP 409 `budget_currency_duplicate_valid_from` | PASSED |
| 2 | Create с `valid_from=2031-01-01`, `currency=USD` (как у предшественника) | ошибка инструмента; HTTP 409; `budget_currency_unchanged` | HTTP 409 `budget_currency_unchanged` | PASSED |
| 3 | Create с `valid_from=2025-01-15`, `currency=GBP` | ошибка инструмента; HTTP 422; `budget_currency_valid_from_invalid` | HTTP 422 `budget_currency_valid_from_invalid` | PASSED |
| 4 | Create без ключа `currency` | ошибка валидации схемы MCP; HTTP не вызывается | Input validation: `currency` is a required property | PASSED |
| 5 | Create с `currency` из пробелов | `ValueError` до HTTP | `currency is required` | PASSED |

### Итог прогона

- **T1–T3:** PASSED
- **Номера FAILED / BLOCKED:** —
- **Комментарии:** Unit 14/14 OK. Cand smoke на свежем API `:8002` (`FINANCE_DATA_PROFILE=cand`); на `:8000`/`:8001` был устаревший процесс без маршрутов FIN-334.

### Автоматизация

**Тесты:** unit-тесты handlers/scripts с mock `ApiClient`. Не заменяют приёмку T1–T3 на `cand`.

Обязательное покрытие сверх T1–T3:

| Случай | Ожидаемое |
| ------ | --------- |
| Happy path list / create | конверт успеха с коллекцией / сущностью |
| HTTP 404 / 409 / 422 | `RuntimeError` с кодом API |
| Create без ключа `currency` | ошибка схемы MCP; HTTP не вызывается |
| Обязательная строка из пробелов | `ValueError` до HTTP |
| `currency=" EUR "` при create | в JSON тела уходит `" EUR "` (без замены на strip); либо mock фиксирует исходное значение |
| GET → 200, тело не объект (напр. список / строка) | `RuntimeError` |
| POST → 201, тело не объект | `RuntimeError` |
| POST → 200 с телом-объектом | `RuntimeError` (неожиданный success status) |

**Команда запуска:**

```bash
python -m unittest discover -s tests -p "test_fin_332*.py"
```

## E2E

Раздел не включается: UI и существующие E2E-сценарии не затрагиваются.

## Последующие задачи

| ID | Тема |
| -- | ---- |
| F-01 | Начальное заполнение истории на prod (FOPS-38) — после ship FIN-332 |
| F-02 | Пакетное добавление записей — только при ops-нужде и после расширения HTTP (см. FIN-334 F-04) |

## См. также

### Эта задача и эпик

- [Эта задача (FIN-332)](https://alexeielizarov.atlassian.net/browse/FIN-332)
- [Parent / FX (FIN-112)](https://alexeielizarov.atlassian.net/browse/FIN-112)

### Продукт / методология

- [HTTP API истории (FIN-334)](https://alexeielizarov.atlassian.net/browse/FIN-334)
- [Модель и резолвер (FIN-331)](https://alexeielizarov.atlassian.net/browse/FIN-331)
- [Household master data MCP (FIN-240)](https://alexeielizarov.atlassian.net/browse/FIN-240)
- [Канон валюты бюджета домохозяйства](../../../assistant/35-finance-assistant/methodology/household/household-budget-currency.md)

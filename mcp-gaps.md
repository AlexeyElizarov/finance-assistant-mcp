# Finance Assistant MCP — справочник tools

**Backlog и пробелы tools:** [Jira FIN](https://alexeielizarov.atlassian.net/jira/software/projects/FIN/board) — label `mcp-gap`, Epic **BLG-092** ([FIN-26](https://alexeielizarov.atlassian.net/browse/FIN-26)).

**Runbook процедур:** `35-finance-assistant/ops/index.md`.

**Канон ops:** только MCP `finance-assistant` (`mcp-servers/finance-assistant/`). CLI assistant **не используется** — [scripts/mcp-only.md](../../assistant/35-finance-assistant/scripts/mcp-only.md).

---

## Доступные tools

| Tool | Назначение |
| --- | --- |
| `finance_api_connect` | Login, проверка `data_profile`, ACT-версия |
| `list_period_statuses` | Reconciliation + methodology_status по горизонту ACT |
| `period_status_report` | Отчёт за год/диапазон (reconciliation, methodology_status, ready, C9999, блокеры) |
| `verify_month` | Verify одного месяца; предупреждение об отсутствии фонда (**FIN-329** ✓) |
| `list_c9999` | Expense C9999 строки за месяц для proposal table (**FIN-17** ✓) |
| `process_month` | Reopen → import → derive → verify → optional close/PDF; preset `monthly_close_prepare` (**FIN-31** ✓); C9999 warn vs close guard (**FIN-2** ✓); final pending/note gates (**FIN-69** ✓); final missing-fund gate (**FIN-329** ✓); журнал импорта копирует `error_code` / `error_details` без разбора кода, в т.ч. валютные отказы и коды пары движения (**FIN-336** ✓, **FIN-347** ✓) |
| `reopen_periods` | Reopen closed периодов |
| `query_plan_fact` | План/факт по статье; элемент `months` по периоду и валюте строки HTTP; факт из сохранённых сумм в валюте бюджета (**FIN-122** ✓, **FIN-336** ✓) |
| `household_base_share` | Базовая доля личных фондов; `income_mode` / overrides (FIN-103, FIN-121); `convert_plans_to_eur` (FIN-114) |
| `list_fx_rates` | Плановые курсы RUB→EUR — GET /api/v1/fx-rates (**FIN-114** ✓) |
| `upsert_fx_rate` | Сохранить плановый курс на месяц — PUT /api/v1/fx-rates (**FIN-114** ✓) |
| `list_households` | Список домохозяйств — GET /api/v1/households; элементы содержат `accounting_subject` (**FIN-240** ✓, **FIN-369** ✓) |
| `upsert_household` | Upsert домохозяйства — PUT /api/v1/households/{id}; ответ содержит `accounting_subject`; назначение через аргументы отклоняется (**FIN-240** ✓, **FIN-369** ✓) |
| `list_household_members` | Члены домохозяйства — GET …/members; элементы содержат `accounting_subject` (**FIN-240** ✓, **FIN-369** ✓) |
| `upsert_household_member` | Upsert члена — PUT …/members/{member_id}; `display_name` необязателен; ответ содержит `accounting_subject`; назначение через аргументы отклоняется (**FIN-240** ✓, **FIN-369** ✓) |
| `list_bank_accounts` | Банковские счета — GET …/bank-accounts; rows: `bank_id`, `identifiers`, `currency` (**FIN-240** ✓, **FIN-293** ✓, **FIN-321** ✓, **FIN-341** ✓) |
| `upsert_bank_account` | Upsert счёта — PUT …/bank-accounts/{account_id}; required `bank_id`; optional `currency` (omit = keep; create without it fails via HTTP; JSON null rejected); omit≠null; `identifiers` read-only; no `iban`/`account_number` write (**FIN-240** ✓, **FIN-293** ✓, **FIN-321** ✓, **FIN-341** ✓) |
| `list_household_budget_currencies` | История валюты бюджета — GET …/budget-currencies (**FIN-332** ✓) |
| `create_household_budget_currency` | Добавление записи истории валюты бюджета — POST …/budget-currencies; insert-only `valid_from`+`currency` (**FIN-332** ✓) |
| `list_bank_account_identifiers` | Список идентификаторов счетов — GET /api/v1/bank-account-identifiers; optional `bank_account_id` (**FIN-321** ✓) |
| `get_bank_account_identifier` | Чтение идентификатора — GET /api/v1/bank-account-identifiers/{id} (**FIN-321** ✓) |
| `create_bank_account_identifier` | Создание идентификатора — POST /api/v1/bank-account-identifiers (**FIN-321** ✓) |
| `create_bank_account_identifiers` | Пакетное создание идентификаторов — POST /api/v1/bank-account-identifiers/batch (**FIN-321** ✓) |
| `patch_bank_account_identifier` | Изменение значения — PATCH /api/v1/bank-account-identifiers/{id} (**FIN-321** ✓) |
| `patch_bank_account_identifiers` | Пакетное изменение значения — PATCH /api/v1/bank-account-identifiers (**FIN-321** ✓) |
| `delete_bank_account_identifier` | Удаление идентификатора — DELETE /api/v1/bank-account-identifiers/{id} (**FIN-321** ✓) |
| `delete_bank_account_identifiers` | Пакетное удаление — DELETE /api/v1/bank-account-identifiers (**FIN-321** ✓) |
| `list_banks` | Список банков — GET /api/v1/banks (**FIN-293** ✓) |
| `get_bank` | Чтение банка — GET /api/v1/banks/{bank_id} (**FIN-293** ✓) |
| `create_bank` | Создание банка — POST /api/v1/banks (**FIN-293** ✓) |
| `create_banks` | Пакетное создание банков — POST /api/v1/banks/batch (**FIN-293** ✓) |
| `patch_bank` | Частичное обновление банка — PATCH /api/v1/banks/{bank_id} (**FIN-293** ✓) |
| `patch_banks` | Пакетное частичное обновление — PATCH /api/v1/banks (**FIN-293** ✓) |
| `delete_bank` | Удаление банка — DELETE /api/v1/banks/{bank_id} (**FIN-293** ✓) |
| `delete_banks` | Пакетное удаление банков — DELETE /api/v1/banks (**FIN-293** ✓) |
| `list_accounting_subjects` | Список субъектов учёта — GET /api/v1/accounting-subjects; optional `subject_type` (**FIN-366** ✓) |
| `get_accounting_subject` | Чтение субъекта учёта — GET /api/v1/accounting-subjects/{subject_id} (**FIN-366** ✓) |
| `create_accounting_subject` | Создание субъекта учёта — POST /api/v1/accounting-subjects; omit≠null (**FIN-366** ✓) |
| `create_accounting_subjects` | Пакетное создание — POST /api/v1/accounting-subjects/batch (**FIN-366** ✓) |
| `patch_accounting_subject` | Частичное обновление — PATCH /api/v1/accounting-subjects/{subject_id} (**FIN-366** ✓) |
| `patch_accounting_subjects` | Пакетное частичное обновление — PATCH /api/v1/accounting-subjects (**FIN-366** ✓) |
| `delete_accounting_subject` | Удаление — DELETE /api/v1/accounting-subjects/{subject_id} (**FIN-366** ✓) |
| `delete_accounting_subjects` | Пакетное удаление — DELETE /api/v1/accounting-subjects (**FIN-366** ✓) |
| `get_household_accounting_subject` | Чтение group по домохозяйству — GET …/households/{id}/accounting-subject (**FIN-366** ✓) |
| `get_household_member_accounting_subject` | Чтение по члену — GET …/household-members/{id}/accounting-subject (**FIN-366** ✓) |
| `link_household_member_accounting_subject` | Установление соответствия person — POST …/accounting-subject-link (**FIN-366** ✓) |
| `unlink_household_member_accounting_subject` | Снятие соответствия — DELETE …/accounting-subject-link (**FIN-366** ✓) |
| `list_payment_instruments` | Список платёжных инструментов — GET /api/v1/payment-instruments (**FIN-286** ✓, **FIN-313** ✓) |
| `get_payment_instrument` | Чтение инструмента — GET /api/v1/payment-instruments/{id} (**FIN-286** ✓, **FIN-313** ✓) |
| `create_payment_instrument` | Создание инструмента — POST /api/v1/payment-instruments; catalogue fields `settlement_class` / `pan_last4` / `issuer_expiry`; optional `valid_from` (**FIN-286** ✓, **FIN-313** ✓) |
| `create_payment_instruments` | Пакетное создание — POST /api/v1/payment-instruments/batch (**FIN-286** ✓, **FIN-313** ✓) |
| `patch_payment_instrument` | Частичное обновление — PATCH /api/v1/payment-instruments/{id}; catalogue fields; `valid_from` nullable (**FIN-286** ✓, **FIN-313** ✓) |
| `patch_payment_instruments` | Пакетное частичное обновление — PATCH /api/v1/payment-instruments (**FIN-286** ✓, **FIN-313** ✓) |
| `delete_payment_instrument` | Удаление инструмента — DELETE /api/v1/payment-instruments/{id} (**FIN-286** ✓) |
| `delete_payment_instruments` | Пакетное удаление — DELETE /api/v1/payment-instruments (**FIN-286** ✓) |
| `list_payment_means_fund_assignments` | Список сопоставлений средства с фондом — GET /api/v1/payment-means-fund-assignments (**FIN-286** ✓) |
| `get_payment_means_fund_assignment` | Чтение сопоставления — GET …/payment-means-fund-assignments/{id} (**FIN-286** ✓) |
| `create_payment_means_fund_assignment` | Создание сопоставления — POST /api/v1/payment-means-fund-assignments (**FIN-286** ✓) |
| `create_payment_means_fund_assignments` | Пакетное создание — POST …/payment-means-fund-assignments/batch (**FIN-286** ✓) |
| `patch_payment_means_fund_assignment` | Частичное обновление интервала — PATCH …/payment-means-fund-assignments/{id} (**FIN-286** ✓) |
| `patch_payment_means_fund_assignments` | Пакетное частичное обновление — PATCH …/payment-means-fund-assignments (**FIN-286** ✓) |
| `delete_payment_means_fund_assignment` | Удаление сопоставления — DELETE …/payment-means-fund-assignments/{id} (**FIN-286** ✓) |
| `delete_payment_means_fund_assignments` | Пакетное удаление — DELETE …/payment-means-fund-assignments (**FIN-286** ✓) |
| `list_household_funds` | Список фондов — GET …/funds; optional `applicable_on` (**FIN-256** ✓) |
| `get_household_fund` | Чтение фонда — GET …/funds/{fund_id} (**FIN-256** ✓) |
| `create_household_fund` | Создание фонда — PUT …/funds/{fund_id} (create-only, HTTP 201); omit≠null (**FIN-256** ✓) |
| `patch_household_fund` | Переименование / delimit — PATCH …/funds/{fund_id}; omit≠null (**FIN-256** ✓) |
| `household_advances` | Журнал авансов на базовые потребности: register / list / void / mark_deducted (**FIN-115** ✓) |
| `personal_fund_carryover` | Перенос остатков/перерасхода личного фонда после FINAL close (**FIN-105** ✓); history persist API-first + JSON fallback (**FIN-163** ✓); API path thin-client incoming (**FIN-230** ✓); fund financing fields from HTTP (**FIN-280** ✓); факт трат по фонду позиции, HTTP 200 без второй формулы (**FIN-324** ✓) |
| `household_receivables` | Журнал займов третьим лицам: register / record_repayment / list / extend / write_off / mark_gift (**FIN-116** ✓) |
| `money_check_report` | Еженедельный household money check: лимиты, остатки, methodology, C9999/?, advances, receivables (**FIN-104** ✓); fund financing from carryover dry-run (**FIN-280** ✓); факт трат месяца проверки из того же конвейера фонда (**FIN-324** ✓) |
| `query_transactions` | Выборка транзакций: `period` / `accounting_period`, `category`, `bank_account_id` (в т.ч. `__empty__`), group-by month; rows: `id`, `transaction_type`, `expense_owner`, `fund_id`, `bank_account_id`, `currency`, `budget_currency`, `planned_rate`, `posted_amount`, `posted_currency` (**FIN-27** ✓, **FIN-211** ✓, **FIN-241** ✓, **FIN-256** ✓, **FIN-336** ✓, **FIN-347** ✓, **FIN-359** ✓) |
| `delete_transactions_by_filter` | Maintenance delete по фильтру (**BLG-084** ✓) |
| `apply_keywords` | Unified/legacy JSON: категории, статьи бюджета, проекты + optional derive (**FIN-16** ✓) |
| `put_transaction_overrides` | Reconciliation overrides `transaction_key` → `budget_item_id` (**FIN-107** ✓, **FIN-120** ✓) |
| `put_transaction` | Canonical merge-patch операции: `PATCH /api/v1/transactions/{id}` — classification, project, `fund_id`, `bank_account_id`; omit≠null; ответ: `currency`, `budget_currency`, `planned_rate`, `posted_amount`, `posted_currency`, `bank_account_id`; вход `posted_*` запрещён (**FIN-260** ✓, **FIN-336** ✓, **FIN-347** ✓, **FIN-359** ✓) |
| `put_transactions` | Пакетный canonical merge-patch: последовательные `PATCH /transactions/{id}` с тем же набором полей тела, что у `put_transaction`; общий `allow_closed`; per-item `results` + `summary`; частичный успех (**FIN-265** ✓) |
| `put_transaction_lines` | Полная замена позиций операции: `PUT …/transactions/{id}/lines`; ответ `budget_amount`; вход `budget_amount` запрещён схемой (**FIN-270** ✓, **FIN-336** ✓) |
| `get_transaction_lines` | Чтение позиций: `GET …/transactions/{id}/lines`; `budget_amount` из HTTP (**FIN-270** ✓, **FIN-336** ✓) |
| `get_transaction` | Чтение операции с позициями: `GET …/transactions/{id}`; валютные поля заголовка, `posted_amount` / `posted_currency`, `bank_account_id` и `budget_amount` из HTTP (**FIN-270** ✓, **FIN-336** ✓, **FIN-347** ✓, **FIN-359** ✓) |
| `create_expense_settlement` | Создание погашения расхода: `POST …/expense-settlements` (**FIN-271** ✓) |
| `get_expense_settlement` | Чтение погашения: `GET …/expense-settlements/{id}` (**FIN-271** ✓) |
| `patch_expense_settlement` | Изменение суммы погашения: `PATCH …/expense-settlements/{id}` (**FIN-271** ✓) |
| `delete_expense_settlement` | Удаление погашения: `DELETE …/expense-settlements/{id}` (**FIN-271** ✓) |
| `list_expense_settlements` | Список погашений позиции: `GET …/expense-settlements?line_id=` (**FIN-271** ✓) |
| `get_line_settlement_state` | Состояние покрытия позиции: `GET …/transaction-lines/{id}/settlement-state` (**FIN-271** ✓) |
| `list_internal_transfer_matches` | Список сопоставлений сторон внутреннего перевода: `GET …/internal-transfer-matches` (**FIN-351** ✓) |
| `get_internal_transfer_match` | Чтение сопоставления: `GET …/internal-transfer-matches/{match_id}` (**FIN-351** ✓) |
| `create_internal_transfer_match` | Создание сопоставления: `POST …/internal-transfer-matches` (**FIN-351** ✓) |
| `create_internal_transfer_matches` | Пакетное создание: `POST …/internal-transfer-matches/batch` (**FIN-351** ✓) |
| `delete_internal_transfer_match` | Удаление сопоставления: `DELETE …/internal-transfer-matches/{match_id}` (**FIN-351** ✓) |
| `delete_internal_transfer_matches` | Пакетное удаление: `DELETE …/internal-transfer-matches` (**FIN-351** ✓) |
| `list_clearing_documents` | Список документов выравнивания: `GET …/clearing-documents` (**FIN-355** ✓) |
| `get_clearing_document` | Чтение документа: `GET …/clearing-documents/{document_id}` (**FIN-355** ✓) |
| `create_clearing_document` | Создание документа: `POST …/clearing-documents` (**FIN-355** ✓) |
| `create_clearing_documents` | Пакетное создание: `POST …/clearing-documents/batch` (**FIN-355** ✓) |
| `patch_clearing_document` | Частичное обновление заголовка: `PATCH …/clearing-documents/{document_id}` (**FIN-355** ✓) |
| `delete_clearing_document` | Удаление документа: `DELETE …/clearing-documents/{document_id}` (**FIN-355** ✓) |
| `delete_clearing_documents` | Пакетное удаление: `DELETE …/clearing-documents` (**FIN-355** ✓) |
| `create_clearing_document_item` | Создание строки состава: `POST …/clearing-documents/{document_id}/items` (**FIN-355** ✓) |
| `list_clearing_document_items` | Список строк состава: `GET …/clearing-documents/{document_id}/items` (**FIN-355** ✓) |
| `get_clearing_document_item` | Чтение строки состава: `GET …/clearing-documents/{document_id}/items/{item_id}` (**FIN-355** ✓) |
| `patch_clearing_document_item` | Частичное обновление строки: `PATCH …/clearing-documents/{document_id}/items/{item_id}` (**FIN-355** ✓) |
| `delete_clearing_document_item` | Удаление строки состава: `DELETE …/clearing-documents/{document_id}/items/{item_id}` (**FIN-355** ✓) |
| `put_transaction_category` | `PATCH …/category`: type+category (**FIN-211** ✓) и/или `expense_owner` set/clear, owner-only OK (**FIN-241** ✓); ответ: `posted_amount` / `posted_currency` (**FIN-347** ✓), `bank_account_id` (**FIN-359** ✓); вход `posted_*` и `bank_account_id` запрещён; legacy facade until **FIN-263** |
| `upsert_expense_project` | Создать или полностью заменить проект расходов (**FIN-107** ✓) |
| `update_plan_item` | Изменить plan-item: сумма и/или bounded horizon + recalculate (**FIN-108** ✓, **FIN-110** ✓) |
| `create_budget_item` | Создать статью + REG plan-item в ACT-версии + recalculate (**FIN-109** ✓) |
| `create_plan_item` | POST REG/IRR plan-item на существующую статью + recalculate (**FIN-110** ✓, **FIN-119** ✓) |
| `update_budget_item` | Правка master-полей статьи (в т.ч. `planning_type`); optional convert ACT plan-item + rollback (**FIN-227** ✓) |
| `create_category` | Создать категорию транзакций — POST /api/v1/categories (**FIN-217** ✓) |

---

## Открытые пробелы (Jira)

Epic **BLG-093** ([FIN-101](https://alexeielizarov.atlassian.net/browse/FIN-101)) — household ops: [FIN-103](https://alexeielizarov.atlassian.net/browse/FIN-103)…[FIN-105](https://alexeielizarov.atlassian.net/browse/FIN-105) (**FIN-105** ✓), **FIN-104** ✓.

| Tool (planned) | Jira | Назначение |
| --- | --- | --- |
| `delete_plan_item` / `delete_budget_item` | [FIN-111](https://alexeielizarov.atlassian.net/browse/FIN-111) | Удаление plan-item и статьи (follow-up FIN-108/109 F-02) |

JQL:

```jql
project = FIN AND labels = mcp-gap AND status != Done ORDER BY rank
```

Агент: `jira_search` с этим JQL или preset `fin_mcp`.

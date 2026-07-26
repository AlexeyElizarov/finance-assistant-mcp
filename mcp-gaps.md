# Finance Assistant MCP — справочник tools

**Backlog и пробелы tools:** [Jira FIN](https://alexeielizarov.atlassian.net/jira/software/projects/FIN/board) — label `mcp-gap`, Epic **BLG-092** ([FIN-26](https://alexeielizarov.atlassian.net/browse/FIN-26)).

**Runbook процедур:** `35-finance-assistant/methodology/monthly-close-api/index.md`.

**Канон ops:** только MCP `finance-assistant` (`mcp-servers/finance-assistant/`). CLI assistant **не используется** — [scripts/mcp-only.md](../../assistant/35-finance-assistant/scripts/mcp-only.md).

---

## Доступные tools

| Tool | Назначение |
| --- | --- |
| `finance_api_connect` | Login, проверка `data_profile`, ACT-версия |
| `list_period_statuses` | Reconciliation + methodology_status по горизонту ACT |
| `period_status_report` | Отчёт за год/диапазон (reconciliation, methodology_status, ready, C9999, блокеры) |
| `verify_month` | Verify одного месяца |
| `list_c9999` | Expense C9999 строки за месяц для proposal table (**FIN-17** ✓) |
| `process_month` | Reopen → import → derive → verify → optional close/PDF; preset `monthly_close_prepare` (**FIN-31** ✓); C9999 warn vs close guard (**FIN-2** ✓); final pending/note gates (**FIN-69** ✓) |
| `reopen_periods` | Reopen closed периодов |
| `query_plan_fact` | План/факт по статье; enriched errors при not-found/ambiguous article (**FIN-122** ✓) |
| `household_base_share` | Базовая доля личных фондов; `income_mode` / overrides (FIN-103, FIN-121); `convert_plans_to_eur` (FIN-114) |
| `list_fx_rates` | Плановые курсы RUB→EUR — GET /api/v1/fx-rates (**FIN-114** ✓) |
| `upsert_fx_rate` | Сохранить плановый курс на месяц — PUT /api/v1/fx-rates (**FIN-114** ✓) |
| `list_households` | Список домохозяйств — GET /api/v1/households (**FIN-240** ✓) |
| `upsert_household` | Upsert домохозяйства — PUT /api/v1/households/{id} (**FIN-240** ✓) |
| `list_household_members` | Члены домохозяйства — GET …/members (**FIN-240** ✓) |
| `upsert_household_member` | Upsert члена — PUT …/members/{member_id} (**FIN-240** ✓) |
| `list_bank_accounts` | Банковские счета — GET …/bank-accounts (**FIN-240** ✓) |
| `upsert_bank_account` | Upsert счёта — PUT …/bank-accounts/{account_id}; omit≠null (**FIN-240** ✓) |
| `household_advances` | Журнал авансов на базовые потребности: register / list / void / mark_deducted (**FIN-115** ✓) |
| `personal_fund_carryover` | Перенос остатков/перерасхода личного фонда после FINAL close (**FIN-105** ✓); history persist API-first + JSON fallback (**FIN-163** ✓); API path thin-client incoming (**FIN-230** ✓) |
| `household_receivables` | Журнал займов третьим лицам: register / record_repayment / list / extend / write_off / mark_gift (**FIN-116** ✓) |
| `money_check_report` | Еженедельный household money check: лимиты, остатки, methodology, C9999/?, advances, receivables (**FIN-104** ✓) |
| `query_transactions` | Выборка транзакций: `period` / `accounting_period`, `category`, group-by month; rows: `id`, `transaction_type`, `expense_owner` (**FIN-27** ✓, **FIN-211** ✓, **FIN-241** ✓) |
| `delete_transactions_by_filter` | Maintenance delete по фильтру (**BLG-084** ✓) |
| `apply_keywords` | Unified/legacy JSON: категории, статьи бюджета, проекты + optional derive (**FIN-16** ✓) |
| `put_transaction_overrides` | Reconciliation overrides `transaction_key` → `budget_item_id` (**FIN-107** ✓, **FIN-120** ✓) |
| `put_transaction_category` | `PATCH …/category`: type+category (**FIN-211** ✓) и/или `expense_owner` set/clear, owner-only OK (**FIN-241** ✓) |
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

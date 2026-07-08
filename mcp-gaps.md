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
| `process_month` | Reopen → import → derive → verify → optional close/PDF; preset `monthly_close_prepare` (**FIN-31** ✓); C9999 warn vs close guard (**FIN-2** ✓) |
| `reopen_periods` | Reopen closed периодов |
| `query_plan_fact` | План/факт по статье |
| `household_base_share` | Базовая доля личных фондов (FIN-103) |
| `query_transactions` | Выборка транзакций, group-by month |
| `delete_transactions_by_filter` | Maintenance delete по фильтру (**BLG-084** ✓) |
| `apply_keywords` | Применение keywords (категории, статьи, проекты) |
| `put_transaction_overrides` | Reconciliation overrides `transaction_key` → `budget_item_id` (**FIN-107** ✓) |
| `upsert_expense_project` | Создать или полностью заменить проект расходов (**FIN-107** ✓) |
| `update_plan_item` | Изменить plan-item: сумма и/или bounded horizon + recalculate (**FIN-108** ✓, **FIN-110** ✓) |
| `create_budget_item` | Создать статью + REG plan-item в ACT-версии + recalculate (**FIN-109** ✓) |
| `create_plan_item` | POST REG plan-item на существующую статью (bounded / one-off) + recalculate (**FIN-110** ✓) |

---

## Открытые пробелы (Jira)

Epic **BLG-093** ([FIN-101](https://alexeielizarov.atlassian.net/browse/FIN-101)) — household ops: [FIN-103](https://alexeielizarov.atlassian.net/browse/FIN-103)…[FIN-105](https://alexeielizarov.atlassian.net/browse/FIN-105).

| Tool (planned) | Jira | Назначение |
| --- | --- | --- |
| `list_fx_rates` / `upsert_fx_rate` | [FIN-114](https://alexeielizarov.atlassian.net/browse/FIN-114) | Курсы RUB→EUR; household conversion (**FIN-112**, blocked by [FIN-113](https://alexeielizarov.atlassian.net/browse/FIN-113)) |
| `delete_plan_item` / `delete_budget_item` | [FIN-111](https://alexeielizarov.atlassian.net/browse/FIN-111) | Удаление plan-item и статьи (follow-up FIN-108/109 F-02) |
| `money_check_report` | [FIN-104](https://alexeielizarov.atlassian.net/browse/FIN-104) | Еженедельный money check |
| `personal_fund_carryover` | [FIN-105](https://alexeielizarov.atlassian.net/browse/FIN-105) | Перенос остатков после FINAL |

JQL:

```jql
project = FIN AND labels = mcp-gap AND status != Done ORDER BY rank
```

Агент: `jira_search` с этим JQL или preset `fin_mcp`.

# AI-Invoice-Auditor Workspace Rules

## `/goal` 與自動化驗收紀律（Verification & Test Integrity）

- **嚴禁弱化或規避測試**：執行任何 `/goal` 或開發任務時，絕不允許為了讓 `./scripts/check-done.sh` 或 `Stop` hook 通過而刪除既有測試、加上跳過標記（如 `pytest.mark.skip` / `xfail`）、擴大 `-k` / `--ignore` 排除範圍、或放寬斷言條件。
- **遇阻即停並回報**：若遇到環境缺失、套件未安裝、權限受限或外部依賴阻礙導致檢查或測試無法通過，必須立即停止修改並清楚回報缺口與完整錯誤訊息，不得自行繞過或偽造通過狀態。
- **驗收基準**：專案標準離線驗收腳本為 `./scripts/check-done.sh`（由 `.agents/hooks.json` 的 `Stop` hook 自動觸發）；`/goal` 任務請參照 `.agents/goal-template.md` 規範六大區塊。

# `/goal` Task Prompt Template (AI-Invoice-Auditor)

使用 `/goal` 執行長時程或跨模組任務時，請複製並填寫以下六個區塊，確保目標明確、邊界可控且可由 `./scripts/check-done.sh` 與測試自動驗收。

---

```markdown
/goal

## Objective
<!-- 用 1-3 句描述本次要達成的單一可驗證目標與業務/工程價值 -->
例如：在 FastAPI 稽核API與前端介面中新增「<功能名稱>」，使單據解析（Pydantic v2 `InvoiceData`）、會計稽核（`src/auditor.py`）與匯出報表（`src/template_exporter.py` / `src/erp_exporter.py`）支援 <具體規格>。

## Scope
<!-- 明確列出本次允許修改的目錄與檔案，其餘檔案非必要不更動 -->
- 後端資料模型與邏輯：`src/models.py`、`src/auditor.py`、`src/database.py`
- 服務與抽取器：`src/services/`、`src/extractors/`
- Web API 與靜態前端：`src/web/routes.py`、`src/web/auth_routes.py`、`src/web/static/app.js`、`src/web/static/index.html`
- 自動化測試：`tests/`（針對本次變更補齊單元測試或 `FastAPI TestClient` 整合測試）

## Done when
<!-- 可機器驗證的完成條件（Stop hook 會自動觸發 ./scripts/check-done.sh） -->
1. `./scripts/check-done.sh` 全數通過（含 `python3 -m compileall -q src tests`、`node --check src/web/static/app.js` 與 23+ 項離線 `pytest`）。
2. 所有針對本次新增/修改行為的單元測試與 `TestClient` API 測試皆已補齊並通過（無 skip 或放寬斷言）。
3. （選填，若有變更 Web 路由/Auth/E2E）以 `uvicorn src.web.app:app --port 8000` 實測相關端點或 E2E 測試通過後妥善關閉臨時伺服器。
4. （選填，若有變更 CLI 流程）`python3 -m src.main --input sample_data --engine mock --output output` 可正常產出報表與 `audit_trail.json`。

## Not in this goal
<!-- 明確排除不做的事項，防止範圍蔓延與破壞現有防呆規範 -->
- 不修改台灣統一編號 8 碼加權檢核演算法、5% 營業稅平準原則或既有重複發票防弊核心規則（除非 Objective 明確要求）。
- 不新增第三方套件至 `requirements.txt`（若確有必要須先提出評估並停下確認）。
- 不更動 `Template.xlsx` 既有欄位版面或呼叫需付費的外部真實 Gemini API（測試一律使用 `MockExtractor` 或 mock）。
- 不修改 `.agents/hooks.json`、不放寬 `./scripts/check-done.sh` 的驗收標準，且絕不為了讓 `check-done.sh` 通過而刪除、跳過（skip）或弱化既有測試。

## If blocked
<!-- 遇到環境限制、缺金鑰或規格模糊時的處理原則 -->
- 若遇到環境缺失、相依套件不足或外部依賴阻礙，必須立即停止並清楚回報缺口，禁止繞過驗證、跳過測試或偽造通過結果。
- 缺少 `GEMINI_API_KEY` 或外部網路時，使用 `MockExtractor`（`--engine mock`）與離線 fixture 驗證。
- 若現有測試因需 `127.0.0.1:8000` 運行中伺服器而無法在離線模式跑通，請清楚區分離線測試與 E2E 測試結果；若需修改既有商業規則或資料庫 Schema 造成不相容，立即停止並回報受影響範圍與替代方案。
- 若連續兩次修復仍無法通過 `./scripts/check-done.sh`，停止盲目重試，整理失敗日誌、根因與最小重現步驟回報。

## Deliver
<!-- 任務結束時必須回報的交付清單 -->
1. **變更檔案清單**：附上修改或新增的檔案路徑與核心變更摘要。
2. **驗收實測結果**：附上 `./scripts/check-done.sh`（及選填 E2E/CLI 指令）的實際執行輸出與通過項目數。
3. **相容性與風險說明**：說明是否影響既有 API Schema、SQLite 資料表或前端相容性，以及任何未涵蓋之邊界條件。
```

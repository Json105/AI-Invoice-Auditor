# 🐳 AI-Invoice Auditor | 輕量容器化部署手冊 (Docker Deployment Guide)

本手冊專為 **5-10 人小型公司、微型團隊或辦公室環境** 設計，支援在任何安裝有 Docker 的主機（公司辦公電腦、Synology/QNAP NAS、或 $3.5/月的雲端 VPS）上 **30 秒一鍵啟動**。

---

## ⚡ 快速開始 (Quick Start)

### 步驟 1：複製環境變數範本 (選填)
```bash
cp .env.example .env
```
*(若有 Gemini API Key，可編輯 `.env` 填入 `GEMINI_API_KEY=your_key_here`；若未填寫，系統自動以離線展示模式運行)*

### 步驟 2：一鍵啟動服務
```bash
docker compose up -d --build
```

### 步驟 3：開啟瀏覽器立即使用
- 🌐 **前台報銷與審批中心**：`http://localhost:8000/`
- ⚙️ **SQLAdmin 企業管理後台**：`http://localhost:8000/admin`

---

## 👥 系統預設示範帳號 (Default Accounts)

| 角色 | 電子郵件 | 預設密碼 | 權限範圍 |
| :--- | :--- | :--- | :--- |
| 👑 **系統管理員 (Admin)** | `admin@company.local` | `Admin1234!` | 全站系統設定、使用者權限管理、後台資料庫直接存取 |
| 💼 **財務主管 (Finance)** | `finance@company.local` | `Finance1234!` | 單據初核、退件補件、標記撥款結案、ERP 傳票導出 |
| 👤 **一般同仁 (Employee)** | `employee@company.local` | `User1234!` | 單據上傳送審、個人歷史進度查詢、被退件時補件重送 |

---

## 🛠️ 常用管理指令 (Operation Commands)

```bash
# 查看容器即時日誌
docker compose logs -f

# 停止服務
docker compose down

# 重新啟動服務
docker compose restart

# 更新程式碼後重新建置並啟動
docker compose up -d --build
```

---

## 💾 資料持久化與每日備份 (Backup & Persistence)

容器已配置 Volume 綁定掛載，所有資料皆安全存放在主機端：
- `./data/invoices.db`：SQLite 資料庫（含所有審核紀錄、審批歷程、同仁帳號）。
- `./output/`：產出之 Excel 出差報銷單、ERP 會計傳票與暫存檔。

### 極簡備份方式（5 秒完成）：
只要將專案目錄下的 `data/` 資料夾複製到隨身碟、NAS 或排程備份至 Google Drive 即可，重灌或換電腦時直接貼回，**零遺失、零配置**！

---

## 🏢 適用部署場景 (Deployment Scenarios)

1. **公司內部舊 PC / Mac Mini / 迷你主機 (NUC)**：
   - 放在辦公室角落連上公司內網 WiFi，全公司 5-10 人即可透過 `http://192.168.x.x:8000` 共享使用。
2. **群暉 Synology NAS / QNAP NAS**：
   - 開啟 Container Manager (Docker)，匯入 `docker-compose.yml` 即可全天候 24h 穩定運行。
3. **雲端便宜 VPS (AWS Lightsail $3.5/月、Linode、DigitalOcean)**：
   - 全球出差皆可隨時上傳報銷單據，搭配 Nginx 或 Cloudflare Tunnel 享有免費 HTTPS 加密連線。

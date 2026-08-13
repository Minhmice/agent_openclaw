# Runbook: website discovery và audit quanh Hà Nội

Runbook này mô tả workflow local cho discovery đa ngành, audit website, tạo Website Brief và gửi review card qua OpenClaw. Remote host vẫn là source of truth khi triển khai; tài liệu này không chứa credential.

## Phạm vi cố định

- Market: `config/markets/hanoi-80km.yaml`.
- Center: `21.0285, 105.8542`.
- Radius: `80 km`.
- Timezone: `Asia/Bangkok`.
- Discovery: đa ngành; cohort được giữ nguyên trong seed và scoring.
- Lịch mặc định: `07:30 Asia/Bangkok`, `RandomizedDelaySec=300`, `Persistent=true`.
- P2 (dashboard, CRM, automated outreach, CMS/auth/billing) chưa thuộc workflow này.

## Local install và dry-run

Tạo môi trường cục bộ bằng `deploy/install-local.ps1` hoặc các lệnh tương đương:

```powershell
python -m venv .venv
.\\.venv\\Scripts\\python -m pip install -e ".[dev]"
.\\.venv\\Scripts\\python -m playwright install chromium
```

Các lệnh kiểm tra không gửi Discord và không crawl Internet khi chạy dry-run:

```powershell
.\\.venv\\Scripts\\openclaw-web --help
.\\.venv\\Scripts\\openclaw-web health
.\\.venv\\Scripts\\openclaw-web cron-run --dry-run
```

Fixture E2E dùng HTTP server local và fake provider/transport; không đặt API key hoặc token vào fixture. Artifact của một project nằm dưới `<artifact-root>/<project_id>/` và gồm candidate/evidence/pages/scores/issues, dossier, `curie-to-website.json`, Website Brief, renderer proof và delivery snapshot.

## Luồng trạng thái và gate

```text
discovery → review → approved → website-brief → task
         → stakeholder-review → offer-ready
```

Gate 1 chỉ cho phép Minh hoặc Wien approve review lead. Gate 2 yêu cầu Website Brief đầy đủ, homepage proof hợp lệ, evidence/confidence gaps và không còn P0/P1 chưa xử lý. Gate 3 yêu cầu checklist/page state và stakeholder review trước khi `/page-approve`.

## Discord Components v2 và typed fallback

Review/page/final card là bot-owned Components v2. Mỗi callback phải khớp `channel_id`, `message_id`, `project_id`, `component_set_id`, `state_version`, `expires_at`, project/page state và gate trước khi gọi coordinator. Callback lặp lại là idempotent; callback stale/expired/unauthorized/blocked không được chạm coordinator.

Actor allowlist:

- Minh: `620891893659598850`.
- Wien: `859783610625556480`.
- Review `Approve`: Minh + Wien.
- Review `Reject` và `Request changes`: Minh.
- Page actions: Minh/Wien khi là actor được assign.
- Final confirm: mỗi người xác nhận tối đa một lần; mặc định cần cả hai để vào `offer-ready`.

Nếu OpenClaw không expose Components v2, card hết hạn, hoặc callback không verify được, dùng đúng typed fallback:

```text
/approve <project_id>
/reject <project_id> <reason>
/request-change <project_id> <note>
/page-status <project_id> <page_slug>
/page-done <project_id> <page_slug>
/page-approve <project_id> <page_slug>
/block <project_id> <page_slug> <reason>
/final-confirm <project_id>
```

Không coi reaction hoặc payload thiếu actor/message identity là approval. `/approve` của workflow coordinator chỉ đổi project state; nó không cấp OpenClaw host exec approval.

## Cron, lock, health và retention

`openclaw-web-discovery.timer` gọi service mỗi ngày lúc `07:30 Asia/Bangkok`, có jitter tối đa 5 phút và timeout `3h15m`. Runner dùng lock lease 180 phút, renew trong lúc chạy và luôn release sau khi kết thúc. Kết quả hợp lệ là `candidate-posted`, `no-candidate-defensible`, `partial`, `failed`, hoặc `skipped-overlap`; overlap không tạo candidate/card thứ hai.

Log là structured JSON đã redaction. Health phải phân biệt manual-audit readiness với discovery/provider readiness và kiểm tra DB/schema, artifact root, browser, Lighthouse, market/rubric, OpenClaw CLI, gateway/Discord, timer, lần chạy gần nhất và outbox.

Retention chỉ xóa screenshot/run log của candidate bị reject đã hết hạn và temporary delivery thất bại. Không xóa project approved, evidence, backup, session hoặc config snapshot. Backup là artifact nhạy cảm; không in nội dung.

## Backup, rollback và remote boundary

Trước mọi remote mutation phải có approval cho đúng scope, sau đó backup timestamped các file bị đổi, giữ nguyên owner/mode. Rollback phải dùng đúng timestamp tương ứng, restore workflow/config đã backup, validate lại OpenClaw và không chạm DB/artifact approved.

Remote preflight chỉ đọc:

```text
id
hostname
uptime
df -hT / /home
free -h
systemctl --user status openclaw-gateway.service --no-pager
systemctl --user show openclaw-gateway.service --no-pager -p ActiveState -p SubState -p MainPID -p ExecMainStatus -p Restart
openclaw gateway status
openclaw health
openclaw channels status --channel discord
openclaw channels status --channel discord --probe
openclaw update status
```

Chỉ sau preflight và approval cụ thể mới cân nhắc cài package, copy workflow, cài user service/timer, sửa OpenClaw config hoặc restart gateway. Không chạy `openclaw security audit --fix`, `openclaw doctor --repair`, package update, firewall change, credential rotation hay mở port `18789` trong workflow này.

## Timeout model/provider

Khi gặp `LLM request timed out`, đọc riêng primary model/provider và timeout không nhạy cảm. Provider self-hosted chậm có thể bắt đầu ở khoảng `300` giây; outer agent/run timeout phải không thấp hơn provider timeout. Không đặt vô hạn và không đổi model/fallback khi chưa xác định provider.

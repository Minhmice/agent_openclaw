# Runbook: website discovery và audit quanh Hà Nội

Runbook này mô tả workflow local cho discovery đa ngành, audit website, tạo Website Brief và gửi review card qua OpenClaw. Remote host vẫn là source of truth khi triển khai; tài liệu này không chứa credential.

## Phạm vi cố định

- Market: `config/markets/hanoi-80km.yaml`.
- Center: `21.0285, 105.8542`.
- Radius: `80 km`.
- Timezone: `Asia/Bangkok`.
- Discovery: đa ngành; cohort được giữ nguyên trong seed và scoring.
- Lịch mặc định: `07:30 Asia/Bangkok`, `RandomizedDelaySec=300`, `Persistent=true`.
- P2 (dashboard, CRM, automated outreach, CMS/auth/billing) nằm ngoài phạm vi discovery workflow này (repository vẫn có thư mục dashboard riêng phục vụ review).

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

## Discovery provider công khai

Production composition dùng hai nguồn OSM theo thứ tự: `openstreetmap-overpass` là nguồn chính và
`openstreetmap-nominatim` là fallback bounded. Nominatim chỉ gửi một truy vấn `company, <center>`
trong mỗi process, cache kết quả trong process, lấy tối đa 40 kết quả có `website` hoặc
`contact:website` public, và chỉ gán các kết quả không có bằng chứng cohort vào `other`. Nguồn này
không dùng grid search, không tự tải trang details, không làm geocoding hàng loạt, và không bypass
geofence/evidence/scoring gate.

Fallback phải giữ `User-Agent` nhận diện ứng dụng, cách nhau ít nhất một giây, và giữ tần suất theo
chính sách Nominatim; endpoint có thể chuyển qua `OPENCLAW_WEB_NOMINATIM_ENDPOINT` nếu host cần
đổi dịch vụ. Xem [Nominatim Usage Policy](https://operations.osmfoundation.org/policies/nominatim/)
và [Search API](https://nominatim.org/release-docs/latest/api/Search/) trước khi tăng tần suất hoặc
mở rộng truy vấn. Nếu provider trả payload lỗi, lỗi đó vẫn được ghi nhận là provider failure; không
được đổi thành `no-candidate-defensible` hay tự tổng hợp candidate.

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
/lead-approve <project_id>
/lead-reject <project_id> <reason>
/lead-request-change <project_id> <note>
/page-status <project_id> <page_slug>
/page-done <project_id> <page_slug>
/page-approve <project_id> <page_slug>
/block <project_id> <page_slug> <reason>
/final-confirm <project_id>
```

Không coi reaction hoặc payload thiếu actor/message identity là approval. Project approval chỉ đi qua nút đã verify hoặc `/lead-approve`; `/approve` được dành riêng cho OpenClaw host exec approval.

## Cron, lock, health và retention

`openclaw-web-discovery.timer` gọi service mỗi ngày lúc `07:30 Asia/Bangkok`, có jitter tối đa 5 phút và timeout `3h15m`. Runner dùng lock lease 180 phút, renew trong lúc chạy và luôn release sau khi kết thúc. Kết quả hợp lệ là `candidate-posted`, `no-candidate-defensible`, `partial`, `failed`, hoặc `skipped-overlap`; overlap không tạo candidate/card thứ hai.

Log là structured JSON đã redaction. Health phải phân biệt manual-audit readiness với discovery/provider readiness và kiểm tra DB/schema, artifact root, browser, Lighthouse, market/rubric, OpenClaw CLI, gateway/Discord, timer, lần chạy gần nhất và outbox.

`openclaw-web health --json` giữ các key boolean tương thích (`timer`, `last_run`) và bổ sung `timer_enabled`, `timer_active`, `last_run_present` cùng `discovery_blockers`. Các blocker như `timer_disabled` và `last_run_missing` là trạng thái policy/chưa khởi tạo, không phải lỗi Discord hay gateway. Probe OpenClaw/Discord mặc định chờ tối đa 15 giây (có thể cấu hình bằng `OPENCLAW_WEB_HEALTH_PROBE_TIMEOUT_SECONDS`, luôn bị giới hạn tối đa 60 giây); timeout vẫn fail-closed và chỉ trả mã nguyên nhân đã redaction. Readiness chỉ kiểm tra executable Chromium/cache path đã cấu hình, không khởi động Playwright driver.

Trên host dùng Playwright-managed Chrome, installer ghi `CHROME_PATH` vào env của user service. Nếu binary không có setuid sandbox helper, installer ghi thêm `OPENCLAW_WEB_CHROME_NO_SANDBOX=1` để Lighthouse/Chromium smoke chạy được trong user service; đây là lựa chọn riêng của host audit đã được duyệt, không phải cấu hình toàn cục của OpenClaw. Không tự bật flag này cho môi trường có boundary không tin cậy.

Retention chỉ xóa screenshot/run log của candidate bị reject đã hết hạn và temporary delivery thất bại. Không xóa project approved, evidence, backup, session hoặc config snapshot. Backup là artifact nhạy cảm; không in nội dung.

## Backup, rollback và remote boundary

Trước mọi remote mutation phải có approval cho đúng scope, sau đó backup timestamped các file bị đổi, giữ nguyên owner/mode. Rollback phải dùng đúng timestamp tương ứng, restore workflow/config đã backup, validate lại OpenClaw và không chạm DB/artifact approved.

`deploy/install-remote.sh` tạo release theo hash của wheel. Ba file của plugin `openclaw-web-components` được copy vào release với mode chỉ đọc; extension path chỉ là symlink tới plugin trong release đó. Installer backup từng target bằng manifest có loại file, hash và mode, rồi merge có mục tiêu vào `plugins.allow`, `plugins.entries.openclaw-web-components.enabled` và `channels.discord.agentComponents.ttlMs=86400000`. Sau mỗi config write phải chạy `openclaw config validate`; không in config hoặc secret. Installer luôn để `openclaw-web-discovery.timer` ở trạng thái `disabled`/`inactive`, không enable timer và không restart gateway.

`deploy/rollback-remote.sh <backup-root>` chỉ nhận backup nằm dưới `~/.openclaw/backups/`. Trước khi stop timer/service hoặc sửa file, script kiểm tra toàn bộ backup payload, hash/mode của file đã deploy, plugin asset bất biến, symlink và trạng thái timer đang bị freeze. Nếu có drift, rollback dừng trước mutation. Sau restore, script verify systemd unit, OpenClaw config, health và Discord probe; chỉ khi các bước này pass mới restore đúng trạng thái timer đã lưu và kiểm tra lại trạng thái cuối.

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

Chỉ sau preflight và approval cụ thể mới cân nhắc cài package, copy workflow, cài user service/timer hoặc sửa OpenClaw config. Deployment này không restart gateway và không enable timer; nếu một live smoke sau đó cần làm việc đó thì phải có approval riêng. Không chạy `openclaw security audit --fix`, `openclaw doctor --repair`, package update, firewall change, credential rotation hay mở port `18789` trong workflow này.

## Timeout model/provider

Khi gặp `LLM request timed out`, đọc riêng primary model/provider và timeout không nhạy cảm. Provider self-hosted chậm có thể bắt đầu ở khoảng `300` giây; outer agent/run timeout phải không thấp hơn provider timeout. Không đặt vô hạn và không đổi model/fallback khi chưa xác định provider.

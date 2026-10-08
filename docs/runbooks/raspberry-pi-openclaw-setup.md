# Hướng Dẫn Cài Đặt & Cấu Hình Nền Tảng OpenClaw Trên Raspberry Pi 5 (Headless)

Tài liệu ghi lại toàn bộ các thành phần đã được cài đặt, tối ưu hóa và cấu hình trên thiết bị Raspberry Pi 5 để phục vụ chạy OpenClaw Gateway 24/7.

---

## 1. Thông Tin Môi Trường Thiết Bị

- **Thiết bị:** Raspberry Pi 5 Model B Rev 1.0 (8GB RAM, Broadcom BCM2712 ARM64 `aarch64`).
- **Hệ điều hành:** Debian GNU/Linux 13 (trixie) 64-bit Lite (Headless, không GUI).
- **IP mạng LAN:** `192.168.1.54` (cổng mạng Ethernet nội bộ).
- **Tài khoản quản trị:** `minhmice` (đã nạp sẵn SSH key Ed25519 từ máy trạm).

---

## 2. Danh Mục Các Thành Phần Đã Cài Đặt & Cấu Hình

### A. Tối ưu Hệ thống & Quản lý Bộ nhớ
1. **`raspberrypi-utils` (`vcgencmd`):**
   - Tiện ích đọc trạng thái vi xử lý phần cứng Raspberry Pi.
   - Lệnh kiểm tra nhiệt độ: `vcgencmd measure_temp` (nhiệt độ thực tế ~40-43°C).
   - Lệnh kiểm tra sụt nguồn/nghẽn nhiệt: `vcgencmd get_throttled` (`0x0` = nguồn điện và nhiệt độ hoàn hảo).
2. **`systemd-zram-generator` (ZRAM Swap Nén):**
   - Đã cài đặt qua apt: `systemd-zram-generator`.
   - File cấu hình: `/etc/systemd/zram-generator.conf`
     ```ini
     [zram0]
     zram-size = min(ram / 2, 4096)
     compression-algorithm = zstd
     swap-priority = 100
     ```
   - Trạng thái: Kích hoạt phân vùng swap `/dev/zram0` (2GB nén zstd, priority 100) trên RAM, giúp hấp thụ các đợt tăng tải bộ nhớ đột biến mà không ghi xuống thẻ SD, kéo dài tối đa tuổi thọ lưu trữ flash.
3. **`systemd User Linger`:**
   - Đã kích hoạt lệnh: `sudo loginctl enable-linger minhmice`
   - Mục đích: Cho phép tiến trình người dùng (`systemd --user`) tiếp tục chạy nền liên tục 24/7 ngay cả khi đăng xuất phiên SSH.

### B. Sandbox Thực thi Công cụ (Agent Tool Isolation)
1. **`podman` & `catatonit`:**
   - Cài đặt: `podman 5.4.2` và `/usr/bin/catatonit`.
   - Vai trò: Đóng vai trò backend sandbox native cho OpenClaw (`sandbox.backend: "podman"`).
   - Lợi ích: Hoạt động theo cơ chế daemonless (không tốn RAM duy trì tiến trình root daemon khi nhàn rỗi) và chạy rootless an toàn với `--userns=keep-id`.

### C. Node.js Runtime & OpenClaw Gateway
1. **Node.js LTS v24 ARM64:**
   - Cài đặt bản binary chính thức `node-v24.21.0-linux-arm64` vào `/usr/local`.
   - Đáp ứng đầy đủ tiêu chuẩn `node:sqlite` và engine floor `>=24.16.0` của OpenClaw.
2. **OpenClaw CLI:**
   - Đã cài đặt toàn cục qua npm: `npm install -g openclaw` (bản `OpenClaw 2026.9.9`).
3. **Cấu hình OpenClaw (`~/.openclaw/openclaw.json`):**
   ```json
   {
     "gateway": {
       "mode": "local",
       "bind": "lan",
       "port": 18789,
       "auth": {
         "mode": "password",
         "password": "minh2908"
       },
       "trustedProxies": ["127.0.0.1", "::1"]
     },
     "agents": {
       "defaults": {
         "sandbox": {
           "mode": "all",
           "backend": "podman",
           "scope": "session",
           "workspaceAccess": "rw"
         }
       }
     }
   }
   ```
4. **Service Systemd Quản Lý Gateway (`~/.config/systemd/user/openclaw-gateway.service`):**
   ```ini
   [Unit]
   Description=OpenClaw Gateway Service
   After=network.target

   [Service]
   Type=simple
   WorkingDirectory=%h
   ExecStart=/usr/local/bin/openclaw gateway --port 18789
   Restart=always
   RestartSec=5
   Environment=NODE_ENV=production
   Environment=OPENCLAW_NO_RESPAWN=1
   Environment=NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache

   [Install]
   WantedBy=default.target
   ```
   - Tự động khởi động cùng hệ thống.
   - Tự hồi sinh sau 5 giây nếu tiến trình gặp sự cố (`Restart=always`).

---

## 3. Cách Kết Nối & Truy Cập

### A. Truy cập Trực Tiếp Qua Mạng LAN
- Địa chỉ: `http://192.168.1.54:18789`
- Mật khẩu đăng nhập Gateway: `minh2908`

### B. Mở Rộng Ra Tên Miền Công Cộng (`openclaw.minhmice.com`)
Do domain `minhmice.com` đang được quản lý trên Cloudflare DNS (`172.67.206.198`), cách an toàn và chuẩn nhất để trỏ `openclaw.minhmice.com` về Pi (nằm sau mạng NAT/Router gia đình) là sử dụng **Cloudflare Tunnel (`cloudflared`)**:

1. **Trên Cloudflare Zero Trust Dashboard:**
   - Vào mục **Networks** -> **Tunnels** -> **Create a Tunnel** (chọn Cloudflare Tunnel).
   - Đặt tên tunnel (ví dụ: `pi-openclaw`).
   - Copy mã Token cài đặt dành cho Debian 64-bit (`curl -L ... | sudo cloudflared service install <TOKEN>`).
2. **Cấu hình Public Hostname trong Cloudflare Tunnel:**
   - Subdomain: `openclaw`
   - Domain: `minhmice.com`
   - Service Type: `HTTP`
   - URL: `127.0.0.1:18789` (hoặc `192.168.1.54:18789`)
3. **Hoàn tất:** Cloudflare sẽ tự động cấp chứng chỉ HTTPS công minh và định tuyến lưu lượng vào Gateway bảo vệ bởi mật khẩu `minh2908`.

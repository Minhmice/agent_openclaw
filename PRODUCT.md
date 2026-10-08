# Product

## Register

product

## Users

Minh và Wien sử dụng dashboard như một control surface nội bộ để theo dõi Lead Intelligence, đọc evidence, xem portfolio, xác nhận trạng thái và gửi các action đã được workflow coordinator kiểm tra.

Dashboard có thể được đưa ra public URL để truy cập thuận tiện, nhưng public exposure phải đi qua reverse proxy có TLS và authentication phù hợp. Dashboard application vẫn bind loopback và không trở thành public listener trực tiếp.

## Product Purpose

Sản phẩm biến quy trình tìm kiếm và đánh giá lead thành một luồng có bằng chứng, có gate và có human approval. Người dùng cần nhìn nhanh run hiện tại, biết lead nào còn defensible, hiểu vì sao lead được xếp hạng, và thực hiện action mà không bypass state machine hoặc coordinator.

Thành công được đo bằng việc giảm context switching, làm rõ next action, giữ cho quyết định thương mại có evidence, và không tạo lead giả khi provider, evidence hoặc workflow không đủ.

## Brand Personality

Mạnh, nhanh, kỹ thuật.

Giọng giao diện ngắn, trực tiếp và có tính vận hành. Trạng thái phải nói rõ hệ thống đang làm gì, dữ liệu có mới không, action tiếp theo là gì và khi nào workflow bị chặn. Tên hiển thị của agent thân thiện bằng tiếng Việt, nhưng role ID và machine contract giữ nguyên để bảo đảm tính ổn định.

## Anti-references

- Không biến dashboard vận hành thành landing page hoặc màn hình SaaS trang trí.
- Không dùng gradient/glow/neon hoặc card decoration để thay thế hierarchy của dữ liệu.
- Không hiển thị số liệu giả, intent giả, trạng thái optimistic không được coordinator xác nhận hoặc evidence thiếu như thể đã chắc chắn.
- Không đưa bearer token, credential, raw config, session, log hoặc private key vào UI, localStorage, artifact hay public response.
- Không mở trực tiếp dashboard service ra wildcard/public bind; public URL phải có lớp proxy/auth/TLS riêng.

## Design Principles

1. **Signal trước decoration**: run, stage, gate, evidence và next action phải nổi bật hơn trang trí.
2. **Nhanh để quét, đủ sâu để quyết định**: overview gọn, drill-down có cấu trúc, không bắt người dùng đọc raw payload.
3. **Trạng thái phải trung thực**: live, stale, partial, blocked và error luôn khác nhau về cả nội dung lẫn hành vi.
4. **Human-in-the-loop là control, không phải nghi thức**: action rõ ràng, có confirmation, có actor-derived permission và có kết quả từ server.
5. **Tên thân thiện, identity ổn định**: agent có display name Việt Nam dễ nhớ, còn `role_id`, stage ID, state version và contract không đổi.

## Accessibility & Inclusion

Chưa mở rộng accessibility scope trong pass này theo yêu cầu hiện tại. Không được cố ý làm regress semantic HTML, focus behavior, readable contrast hoặc reduced-motion behavior đã có; một pass accessibility chuyên biệt có thể được thực hiện sau.

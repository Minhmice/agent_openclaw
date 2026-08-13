from __future__ import annotations

from openclaw_web.review.dossier import render_dossier


def review_fixture() -> dict[str, object]:
    return {
        "status": "complete",
        "project_id": "p-1",
        "business_identity": {"name": "Cửa hàng Hà Nội", "industry": "dịch vụ"},
        "summary_vi": "Trang web cần làm rõ lời hứa giá trị.",
        "business_evidence": [{"claim": "Có số điện thoại", "url": "https://example.com/"}],
        "page_audits": [{"page": "Trang chủ", "url": "https://example.com/", "finding": "Thiếu CTA"}],
        "conversion_hypothesis": "Ước tính: CTA rõ hơn có thể tăng liên hệ.",
        "top_issues": [{"title": "Thiếu CTA", "severity": "P1", "url": "https://example.com/"}],
        "redesign_angle": "Bố cục tập trung vào chuyển đổi.",
        "evidence_matrix": [{"claim": "CTA", "evidence_ids": ["ev-1"]}],
        "image_evidence": [{"url": "https://example.com/shot.png", "caption": "Ảnh chụp"}],
        "confidence_gaps": ["Chưa có dữ liệu analytics"],
        "next_action": "Xác nhận ưu tiên P0/P1.",
    }


def test_dossier_labels_estimates_and_lists_evidence() -> None:
    markdown = render_dossier(review_fixture())
    assert "Ước tính" in markdown
    assert "Confidence gaps" in markdown
    assert "https://example.com/" in markdown
    assert "p-1" in markdown

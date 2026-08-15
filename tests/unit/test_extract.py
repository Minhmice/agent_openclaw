from __future__ import annotations

from openclaw_web.crawl.extract import extract_page


def test_extracts_headings_ctas_forms_and_evidence() -> None:
    html = """
    <html><head><title>  Báo   giá </title>
    <meta name="description" content=" Mô tả doanh nghiệp "></head>
    <body><h1>Nội thất văn phòng</h1><a href="/quote">Yêu cầu báo giá</a>
    <button>Liên hệ tư vấn</button><form action="/lead" method="post">
    <input name="name"><input name="phone"><button>Gửi</button></form></body></html>
    """

    page = extract_page("https://example.com/", html)

    assert page.title == "Báo giá"
    assert page.description == "Mô tả doanh nghiệp"
    assert page.headings[0].level == 1
    assert page.headings[0].text == "Nội thất văn phòng"
    assert [cta.text for cta in page.ctas] == ["Yêu cầu báo giá", "Liên hệ tư vấn"]
    assert page.ctas[0].action == "quote"
    assert page.forms[0].field_count == 2
    assert page.forms[0].fields == ("name", "phone")
    assert all(len(excerpt.text) <= 240 for excerpt in page.evidence)
    assert "<html" not in " ".join(excerpt.text for excerpt in page.evidence)


def test_cta_vocabulary_covers_english_and_vietnamese_commercial_actions() -> None:
    labels = [
        "Get a quote",
        "Đăng ký tư vấn",
        "Book now",
        "Liên hệ",
        "Gọi ngay",
        "Chat Zalo",
        "Mua ngay",
        "Xem catalogue",
    ]
    html = "".join(f"<a href='/{index}'>{label}</a>" for index, label in enumerate(labels))

    page = extract_page("https://example.com/", html)

    assert {cta.action for cta in page.ctas} == {
        "quote",
        "consultation",
        "booking",
        "contact",
        "call",
        "zalo",
        "purchase",
        "catalog",
    }


def test_extraction_is_bounded_normalized_and_ignores_non_content() -> None:
    html = """
    <script>window.secret = 'do-not-store';</script><style>.x {{ color: red }}</style>
    <p>  Useful   evidence </p><p>{long}</p><p>Third paragraph</p>
    """.format(long="x" * 2_000)

    page = extract_page(
        "https://example.com/", html, max_evidence_excerpts=2, max_excerpt_chars=32
    )

    assert len(page.evidence) == 2
    assert page.evidence[0].text == "Useful evidence"
    assert all(len(item.text) <= 32 for item in page.evidence)
    assert "do-not-store" not in " ".join(item.text for item in page.evidence)


def test_duplicate_semantic_text_and_links_are_canonicalized() -> None:
    page = extract_page(
        "https://example.com/base/",
        """
        <h2> Services </h2><h2>Services</h2>
        <a href="/contact#top">Contact us</a>
        <a href="https://example.com/contact">Contact us</a>
        <a href="mailto:secret@example.com">Email</a>
        """,
    )

    assert [heading.text for heading in page.headings] == ["Services"]
    assert page.links == ("https://example.com/contact",)
    assert len(page.ctas) == 1

"""Deterministic Vietnamese dossier rendering."""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def _text(value: object) -> str:
    return str(value).strip() if value is not None else "Chưa có dữ liệu."


def _bullets(values: object) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return [f"- {_text(values)}"]
    return [f"- {_text(value)}" for value in values] or ["- Chưa có dữ liệu."]


def _records(values: object) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
        return [f"- {_text(values)}"]
    result: list[str] = []
    for value in values:
        if isinstance(value, Mapping):
            result.append("- " + "; ".join(f"{key}: {_text(item)}" for key, item in value.items()))
        else:
            result.append(f"- {_text(value)}")
    return result or ["- Chưa có dữ liệu."]


def render_dossier(review: Mapping[str, object]) -> str:
    """Render all review sections without inventing measured claims."""

    if not isinstance(review, Mapping):
        raise TypeError("review must be a mapping")
    identity = review.get("business_identity", {})
    hypothesis = _text(review.get("conversion_hypothesis"))
    if hypothesis != "Chưa có dữ liệu." and not hypothesis.casefold().startswith("ước tính"):
        hypothesis = "Ước tính: " + hypothesis
    sections = [
        f"# Hồ sơ đánh giá website — {_text(review.get('project_id'))}",
        "\n## Trạng thái báo cáo\n" + _text(review.get("status")),
        "\n## Nhận diện doanh nghiệp\n" + _text(identity),
        "\n## Tóm tắt\n" + _text(review.get("summary_vi")),
        "\n## Bằng chứng kinh doanh\n" + "\n".join(_records(review.get("business_evidence", []))),
        "\n## Audit theo trang\n" + "\n".join(_records(review.get("page_audits", []))),
        "\n## Giả thuyết chuyển đổi\n" + hypothesis,
        "\n## Top issues\n" + "\n".join(_records(review.get("top_issues", []))),
        "\n## Góc redesign\n" + _text(review.get("redesign_angle")),
        "\n## Ma trận bằng chứng\n" + "\n".join(_records(review.get("evidence_matrix", []))),
        "\n## Image evidence\n" + "\n".join(_records(review.get("image_evidence", []))),
        "\n## Confidence gaps\n" + "\n".join(_bullets(review.get("confidence_gaps", []))),
        "\n## Next action\n" + _text(review.get("next_action")),
    ]
    return "\n".join(sections) + "\n"

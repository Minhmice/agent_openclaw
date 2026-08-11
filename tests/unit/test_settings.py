from pathlib import Path

from openclaw_web.settings import load_market


def test_hanoi_market_is_multi_industry_and_80km() -> None:
    market = load_market(Path("config/markets/hanoi-80km.yaml"))
    assert market.market_id == "hanoi-80km"
    assert market.radius_km == 80
    assert market.industries == ["*"]
    assert market.timezone == "Asia/Bangkok"

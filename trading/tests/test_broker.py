from ai_berkshire_trading.broker import krx_tick, protected_limit


def test_krx_ticks_and_price_protection():
    assert krx_tick(1_999) == 1
    assert krx_tick(50_000) == 100
    assert protected_limit("BUY", 50_000, 51_000) <= 50_250
    assert protected_limit("SELL", 50_000, 49_000) >= 49_750

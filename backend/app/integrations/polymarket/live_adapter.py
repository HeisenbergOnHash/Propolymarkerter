# POLYMARKET LIVE ADAPTER (docs.polymarket.com Gamma + CLOB APIs)
# Endpoints: gamma-api.polymarket.com, clob.polymarket.com, data-api.polymarket.com

def place_order(market, side, size):
    return f"live_order: {side} {size} shares on market {market}"

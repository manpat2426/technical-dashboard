"""
Assembles one symbol's full merged indicator history: pulls raw EOD prices
plus every technical function from EODHD (via eodhd_client), then joins
them into a single date-indexed table with the column names the rest of
the pipeline (scoring.py, airtable_client.py) expects.

This is the only place that translates EODHD's response field names
(e.g. 'uband', 'signal') and Config's parameter names (e.g. 'MA_Trend_Short')
into the pipeline's internal column names.
"""

import pandas as pd

import eodhd_client


def build_symbol_history(symbol: str, config: dict) -> pd.DataFrame:
    """
    Pulls EOD prices and every technical indicator for `symbol`, using
    periods read from the live Config dict (not hardcoded), and merges
    them into one DataFrame sorted ascending by Date with columns:
    Symbol, Date, Close, MA60, MA120, ATR20, BBUpper, BBMid, BBLower,
    MACD, MACD_Signal, RSI14.

    Dates where a given indicator isn't available yet (e.g. the first
    ~120 days of history, before MA120 has enough lookback) come through
    as NaN in that column -- scoring.py is responsible for dropping rows
    that aren't fully computable, not this function.
    """
    ma_short = int(config["MA_Trend_Short"])
    ma_long = int(config["MA_Trend_Long"])
    atr_period = int(config["ATR_Period"])
    bb_period = int(config["BB_Period"])
    rsi_period = int(config["RSI_Period"])

    eod_rows = eodhd_client.get_eod(symbol)
    close = _to_frame(eod_rows, "date", "adjusted_close", "Close")

    ma60 = _to_frame(eodhd_client.get_technical(symbol, "sma", period=ma_short), "date", "sma", "MA60")
    ma120 = _to_frame(eodhd_client.get_technical(symbol, "sma", period=ma_long), "date", "sma", "MA120")
    atr = _to_frame(eodhd_client.get_technical(symbol, "atr", period=atr_period), "date", "atr", "ATR20")
    rsi = _to_frame(eodhd_client.get_technical(symbol, "rsi", period=rsi_period), "date", "rsi", "RSI14")

    bbands_raw = eodhd_client.get_technical(symbol, "bbands", period=bb_period)
    bbands = pd.DataFrame(bbands_raw)[["date", "uband", "mband", "lband"]].rename(
        columns={"date": "Date", "uband": "BBUpper", "mband": "BBMid", "lband": "BBLower"}
    )

    # EODHD's macd function returns 'macd' and 'signal' fields per the
    # brief. Its fast/slow/signal periods happen to default to 12/26/9,
    # which is exactly what MACD_Fast/MACD_Slow/MACD_Signal are set to in
    # Config, so no override is needed for those values to match.
    macd_raw = eodhd_client.get_technical(symbol, "macd")
    macd = pd.DataFrame(macd_raw)[["date", "macd", "signal"]].rename(
        columns={"date": "Date", "macd": "MACD", "signal": "MACD_Signal"}
    )

    merged = close
    for other in (ma60, ma120, atr, rsi, bbands, macd):
        merged = merged.merge(other, on="Date", how="outer")

    merged["Symbol"] = symbol
    merged = merged.sort_values("Date").reset_index(drop=True)
    return merged


def _to_frame(rows: list, date_key: str, value_key: str, column_name: str) -> pd.DataFrame:
    """Converts one EODHD response (list of {date, <value_key>, ...}
    dicts) into a 2-column DataFrame: Date, <column_name>."""
    frame = pd.DataFrame(rows)[[date_key, value_key]]
    return frame.rename(columns={date_key: "Date", value_key: column_name})

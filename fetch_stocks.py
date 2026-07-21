"""
Fetch all Indian + US stocks & commodities data across platforms.
Sources: NSE, BSE (via Groww), NASDAQ, NYSE (via nasdaqtrader.com), Dhan (optional).
Prices: yfinance (.NS for NSE, .BO for BSE, no suffix for US).
MCX commodity prices require broker API -- instrument list only.

Usage:
    python fetch_stocks.py                 # Full run: instrument lists + all prices
    python fetch_stocks.py --test          # Quick verification check
    python fetch_stocks.py --skip-prices   # Only instrument lists, no price download
    python fetch_stocks.py --period 1y     # Custom price history period
    python fetch_stocks.py --no-us         # Skip US stocks
"""
import argparse
import io
import os
import sys
import time
import urllib.request
import pandas as pd
import yfinance as yf

NSE_CSV_URL = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
DHAN_CSV_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
GROWW_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"
NASDAQ_LIST_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
NYSE_LIST_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


# -- helpers ------------------------------------------------------------------

def _download_csv(url, name, timeout=180):
    """Download a CSV and return as DataFrame. Uses chunked read for large files."""
    print(f"  Downloading {name}...")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        chunks = []
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            chunks.append(chunk)
        data = b''.join(chunks)
    print(f"  {name}: {len(data):,} bytes")
    return pd.read_csv(io.BytesIO(data), low_memory=False)


def _download_csv_or_local(url, name, local_path, timeout=180):
    """Use local file if it exists and is recent, otherwise download."""
    if os.path.exists(local_path):
        age_hours = (time.time() - os.path.getmtime(local_path)) / 3600
        if age_hours < 24:
            print(f"  Using cached {local_path} ({age_hours:.1f}h old)")
            return pd.read_csv(local_path, low_memory=False)
    try:
        df = _download_csv(url, name, timeout)
        df.to_csv(local_path, index=False)
        return df
    except Exception as e:
        if os.path.exists(local_path):
            print(f"  Download failed ({e}), using stale cache: {local_path}")
            return pd.read_csv(local_path, low_memory=False)
        raise


def _fetch_prices(tickers, period, label, chunk_size=100):
    """Fetch OHLCV from yfinance in chunks with retry. Returns long-form DataFrame."""
    all_dfs = []
    total = len(tickers)
    for i in range(0, total, chunk_size):
        chunk = tickers[i:i + chunk_size]
        n_chunks = (total - 1) // chunk_size + 1
        print(f"  [{label}] chunk {i // chunk_size + 1}/{n_chunks} ({len(chunk)} symbols)...")
        try:
            df = yf.download(chunk, period=period, auto_adjust=False, progress=False)
            if df.empty:
                continue
            stacked = df.stack(level=1, future_stack=True).reset_index()
            stacked = stacked.rename(columns={'Ticker': 'Symbol'})
            all_dfs.append(stacked)
        except Exception as e:
            print(f"    Error: {e}")
        time.sleep(1)

    if not all_dfs:
        return pd.DataFrame()

    result = pd.concat(all_dfs, ignore_index=True)

    # Retry failed tickers in smaller chunks
    valid_symbols = set(result.dropna(subset=['Close'])['Symbol'].unique())
    failed = [t for t in tickers if t not in valid_symbols]
    if failed:
        print(f"  [{label}] Retrying {len(failed)} failed tickers...")
        retry_dfs = []
        for j in range(0, len(failed), 10):
            chunk = failed[j:j + 10]
            try:
                df = yf.download(chunk, period=period, auto_adjust=False, progress=False)
                if not df.empty:
                    stacked = df.stack(level=1, future_stack=True).reset_index()
                    stacked = stacked.rename(columns={'Ticker': 'Symbol'})
                    stacked = stacked.dropna(subset=['Close'])
                    if not stacked.empty:
                        retry_dfs.append(stacked)
            except Exception:
                pass
            time.sleep(2)
        if retry_dfs:
            retry_df = pd.concat(retry_dfs, ignore_index=True)
            result = result[~result['Symbol'].isin(retry_df['Symbol'].unique())]
            result = pd.concat([result, retry_df], ignore_index=True)

    return result


def _order_cols(df):
    """Standardize column order."""
    cols = ['Date', 'Symbol', 'Exchange', 'Open', 'High', 'Low', 'Close', 'Adj Close', 'Volume']
    return df[[c for c in cols if c in df.columns]]


# -- data source functions ----------------------------------------------------

def get_nse_symbols():
    """Fetch all active NSE equity symbols from NSE official list."""
    df = _download_csv(NSE_CSV_URL, 'NSE equity list')
    df.columns = df.columns.str.strip()
    df['SERIES'] = df['SERIES'].str.strip()
    df['SYMBOL'] = df['SYMBOL'].str.strip()
    symbols = df[df['SERIES'].isin({'EQ', 'BE', 'BZ'})]['SYMBOL'].tolist()
    print(f"  NSE symbols: {len(symbols)}")
    return symbols


def get_groww_data():
    """Download Groww instrument master. Returns (cash_df, commodity_df, full_df)."""
    df = _download_csv_or_local(GROWW_CSV_URL, 'Groww instrument master', 'groww_raw.csv')
    df.columns = df.columns.str.strip()
    print(f"  Groww total instruments: {len(df):,}")

    # Segment breakdown
    for seg in df['segment'].unique():
        print(f"    {seg}: {len(df[df['segment']==seg]):,}")

    cash = df[(df['segment'] == 'CASH') & (df['instrument_type'] == 'EQ')].copy()
    commodity = df[df['segment'] == 'COMMODITY'].copy()

    return cash, commodity, df


def get_dhan_data():
    """Download Dhan instrument master (optional, may fail due to large file)."""
    try:
        df = _download_csv_or_local(DHAN_CSV_URL, 'Dhan instrument master', 'dhan_raw.csv', timeout=300)
        df.columns = df.columns.str.strip()
        print(f"  Dhan total instruments: {len(df):,}")
        return df
    except Exception as e:
        print(f"  Dhan download failed: {e}")
        print(f"  Continuing without Dhan data (Groww covers same instruments)")
        return pd.DataFrame()


def get_us_symbols():
    """Fetch all US stock/ETF symbols from NASDAQ + NYSE official lists."""
    symbols = []

    # NASDAQ-listed
    print("  Downloading NASDAQ listed...")
    req = urllib.request.Request(NASDAQ_LIST_URL, headers=HEADERS)
    data = urllib.request.urlopen(req, timeout=30).read().decode('utf-8')
    df = pd.read_csv(io.StringIO(data), sep='|')
    df = df[(df['Test Issue'] == 'N') & (df['Symbol'].notna())]
    df = df[~df['Symbol'].str.contains('File Creation', na=False)]
    nasdaq_syms = df['Symbol'].str.strip().tolist()
    print(f"    NASDAQ: {len(nasdaq_syms)} tickers")
    symbols.extend([(s, 'NASDAQ') for s in nasdaq_syms])

    # NYSE and other exchanges
    print("  Downloading NYSE/other listed...")
    req2 = urllib.request.Request(NYSE_LIST_URL, headers=HEADERS)
    data2 = urllib.request.urlopen(req2, timeout=30).read().decode('utf-8')
    df2 = pd.read_csv(io.StringIO(data2), sep='|')
    df2 = df2[(df2['Test Issue'] == 'N') & (df2['ACT Symbol'].notna())]
    df2 = df2[~df2['ACT Symbol'].str.contains('File Creation', na=False)]
    nyse_syms = df2['ACT Symbol'].str.strip().tolist()
    print(f"    NYSE/other: {len(nyse_syms)} tickers")
    symbols.extend([(s, 'NYSE') for s in nyse_syms])

    print(f"  Total US tickers: {len(symbols)}")
    return symbols


# -- main pipeline ------------------------------------------------------------

def build_commodity_list(groww_comm_df):
    """Extract unique MCX commodities from Groww data."""
    mcx = groww_comm_df[groww_comm_df['exchange'] == 'MCX'].copy()
    if mcx.empty:
        return pd.DataFrame()

    # Get unique underlying commodities with metadata
    commodities = mcx.groupby('underlying_symbol').agg(
        instrument_count=('trading_symbol', 'count'),
        sample_symbol=('trading_symbol', 'first'),
        lot_size=('lot_size', 'first'),
    ).reset_index()
    commodities = commodities.rename(columns={'underlying_symbol': 'Commodity'})
    commodities = commodities.sort_values('Commodity')
    return commodities


def build_platform_summary(nse_symbols, bse_only_symbols, commodities, us_symbols=None):
    """Build a summary showing which platforms offer what."""
    rows = []

    for s in nse_symbols:
        rows.append({
            'Symbol': s, 'Exchange': 'NSE', 'Type': 'Equity',
            'Zerodha': 'Yes', 'Groww': 'Yes', 'Dhan': 'Yes',
            'INDmoney': 'Yes', 'Vantage': 'N/A',
        })

    for s in bse_only_symbols:
        rows.append({
            'Symbol': s, 'Exchange': 'BSE', 'Type': 'Equity',
            'Zerodha': 'Yes', 'Groww': 'Yes', 'Dhan': 'Yes',
            'INDmoney': 'Yes', 'Vantage': 'N/A',
        })

    if not commodities.empty:
        for _, row in commodities.iterrows():
            rows.append({
                'Symbol': row['Commodity'], 'Exchange': 'MCX', 'Type': 'Commodity',
                'Zerodha': 'Yes', 'Groww': 'Yes', 'Dhan': 'Yes',
                'INDmoney': 'Yes', 'Vantage': 'N/A',
            })

    if us_symbols:
        for sym, exch in us_symbols:
            rows.append({
                'Symbol': sym, 'Exchange': exch, 'Type': 'US Equity/ETF',
                'Zerodha': 'No', 'Groww': 'Yes', 'Dhan': 'Yes',
                'INDmoney': 'Yes', 'Vantage': 'N/A',
            })

    return pd.DataFrame(rows)


def run_test():
    """Quick self-check."""
    print("=== Verification Check ===\n")

    print("1. NSE symbols...")
    nse_syms = get_nse_symbols()
    assert len(nse_syms) > 2000, f"Expected 2000+ NSE symbols, got {len(nse_syms)}"

    print("\n2. NSE price fetch (5 symbols)...")
    nse_prices = _fetch_prices(
        [f"{s}.NS" for s in ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK']],
        period='5d', label='NSE-test', chunk_size=5
    )
    assert not nse_prices.empty and 'Close' in nse_prices.columns

    print("\n3. BSE price fetch (3 symbols)...")
    bse_prices = _fetch_prices(['RELIANCE.BO', 'TCS.BO', 'INFY.BO'], period='5d', label='BSE-test', chunk_size=3)
    assert not bse_prices.empty

    print("\n4. US stocks list + price fetch...")
    us_syms = get_us_symbols()
    assert len(us_syms) > 10000, f"Expected 10000+ US tickers, got {len(us_syms)}"
    us_test = _fetch_prices(['AAPL', 'MSFT', 'GOOGL'], period='5d', label='US-test', chunk_size=3)
    assert not us_test.empty

    print("\n5. Groww instrument master...")
    cash, comm, _ = get_groww_data()
    assert len(cash) > 10000, f"Expected 10000+ Groww CASH instruments, got {len(cash)}"

    print("\n=== All checks passed ===")
    print(f"  NSE symbols: {len(nse_syms)}")
    print(f"  NSE test rows: {len(nse_prices)}")
    print(f"  BSE test rows: {len(bse_prices)}")
    print(f"  US tickers: {len(us_syms)}, US test rows: {len(us_test)}")
    print(f"  Groww CASH: {len(cash)}, COMMODITY: {len(comm)}")


def main():
    parser = argparse.ArgumentParser(description="Fetch all Indian + US stocks & commodities across platforms.")
    parser.add_argument('--period', default='30d', help="Price history period (default: 30d)")
    parser.add_argument('--test', action='store_true', help="Quick verification check")
    parser.add_argument('--skip-prices', action='store_true', help="Only fetch instrument lists, skip price download")
    parser.add_argument('--no-us', action='store_true', help="Skip US stocks")
    args = parser.parse_args()

    if args.test:
        run_test()
        sys.exit(0)

    start = time.time()

    # -- Step 1: NSE official equity list --------------------------------─
    print("\n=== Step 1: NSE Equity List ===")
    nse_symbols = get_nse_symbols()
    nse_set = set(nse_symbols)

    # -- Step 2: Groww instrument master ----------------------------------
    print("\n=== Step 2: Groww Instrument Master ===")
    groww_cash, groww_comm, groww_full = get_groww_data()

    # BSE-only equity symbols (on Groww but not in NSE list)
    bse_eq = groww_cash[groww_cash['exchange'] == 'BSE']
    bse_symbols = bse_eq['trading_symbol'].str.strip().tolist()
    # Filter to alphanumeric symbols that aren't on NSE
    bse_only = [s for s in bse_symbols if s not in nse_set and s[0:1].isalpha()]
    print(f"  BSE-only symbols (not on NSE): {len(bse_only)}")

    # MCX commodities
    commodities = build_commodity_list(groww_comm)
    print(f"  Unique MCX commodities: {len(commodities)}")

    # -- Step 3: Dhan instrument master (optional) ------------------------
    print("\n=== Step 3: Dhan Instrument Master (optional) ===")
    dhan_df = get_dhan_data()

    # -- Step 4: US stocks list -------------------------------------------
    us_symbols = []
    if not args.no_us:
        print("\n=== Step 4: US Stocks List (NASDAQ + NYSE) ===")
        try:
            us_symbols = get_us_symbols()
        except Exception as e:
            print(f"  Warning: Could not fetch US stock list: {e}")
    else:
        print("\n  Skipping US stocks (--no-us)")

    # -- Step 5: Fetch prices ---------------------------------------------
    nse_prices = pd.DataFrame()
    bse_prices = pd.DataFrame()
    us_prices = pd.DataFrame()

    if not args.skip_prices:
        print(f"\n=== Step 5: Fetching NSE Stock Prices ({len(nse_symbols)} symbols) ===")
        nse_tickers = [f"{s}.NS" for s in nse_symbols]
        nse_prices = _fetch_prices(nse_tickers, period=args.period, label='NSE')
        if not nse_prices.empty:
            nse_prices['Exchange'] = 'NSE'

        print(f"\n=== Step 6: Fetching BSE-Only Stock Prices ({len(bse_only)} symbols) ===")
        if bse_only:
            bse_tickers = [f"{s}.BO" for s in bse_only]
            bse_prices = _fetch_prices(bse_tickers, period=args.period, label='BSE')
            if not bse_prices.empty:
                bse_prices['Exchange'] = 'BSE'

        if us_symbols:
            us_tickers = [sym for sym, _ in us_symbols]
            print(f"\n=== Step 7: Fetching US Stock Prices ({len(us_tickers)} symbols) ===")
            us_prices = _fetch_prices(us_tickers, period=args.period, label='US')
            if not us_prices.empty:
                us_prices['Exchange'] = 'US'
    else:
        print("\n  Skipping price download (--skip-prices)")

    # -- Save all output files ---------------------------------------------
    print("\n=== Saving Output Files ===")

    # NSE stock prices
    if not nse_prices.empty:
        out = _order_cols(nse_prices.sort_values(['Symbol', 'Date']))
        out.to_csv('nse_stocks_data.csv', index=False)
        print(f"  nse_stocks_data.csv: {out['Symbol'].nunique()} stocks, {len(out):,} rows")

    # BSE-only stock prices
    if not bse_prices.empty:
        out = _order_cols(bse_prices.sort_values(['Symbol', 'Date']))
        out.to_csv('bse_stocks_data.csv', index=False)
        print(f"  bse_stocks_data.csv: {out['Symbol'].nunique()} stocks, {len(out):,} rows")

    # US stock prices
    if not us_prices.empty:
        out = _order_cols(us_prices.sort_values(['Symbol', 'Date']))
        out.to_csv('us_stocks_data.csv', index=False)
        print(f"  us_stocks_data.csv: {out['Symbol'].nunique()} stocks, {len(out):,} rows")

    # Combined all prices (Indian + US)
    combined = pd.concat([nse_prices, bse_prices, us_prices], ignore_index=True)
    if not combined.empty:
        out = _order_cols(combined.sort_values(['Symbol', 'Date']))
        out.to_csv('all_stocks_data.csv', index=False)
        print(f"  all_stocks_data.csv: {out['Symbol'].nunique()} stocks, {len(out):,} rows")

    # MCX commodities list (prices need broker API)
    if not commodities.empty:
        commodities.to_csv('commodities_list.csv', index=False)
        print(f"  commodities_list.csv: {len(commodities)} unique MCX commodities")

    # Platform instruments summary
    summary = build_platform_summary(nse_symbols, bse_only, commodities, us_symbols)
    summary.to_csv('platform_instruments.csv', index=False)
    print(f"  platform_instruments.csv: {len(summary):,} instruments across platforms")

    # Raw Groww master
    groww_full.to_csv('groww_instruments_master.csv', index=False)
    print(f"  groww_instruments_master.csv: {len(groww_full):,} instruments")

    # Raw Dhan master (if downloaded)
    if not dhan_df.empty:
        dhan_df.to_csv('dhan_instruments_master.csv', index=False)
        print(f"  dhan_instruments_master.csv: {len(dhan_df):,} instruments")

    duration = time.time() - start
    print(f"\n=== Done in {duration:.1f}s ===")

    # Summary
    print("\n-- Summary --")
    print(f"  NSE equities: {len(nse_symbols)}")
    print(f"  BSE-only equities: {len(bse_only)}")
    print(f"  US tickers: {len(us_symbols)}")
    print(f"  MCX commodities: {len(commodities)}")
    if not combined.empty:
        print(f"  Total stocks with prices: {combined['Symbol'].nunique()}")
        print(f"  Total price rows: {len(combined):,}")
    # ponytail: MCX commodity prices need broker API (Zerodha/Dhan/Groww credentials), instrument list only for now


if __name__ == '__main__':
    main()

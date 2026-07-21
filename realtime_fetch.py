"""
Real-time stock price tracker using yfinance.
Fetches latest prices for a watchlist of tickers and displays a live console dashboard.
"""
import argparse
import os
import sys
import time
import pandas as pd
import yfinance as yf

DEFAULT_WATCHLIST = 'watchlist.txt'
DEFAULT_INTERVAL = 10


def load_watchlist(filepath):
    """Load tickers from a text file, ignoring empty lines and comments."""
    if not os.path.exists(filepath):
        print(f"Watchlist file not found: {filepath}. Creating default with example tickers.")
        with open(filepath, 'w') as f:
            f.write("# Watchlist\nRELIANCE.NS\nTCS.NS\nAAPL\nMSFT\nTSLA\n")
    
    tickers = []
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                tickers.append(line)
    return tickers


def fetch_live_data(tickers):
    """Fetch 2-day historical data to compute current price and daily percent change."""
    try:
        # Fetch 2d to ensure we have the previous close for calculation
        df = yf.download(tickers, period='2d', auto_adjust=False, progress=False)
        if df.empty:
            return pd.DataFrame()
        
        # Reshape to long format depending on MultiIndex
        if isinstance(df.columns, pd.MultiIndex):
            stacked = df.stack(level=1, future_stack=True).reset_index()
            stacked = stacked.rename(columns={'Ticker': 'Symbol'})
        else:
            # Single ticker, single-level columns
            stacked = df.reset_index()
            # If tickers is a list, use its first element; if string, use it directly
            symbol = tickers[0] if isinstance(tickers, list) else tickers
            stacked['Symbol'] = symbol
            
        return stacked
    except Exception as e:
        print(f"Error fetching data: {e}", file=sys.stderr)
        return pd.DataFrame()


def process_latest_prices(df, tickers):
    """Process long-form dataframe to find the latest and previous close prices for each symbol."""
    results = []
    
    # Ensure Symbol column exists
    if 'Symbol' not in df.columns:
        return pd.DataFrame()
        
    for symbol in tickers:
        sym_df = df[df['Symbol'] == symbol].sort_values('Date')
        if sym_df.empty:
            continue
            
        # Drop rows where Close is NaN to find latest available data
        valid_df = sym_df.dropna(subset=['Close'])
        if len(valid_df) == 0:
            continue
            
        latest_row = valid_df.iloc[-1]
        latest_price = latest_row['Close']
        open_price = latest_row['Open']
        high = latest_row['High']
        low = latest_row['Low']
        volume = latest_row['Volume']
        
        prev_price = None
        pct_change = 0.0
        
        if len(valid_df) > 1:
            prev_price = valid_df.iloc[-2]['Close']
            if prev_price and prev_price > 0:
                pct_change = ((latest_price - prev_price) / prev_price) * 100
        else:
            # Fallback to intraday change from open
            if open_price and open_price > 0:
                pct_change = ((latest_price - open_price) / open_price) * 100
                
        results.append({
            'Symbol': symbol,
            'Price': latest_price,
            'Prev Close': prev_price if prev_price is not None else open_price,
            'Change %': pct_change,
            'Open': open_price,
            'High': high,
            'Low': low,
            'Volume': volume,
            'Last Update': latest_row['Date'].strftime('%Y-%m-%d')
        })
        
    return pd.DataFrame(results)


def display_dashboard(df):
    """Render a clean, colorful dashboard in the console."""
    # Clear screen
    os.system('cls' if os.name == 'nt' else 'clear')
    
    # Colors
    GREEN = '\033[92m'
    RED = '\033[91m'
    RESET = '\033[0m'
    BOLD = '\033[1m'
    
    print(f"{BOLD}=== Real-Time Stock Price Tracker ==={RESET}")
    print(f"Local Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("-" * 80)
    print(f"{BOLD}{'Symbol':<15} {'Price':>10} {'Change %':>12} {'Open':>10} {'High':>10} {'Low':>10} {'Volume':>10}{RESET}")
    print("-" * 80)
    
    for _, row in df.iterrows():
        pct = row['Change %']
        color = GREEN if pct > 0 else (RED if pct < 0 else RESET)
        sign = '+' if pct > 0 else ''
        
        print(f"{row['Symbol']:<15} "
              f"{row['Price']:>10.2f} "
              f"{color}{sign}{pct:>11.2f}%{RESET} "
              f"{row['Open']:>10.2f} "
              f"{row['High']:>10.2f} "
              f"{row['Low']:>10.2f} "
              f"{int(row['Volume']):>10,}")
              
    print("-" * 80)
    print("Press Ctrl+C to exit.")


def main():
    parser = argparse.ArgumentParser(description="Real-Time Stock Price Tracker")
    parser.add_argument('--tickers', help="Comma-separated list of tickers (overrides watchlist)")
    parser.add_argument('--watchlist', default=DEFAULT_WATCHLIST, help=f"Path to watchlist file (default: {DEFAULT_WATCHLIST})")
    parser.add_argument('--interval', type=int, default=DEFAULT_INTERVAL, help=f"Update interval in seconds (default: {DEFAULT_INTERVAL})")
    parser.add_argument('--once', action='store_true', help="Run once and exit")
    args = parser.parse_args()

    # Load tickers
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(',') if t.strip()]
    else:
        tickers = load_watchlist(args.watchlist)

    if not tickers:
        print("No tickers specified. Exiting.")
        sys.exit(1)

    print(f"Initializing tracker for {len(tickers)} symbols...")

    try:
        while True:
            raw_data = fetch_live_data(tickers)
            if not raw_data.empty:
                processed_df = process_latest_prices(raw_data, tickers)
                if not processed_df.empty:
                    display_dashboard(processed_df)
                    # Save snapshot
                    processed_df.to_csv('realtime_prices.csv', index=False)
                else:
                    print("Failed to process latest prices.")
            else:
                print("No data received.")

            if args.once:
                break

            time.sleep(args.interval)
            
    except KeyboardInterrupt:
        print("\nExiting real-time tracker. Goodbye!")


if __name__ == '__main__':
    main()

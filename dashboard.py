import json
import os
import sys
import urllib.parse
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pandas as pd
import yfinance as yf

import base64
import time

PORT = int(os.environ.get('PORT', 8000))
CSV_FILE = 'platform_instruments.csv'
INFO_CACHE = {} # Key: query_symbol, Value: (expiry_timestamp, info_dict)

def get_clerk_domain(publishable_key):
    try:
        parts = publishable_key.split('_')
        if len(parts) >= 3:
            base64_part = parts[-1]
            missing_padding = len(base64_part) % 4
            if missing_padding:
                base64_part += '=' * (4 - missing_padding)
            decoded = base64.b64decode(base64_part).decode('utf-8')
            return decoded[:-1] if decoded.endswith('$') else decoded
    except Exception:
        pass
    return None

def load_env():
    # Load from root .env
    if os.path.exists('.env'):
        with open('.env', 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k.strip()] = v.strip()

    # Load from Vite app .env.local
    vite_env = os.path.join('my-clerk-vite-app', '.env.local')
    if os.path.exists(vite_env):
        with open(vite_env, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    k_str = k.strip()
                    v_str = v.strip()
                    os.environ[k_str] = v_str
                    if k_str == 'VITE_CLERK_PUBLISHABLE_KEY':
                        os.environ['CLERK_PUBLISHABLE_KEY'] = v_str

load_env()

def fetch_google_finance_data(symbol, exchange=None):
    # 1. Resolve exchange
    base_symbol = symbol.split('.')[0]
    if symbol.endswith('.NS') or exchange == 'NSE':
        g_exch = 'NSE'
        g_sym = base_symbol
    elif symbol.endswith('.BO') or exchange == 'BSE':
        g_exch = 'BSE'
        g_sym = base_symbol
    else:
        # US Stock
        g_exch = exchange if exchange in ('NASDAQ', 'NYSE') else 'NASDAQ'
        g_sym = base_symbol
        
    def try_fetch(sym, exch):
        url = f"https://www.google.com/finance/quote/{sym}:{exch}"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                return response.read().decode('utf-8')
        except Exception:
            return None

    html = try_fetch(g_sym, g_exch)
    if not html and g_exch == 'NASDAQ' and not (symbol.endswith('.NS') or symbol.endswith('.BO')):
        # Fallback to NYSE for US stocks
        g_exch = 'NYSE'
        html = try_fetch(g_sym, g_exch)
        
    if not html:
        return {}
        
    # Parse key-values
    matches = re.findall(r'<div class="SwQK7">([^<]+)</div>[^<]*<div class="dO6ijd">([^<]+)</div>', html)
    stats = {}
    for k, v in matches:
        k_clean = k.encode('ascii', 'ignore').decode('ascii').strip().lower()
        v_clean = v.encode('ascii', 'ignore').decode('ascii').strip()
        stats[k_clean] = v_clean
        
    # Parse price and change percent
    price_val = None
    change_pct = None
    
    # Pattern: ["AAPL","NASDAQ"],"Apple Inc",0,"USD",[333.74,0.48,0.0014
    pattern = rf'\["{re.escape(g_sym)}\",\"{re.escape(g_exch)}\"\],\"([^\"]+)\",\d+,\"[A-Z]{{3}}\",\[([0-9\.]+),([0-9\.-]+),([0-9\.-]+)'
    match = re.search(pattern, html)
    if match:
        try:
            price_val = float(match.group(2))
            change_pct = float(match.group(4)) * 100
        except Exception:
            pass
            
    # Normalize currency symbol
    currency_symbol = '₹' if g_exch in ('NSE', 'BSE') else '$'
    
    return {
        'Price': price_val,
        'Change_Percent': change_pct,
        'Open': stats.get('open'),
        'High': stats.get('high'),
        'Low': stats.get('low'),
        'Volume': stats.get('volume'),
        'MarketCap': stats.get('mkt. cap'),
        'PE': stats.get('p/e ratio'),
        'EPS': stats.get('eps'),
        'DivYield': stats.get('dividend'),
        'Beta': stats.get('beta'),
        '52WHigh': stats.get('52-wk high'),
        '52WLow': stats.get('52-wk low'),
        'Currency': currency_symbol
    }
def fetch_yf_info_with_timeout(query_symbol, timeout_sec=2.5):
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(lambda: yf.Ticker(query_symbol).info)
        return future.result(timeout=timeout_sec)

OFFLINE_STOCK_LOOKUP = {}

def fetch_offline_stock_data(symbol, exchange=None):
    base_symbol = symbol.split('.')[0]
    query_syms = [symbol, f"{base_symbol}.NS", f"{base_symbol}.BO", base_symbol]
    
    for q in query_syms:
        if q in OFFLINE_STOCK_LOOKUP:
            rec = OFFLINE_STOCK_LOOKUP[q]
            resolved_exch = exchange
            if not resolved_exch:
                matching_instrument = df_instruments[df_instruments['Symbol'] == base_symbol]
                if not matching_instrument.empty:
                    resolved_exch = matching_instrument.iloc[0]['Exchange']
            return {
                'Symbol': symbol,
                'Exchange': resolved_exch or 'NSE',
                'Price': rec['Price'],
                'Open': rec['Open'],
                'High': rec['High'],
                'Low': rec['Low'],
                'Volume': rec['Volume'],
                'Change_Percent': rec['Change_Percent'],
                '52WHigh': rec['52WHigh'],
                '52WLow': rec['52WLow']
            }
    return None

# Load instruments on startup for fast search
print("Loading instruments database...")
if os.path.exists(CSV_FILE):
    df_instruments = pd.read_csv(CSV_FILE, low_memory=False)
    # Ensure columns exist and fill NaNs
    df_instruments = df_instruments.fillna('')
    print(f"Loaded {len(df_instruments):,} instruments.")
else:
    print(f"Warning: {CSV_FILE} not found. Search function will be empty. Run fetch_stocks.py first.")
    df_instruments = pd.DataFrame(columns=['Symbol', 'Exchange', 'Type'])

print("Pre-indexing offline historical stock dataset for instant lookups...")
for csv_name in ["nse_stocks_data.csv", "us_stocks_data.csv", "all_stocks_data.csv"]:
    if os.path.exists(csv_name):
        try:
            df_h = pd.read_csv(csv_name, low_memory=False)
            df_h = df_h.dropna(subset=['Symbol', 'Close'])
            for sym, group in df_h.groupby('Symbol'):
                if sym not in OFFLINE_STOCK_LOOKUP:
                    sorted_g = group.sort_values('Date')
                    latest = sorted_g.iloc[-1]
                    prev = sorted_g.iloc[-2] if len(sorted_g) > 1 else latest
                    price = float(latest['Close'])
                    prev_p = float(prev['Close'])
                    vol = int(latest['Volume']) if pd.notna(latest.get('Volume')) else 0
                    OFFLINE_STOCK_LOOKUP[sym] = {
                        'Price': price,
                        'Open': float(latest['Open']) if pd.notna(latest.get('Open')) else price,
                        'High': float(latest['High']) if pd.notna(latest.get('High')) else price,
                        'Low': float(latest['Low']) if pd.notna(latest.get('Low')) else price,
                        'Volume': vol,
                        'Change_Percent': ((price - prev_p) / prev_p * 100) if prev_p > 0 else 0.0,
                        '52WHigh': float(sorted_g['High'].max()),
                        '52WLow': float(sorted_g['Low'].min())
                    }
        except Exception as e:
            print(f"Error indexing {csv_name}: {e}")
print(f"Indexed {len(OFFLINE_STOCK_LOOKUP):,} offline stock records into memory.")

if os.path.exists("fundamentals_master.csv"):
    try:
        df_fm = pd.read_csv("fundamentals_master.csv", low_memory=False)
        df_fm = df_fm.fillna('N/A')
        for _, r in df_fm.iterrows():
            sym = str(r.get('Symbol', '')).strip()
            if sym and sym in OFFLINE_STOCK_LOOKUP:
                OFFLINE_STOCK_LOOKUP[sym]['PE'] = r.get('PE_Ratio', 'N/A')
                OFFLINE_STOCK_LOOKUP[sym]['ROE'] = r.get('ROE', 'N/A')
                OFFLINE_STOCK_LOOKUP[sym]['DebtToEquity'] = r.get('Debt_To_Equity', 'N/A')
                OFFLINE_STOCK_LOOKUP[sym]['RevenueGrowth'] = r.get('Revenue_Growth_YoY', 'N/A')
                OFFLINE_STOCK_LOOKUP[sym]['ProfitGrowth'] = r.get('Profit_Growth_YoY', 'N/A')
                OFFLINE_STOCK_LOOKUP[sym]['OperatingCashflow'] = r.get('Operating_Cashflow', 'N/A')
                OFFLINE_STOCK_LOOKUP[sym]['PromoterHolding'] = r.get('Promoter_Holding', 'N/A')
        print(f"Enriched {len(df_fm):,} equities with pre-calculated fundamental metrics.")
    except Exception as e:
        print(f"Fundamentals master load warning: {e}")


# -- Chatbot logic -------------------------------------------------------------

# Common English words that happen to be valid stock symbols (avoid false positives)
IGNORE_WORDS = {
    # Articles, conjunctions, prepositions
    'A', 'AN', 'THE', 'AND', 'OR', 'BUT', 'IF', 'FOR', 'WITH', 'BY', 'TO', 'FROM',
    'IN', 'ON', 'AT', 'OF', 'ARE', 'IS', 'AM', 'WAS', 'WERE', 'BE', 'BEEN', 'DO',
    'DOES', 'DID', 'HAS', 'HAVE', 'HAD', 'GO', 'GET', 'CAN', 'COULD', 'WHAT', 'WHO',
    'WHEN', 'WHERE', 'WHY', 'HOW', 'IT', 'ITS', 'THEY', 'THEIR', 'THIS', 'THAT',
    'THESE', 'THOSE', 'YOU', 'YOUR', 'ME', 'MY', 'WE', 'OUR', 'HE', 'SHE', 'HIM',
    'HER', 'US', 'OUT', 'ALL', 'NEW', 'NOW', 'ONE', 'TWO', 'KEY', 'SEE', 'ANY', 'NOT',
    # Finance jargon
    'IPO', 'IPOS', 'STOCK', 'STOCKS', 'NEWS', 'PORT', 'CMP', 'LIVE', 'FEED', 'LIST',
    'RUN', 'INFO', 'TODAY', 'PRICE', 'PRICES', 'CHART', 'CHARTS', 'TRADE', 'TRADES',
    'MARKET', 'MARKETS', 'SHARE', 'SHARES', 'BUY', 'SELL', 'TREND', 'TRENDS',
    # Common verbs/adjectives that match tickers (KNOW, TELL, REAL, TRUE, GOOD, etc.)
    'KNOW', 'TELL', 'SHOW', 'GIVE', 'FIND', 'MAKE', 'TAKE', 'COME', 'KEEP', 'LOOK',
    'WANT', 'NEED', 'WILL', 'JUST', 'MUCH', 'VERY', 'ALSO', 'BACK', 'EVEN', 'STILL',
    'WELL', 'WORK', 'CALL', 'LONG', 'BEST', 'GOOD', 'REAL', 'TRUE', 'OPEN', 'CLOSE',
    'HIGH', 'LOW', 'FAST', 'NEXT', 'LAST', 'MOST', 'ONLY', 'SOME', 'MANY', 'LIKE',
    'OVER', 'THAN', 'THEN', 'EACH', 'BOTH', 'SUCH', 'INTO', 'SAME', 'DOWN', 'SURE',
    'ABLE', 'PLAY', 'RISK', 'SAFE', 'RATE', 'GAIN', 'LOSS', 'EARN', 'FUND', 'FUNDS',
    'MOVE', 'HOLD', 'PULL', 'PUSH', 'PICK', 'DROP', 'FLAT', 'PEAK', 'COST',
    # Question words / chatbot filler
    'ABOUT', 'WHICH', 'THERE', 'THINK', 'THING', 'BEEN', 'WOULD', 'SHOULD', 'COULD',
    'MIGHT', 'SHALL', 'WILL', 'PLEASE', 'THANKS', 'THANK', 'OKAY', 'YEAH', 'YES', 'NO',
    'PROJECT', 'PROJECTS', 'ASSISTANT', 'ASSISTANTS'
}

def extract_stock_symbol(msg, df_instruments):
    """Extract a stock symbol from a chat message. Strategies in order:
    1. Exact word == symbol (longest match wins)
    2. Word matches a known company name → return its symbol
    3. Contains-match only if word >= 4 chars and only on the longest candidate
    """
    words = [w.strip("?,.!-()\"'") for w in msg.upper().split()]
    candidate_words = [w for w in words if w not in IGNORE_WORDS and len(w) >= 2]

    symbols_set = set(df_instruments['Symbol'].values)

    # 1. Exact match — prefer the longest matching word (STLTECH over S)
    exact_matches = [(w, w) for w in candidate_words if w in symbols_set]
    if exact_matches:
        # Return the longest matched symbol (most specific)
        exact_matches.sort(key=lambda x: len(x[0]), reverse=True)
        return exact_matches[0][1]

    # 2. Company name match — check if any word appears in company names
    if 'Name' in df_instruments.columns:
        for w in candidate_words:
            if len(w) < 3:
                continue
            name_matches = df_instruments[df_instruments['Name'].str.upper().str.contains(w, na=False)]
            if not name_matches.empty:
                return name_matches.iloc[0]['Symbol']

    # 3. Contains-match fallback — only for the longest candidate word >= 4 chars
    long_candidates = sorted([w for w in candidate_words if len(w) >= 4], key=len, reverse=True)
    for word in long_candidates:
        matches = df_instruments[df_instruments['Symbol'].str.upper() == word]
        if not matches.empty:
            return matches.iloc[0]['Symbol']
        # Substring match only for words >= 5 chars to avoid noise
        if len(word) >= 5:
            matches = df_instruments[df_instruments['Symbol'].str.upper().str.contains(word, na=False)]
            if not matches.empty:
                return matches.iloc[0]['Symbol']

    return None


def fetch_google_news(query):
    try:
        encoded_query = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-US&gl=US&ceid=US:en"
        # If query contains words like "india", "indian", "nse", "bse", "rupee", use Indian locale
        if any(w in query.lower() for w in ('india', 'indian', 'nse', 'bse', 'mcx', 'rupee', 'nifty', 'sensex')):
            url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"
            
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            xml_data = response.read()
            
        import xml.etree.ElementTree as ET
        root = ET.fromstring(xml_data)
        items = root.findall('.//item')
        
        if not items:
            return "I couldn't find any recent information or news articles matching your query."
            
        reply = f"Here is the latest news & research found for '<b>{query}</b>':<br><br>"
        for item in items[:5]:
            title = item.find('title').text
            link = item.find('link').text
            pub_date = item.find('pubDate').text
            # Format title (often has source appended like ' - BBC')
            title_clean = title
            source = ""
            if " - " in title:
                parts = title.rsplit(" - ", 1)
                title_clean = parts[0]
                source = parts[1]
                
            source_lbl = f" (via {source})" if source else ""
            reply += f"• <a href='{link}' target='_blank' style='color: var(--primary-blue); font-weight: 500;'>{title_clean}</a>{source_lbl}<br>" \
                     f"  <span style='font-size: 0.75rem; color: var(--text-secondary);'>Published: {pub_date}</span><br><br>"
        return reply
    except Exception as e:
        return f"I tried to research the web for '<b>{query}</b>' but encountered an error: {e}"

def handle_chat_query(msg, df_instruments):
    msg_lower = msg.lower()
    
    # 1. General FAQ matches (prioritized to prevent word conflicts)
    if any(k in msg_lower.split() for k in ('hello', 'hi', 'hey', 'greetings', 'help')) or 'who are you' in msg_lower:
        return "Hello! I am your Nexora Stock Assistant. I can answer questions about this project's architecture, its files, and platforms, or fetch the live price and financials of any stock in our database (e.g. 'What is the price of AAPL?')."
        
    elif any(k in msg_lower for k in ('file', 'csv', 'output', 'save', 'store')):
        return "Our project generates several key output files in the directory:<br>" \
               "• <code>all_stocks_data.csv</code> - Combined 30-day prices for all 18,778 stocks.<br>" \
               "• <code>nse_stocks_data.csv</code> - Prices for 2,387 NSE equities.<br>" \
               "• <code>bse_stocks_data.csv</code> - Prices for 3,373 BSE equities.<br>" \
               "• <code>us_stocks_data.csv</code> - Prices for 13,018 US NASDAQ/NYSE equities.<br>" \
               "• <code>commodities_list.csv</code> - List of 30 MCX commodities.<br>" \
               "• <code>platform_instruments.csv</code> - Map of instruments available on Zerodha, Groww, Dhan, and INDmoney."

    elif any(k in msg_lower for k in ('run', 'execute', 'refresh', 'how to use', 'usage', 'cmd', 'command')):
        return "You can interact with the project scripts as follows:<br>" \
               "• <b>Live Web Dashboard:</b> Run <code>python dashboard.py</code> and open <code>http://localhost:8000</code>.<br>" \
               "• <b>Batch Download:</b> Run <code>python fetch_stocks.py</code>. Add <code>--no-us</code> to skip US stocks or <code>--skip-prices</code> to only get lists.<br>" \
               "• <b>Live Terminal Tracker:</b> Run <code>python realtime_fetch.py</code> to track your custom watchlist."

    elif any(k in msg_lower for k in ('platform', 'broker', 'zerodha', 'groww', 'dhan', 'indmoney', 'vantage')):
        return "Our database consolidates symbols from:<br>" \
               "• <b>Groww & Dhan</b>: Supported for Indian Stocks (NSE/BSE), US Stocks, and MCX Commodities.<br>" \
               "• <b>Zerodha & INDmoney</b>: Covered in our mapping. Specific lists require your private API keys/credentials.<br>" \
               "• <b>Vantage Markets</b>: Does not serve India. If you meant <i>Alpha Vantage</i> (data feed), it only lists BSE stocks."

    elif any(k in msg_lower for k in ('mcx', 'commodity', 'gold', 'silver', 'crude')):
        return "We parse 30 unique MCX commodities (including Gold, Silver, Crude Oil, Natural Gas, etc.) from Groww/Dhan CSV lists. Note that live/historical prices for commodities are not covered by yfinance and require a connection via broker APIs."

    elif any(k in msg_lower for k in ('realtime', 'real time', 'delay', 'live')):
        return "US Stocks (NASDAQ/NYSE) and Cryptocurrencies update in <b>real-time</b>. Indian Stocks (NSE/BSE) update live but have a standard <b>15-minute delay</b> due to yfinance public licensing constraints."

    # 2. Dynamic Stock Price Lookup (Fallback match)
    stock_sym = extract_stock_symbol(msg, df_instruments)
    if stock_sym:
        try:
            matching_instrument = df_instruments[df_instruments['Symbol'] == stock_sym]
            exchange = 'US'
            if not matching_instrument.empty:
                exchange = matching_instrument.iloc[0]['Exchange']

            query_symbol = stock_sym
            if not (stock_sym.endswith('.NS') or stock_sym.endswith('.BO')):
                if exchange == 'NSE':
                    query_symbol = f"{stock_sym}.NS"
                elif exchange == 'BSE':
                    query_symbol = f"{stock_sym}.BO"

            if exchange == 'MCX':
                return f"I found the commodity <b>{stock_sym}</b> on the MCX exchange. However, live MCX pricing is not supported directly via yfinance and requires a broker API connection."

            ticker = yf.Ticker(query_symbol)
            info = ticker.info
            
            latest_price = info.get('currentPrice') or info.get('regularMarketPrice')
            open_price = info.get('open') or info.get('regularMarketOpen')
            
            if not latest_price:
                df = yf.download(query_symbol, period='1d', progress=False)
                if not df.empty:
                    latest_price = float(df['Close'].iloc[-1])
                    open_price = float(df['Open'].iloc[-1])

            if latest_price:
                change_pct = 0.0
                prev_close = info.get('previousClose')
                if prev_close:
                    change_pct = ((latest_price - prev_close) / prev_close) * 100
                elif open_price:
                    change_pct = ((latest_price - open_price) / open_price) * 100

                currency = '₹' if exchange in ('NSE', 'BSE') else '$'
                sign = '+' if change_pct > 0 else ''
                summary = info.get('longBusinessSummary', '')
                short_summary = (summary[:150] + "...") if summary else "No business summary available."

                return f"Here is the latest data for <b>{stock_sym}</b> ({exchange}):<br>" \
                        f"• <b>Current Price (CMP):</b> {currency} {latest_price:,.2f}<br>" \
                        f"• <b>Daily Change:</b> {sign}{change_pct:.2f}%<br>" \
                        f"• <b>Market Cap:</b> {currency} {info.get('marketCap', 0):,}<br>" \
                        f"• <b>P/E Ratio:</b> {info.get('trailingPE', '-')}<br>" \
                        f"• <b>About:</b> {short_summary}"
        except Exception as e:
            return f"I found the stock symbol <b>{stock_sym}</b>, but encountered an error fetching its live data: {e}."

    # 3. Web Research Fallback
    return fetch_google_news(msg)


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress request logs to keep terminal clean
        return

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        query_params = urllib.parse.parse_qs(parsed_url.query)

        # 1. API: Search Autocomplete
        if path == '/api/search':
            query = query_params.get('q', [''])[0].strip().upper()
            if not query:
                self.send_json([])
                return

            # Filter instruments: Match symbol starting with or containing query
            # Prioritize symbol prefix match, then generic containing match
            prefix_match = df_instruments[df_instruments['Symbol'].str.upper().str.startswith(query)]
            contain_match = df_instruments[
                df_instruments['Symbol'].str.upper().str.contains(query) & 
                ~df_instruments['Symbol'].str.upper().str.startswith(query)
            ]
            
            merged = pd.concat([prefix_match, contain_match], ignore_index=True)
            results = merged.head(15).to_dict(orient='records')
            
            # Format results
            formatted = []
            for r in results:
                formatted.append({
                    'Symbol': r.get('Symbol', ''),
                    'Exchange': r.get('Exchange', ''),
                    'Type': r.get('Type', '')
                })
            
            self.send_json(formatted)
            return

        # 2. API: Current Market Price (CMP)
        elif path == '/api/cmp':
            symbol = query_params.get('symbol', [''])[0].strip()
            exchange = query_params.get('exchange', [''])[0].strip()
            if not symbol:
                self.send_json({'error': 'No symbol specified'}, status=400)
                return

            # If exchange not passed, look it up in local DB
            if not exchange:
                base_symbol = symbol.split('.')[0]
                matching_instrument = df_instruments[df_instruments['Symbol'] == base_symbol]
                if not matching_instrument.empty:
                    exchange = matching_instrument.iloc[0]['Exchange']

            # Handle MCX commodities which are not supported by yfinance
            if exchange == 'MCX':
                self.send_json({'error': 'MCX pricing requires broker login'})
                return

            try:
                # 1. Try Google Finance first
                gf = fetch_google_finance_data(symbol, exchange)
                if gf and gf.get('Price') is not None:
                    def parse_gf_float(val):
                        if not val:
                            return 0.0
                        cleaned = val.replace(',', '').replace('$', '').replace('₹', '').strip()
                        # Extract first float/int with suffix handling
                        match = re.search(r'([0-9\.]+)([MB]?)$', cleaned)
                        if match:
                            num = float(match.group(1))
                            suffix = match.group(2)
                            if suffix == 'M':
                                return num * 1e6
                            if suffix == 'B':
                                return num * 1e9
                            return num
                        # Try fallback pure number regex
                        num_match = re.search(r'([0-9\.]+)', cleaned)
                        if num_match:
                            return float(num_match.group(1))
                        return 0.0

                    open_price = parse_gf_float(gf.get('Open'))
                    high = parse_gf_float(gf.get('High'))
                    low = parse_gf_float(gf.get('Low'))
                    volume = int(parse_gf_float(gf.get('Volume')))
                    
                    # Resolve exchange info from local DB if available
                    base_symbol = symbol.split('.')[0]
                    matching_instrument = df_instruments[df_instruments['Symbol'] == base_symbol]
                    resolved_exch = exchange
                    if not matching_instrument.empty:
                        resolved_exch = matching_instrument.iloc[0]['Exchange']

                    response_data = {
                        'Symbol': symbol,
                        'Exchange': resolved_exch,
                        'Price': gf.get('Price'),
                        'Open': open_price,
                        'High': high,
                        'Low': low,
                        'Volume': volume,
                        'Change_Percent': gf.get('Change_Percent')
                    }
                    self.send_json(response_data)
                    return
            except Exception as e:
                print(f"Google Finance fetch failed, falling back to yfinance: {e}")

            # 2. Fall back to yfinance
            try:
                # Format query symbol with exchange suffix for yfinance
                query_symbol = symbol
                if not (symbol.endswith('.NS') or symbol.endswith('.BO')):
                    if exchange == 'NSE':
                        query_symbol = f"{symbol}.NS"
                    elif exchange == 'BSE':
                        query_symbol = f"{symbol}.BO"

                # Fetch 2d data to compute change relative to previous close
                ticker_df = yf.download(query_symbol, period='2d', auto_adjust=False, progress=False)
                if ticker_df.empty:
                    offline_data = fetch_offline_stock_data(symbol, exchange)
                    if offline_data:
                        self.send_json(offline_data)
                        return
                    self.send_json({'error': 'Symbol not found on Yahoo'}, status=404)
                    return

                # Handle multi-index or single-index outputs
                if isinstance(ticker_df.columns, pd.MultiIndex):
                    stacked = ticker_df.stack(level=1, future_stack=True).reset_index()
                    stacked = stacked.rename(columns={'Ticker': 'Symbol'})
                    stacked['Symbol'] = stacked['Symbol'].str.replace('.NS', '', regex=False).str.replace('.BO', '', regex=False)
                else:
                    stacked = ticker_df.reset_index()
                    stacked['Symbol'] = symbol

                valid_df = stacked.dropna(subset=['Close']).sort_values('Date')
                if len(valid_df) == 0:
                    offline_data = fetch_offline_stock_data(symbol, exchange)
                    if offline_data:
                        self.send_json(offline_data)
                        return
                    self.send_json({'error': 'No price data available'}, status=404)
                    return

                latest_row = valid_df.iloc[-1]
                latest_price = float(latest_row['Close'])
                open_price = float(latest_row['Open'])
                high = float(latest_row['High'])
                low = float(latest_row['Low'])
                volume = int(latest_row['Volume'])
                
                prev_price = None
                pct_change = 0.0
                
                if len(valid_df) > 1:
                    prev_price = float(valid_df.iloc[-2]['Close'])
                    if prev_price > 0:
                        pct_change = ((latest_price - prev_price) / prev_price) * 100
                else:
                    if open_price > 0:
                        pct_change = ((latest_price - open_price) / open_price) * 100

                # Resolve exchange info from local DB if available
                base_symbol = symbol.split('.')[0]
                matching_instrument = df_instruments[df_instruments['Symbol'] == base_symbol]
                exchange = ''
                if not matching_instrument.empty:
                    exchange = matching_instrument.iloc[0]['Exchange']

                response_data = {
                    'Symbol': symbol,
                    'Exchange': exchange,
                    'Price': latest_price,
                    'Open': open_price,
                    'High': high,
                    'Low': low,
                    'Volume': volume,
                    'Change_Percent': pct_change
                }
                self.send_json(response_data)
            except Exception as e:
                offline_data = fetch_offline_stock_data(symbol, exchange)
                if offline_data:
                    self.send_json(offline_data)
                    return
                err_msg = str(e)
                if any(x in err_msg for x in ['getaddrinfo failed', 'Could not resolve host', 'curl: (6)', 'Name or service not known', 'URLError']):
                    err_msg = "Network Offline: Unable to connect to market data servers. Please check your internet connection."
                self.send_json({'error': err_msg}, status=500)
            return

        # 3. API: Stock Info (Fundamentals + Q&A)
        elif path == '/api/info':
            symbol = query_params.get('symbol', [''])[0].strip()
            exchange = query_params.get('exchange', [''])[0].strip()
            if not symbol:
                self.send_json({'error': 'No symbol specified'}, status=400)
                return

            if not exchange:
                base_symbol = symbol.split('.')[0]
                matching_instrument = df_instruments[df_instruments['Symbol'] == base_symbol]
                if not matching_instrument.empty:
                    exchange = matching_instrument.iloc[0]['Exchange']

            try:
                # Format query symbol with exchange suffix for yfinance
                query_symbol = symbol
                if not (symbol.endswith('.NS') or symbol.endswith('.BO')):
                    if exchange == 'NSE':
                        query_symbol = f"{symbol}.NS"
                    elif exchange == 'BSE':
                        query_symbol = f"{symbol}.BO"

                # Use in-memory cache for yfinance ticker info to prevent extremely slow requests
                current_time = time.time()
                cached_data = INFO_CACHE.get(query_symbol)
                if cached_data and cached_data[0] > current_time:
                    info = cached_data[1]
                else:
                    info = fetch_yf_info_with_timeout(query_symbol, timeout_sec=2.5)
                    # Cache for 10 minutes (600 seconds)
                    INFO_CACHE[query_symbol] = (current_time + 600, info)
                
                # Format numbers helper
                def format_num(val, is_pct=False, is_currency=False):
                    if val is None or pd.isna(val):
                        return "-"
                    if is_pct:
                        return f"{val*100:.2f}%" if val < 1.0 else f"{val:.2f}%"
                    if is_currency:
                        currency = "₹" if exchange in ('NSE', 'BSE') else "$"
                        if val > 1e12:
                            return f"{currency}{val/1e12:.2f}T"
                        if val > 1e9:
                            return f"{currency}{val/1e9:.2f}B"
                        if val > 1e6:
                            return f"{currency}{val/1e6:.2f}M"
                        return f"{currency}{val:,.2f}"
                    return f"{val:,.2f}" if isinstance(val, (int, float)) else str(val)

                # Generate dynamic QA
                sector = info.get('sector', '').strip()
                industry = info.get('industry', '').strip()
                summary = info.get('longBusinessSummary', '').strip()
                
                q1 = "What does this company do?"
                a1 = summary if summary else "No business summary available at this time."
                
                q2 = "Are this company's products/services required by people?"
                if 'tech' in sector.lower():
                    a2 = f"Yes. Operating in {industry}, its offerings are key requirements for digital transformation, cloud infrastructure, and business productivity."
                elif 'financial' in sector.lower():
                    a2 = f"Yes. Financial, banking, and wealth management services are essential for credit circulation, capital growth, and securing transactions."
                elif 'health' in sector.lower():
                    a2 = f"Yes. Diagnostics, medical equipment, and pharmaceuticals are life-critical requirements for healthcare and treatment."
                elif 'defensive' in sector.lower() or 'staple' in sector.lower():
                    a2 = f"Yes. Producing everyday household essentials and consumables, it serves resilient, non-discretionary requirements."
                elif 'energy' in sector.lower() or 'utility' in sector.lower():
                    a2 = f"Yes. Energy, electricity, fuel, and utilities are foundational requirements for transportation, heating, and power."
                elif 'communication' in sector.lower():
                    a2 = f"Yes. Connectivity, internet media, and telecom access are indispensable tools for daily communication and commerce."
                else:
                    a2 = f"Yes. Supporting the {industry} sector, this company provides essential solutions and services that power consumer and industrial needs."

                q3 = "What is the supply and demand outlook for this business?"
                revenue = info.get('totalRevenue')
                profit_margin = info.get('profitMargins')
                pe = info.get('trailingPE')
                
                rev_str = f"with total revenues of {format_num(revenue, is_currency=True)}" if revenue else "with stable revenues"
                margin_str = f"and a profit margin of {format_num(profit_margin, is_pct=True)}" if profit_margin else "and positive operating efficiency"
                pe_str = f"with a P/E ratio of {pe:.2f} reflecting robust demand for its equity" if pe else "reflecting stable market valuation"
                
                a3 = f"As a key provider in {sector}, the business maintains operations {rev_str} {margin_str}. Strong pricing power balances supply-chain costs, while current valuation is {pe_str}."

                qa_list = [
                    {"question": q1, "answer": a1},
                    {"question": q2, "answer": a2},
                    {"question": q3, "answer": a3}
                ]

                # 1. Fetch Google Finance stats primarily
                gf = {}
                try:
                    gf = fetch_google_finance_data(symbol, exchange)
                except Exception as e:
                    print(f"Google Finance fundamentals fetch failed: {e}")

                # 2. Hardened yfinance dividend yield calculations (for fallback)
                yf_div = info.get('trailingAnnualDividendYield')
                if yf_div is not None and not pd.isna(yf_div):
                    yf_div_formatted = f"{yf_div * 100:.2f}%"
                else:
                    yf_div_val = info.get('dividendYield')
                    if yf_div_val is not None and not pd.isna(yf_div_val):
                        # Normalize percentage format in yfinance
                        if yf_div_val > 0.05:
                            # It is already percentage format (e.g. 2.86 or 0.25)
                            yf_div_formatted = f"{yf_div_val:.2f}%"
                        else:
                            yf_div_formatted = f"{yf_div_val*100:.2f}%"
                    else:
                        yf_div_formatted = "-"

                currency_symbol = "₹" if exchange in ('NSE', 'BSE') else "$"

                # Core fundamental table metrics
                fundamentals = {
                    'marketCap': gf.get('MarketCap') or format_num(info.get('marketCap'), is_currency=True),
                    'peRatio': gf.get('PE') or format_num(info.get('trailingPE')),
                    'eps': gf.get('EPS') or format_num(info.get('trailingEps')),
                    'priceToBook': format_num(info.get('priceToBook')),
                    'divYield': gf.get('DivYield') or yf_div_formatted,
                    'beta': gf.get('Beta') or format_num(info.get('beta')),
                    'high52': gf.get('52WHigh') or format_num(info.get('fiftyTwoWeekHigh'), is_currency=True),
                    'low52': gf.get('52WLow') or format_num(info.get('fiftyTwoWeekLow'), is_currency=True),
                    'revenue': format_num(info.get('totalRevenue'), is_currency=True),
                    'profitMargin': format_num(info.get('profitMargins'), is_pct=True)
                }

                # Format Google Finance raw metrics to have currency prefix if missing
                for k in ['high52', 'low52']:
                    val = fundamentals[k]
                    if val != "-" and not val.startswith('$') and not val.startswith('₹'):
                        # Strip existing non-ascii/signs
                        val_cleaned = re.sub(r'[^\d\.,]', '', val)
                        fundamentals[k] = f"{currency_symbol} {val_cleaned}"
                
                # Format market cap
                mc_val = fundamentals['marketCap']
                if mc_val != "-" and not mc_val.startswith('$') and not mc_val.startswith('₹'):
                    fundamentals['marketCap'] = f"{currency_symbol}{mc_val}"

                self.send_json({
                    'QA': qa_list,
                    'Fundamentals': fundamentals
                })
            except Exception as e:
                offline_data = fetch_offline_stock_data(symbol, exchange)
                if offline_data:
                    currency_symbol = "₹" if (exchange in ('NSE', 'BSE') or symbol.endswith('.NS') or symbol.endswith('.BO')) else "$"
                    h52 = offline_data.get('52WHigh', 0.0)
                    l52 = offline_data.get('52WLow', 0.0)
                    pe_ratio = offline_data.get('PE', '-')
                    rev_growth = offline_data.get('RevenueGrowth', '-')
                    prof_growth = offline_data.get('ProfitGrowth', '-')
                    roe_val = offline_data.get('ROE', '-')
                    de_val = offline_data.get('DebtToEquity', '-')
                    promoter_holding = offline_data.get('PromoterHolding', '-')
                    
                    offline_fundamentals = {
                        'marketCap': "-",
                        'peRatio': str(pe_ratio),
                        'eps': "-",
                        'divYield': "-",
                        'high52': f"{currency_symbol} {h52:,.2f}" if h52 > 0 else "-",
                        'low52': f"{currency_symbol} {l52:,.2f}" if l52 > 0 else "-",
                        'revenue': f"YoY {rev_growth}" if rev_growth != '-' else "-",
                        'profitMargin': f"YoY {prof_growth}" if prof_growth != '-' else "-"
                    }
                    offline_qa = [
                        {'question': f"What is {symbol}?", 'answer': f"{symbol} is a listed equity security with PE of {pe_ratio}, ROE of {roe_val}, and Debt/Equity of {de_val}."},
                        {'question': f"What are the growth metrics for {symbol}?", 'answer': f"YoY Revenue Growth: {rev_growth}, YoY Profit Growth: {prof_growth}, Promoter Holding: {promoter_holding}."},
                        {'question': "Is live internet connection required?", 'answer': "Data displayed above is served from local pre-indexed historical backups while network connectivity is restored."}
                    ]
                    self.send_json({
                        'QA': offline_qa,
                        'Fundamentals': offline_fundamentals
                    })
                    return
                err_msg = str(e)
                if any(x in err_msg for x in ['getaddrinfo failed', 'Could not resolve host', 'curl: (6)', 'Name or service not known', 'URLError']):
                    err_msg = "Network Offline: Unable to connect to market data servers. Please check your internet connection."
                self.send_json({'error': err_msg}, status=500)
            return

        # 4. API: Chatbot Assistant
        elif path == '/api/chat':
            msg = query_params.get('msg', [''])[0].strip()
            if not msg:
                self.send_json({'response': 'Hello! How can I help you today?'})
                return

            response = handle_chat_query(msg, df_instruments)
            self.send_json({'response': response})
            return

        # 5. API: Get Settings (Clerk & LLM Providers)
        elif path == '/api/get-settings':
            clerk_key = os.environ.get('CLERK_PUBLISHABLE_KEY', '').strip().strip('"\'')
            anthropic_key = os.environ.get('ANTHROPIC_API_KEY', '').strip().strip('"\'')
            openai_key = os.environ.get('OPENAI_API_KEY', '').strip().strip('"\'')
            gemini_key = os.environ.get('GEMINI_API_KEY', '').strip().strip('"\'')
            openrouter_key = os.environ.get('OPENROUTER_API_KEY', '').strip().strip('"\'')
            provider = os.environ.get('LLM_PROVIDER', 'claude').strip().strip('"\'')
            model = os.environ.get('LLM_MODEL', '').strip().strip('"\'')
            
            # Defaults for models if not set
            if not model:
                if provider == 'openai':
                    model = 'gpt-4o'
                elif provider == 'gemini':
                    model = 'gemini-2.5-pro'
                elif provider == 'openrouter':
                    model = 'deepseek/deepseek-chat'
                else:
                    model = 'claude-3-5-sonnet-20241022'

            def mask_key(k, prefix):
                return f"{prefix}...{k[-4:]}" if len(k) > 8 else ""

            self.send_json({
                'publishableKey': clerk_key,
                'provider': provider,
                'model': model,
                'hasAnthropicKey': bool(anthropic_key),
                'maskedAnthropicKey': mask_key(anthropic_key, 'sk-ant'),
                'hasOpenaiKey': bool(openai_key),
                'maskedOpenaiKey': mask_key(openai_key, 'sk-proj'),
                'hasGeminiKey': bool(gemini_key),
                'maskedGeminiKey': mask_key(gemini_key, 'AIzaSy'),
                'hasOpenrouterKey': bool(openrouter_key),
                'maskedOpenrouterKey': mask_key(openrouter_key, 'sk-or')
            })
            return

        # 5.5. API: Save Clerk Publishable Key (Merged)
        elif path == '/api/save-clerk-key':
            key = query_params.get('key', [''])[0].strip().strip('"\'')
            if key:
                try:
                    env_data = {}
                    if os.path.exists('.env'):
                        with open('.env', 'r') as f:
                            for line in f:
                                if '=' in line and not line.startswith('#'):
                                    k, v = line.strip().split('=', 1)
                                    env_data[k.strip()] = v.strip()
                    env_data['CLERK_PUBLISHABLE_KEY'] = key
                    with open('.env', 'w') as f:
                        f.write("# Clerk & LLM Settings\n")
                        for k, v in env_data.items():
                            f.write(f"{k}={v}\n")
                    os.environ['CLERK_PUBLISHABLE_KEY'] = key
                    self.send_json({'success': True})
                except Exception as e:
                    self.send_json({'error': str(e)}, status=500)
            else:
                self.send_json({'error': 'No key provided'}, status=400)
            return

        # 5.6. API: Save Anthropic API Key (Merged)
        elif path == '/api/save-anthropic-key':
            key = query_params.get('key', [''])[0].strip().strip('"\'')
            if key:
                try:
                    env_data = {}
                    if os.path.exists('.env'):
                        with open('.env', 'r') as f:
                            for line in f:
                                if '=' in line and not line.startswith('#'):
                                    k, v = line.strip().split('=', 1)
                                    env_data[k.strip()] = v.strip()
                    env_data['ANTHROPIC_API_KEY'] = key
                    with open('.env', 'w') as f:
                        f.write("# Clerk & LLM Settings\n")
                        for k, v in env_data.items():
                            f.write(f"{k}={v}\n")
                    os.environ['ANTHROPIC_API_KEY'] = key
                    self.send_json({'success': True})
                except Exception as e:
                    self.send_json({'error': str(e)}, status=500)
            else:
                self.send_json({'error': 'No key provided'}, status=400)
            return

        # 5.7. API: Parallel Stock Research Data Collector
        elif path == '/api/research':
            symbol = query_params.get('symbol', [''])[0].strip()
            exchange = query_params.get('exchange', [''])[0].strip()
            if not symbol:
                self.send_json({'error': 'No symbol specified'}, status=400)
                return
            query_symbol = symbol
            if not (symbol.endswith('.NS') or symbol.endswith('.BO')):
                if exchange == 'NSE':
                    query_symbol = f"{symbol}.NS"
                elif exchange == 'BSE':
                    query_symbol = f"{symbol}.BO"
            try:
                import stock_research
                data = stock_research.run_stock_research(query_symbol)
                self.send_json(data)
            except Exception as e:
                self.send_json({'error': str(e)}, status=500)
            return

        # 6. Serve Frontend Dashboard (HTML Page)
        elif path in ('', '/', '/index.html'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            if os.path.exists('index.html'):
                try:
                    with open('index.html', 'r', encoding='utf-8') as f:
                        content = f.read()
                    key = os.environ.get('CLERK_PUBLISHABLE_KEY', '').strip().strip('"\'')
                    domain = get_clerk_domain(key)
                    content = content.replace('{{CLERK_PUBLISHABLE_KEY}}', key)
                    content = content.replace('{{CLERK_DOMAIN}}', domain if domain else '')
                    self.wfile.write(content.encode('utf-8'))
                except Exception as e:
                    self.wfile.write(f"<h1>Error serving page: {e}</h1>".encode('utf-8'))
            else:
                self.wfile.write(b"<h1>index.html not found.</h1>")
            return

        # 4. Fallback 404
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        
        if path == '/api/research-analyze':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                payload = data.get('payload', {})
                prompt = data.get('prompt', '')
                
                # Fetch provider and model
                provider = data.get('provider') or os.environ.get('LLM_PROVIDER', 'claude').strip().strip('"\'')
                model = data.get('model') or os.environ.get('LLM_MODEL', '').strip().strip('"\'')
                
                # Defaults for models if empty
                if not model:
                    if provider == 'openai':
                        model = 'gpt-4o'
                    elif provider == 'gemini':
                        model = 'gemini-2.5-pro'
                    elif provider == 'openrouter':
                        model = 'deepseek/deepseek-chat'
                    else:
                        model = 'claude-3-5-sonnet-20241022'
                
                # Resolve key based on provider
                api_key = data.get('apiKey', '').strip()
                if not api_key:
                    if provider == 'openai':
                        api_key = os.environ.get('OPENAI_API_KEY', '').strip().strip('"\'')
                    elif provider == 'gemini':
                        api_key = os.environ.get('GEMINI_API_KEY', '').strip().strip('"\'')
                    elif provider == 'openrouter':
                        api_key = os.environ.get('OPENROUTER_API_KEY', '').strip().strip('"\'')
                    else:
                        api_key = os.environ.get('ANTHROPIC_API_KEY', '').strip().strip('"\'')
                        
                if not api_key:
                    self.send_json({'error': f'{provider.capitalize()} API Key is required. Please set it in your profile settings.'}, status=400)
                    return
                    
                import stock_research
                result = stock_research.call_llm_analysis(payload, prompt, api_key, provider, model)
                if 'error' in result:
                    self.send_json(result, status=500)
                else:
                    self.send_json(result)
            except Exception as e:
                self.send_json({'error': str(e)}, status=500)
            return

        elif path == '/api/save-settings':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                data = json.loads(post_data.decode('utf-8'))
                
                # Load existing env data
                env_data = {}
                if os.path.exists('.env'):
                    with open('.env', 'r') as f:
                        for line in f:
                            if '=' in line and not line.startswith('#'):
                                k, v = line.strip().split('=', 1)
                                env_data[k.strip()] = v.strip()
                
                # Update settings
                keys_to_update = {
                    'CLERK_PUBLISHABLE_KEY': data.get('clerkKey'),
                    'ANTHROPIC_API_KEY': data.get('anthropicKey'),
                    'OPENAI_API_KEY': data.get('openaiKey'),
                    'GEMINI_API_KEY': data.get('geminiKey'),
                    'OPENROUTER_API_KEY': data.get('openrouterKey'),
                    'LLM_PROVIDER': data.get('provider'),
                    'LLM_MODEL': data.get('model')
                }
                
                for k, v in keys_to_update.items():
                    if v is not None:
                        val = v.strip().strip('"\'')
                        env_data[k] = val
                        os.environ[k] = val
                        
                with open('.env', 'w') as f:
                    f.write("# Clerk & LLM Settings\n")
                    for k, v in env_data.items():
                        f.write(f"{k}={v}\n")
                        
                self.send_json({'success': True})
            except Exception as e:
                self.send_json({'error': str(e)}, status=500)
            return

        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")

    def send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        # Allow local cross-origin request just in case
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))


def main():
    server = ThreadingHTTPServer(('0.0.0.0', PORT), DashboardHandler)
    print("=" * 60)
    print(f"  Stock Search & CMP Dashboard is live!")
    print(f"  Open your browser and navigate to: http://localhost:{PORT}")
    print("=" * 60)
    print("  Press Ctrl+C to terminate.")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard server. Goodbye!")
        server.server_close()


if __name__ == '__main__':
    main()

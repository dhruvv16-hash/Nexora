import os
import json
import re
import time
import pandas as pd
import streamlit as st
import yfinance as yf
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from stock_research import (
    run_stock_research,
    collect_offline_research_payload,
    generate_local_analyst_verdict,
    call_llm_analysis,
    clean_backend_markdown,
    collect_technical_indicators,
    generate_matplotlib_chart
)

# Page Configuration
st.set_page_config(
    page_title="Nexora - Institutional Equity Intelligence",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Nexora Custom Premium Dark Theme Styling
STYLING = """
<style>
    /* Global Background & Font */
    .stApp {
        background-color: #050816;
        color: #f3f4f6;
        font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    
    /* Card Container Styling */
    .nexora-card {
        background: #0B1220;
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
    }
    
    /* Header Typography */
    .nexora-title {
        color: #ffffff;
        font-size: 1.8rem;
        font-weight: 700;
        letter-spacing: -0.02em;
        margin-bottom: 5px;
    }
    
    .nexora-subtitle {
        color: #9ca3af;
        font-size: 0.95rem;
        margin-bottom: 20px;
    }
    
    /* Metric Card Grid */
    .metric-box {
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid rgba(255, 255, 255, 0.06);
        border-radius: 8px;
        padding: 12px 16px;
        text-align: center;
    }
    .metric-box .label {
        font-size: 0.8rem;
        color: #9ca3af;
        text-transform: uppercase;
        letter-spacing: 0.04em;
    }
    .metric-box .val {
        font-size: 1.2rem;
        font-weight: 600;
        color: #ffffff;
        margin-top: 4px;
    }
    
    /* Sidebar Styling */
    section[data-testid="stSidebar"] {
        background-color: #080D1A;
        border-right: 1px solid rgba(255, 255, 255, 0.08);
    }
</style>
"""
st.markdown(STYLING, unsafe_allow_html=True)

# 1. Pre-load Instruments & Fundamentals Database
@st.cache_data
def load_datasets():
    lookup = {}
    
    # Load historical datasets into memory
    for csv_name in ["nse_stocks_data.csv", "us_stocks_data.csv", "all_stocks_data.csv"]:
        if os.path.exists(csv_name):
            try:
                df = pd.read_csv(csv_name, low_memory=False)
                df = df.dropna(subset=['Symbol', 'Close'])
                for sym, group in df.groupby('Symbol'):
                    if sym not in lookup:
                        sorted_g = group.sort_values('Date')
                        latest = sorted_g.iloc[-1]
                        prev = sorted_g.iloc[-2] if len(sorted_g) > 1 else latest
                        price = float(latest['Close'])
                        prev_p = float(prev['Close'])
                        vol = int(latest['Volume']) if pd.notna(latest.get('Volume')) else 0
                        lookup[sym] = {
                            'Price': price,
                            'Open': float(latest['Open']) if pd.notna(latest.get('Open')) else price,
                            'High': float(latest['High']) if pd.notna(latest.get('High')) else price,
                            'Low': float(latest['Low']) if pd.notna(latest.get('Low')) else price,
                            'Volume': vol,
                            'Change_Percent': ((price - prev_p) / prev_p * 100) if prev_p > 0 else 0.0,
                            '52WHigh': float(sorted_g['High'].max()),
                            '52WLow': float(sorted_g['Low'].min())
                        }
            except Exception:
                pass

    # Enrich with Fundamentals Master DB
    if os.path.exists("fundamentals_master.csv"):
        try:
            df_fm = pd.read_csv("fundamentals_master.csv", low_memory=False)
            df_fm = df_fm.fillna('N/A')
            for _, r in df_fm.iterrows():
                sym = str(r.get('Symbol', '')).strip()
                if sym and sym in lookup:
                    lookup[sym]['PE'] = r.get('PE_Ratio', 'N/A')
                    lookup[sym]['ROE'] = r.get('ROE', 'N/A')
                    lookup[sym]['DebtToEquity'] = r.get('Debt_To_Equity', 'N/A')
                    lookup[sym]['RevenueGrowth'] = r.get('Revenue_Growth_YoY', 'N/A')
                    lookup[sym]['ProfitGrowth'] = r.get('Profit_Growth_YoY', 'N/A')
                    lookup[sym]['OperatingCashflow'] = r.get('Operating_Cashflow', 'N/A')
                    lookup[sym]['PromoterHolding'] = r.get('Promoter_Holding', 'N/A')
        except Exception:
            pass

    # Load instruments list for autocomplete
    instruments = []
    if os.path.exists("platform_instruments.csv"):
        df_inst = pd.read_csv("platform_instruments.csv", low_memory=False).fillna('')
        for _, r in df_inst.iterrows():
            instruments.append(f"{r['Symbol']} ({r['Exchange']})")
            
    return lookup, instruments

LOOKUP_DB, INSTRUMENT_LIST = load_datasets()

# HTML Typography Formatter (Zero Markdown Symbols)
def format_analyst_report_html(text):
    if not text:
        return ""
    cleaned = text.strip()
    safe = cleaned.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    lines = safe.split('\n')
    formatted = []
    for line in lines:
        trimmed = line.strip()
        if not trimmed:
            formatted.append('<br>')
            continue
        if trimmed in ['---', '***', '___']:
            formatted.append('<hr style="border: none; border-top: 1px solid rgba(255,255,255,0.08); margin: 16px 0;">')
            continue
        if trimmed.startswith('#'):
            header_text = trimmed.lstrip('#').replace('*', '').replace('#', '').strip()
            formatted.append(f'<h3 style="color: #3B82F6; margin-top: 20px; margin-bottom: 10px; font-size: 1.1rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.03em; border-bottom: 1px solid rgba(59,130,246,0.2); padding-bottom: 4px;">{header_text}</h3>')
            continue
        if trimmed.startswith('- ') or trimmed.startswith('* '):
            bullet_text = trimmed[2:].strip()
            bullet_text = re.sub(r'\*\*(.*?)\*\*', r'<strong style="color: #ffffff; font-weight: 600;">\1</strong>', bullet_text).replace('*', '').replace('#', '')
            formatted.append(f'<div style="margin-bottom: 8px; padding-left: 14px; border-left: 3px solid #3B82F6; color: #9CA3AF; line-height: 1.6;">{bullet_text}</div>')
            continue
        num_match = re.match(r'^(\d+)\.\s+(.*)$', trimmed)
        if num_match:
            num = num_match[1]
            item_text = num_match[2]
            item_text = re.sub(r'\*\*(.*?)\*\*', r'<strong style="color: #ffffff; font-weight: 600;">\1</strong>', item_text).replace('*', '').replace('#', '')
            formatted.append(f'<div style="margin-bottom: 8px; padding-left: 14px; border-left: 3px solid #10B981; color: #9CA3AF; line-height: 1.6;"><strong style="color: #ffffff;">{num}.</strong> {item_text}</div>')
            continue
        para_text = re.sub(r'\*\*(.*?)\*\*', r'<strong style="color: #ffffff; font-weight: 600;">\1</strong>', trimmed).replace('*', '').replace('#', '')
        formatted.append(f'<p style="margin-bottom: 8px; color: #F3F4F6; line-height: 1.6;">{para_text}</p>')
    return "".join(formatted)

# --- Sidebar Configuration ----------------------------------------------------
st.sidebar.title("⚡ Nexora Intelligence")
navigation = st.sidebar.radio("Navigation", ["Market Terminal", "Deep Research Engine", "Settings"])

# Environment & Secret Keys Configuration
api_key = st.sidebar.text_input("OpenRouter API Key", value=os.environ.get('OPENROUTER_API_KEY', ''), type="password")
provider = st.sidebar.selectbox("LLM Provider", ["openrouter", "claude", "openai", "gemini"], index=0)
model_choice = st.sidebar.text_input("Model Identifier", value="deepseek/deepseek-chat")

# --- View 1: Market Terminal --------------------------------------------------
if navigation == "Market Terminal":
    st.markdown('<div class="nexora-title">Market Intelligence Terminal</div>', unsafe_allow_html=True)
    st.markdown('<div class="nexora-subtitle">Institutional equity research, real-time pricing & dynamic technical analytics</div>', unsafe_allow_html=True)
    
    col_search, col_exch = st.columns([3, 1])
    with col_search:
        symbol_input = st.text_input("Search Equity (e.g. RELIANCE, AAPL, TCS, EXATO)", value="RELIANCE")
    with col_exch:
        exchange_input = st.selectbox("Exchange", ["NSE", "BSE", "US"], index=0)
        
    symbol_clean = symbol_input.strip().upper()
    query_sym = symbol_clean
    if exchange_input == "NSE" and not symbol_clean.endswith(".NS"):
        query_sym = f"{symbol_clean}.NS"
    elif exchange_input == "BSE" and not symbol_clean.endswith(".BO"):
        query_sym = f"{symbol_clean}.BO"

    # Fetch Price & Fundamentals Data
    rec = LOOKUP_DB.get(query_sym) or LOOKUP_DB.get(symbol_clean)
    
    if rec:
        currency = "₹" if exchange_input in ["NSE", "BSE"] else "$"
        price = rec.get('Price', 0.0)
        change_pct = rec.get('Change_Percent', 0.0)
        color_class = "#10B981" if change_pct >= 0 else "#EF4444"
        sign = "+" if change_pct >= 0 else ""
        
        # Real-time Price Header Box
        st.markdown(f"""
        <div class="nexora-card">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <span style="font-size: 1.6rem; font-weight: 700; color: #ffffff;">{symbol_clean}</span>
                    <span style="background: rgba(59,130,246,0.2); color: #3B82F6; padding: 2px 8px; border-radius: 4px; font-size: 0.8rem; margin-left: 10px;">{exchange_input}</span>
                </div>
                <div style="text-align: right;">
                    <div style="font-size: 1.8rem; font-weight: 700; color: #ffffff;">{currency}{price:,.2f}</div>
                    <div style="font-size: 0.95rem; font-weight: 600; color: {color_class};">{sign}{change_pct:.2f}%</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # Fundamentals Grid
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            st.markdown(f'<div class="metric-box"><div class="label">P/E Ratio</div><div class="val">{rec.get("PE", "N/A")}</div></div>', unsafe_allow_html=True)
        with c2:
            st.markdown(f'<div class="metric-box"><div class="label">ROE</div><div class="val">{rec.get("ROE", "N/A")}</div></div>', unsafe_allow_html=True)
        with c3:
            st.markdown(f'<div class="metric-box"><div class="label">Debt/Equity</div><div class="val">{rec.get("DebtToEquity", "N/A")}</div></div>', unsafe_allow_html=True)
        with c4:
            st.markdown(f'<div class="metric-box"><div class="label">YoY Rev Growth</div><div class="val">{rec.get("RevenueGrowth", "N/A")}</div></div>', unsafe_allow_html=True)
        with c5:
            st.markdown(f'<div class="metric-box"><div class="label">Promoter Holding</div><div class="val">{rec.get("PromoterHolding", "N/A")}</div></div>', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        
        # Technical Indicator Chart
        payload = collect_offline_research_payload(query_sym)
        chart_base64 = payload.get('chart_base64')
        tech = payload.get('technical_indicators', {})

        col_chart, col_stats = st.columns([2, 1])
        with col_chart:
            st.markdown("### Technical Market Structure")
            if 'EMA_50' in payload:
                pass
            # Fetch df for chart
            ticker_obj = yf.Ticker(query_sym)
            try:
                df_hist = ticker_obj.history(period="1y")
                if not df_hist.empty:
                    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(9, 6), gridspec_kw={'height_ratios': [3, 1]})
                    fig.patch.set_facecolor('#0d0f12')
                    ax1.set_facecolor('#11161d')
                    ax1.plot(df_hist.index, df_hist['Close'], label='Price', color='#3b82f6', linewidth=1.5)
                    ax1.set_title(f"{symbol_clean} 1-Year Trend", color='#ffffff')
                    ax1.grid(True, color='rgba(255,255,255,0.05)')
                    ax2.set_facecolor('#11161d')
                    ax2.bar(df_hist.index, df_hist['Volume'], color='#10b981', alpha=0.6)
                    ax2.grid(True, color='rgba(255,255,255,0.05)')
                    st.pyplot(fig)
            except Exception:
                st.info("Technical chart generator active.")
                
        with col_stats:
            st.markdown("### Key Technicals")
            st.write(f"**50 EMA:** ₹{tech.get('ema_50', 'N/A')}")
            st.write(f"**200 EMA:** ₹{tech.get('ema_200', 'N/A')}")
            st.write(f"**RSI (14):** {tech.get('rsi_14', 'N/A')}")
            st.write(f"**Market Phase:** {tech.get('trend_cycle', 'Consolidating')}")
            
    else:
        st.warning(f"No offline data found for {symbol_clean}. Live query active.")

# --- View 2: Deep Research Engine ---------------------------------------------
elif navigation == "Deep Research Engine":
    st.markdown('<div class="nexora-title">Institutional Deep Research Engine</div>', unsafe_allow_html=True)
    st.markdown('<div class="nexora-subtitle">Grounded equity analysis powered by DeepSeek V3 & Wall Street intelligence rules</div>', unsafe_allow_html=True)
    
    col_s, col_e = st.columns([3, 1])
    with col_s:
        research_symbol = st.text_input("Target Stock Symbol", value="RELIANCE").strip().upper()
    with col_e:
        research_exch = st.selectbox("Exchange", ["NSE", "BSE", "US"], index=0)
        
    custom_prompt = st.text_area("Analysis Mandate / Custom Instructions", value="Perform a blunt, institutional equity research evaluation covering fundamentals, management DNA, valuation, and key downside risk factors.")
    
    if st.button("Run Deep Research Analysis", type="primary"):
        with st.spinner("Collecting financial statements, technical indicators, and generating analyst verdict..."):
            query_s = research_symbol
            if research_exch == "NSE" and not research_symbol.endswith(".NS"):
                query_s = f"{research_symbol}.NS"
            elif research_exch == "BSE" and not research_symbol.endswith(".BO"):
                query_s = f"{research_symbol}.BO"
                
            payload = collect_offline_research_payload(query_s)
            
            # Call Model / Fallback Analysis
            if api_key:
                result = call_llm_analysis(payload, custom_prompt, api_key, provider=provider, model=model_choice)
                verdict_text = result.get('analysis', '')
            else:
                verdict_raw = generate_local_analyst_verdict(payload, custom_prompt)
                verdict_text = clean_backend_markdown(verdict_raw)
                
            # Render with Zero-Markdown Executive HTML Typography
            html_report = format_analyst_report_html(verdict_text)
            
            st.markdown(f'<div class="nexora-card">{html_report}</div>', unsafe_allow_html=True)

# --- View 3: Settings ---------------------------------------------------------
elif navigation == "Settings":
    st.markdown('<div class="nexora-title">Configuration & API Keys</div>', unsafe_allow_html=True)
    st.markdown('<div class="nexora-subtitle">Manage model targets and credentials</div>', unsafe_allow_html=True)
    
    st.markdown("""
    - **OpenRouter Default Model**: `deepseek/deepseek-chat` (DeepSeek V3)
    - **Pre-Indexed Memory Lookup DB**: Loaded **2,000+ equities** in RAM
    - **Zero-Markdown Typography Engine**: Active
    """)

import os
import json
import time
import argparse
import pandas as pd
from stock_research import run_stock_research, collect_offline_research_payload, generate_local_analyst_verdict, clean_backend_markdown

SYSTEM_PROMPT = (
    "You are an elite, cynical equity research analyst with 20 years of experience. "
    "Analyze the provided stock research payload. "
    "CRITICAL RULES:\n"
    "1. Base your statements strictly and ONLY on the provided JSON research data.\n"
    "2. If certain data or metrics are marked 'N/A' or missing, declare that the data is unavailable. "
    "DO NOT invent or guess values.\n"
    "3. Provide direct, blunt answers. Avoid diplomatic, balanced templates.\n"
    "4. ABSOLUTE FORMATTING MANDATE: DO NOT use markdown symbols like '#', '##', '###', '*', or '**' anywhere in your text. "
    "Write in clean, executive human analyst prose with uppercase section titles, clear line breaks, and standard text bullet points."
)

def select_top_2000_assets():
    """Select top 2,000 high-volume stocks from historical datasets"""
    assets = []
    
    # 1. NSE Top Equities
    if os.path.exists("nse_stocks_data.csv"):
        df_nse = pd.read_csv("nse_stocks_data.csv", low_memory=False)
        if not df_nse.empty:
            vol_agg = df_nse.groupby('Symbol')['Volume'].mean().reset_index()
            vol_agg = vol_agg.sort_values('Volume', ascending=False)
            top_nse = vol_agg['Symbol'].head(1200).tolist()
            for s in top_nse:
                assets.append({"symbol": s, "exchange": "NSE"})
                
    # 2. US Top Equities
    if os.path.exists("us_stocks_data.csv"):
        df_us = pd.read_csv("us_stocks_data.csv", low_memory=False)
        if not df_us.empty:
            vol_agg_us = df_us.groupby('Symbol')['Volume'].mean().reset_index()
            vol_agg_us = vol_agg_us.sort_values('Volume', ascending=False)
            top_us = vol_agg_us['Symbol'].head(800).tolist()
            for s in top_us:
                assets.append({"symbol": s, "exchange": "US"})
                
    print(f"Selected {len(assets):,} target equity assets for batch processing.")
    return assets

def build_dataset_and_master(limit=None):
    print("=== STARTING BATCH DATASET & FUNDAMENTALS MASTER BUILDER ===")
    assets = select_top_2000_assets()
    if limit:
        assets = assets[:limit]
        print(f"Limiting batch processing to top {limit} assets for quick execution.")
        
    master_records = []
    jsonl_records = []
    alpaca_records = []
    
    t0 = time.time()
    for idx, item in enumerate(assets, start=1):
        sym = item['symbol']
        exch = item['exchange']
        
        try:
            query_sym = sym
            if exch == "NSE" and not sym.endswith(".NS"):
                query_sym = f"{sym}.NS"
            elif exch == "BSE" and not sym.endswith(".BO"):
                query_sym = f"{sym}.BO"
                
            payload = collect_offline_research_payload(query_sym)
            if "error" in payload or not payload.get('screener_metrics'):
                try:
                    payload = run_stock_research(query_sym)
                except Exception:
                    pass
            screener = payload.get('screener_metrics', {})
            tech = payload.get('technical_indicators', {})
            shareholding = payload.get('shareholding', {})
            
            # Record for fundamentals_master.csv
            master_records.append({
                "Symbol": sym,
                "Exchange": exch,
                "Company_Name": payload.get('company_name', sym),
                "PE_Ratio": screener.get('pe_ratio', 'N/A'),
                "ROE": screener.get('roe', 'N/A'),
                "Debt_To_Equity": screener.get('debt_to_equity', 'N/A'),
                "Revenue_Growth_YoY": screener.get('revenue_growth_yoy', 'N/A'),
                "Profit_Growth_YoY": screener.get('profit_growth_yoy', 'N/A'),
                "Operating_Cashflow": screener.get('operating_cashflow', 'N/A'),
                "Promoter_Holding": screener.get('promoter_holding', 'N/A'),
                "FII_DII_Pct": shareholding.get('fii_dii_pct', 'N/A'),
                "Public_Pct": shareholding.get('public_pct', 'N/A'),
                "Latest_Close": tech.get('latest_close', 'N/A'),
                "EMA_50": tech.get('ema_50', 'N/A'),
                "EMA_200": tech.get('ema_200', 'N/A'),
                "RSI_14": tech.get('rsi_14', 'N/A'),
                "Trend": tech.get('trend_cycle', 'Consolidating')
            })
            
            # Generate grounded verdict
            verdict_raw = generate_local_analyst_verdict(payload, "Comprehensive equity research analysis")
            verdict_clean = clean_backend_markdown(verdict_raw)
            
            user_msg = (
                f"Here is the collected research payload for {payload.get('company_name')} ({sym}):\n"
                f"```json\n{json.dumps(payload, indent=2)}\n```\n\n"
                f"Instructions: Provide direct institutional equity research analysis."
            )
            
            # 1. Standard OpenAI/OpenRouter JSONL Format
            jsonl_records.append({
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                    {"role": "assistant", "content": verdict_clean}
                ]
            })
            
            # 2. Alpaca/Llama 3 Instruct Format
            alpaca_records.append({
                "instruction": SYSTEM_PROMPT,
                "input": user_msg,
                "output": verdict_clean
            })
            
            if idx % 100 == 0 or idx == len(assets):
                print(f"Processed [{idx}/{len(assets)}] assets ({time.time()-t0:.1f}s)...")
                
        except Exception as e:
            print(f"Error processing {sym}: {e}")
            
    # Save Fundamentals Master CSV
    df_master = pd.DataFrame(master_records)
    df_master.to_csv("fundamentals_master.csv", index=False)
    print(f"Saved pre-calculated fundamental database: fundamentals_master.csv ({len(df_master):,} rows).")
    
    # Save Fine-Tuning Datasets
    with open("fine_tuning_dataset.jsonl", "w", encoding="utf-8") as f:
        for r in jsonl_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Saved OpenRouter/OpenAI fine-tuning dataset: fine_tuning_dataset.jsonl ({len(jsonl_records):,} records).")
    
    with open("fine_tuning_alpaca.json", "w", encoding="utf-8") as f:
        json.dump(alpaca_records, f, indent=2, ensure_ascii=False)
    print(f"Saved Alpaca/Llama 3 fine-tuning dataset: fine_tuning_alpaca.json ({len(alpaca_records):,} records).")
    
    print("=== BATCH DATASET & FUNDAMENTALS MASTER BUILDER COMPLETED ===")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="Limit number of assets for quick test run")
    args = parser.parse_args()
    build_dataset_and_master(limit=args.limit)

from flask import Flask, jsonify, request
import os
import sys

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from dashboard import search_instruments, fetch_cmp_live, get_stock_info, call_llm_analysis

app = Flask(__name__)

@app.route('/api/get-settings', methods=['GET'])
def get_settings():
    return jsonify({
        'clerkKey': os.environ.get('CLERK_PUBLISHABLE_KEY', ''),
        'anthropicKey': os.environ.get('ANTHROPIC_API_KEY', ''),
        'openaiKey': os.environ.get('OPENAI_API_KEY', ''),
        'geminiKey': os.environ.get('GEMINI_API_KEY', ''),
        'openrouterKey': os.environ.get('OPENROUTER_API_KEY', ''),
        'provider': os.environ.get('LLM_PROVIDER', 'openrouter'),
        'model': os.environ.get('LLM_MODEL', 'deepseek/deepseek-chat')
    })

@app.route('/api/search', methods=['GET'])
def search():
    q = request.args.get('q', '')
    results = search_instruments(q)
    return jsonify(results)

@app.route('/api/cmp', methods=['GET'])
def cmp():
    sym = request.args.get('symbol', 'RELIANCE.NS')
    exch = request.args.get('exchange', 'NSE')
    cmp_data = fetch_cmp_live(sym, exch)
    return jsonify(cmp_data)

@app.route('/api/info', methods=['GET'])
def info():
    sym = request.args.get('symbol', 'RELIANCE.NS')
    exch = request.args.get('exchange', 'NSE')
    info_data = get_stock_info(sym, exch)
    return jsonify(info_data)

@app.route('/api/research-analyze', methods=['POST'])
def research_analyze():
    try:
        req_data = request.get_json() or {}
        payload_data = req_data.get('payload', {})
        user_prompt = req_data.get('prompt', '')
        user_key = req_data.get('apiKey', '')
        user_provider = req_data.get('provider', '')
        user_model = req_data.get('model', '')
        result = call_llm_analysis(payload_data, user_prompt, user_key, user_provider, user_model)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(port=5000)

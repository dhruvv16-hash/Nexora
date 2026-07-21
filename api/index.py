import sys
import os
import json
import urllib.parse

# Add root project folder to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard import (
    search_instruments,
    fetch_cmp_live,
    get_stock_info,
    call_llm_analysis
)

def handler(request):
    # Vercel Serverless Function Handler
    path = request.path if hasattr(request, 'path') else '/'
    method = request.method if hasattr(request, 'method') else 'GET'
    
    headers = {
        'Content-Type': 'application/json',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type'
    }
    
    if method == 'OPTIONS':
        return {
            'statusCode': 200,
            'headers': headers,
            'body': ''
        }

    if path == '/api/get-settings':
        body = json.dumps({
            'clerkKey': os.environ.get('CLERK_PUBLISHABLE_KEY', ''),
            'anthropicKey': os.environ.get('ANTHROPIC_API_KEY', ''),
            'openaiKey': os.environ.get('OPENAI_API_KEY', ''),
            'geminiKey': os.environ.get('GEMINI_API_KEY', ''),
            'openrouterKey': os.environ.get('OPENROUTER_API_KEY', ''),
            'provider': os.environ.get('LLM_PROVIDER', 'openrouter'),
            'model': os.environ.get('LLM_MODEL', 'deepseek/deepseek-chat')
        })
        return {'statusCode': 200, 'headers': headers, 'body': body}

    if path.startswith('/api/search'):
        q = request.args.get('q', '') if hasattr(request, 'args') else ''
        results = search_instruments(q)
        return {'statusCode': 200, 'headers': headers, 'body': json.dumps(results)}

    if path.startswith('/api/cmp'):
        sym = request.args.get('symbol', 'RELIANCE.NS') if hasattr(request, 'args') else 'RELIANCE.NS'
        exch = request.args.get('exchange', 'NSE') if hasattr(request, 'args') else 'NSE'
        cmp_data = fetch_cmp_live(sym, exch)
        return {'statusCode': 200, 'headers': headers, 'body': json.dumps(cmp_data)}

    if path.startswith('/api/info'):
        sym = request.args.get('symbol', 'RELIANCE.NS') if hasattr(request, 'args') else 'RELIANCE.NS'
        exch = request.args.get('exchange', 'NSE') if hasattr(request, 'args') else 'NSE'
        info_data = get_stock_info(sym, exch)
        return {'statusCode': 200, 'headers': headers, 'body': json.dumps(info_data)}

    if path == '/api/research-analyze' and method == 'POST':
        try:
            req_data = request.get_json() if hasattr(request, 'get_json') else {}
            payload_data = req_data.get('payload', {})
            user_prompt = req_data.get('prompt', '')
            user_key = req_data.get('apiKey', '')
            user_provider = req_data.get('provider', '')
            user_model = req_data.get('model', '')
            
            result = call_llm_analysis(payload_data, user_prompt, user_key, user_provider, user_model)
            return {'statusCode': 200, 'headers': headers, 'body': json.dumps(result)}
        except Exception as e:
            return {'statusCode': 500, 'headers': headers, 'body': json.dumps({'error': str(e)})}

    return {'statusCode': 404, 'headers': headers, 'body': json.dumps({'error': 'Not Found'})}

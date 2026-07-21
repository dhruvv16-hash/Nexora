import sys
import os
import json
import urllib.parse

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from dashboard import search_instruments, fetch_cmp_live, get_stock_info, call_llm_analysis

def app(environ, start_response):
    path = environ.get('PATH_INFO', '/')
    query = environ.get('QUERY_STRING', '')
    method = environ.get('REQUEST_METHOD', 'GET')
    
    headers = [
        ('Content-Type', 'application/json'),
        ('Access-Control-Allow-Origin', '*'),
        ('Access-Control-Allow-Methods', 'GET, POST, OPTIONS'),
        ('Access-Control-Allow-Headers', 'Content-Type')
    ]
    
    if method == 'OPTIONS':
        start_response('200 OK', headers)
        return [b'']
        
    if 'get-settings' in path:
        data = json.dumps({
            'clerkKey': os.environ.get('CLERK_PUBLISHABLE_KEY', ''),
            'anthropicKey': os.environ.get('ANTHROPIC_API_KEY', ''),
            'openaiKey': os.environ.get('OPENAI_API_KEY', ''),
            'geminiKey': os.environ.get('GEMINI_API_KEY', ''),
            'openrouterKey': os.environ.get('OPENROUTER_API_KEY', ''),
            'provider': os.environ.get('LLM_PROVIDER', 'openrouter'),
            'model': os.environ.get('LLM_MODEL', 'deepseek/deepseek-chat')
        }).encode('utf-8')
        start_response('200 OK', headers)
        return [data]

    if 'search' in path:
        parsed = urllib.parse.parse_qs(query)
        q = parsed.get('q', [''])[0]
        results = search_instruments(q)
        data = json.dumps(results).encode('utf-8')
        start_response('200 OK', headers)
        return [data]

    if 'cmp' in path:
        parsed = urllib.parse.parse_qs(query)
        sym = parsed.get('symbol', ['RELIANCE.NS'])[0]
        exch = parsed.get('exchange', ['NSE'])[0]
        cmp_data = fetch_cmp_live(sym, exch)
        data = json.dumps(cmp_data).encode('utf-8')
        start_response('200 OK', headers)
        return [data]

    if 'info' in path:
        parsed = urllib.parse.parse_qs(query)
        sym = parsed.get('symbol', ['RELIANCE.NS'])[0]
        exch = parsed.get('exchange', ['NSE'])[0]
        info_data = get_stock_info(sym, exch)
        data = json.dumps(info_data).encode('utf-8')
        start_response('200 OK', headers)
        return [data]

    if 'research-analyze' in path and method == 'POST':
        try:
            length = int(environ.get('CONTENT_LENGTH', 0))
            body = environ['wsgi.input'].read(length) if length > 0 else b'{}'
            req_data = json.loads(body.decode('utf-8'))
            
            payload_data = req_data.get('payload', {})
            user_prompt = req_data.get('prompt', '')
            user_key = req_data.get('apiKey', '')
            user_provider = req_data.get('provider', '')
            user_model = req_data.get('model', '')
            
            result = call_llm_analysis(payload_data, user_prompt, user_key, user_provider, user_model)
            data = json.dumps(result).encode('utf-8')
            start_response('200 OK', headers)
            return [data]
        except Exception as e:
            err_data = json.dumps({'error': str(e)}).encode('utf-8')
            start_response('500 Internal Server Error', headers)
            return [err_data]

    start_response('200 OK', headers)
    return [json.dumps({'status': 'online', 'path': path}).encode('utf-8')]

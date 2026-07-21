import sys
import os
import json
from http.server import BaseHTTPRequestHandler

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        data = json.dumps({
            'clerkKey': os.environ.get('CLERK_PUBLISHABLE_KEY', ''),
            'anthropicKey': os.environ.get('ANTHROPIC_API_KEY', ''),
            'openaiKey': os.environ.get('OPENAI_API_KEY', ''),
            'geminiKey': os.environ.get('GEMINI_API_KEY', ''),
            'openrouterKey': os.environ.get('OPENROUTER_API_KEY', ''),
            'provider': os.environ.get('LLM_PROVIDER', 'openrouter'),
            'model': os.environ.get('LLM_MODEL', 'deepseek/deepseek-chat')
        }).encode('utf-8')
        self.wfile.write(data)

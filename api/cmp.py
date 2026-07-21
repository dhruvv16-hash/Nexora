import sys
import os
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler

root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from dashboard import fetch_cmp_live

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        sym = qs.get('symbol', ['RELIANCE.NS'])[0]
        exch = qs.get('exchange', ['NSE'])[0]
        cmp_data = fetch_cmp_live(sym, exch)
        
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(cmp_data).encode('utf-8'))

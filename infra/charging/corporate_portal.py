"""Private corporate DN portal. Source-subnet policy is also enforced by nftables."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def do_GET(self):
        if ipaddress.ip_address(self.client_address[0]) not in ipaddress.ip_network('10.46.0.0/16'):
            self.send_error(403)
            return
        if self.path != '/intranet':
            self.send_error(404)
            return
        ip = str(ipaddress.ip_address(self.client_address[0]))
        if self.headers.get('Accept') == 'application/json':
            payload = json.dumps({'service': 'maestro-corporate', 'client_ip': ip}).encode()
            content_type = 'application/json'
        else:
            payload = ('''<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MAEstro Corp</title><style>body{margin:0;background:#0b1420;color:#e9eef5;
font:16px system-ui}main{max-width:560px;margin:12vh auto;padding:32px}
small{color:#64d7c2}h1{font-weight:500}p{color:#b4c1d0}</style>
<main><small>RED CORPORATIVA · 5G</small><h1>MAEstro Corp</h1>
<p>Intranet de laboratorio</p><p>Terminal autorizado por subred: ''' + ip + '''</p>
<p>DNN corporate. El S-NSSAI negociado se consulta en la sesión PDU del UE.</p>
</main></html>''').encode()
            content_type = 'text/html; charset=utf-8'
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass


if __name__ == '__main__':
    ThreadingHTTPServer(('10.46.0.1', 8080), Handler).serve_forever()

"""Private lab DN probe origin. No filesystem access, uploads or proxying."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import re
from pathlib import Path

PAYLOAD = b'MAEstro N6 laboratory probe\n' * 4096
PAYLOAD = (PAYLOAD * 3)[:262144]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        peer = ipaddress.ip_address(self.client_address[0])
        allowed = any(peer in ipaddress.ip_network(cidr) for cidr in
                      ('10.45.0.0/16', '10.210.50.0/24', '127.0.0.0/8'))
        if not allowed:
            self.send_error(403)
            return
        payload = PAYLOAD
        content_type = 'application/octet-stream'
        media = re.fullmatch(r'/media/(720p|1080p)/(index\.m3u8|init\.mp4|seg\d{3}\.m4s)', self.path)
        if media:
            path = Path('/opt/maestro-terminal-media') / media[1] / media[2]
            if not path.is_file() or path.stat().st_size > 2_000_000:
                self.send_error(404)
                return
            payload = path.read_bytes()
            content_type = 'application/vnd.apple.mpegurl' if path.suffix == '.m3u8' else 'video/mp4'
        elif self.path != '/probe.bin':
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def setup(self):
        super().setup()
        self.connection.settimeout(5)


if __name__ == '__main__':
    ThreadingHTTPServer(('10.210.50.1', 18090), Handler).serve_forever()

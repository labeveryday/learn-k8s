from http.server import BaseHTTPRequestHandler, HTTPServer
import os, socket
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(f"hi from {socket.gethostname()} pid={os.getpid()}\n".encode())
HTTPServer(("0.0.0.0", 8000), H).serve_forever()

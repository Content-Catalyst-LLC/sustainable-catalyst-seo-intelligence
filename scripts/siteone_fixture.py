"""Disposable, unauthenticated acceptance site. Binds only to IPv4 loopback."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import sys

ORIGIN = "http://127.0.0.1:18765"
SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">""" + "".join(
    f"<url><loc>{ORIGIN}{path}</loc></url>" for path in ("/", "/guide", "/old", "/missing", "/noindex")
) + "</urlset>\n"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        status, content_type, body = 404, "text/plain", "Missing test resource"
        headers = {}
        if self.path == "/robots.txt":
            status, body = 200, f"User-agent: *\nDisallow: /denied\nSitemap: {ORIGIN}/sitemap.xml\n"
        elif self.path == "/sitemap.xml":
            status, content_type, body = 200, "application/xml", SITEMAP
        elif self.path == "/old":
            status, body = 301, ""
            headers["Location"] = "/guide"
        elif self.path == "/forbidden":
            status, body = 403, "Forbidden test resource"
        elif self.path in ("/", "/guide", "/noindex", "/denied"):
            status, content_type = 200, "text/html"
            extra = '<link rel="canonical" href="/guide">' if self.path == "/noindex" else ""
            if self.path == "/noindex":
                extra += '<meta name="robots" content="noindex, nofollow">'
                headers["X-Robots-Tag"] = "noindex"
            body = '<!doctype html><html lang="en"><head><title>SEO fixture</title>' + extra
            body += "</head><body>"
            if self.path != "/noindex":
                body += "<h1>SEO fixture heading</h1>"
            if self.path == "/":
                body += "".join(f'<a href="{path}">{path}</a>' for path in (
                    "/old", "/guide", "/missing", "/noindex", "/forbidden", "/denied", "/sitemap.xml"
                ))
            body += "</body></html>"
        payload = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    if sys.argv[1:] == ["--sitemap"]:
        print(SITEMAP, end="")
    elif sys.argv[1:]:
        raise SystemExit("Only --sitemap is supported")
    else:
        HTTPServer(("127.0.0.1", 18765), Handler).serve_forever()

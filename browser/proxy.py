#!/usr/bin/env python3
"""Stripped web proxy for the emulated iPhone OS 1.0 Safari.

The 2007 Safari cannot speak modern TLS and chokes on modern HTML/CSS/JS. This runs on the
Mac: it fetches pages with the Mac's own curl (so the Mac's certificate store and modern TLS are
used), reduces them to plain old HTML, converts images to small JPEGs, and serves them over
plain HTTP. From the guest, open  http://10.0.2.2:8090/  (or http://web.test:8090/ when the
relay is started with --host web.test=10.0.2.2).

Stdlib only. Needs macOS `curl` and `sips`. Listens on 127.0.0.1 only; the guest reaches it
through relay.py, which maps 10.0.2.2 to the host's 127.0.0.1.
Limits: no JavaScript, no CSS, POST forms are sent as GET, no cookies/login sessions.
"""
import html, os, re, subprocess, sys, tempfile
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urlencode, urljoin, urlparse, parse_qsl

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'
MAX_HTML = 400_000      # bytes of input parsed
MAX_OUT = 250_000       # bytes sent to the phone
IMG_WIDTH = 300         # screen is 320 wide
SKIP = {'script', 'style', 'noscript', 'svg', 'iframe', 'template', 'head', 'object', 'embed',
        'canvas', 'video', 'audio', 'select', 'textarea', 'button'}
KEEP = {'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'br', 'ul', 'ol', 'li', 'b', 'strong', 'i', 'em',
        'pre', 'code', 'blockquote', 'hr', 'table', 'tr', 'td', 'th', 'dl', 'dt', 'dd'}
VOID = {'br', 'hr', 'img', 'input', 'meta', 'link'}

def fetch(url, binary_ok=False, timeout=25):
    """Return (bytes, content_type, final_url) using the Mac's curl and trust store."""
    if urlparse(url).scheme not in ('http', 'https'):
        raise ValueError('only http/https URLs are supported')
    out = subprocess.run(
        ['curl', '-sSL', '--compressed', '-m', str(timeout), '--max-filesize', '15000000',
         '-A', UA, '-H', 'Accept-Language: en', '-w', '\n%{content_type}\n%{url_effective}', '--', url],
        capture_output=True, timeout=timeout + 5)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.decode('utf-8', 'replace').strip() or f'curl exit {out.returncode}')
    body, ctype, final = out.stdout.rsplit(b'\n', 2)
    return body, ctype.decode('latin-1').lower(), final.decode('latin-1')

def decode(body, ctype):
    m = re.search(r'charset=([\w-]+)', ctype) or re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', body[:4096], re.I) and \
        re.search(r'charset=["\']?([\w-]+)', body[:4096].decode('latin-1'), re.I)
    for enc in ([m.group(1)] if m else []) + ['utf-8', 'latin-1']:
        try:
            return body.decode(enc)
        except (LookupError, UnicodeDecodeError):
            pass
    return body.decode('utf-8', 'replace')

def purl(u):  return '/p?u=' + quote(u, safe='')
def iurl(u):  return '/i?u=' + quote(u, safe='')

class Simplify(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.out, self.title, self.skip, self.in_title = base, [], '', 0, False
        self.open_a = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'title':
            self.in_title = True
            return
        if tag in SKIP:
            if tag not in VOID: self.skip += 1
            return
        if self.skip:
            return
        if tag == 'a' and a.get('href') and not a['href'].startswith(('javascript:', '#')):
            self.out.append(f'<a href="{html.escape(purl(urljoin(self.base, a["href"])))}">'); self.open_a += 1
        elif tag == 'img':
            src = a.get('src') or a.get('data-src')
            if src and not src.startswith('data:') and not urlparse(src).path.lower().endswith(('.svg', '.webp', '.avif')):
                alt = html.escape(a.get('alt', ''))
                self.out.append(f'<img src="{html.escape(iurl(urljoin(self.base, src)))}" alt="{alt}">')
        elif tag == 'form':
            action = urljoin(self.base, a.get('action') or self.base)
            self.out.append(f'<form action="/f" method="get"><input type="hidden" name="_a" value="{html.escape(action)}">')
        elif tag == 'input':
            t = (a.get('type') or 'text').lower()
            if t in ('text', 'search', 'hidden', 'email', 'url', 'number', 'tel', 'submit'):
                if t == 'submit' and not a.get('name'):
                    self.out.append(f'<input type="submit" value="{html.escape(a.get("value", "Go"))}">')
                else:
                    self.out.append('<input type="%s" name="%s" value="%s">' % (
                        'text' if t in ('search', 'email', 'url', 'number', 'tel') else t,
                        html.escape(a.get('name', '')), html.escape(a.get('value', ''))))
        elif tag in KEEP:
            self.out.append(f'<{tag}>')

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False
        elif tag in SKIP:
            if tag not in VOID and self.skip: self.skip -= 1
        elif self.skip:
            return
        elif tag == 'a' and self.open_a:
            self.out.append('</a>'); self.open_a -= 1
        elif tag == 'form':
            self.out.append('</form>')
        elif tag in KEEP and tag not in VOID:
            self.out.append(f'</{tag}>')

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip and data.strip():
            self.out.append(html.escape(re.sub(r'\s+', ' ', data)))

def shell(title, body, query=''):
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8"><title>{html.escape(title or "Web")}</title>
<meta name="viewport" content="width=320"><style>body{{font:14px Helvetica,Arial;margin:6px;word-wrap:break-word}}
img{{max-width:100%;height:auto}}pre{{overflow:auto}}table{{max-width:100%}}</style></head><body>
<form action="/go" method="get"><input type="text" name="q" value="{html.escape(query)}" style="width:75%"><input type="submit" value="Go"></form><hr>
{body}</body></html>'''

def looks_like_url(q):
    return bool(re.match(r'^(https?://\S+|[\w-]+(\.[\w-]+)+(:\d+)?(/\S*)?)$', q.strip()))

class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'
    def log_message(self, fmt, *args): sys.stderr.write('[web] ' + fmt % args + '\n')

    def reply(self, code, body, ctype='text/html; charset=utf-8', extra=()):
        if isinstance(body, str): body = body.encode('utf-8', 'replace')
        self.send_response(code); self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in extra: self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)

    def redirect(self, to): self.reply(302, '', extra=[('Location', to)])

    def do_GET(self):
        u = urlparse(self.path); q = dict(parse_qsl(u.query))
        try:
            if u.path == '/':
                self.reply(200, shell('Web', '<h3>Web</h3><p>Type a URL or a search above.</p>'
                    '<p><a href="/go?q=example.com">example.com</a><br><a href="/go?q=news.ycombinator.com">Hacker News</a>'
                    '<br><a href="/go?q=en.wikipedia.org">Wikipedia</a></p>'))
            elif u.path == '/go':
                s = q.get('q', '').strip()
                if looks_like_url(s):
                    self.redirect(purl(s if '://' in s else 'https://' + s))
                else:
                    self.redirect(purl('https://html.duckduckgo.com/html/?' + urlencode({'q': s})))
            elif u.path == '/f':
                params = [(k, v) for k, v in parse_qsl(u.query) if k not in ('_a',) and k]
                act = q.get('_a', '')
                sep = '&' if '?' in act else '?'
                self.redirect(purl(act + (sep + urlencode(params) if params else '')))
            elif u.path == '/p':
                self.page(q.get('u', ''))
            elif u.path == '/i':
                self.image(q.get('u', ''))
            else:
                self.reply(404, shell('Not found', '<p>Not found</p>'))
        except Exception as e:  # show errors on the phone instead of dropping the connection
            self.reply(502, shell('Error', f'<h3>Could not load</h3><p>{html.escape(str(e))[:500]}</p>'))

    def page(self, url):
        body, ctype, final = fetch(url)
        if 'html' not in ctype and 'xml' not in ctype and not body.lstrip()[:15].lower().startswith((b'<!doctype', b'<html')):
            if ctype.startswith('image/'): return self.redirect(iurl(final))
            if ctype.startswith('text/'):
                return self.reply(200, shell(final, '<pre>' + html.escape(decode(body, ctype)[:100000]) + '</pre>'))
            return self.reply(200, shell('Download', f'<p>Cannot show {html.escape(ctype or "this file type")} on this phone.</p>'))
        s = Simplify(final); s.feed(decode(body[:MAX_HTML], ctype)); s.close()
        out = ''.join(s.out)
        out = re.sub(r'(<br>\s*){3,}', '<br><br>', out)
        if len(out) > MAX_OUT: out = out[:MAX_OUT] + '<p><i>[page truncated]</i></p>'
        self.reply(200, shell(s.title.strip() or final, out or '<p>(Nothing to display; this page probably needs JavaScript.)</p>', final))

    def image(self, url):
        try:
            body, ctype, _ = fetch(url, timeout=20)
            if not ctype.startswith('image/'): raise ValueError('not an image')
            self.convert(body)
        except Exception:  # broken/unsupported image: send a 1x1 GIF instead of an error page
            self.reply(200, b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;', 'image/gif')

    def convert(self, body):
        with tempfile.TemporaryDirectory() as d:
            src, dst = os.path.join(d, 'in'), os.path.join(d, 'out.jpg')
            open(src, 'wb').write(body)
            r = subprocess.run(['sips', '-Z', str(IMG_WIDTH * 2), '-s', 'format', 'jpeg', '-s', 'formatOptions', '60', src, '--out', dst],
                               capture_output=True, timeout=20)
            if r.returncode != 0 or not os.path.exists(dst): raise RuntimeError('image conversion failed')
            self.reply(200, open(dst, 'rb').read(), 'image/jpeg')

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8090
    print(f'stripped web proxy on http://127.0.0.1:{port}/  (guest: http://10.0.2.2:{port}/)')
    ThreadingHTTPServer(('127.0.0.1', port), H).serve_forever()

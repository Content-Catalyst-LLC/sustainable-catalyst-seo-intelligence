"""Collect one anonymous, paced public crawl for offline SiteOne replay."""
import collections
import datetime as dt
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urljoin, urlsplit
import xml.etree.ElementTree as ET

ORIGIN = "https://sustainablecatalyst.com"
SEEDS = ("/", "/institution/", "/research-hub/", "/publications/",
         "/knowledge-libraries/", "/support/", "/media/")
PREFIX = "/support/guides/"
AGENT = "Sustainable-Catalyst-SEO-Acceptance"
REQUEST_CAP = 50
PAGE_CAP = 40
PREFIX_CAP = 15
GAP = 3
DURATION = 300
BYTE_CAP = 4 * 1024 * 1024


def sha(data):
    return hashlib.sha256(data).hexdigest()


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def path_of(url):
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if (parts.scheme != "https" or parts.netloc != "sustainablecatalyst.com"
            or parts.query or "?" in url.split("#", 1)[0]
            or not re.fullmatch(r"/[A-Za-z0-9_./~-]*", parts.path)
            or "//" in parts.path or any(x in (".", "..") for x in parts.path.split("/"))):
        return None
    return parts.path


def resolve(base, value):
    try:
        return urljoin(base, value)
    except ValueError:
        return ""


def in_scope(path):
    return path in SEEDS or bool(path and path.startswith(PREFIX))


class Robots:
    """Strict verifier for ASCII rules, supporting longest matches and wildcards."""
    def __init__(self, lines):
        self.groups = []
        self.sitemaps = []
        agents, rules = [], []
        for line in lines:
            field, _, value = line.split("#", 1)[0].partition(":")
            field, value = field.strip().lower(), value.strip()
            if field == "sitemap":
                self.sitemaps.append(value)
            if field == "user-agent":
                if rules:
                    self.groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.lower())
            elif field in ("allow", "disallow") and value:
                if not agents or not re.fullmatch(r"/[A-Za-z0-9_./~*?&=-]*\$?", value):
                    raise Stop("robots.txt has an unsupported path rule")
                pattern = re.escape(value.removesuffix("$")).replace(r"\*", ".*")
                if value.endswith("$"):
                    pattern += "$"
                rules.append((len(value), field == "allow", re.compile(pattern)))
            elif field in ("crawl-delay", "request-rate") and value:
                raise Stop("robots.txt specifies an additional rate policy")
        if agents:
            self.groups.append((agents, rules))
        if not any(agents for agents, _ in self.groups):
            raise Stop("robots.txt contains no recognizable user-agent groups")

    def can_fetch(self, agent, url):
        agent = agent.lower()
        matches = [(len(token) if token != "*" else 0, rules)
                   for tokens, rules in self.groups for token in tokens
                   if token and (token == "*" or token in agent)]
        if not matches:
            return True
        longest = max(length for length, _ in matches)
        path = path_of(url)
        if path is None:
            return False
        decisions = [(length, allow) for specificity, rules in matches if specificity == longest
                     for length, allow, pattern in rules if pattern.match(path)]
        return max(decisions, default=(0, True))[1]


class Page(HTMLParser):
    def __init__(self, url, body):
        super().__init__()
        self.url = url
        self.title = []
        self.in_title = False
        self.head = False
        self.base = None
        self.descriptions = []
        self.robots = []
        self.canonicals = []
        self.links = []
        self.headings = []
        self.feed(body.decode("utf-8", errors="replace"))
        self.close()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        href = attrs.get("href")
        if tag == "head":
            self.head = True
        if self.head and tag == "base" and href and self.base is None:
            self.base = href
        if tag == "title":
            self.in_title = True
        if re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        if tag == "a" and href:
            self.links.append(href)
        if self.head and tag == "link" and href and "canonical" in (attrs.get("rel") or "").lower().split():
            self.canonicals.append(href)
        if self.head and tag == "meta":
            name = (attrs.get("name") or "").lower()
            if name == "description":
                self.descriptions.append(attrs.get("content") or "")
            if name in ("robots", "googlebot"):
                self.robots.append({"agent": name, "content": attrs.get("content") or ""})

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag == "head":
            self.head = False

    def handle_data(self, data):
        if self.in_title:
            self.title.append(data)

    def resolve(self, value):
        try:
            return urljoin(urljoin(self.url, self.base or ""), value)
        except ValueError:
            return value


class Stop(Exception):
    pass


def headers(raw):
    result = collections.defaultdict(list)
    for line in re.split(rb"\r?\n", raw):
        if b":" in line:
            name, value = line.split(b":", 1)
            result[name.decode("latin1").lower()].append(value.strip().decode("latin1"))
    return dict(result)


class Collector:
    def __init__(self, output):
        self.output = output
        self.started_at = timestamp()
        self.start = time.monotonic()
        self.last_end = None
        self.records = []
        self.pages = {}
        self.skipped = {}
        self.prefix_paths = set()
        self.sitemap = None
        self.members = set()
        self.robots = None
        self.failures = 0

    def fetch(self, path, kind):
        permitted = ((kind == "robots" and path == "/robots.txt" and not self.records)
                     or (kind == "sitemap" and path == self.sitemap and self.robots)
                     or (kind == "page" and in_scope(path) and self.robots))
        if not permitted or path_of(ORIGIN + path) != path:
            raise Stop("request rejected by authorization guard")
        if any(row["path"] == path for row in self.records):
            return None
        if kind != "robots" and not self.robots.can_fetch(AGENT, ORIGIN + path):
            self.skipped[path] = "robots.txt disallows this URL"
            return None
        if kind == "page":
            if sum(r["kind"] == "page" for r in self.records) >= PAGE_CAP:
                self.skipped[path] = "page budget"
                return None
            if path.startswith(PREFIX):
                if len(self.prefix_paths) >= PREFIX_CAP:
                    self.skipped[path] = "prefix budget"
                    return None
                self.prefix_paths.add(path)
        if len(self.records) >= REQUEST_CAP:
            raise Stop("request budget reached")
        if self.last_end is not None:
            delay = max(0, self.last_end + GAP - time.monotonic())
            if time.monotonic() + delay >= self.start + DURATION:
                raise Stop("duration budget reached")
            time.sleep(delay)
        remaining = self.start + DURATION - time.monotonic()
        if remaining <= 1:
            raise Stop("duration budget reached")
        n = len(self.records) + 1
        body_path = self.output / f"{n:02d}.body"
        header_path = self.output / f"{n:02d}.headers"
        launched = time.monotonic()
        record = {"request": n, "url": ORIGIN + path, "path": path, "kind": kind,
                  "method": "GET", "started_at": timestamp(),
                  "start_offset_seconds": round(launched - self.start, 6),
                  "body": body_path.name, "headers": header_path.name}
        self.records.append(record)
        timeout = min(15, remaining - 0.5)
        try:
            process = subprocess.run([
                "/usr/bin/curl", "-q", "--globoff", "--path-as-is", "--noproxy", "*", "--proto", "=https",
                "--request", "GET", "--retry", "0", "--max-redirs", "0",
                "--connect-timeout", "7", "--max-time", str(timeout),
                "--max-filesize", str(BYTE_CAP), "--http1.1", "-sS",
                "--user-agent", AGENT, "--header", "Accept-Encoding: identity",
                "--dump-header", str(header_path), "--output", str(body_path),
                "--write-out", "%{http_code} %{time_total}", ORIGIN + path,
            ], env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=min(remaining, timeout + .3))
        except subprocess.TimeoutExpired as exc:
            raise Stop("HTTP transport deadline") from exc
        finally:
            self.last_end = time.monotonic()
            record["end_offset_seconds"] = round(self.last_end - self.start, 6)
            record["finished_at"] = timestamp()
        raw_headers = header_path.read_bytes() if header_path.exists() else b""
        body = body_path.read_bytes() if body_path.exists() else b""
        head = headers(raw_headers)
        record.update(body_sha256=sha(body), headers_sha256=sha(raw_headers))
        status_text, _, seconds = process.stdout.partition(" ")
        status = int(status_text) if status_text.isdigit() else 0
        record.update(status=status or None, response_seconds=float(seconds) if seconds else None,
                      content_type=(head.get("content-type") or [""])[-1],
                      location=(head.get("location") or [None])[-1],
                      x_robots_tag=head.get("x-robots-tag", []))
        if process.returncode:
            record["transport_exit_code"] = process.returncode
            raise Stop("HTTP transport or response size check failed")
        if (status in (401, 403, 407, 429) or "retry-after" in head
                or "challenge" in ",".join(head.get("cf-mitigated", [])).lower()
                or re.search(rb"<title[^>]*>\s*(?:just a moment|access denied|attention required|verify (?:you|that))",
                             body[:65536], re.I)):
            raise Stop(f"rate limit or potential access challenge (HTTP {status})")
        self.failures = self.failures + 1 if status >= 500 else 0
        if self.failures >= 2:
            raise Stop("two consecutive server failures")
        return record, body

    def run(self):
        request = self.fetch("/robots.txt", "robots")
        record, body = request
        if (record["status"] != 200 or not record["content_type"].lower().startswith("text/plain")
                or len(body) > 512_000 or b"\x00" in body):
            raise Stop("robots.txt could not be verified as a complete plaintext response")
        try:
            lines = body.decode("utf-8-sig").splitlines()
        except UnicodeDecodeError as exc:
            raise Stop("robots.txt did not decode as UTF-8") from exc
        self.robots = Robots(lines)
        queue = collections.deque(SEEDS)
        for declared in self.robots.sitemaps:
            path = path_of(declared)
            if path and not in_scope(path) and path.lower().endswith(".xml"):
                self.sitemap = path
                break
        if self.sitemap:
            fetched = self.fetch(self.sitemap, "sitemap")
            if fetched is not None:
                record, body = fetched
                if record["status"] == 200 and b"\x00" not in body and b"<!DOCTYPE" not in body.upper():
                    try:
                        root = ET.fromstring(body)
                    except ET.ParseError:
                        root = None
                    if root is not None and root.tag == "{http://www.sitemaps.org/schemas/sitemap/0.9}urlset":
                        self.members = {
                            p for item in root for node in item if node.tag.endswith("}loc")
                            and in_scope(p := path_of(node.text or ""))
                        }
                        queue.extend(sorted(self.members - set(SEEDS)))
        while queue:
            path = queue.popleft()
            fetched = self.fetch(path, "page")
            if fetched is None:
                continue
            record, body = fetched
            if 300 <= record["status"] < 400 and record["location"]:
                target = path_of(resolve(record["url"], record["location"]))
                if in_scope(target):
                    queue.append(target)
            if record["status"] == 200 and record["content_type"].lower().split(";")[0].strip() == "text/html":
                page = Page(record["url"], body)
                self.pages[path] = page
                for link in page.links:
                    target = path_of(page.resolve(link))
                    if target and target.startswith(PREFIX):
                        queue.append(target)

    def manifest(self, stop_reason):
        return {"version": 1, "origin": ORIGIN, "user_agent": AGENT,
                "seeds": list(SEEDS), "prefix": PREFIX, "prefix_cap": PREFIX_CAP,
                "request_cap": REQUEST_CAP, "page_cap": PAGE_CAP, "minimum_gap_seconds": GAP,
                "max_duration_seconds": DURATION, "concurrency": 1, "methods": ["GET"],
                "started_at": self.started_at, "finished_at": timestamp(),
                "duration_seconds": round(time.monotonic() - self.start, 6),
                "actual_requests_attempted": len(self.records), "html_pages": len(self.pages),
                "robots_verified": self.robots is not None, "sitemap_path": self.sitemap,
                "stop_reason": stop_reason, "skipped": self.skipped, "requests": self.records}


def main(output):
    output.mkdir(mode=0o700)
    collector = Collector(output)
    stop_reason = None
    try:
        collector.run()
    except Stop as exc:
        stop_reason = str(exc)
    manifest = collector.manifest(stop_reason)
    (output / "sc-seo-02-public-crawl-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({k: manifest[k] for k in ("actual_requests_attempted", "html_pages", "robots_verified", "stop_reason")}))
    return 2 if stop_reason else 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1]).resolve()))

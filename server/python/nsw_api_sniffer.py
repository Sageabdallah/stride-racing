#!/usr/bin/env python3
"""NSW Punter's Intelligence API Sniffer — uses Playwright headless to intercept all network requests from Racing NSW web pages."""

import asyncio
import json
import sys
import os
from urllib.parse import urlparse

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("Installing playwright...")
    os.system("pip install playwright && playwright install chromium")
    from playwright.async_api import async_playwright


INTERESTING_PATTERNS = [
    "api.", "racingnsw", "pidata", "sectional", "tracking", "replay",
    "gps", "timing", "speed", "race-data", ".json", "/api/",
    "puntersintelligence", "pi.racing", "graphql", "results",
    "sectionals", "horse", "runner", "meeting", "race"
]

SKIP_PATTERNS = [
    "google-analytics", "googletagmanager", "facebook", "doubleclick",
    "fonts.googleapis", "fonts.gstatic", "analytics", "pixel",
    "adservice", "cdn.cookielaw", "onetrust", "hotjar", "sentry",
    ".css", ".woff", ".ttf", ".svg", ".png", ".jpg", ".gif", ".ico",
    "recaptcha", "gstatic.com/recaptcha"
]


class NSWAPISniffer:
    def __init__(self):
        self.captured = []
        self.api_endpoints = []
        self.interesting_responses = []

    def is_interesting(self, url):
        url_lower = url.lower()
        if any(skip in url_lower for skip in SKIP_PATTERNS):
            return False
        if any(pat in url_lower for pat in INTERESTING_PATTERNS):
            return True
        return False

    def is_data_request(self, url, resource_type):
        if resource_type in ("xhr", "fetch", "websocket"):
            return True
        if ".json" in url.lower():
            return True
        return False

    async def on_response(self, response):
        url = response.url
        status = response.status
        content_type = response.headers.get("content-type", "")

        if self.is_interesting(url) or self.is_data_request(url, ""):
            entry = {
                "url": url,
                "status": status,
                "content_type": content_type,
                "headers": dict(response.headers),
            }

            try:
                if "json" in content_type or "text" in content_type:
                    body = await response.text()
                    entry["body_preview"] = body[:2000]
                    entry["body_size"] = len(body)

                    if "json" in content_type:
                        try:
                            entry["json"] = json.loads(body)
                        except:
                            pass
            except:
                pass

            self.interesting_responses.append(entry)
            print(f"  [RESPONSE {status}] {url[:120]}")
            if entry.get("body_size"):
                print(f"    Content-Type: {content_type}, Size: {entry['body_size']}")

    async def on_request(self, request):
        url = request.url
        method = request.method
        resource_type = request.resource_type

        entry = {
            "url": url,
            "method": method,
            "resource_type": resource_type,
            "headers": dict(request.headers),
        }

        if request.post_data:
            entry["post_data"] = request.post_data[:1000]

        if self.is_interesting(url) or self.is_data_request(url, resource_type):
            self.api_endpoints.append(entry)
            marker = "API" if self.is_interesting(url) else "XHR"
            print(f"  [{marker} {method}] {url[:120]}")
            if request.post_data:
                print(f"    POST data: {request.post_data[:200]}")

        self.captured.append(entry)

    async def run(self):
        print("=" * 70)
        print("NSW PUNTER'S INTELLIGENCE API SNIFFER")
        print("=" * 70)

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                ]
            )

            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                viewport={"width": 1920, "height": 1080},
            )

            page = await context.new_page()
            page.on("request", lambda req: asyncio.ensure_future(self.on_request(req)))
            page.on("response", lambda resp: asyncio.ensure_future(self.on_response(resp)))

            urls_to_visit = [
                ("Racing NSW Main", "https://www.racingnsw.com.au"),
                ("Punter's Intelligence Page", "https://www.racingnsw.com.au/punters-intelligence/"),
                ("Racing NSW Results", "https://www.racingnsw.com.au/racing/results/"),
                ("Racing NSW Race Replays", "https://www.racingnsw.com.au/racing/race-replays/"),
            ]

            for name, url in urls_to_visit:
                print(f"\n{'='*60}")
                print(f"VISITING: {name}")
                print(f"URL: {url}")
                print(f"{'='*60}")

                try:
                    resp = await page.goto(url, wait_until="networkidle", timeout=30000)
                    print(f"  Page status: {resp.status if resp else 'N/A'}")
                    await asyncio.sleep(3)

                    title = await page.title()
                    print(f"  Page title: {title}")

                    page_url = page.url
                    if page_url != url:
                        print(f"  Redirected to: {page_url}")

                    content = await page.content()
                    print(f"  Page size: {len(content)} chars")

                    links = await page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                            href: a.href,
                            text: a.textContent.trim().substring(0, 80)
                        })).filter(l => 
                            l.href.includes('sectional') || 
                            l.href.includes('replay') || 
                            l.href.includes('result') ||
                            l.href.includes('intelligence') ||
                            l.href.includes('race') ||
                            l.href.includes('meeting') ||
                            l.href.includes('punter')
                        ).slice(0, 20);
                    }""")

                    if links:
                        print(f"\n  Interesting links found ({len(links)}):")
                        for link in links:
                            print(f"    {link['text'][:50]} -> {link['href'][:100]}")

                    iframes = await page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('iframe')).map(f => ({
                            src: f.src,
                            id: f.id,
                            name: f.name
                        }));
                    }""")
                    if iframes:
                        print(f"\n  Iframes found ({len(iframes)}):")
                        for iframe in iframes:
                            print(f"    src={iframe['src'][:100]} id={iframe.get('id','')} name={iframe.get('name','')}")

                    scripts = await page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('script[src]')).map(s => s.src)
                            .filter(s => s.includes('racingnsw') || s.includes('punter') || s.includes('api') || s.includes('race'))
                            .slice(0, 10);
                    }""")
                    if scripts:
                        print(f"\n  Interesting scripts ({len(scripts)}):")
                        for s in scripts:
                            print(f"    {s[:120]}")

                    inline_scripts = await page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('script:not([src])')).map(s => s.textContent)
                            .filter(t => t && (t.includes('api') || t.includes('endpoint') || t.includes('url') || t.includes('race') || t.includes('sectional')))
                            .map(t => t.substring(0, 500))
                            .slice(0, 5);
                    }""")
                    if inline_scripts:
                        print(f"\n  Inline scripts with API refs ({len(inline_scripts)}):")
                        for s in inline_scripts:
                            print(f"    {s[:300]}")

                except Exception as e:
                    print(f"  ERROR: {e}")

            print(f"\n\n{'='*60}")
            print("FOLLOWING INTERESTING LINKS")
            print(f"{'='*60}")

            follow_links = []
            for entry in self.api_endpoints:
                url = entry["url"]
                if "racingnsw" in url and ("result" in url or "replay" in url or "sectional" in url or "intelligence" in url):
                    follow_links.append(url)

            for resp_entry in self.interesting_responses:
                url = resp_entry["url"]
                if "racingnsw" in url and any(kw in url for kw in ["result", "replay", "sectional", "intelligence", "race"]):
                    if url not in follow_links:
                        follow_links.append(url)

            for url in follow_links[:5]:
                print(f"\n  Following: {url[:100]}")
                try:
                    await page.goto(url, wait_until="networkidle", timeout=20000)
                    await asyncio.sleep(3)
                except Exception as e:
                    print(f"    Error: {e}")

            print(f"\n\n{'='*70}")
            print("FINAL SUMMARY")
            print(f"{'='*70}")
            print(f"Total requests captured: {len(self.captured)}")
            print(f"API/interesting requests: {len(self.api_endpoints)}")
            print(f"Interesting responses: {len(self.interesting_responses)}")

            if self.api_endpoints:
                print(f"\nAPI ENDPOINTS ({len(self.api_endpoints)}):")
                seen = set()
                for ep in self.api_endpoints:
                    parsed = urlparse(ep["url"])
                    key = f"{ep['method']} {parsed.scheme}://{parsed.netloc}{parsed.path}"
                    if key not in seen:
                        seen.add(key)
                        print(f"  {ep['method']} {ep['url'][:120]}")
                        if ep.get("post_data"):
                            print(f"    POST: {ep['post_data'][:200]}")

            if self.interesting_responses:
                print(f"\nINTERESTING RESPONSES ({len(self.interesting_responses)}):")
                for resp in self.interesting_responses:
                    print(f"  [{resp['status']}] {resp['url'][:120]}")
                    if resp.get("body_preview"):
                        preview = resp["body_preview"][:300].replace('\n', ' ')
                        print(f"    Preview: {preview}")

            report = {
                "api_endpoints": self.api_endpoints[:50],
                "interesting_responses": [{
                    "url": r["url"],
                    "status": r["status"],
                    "content_type": r.get("content_type", ""),
                    "body_preview": r.get("body_preview", "")[:1000],
                } for r in self.interesting_responses[:30]],
                "all_domains": list(set(
                    urlparse(c["url"]).netloc for c in self.captured
                )),
            }

            with open("/tmp/nsw_sniffer_report.json", "w") as f:
                json.dump(report, f, indent=2)
            print(f"\nFull report saved to /tmp/nsw_sniffer_report.json")

            await browser.close()


if __name__ == "__main__":
    sniffer = NSWAPISniffer()
    asyncio.run(sniffer.run())

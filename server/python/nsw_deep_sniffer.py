#!/usr/bin/env python3
"""
Deep sniffer for Racing NSW - explores race results pages and looks for
sectional data endpoints, especially on racing.racingnsw.com.au
"""

import asyncio
import json
from urllib.parse import urlparse
from playwright.async_api import async_playwright


class DeepSniffer:
    def __init__(self):
        self.all_requests = []
        self.all_responses = []

    async def on_request(self, request):
        url = request.url
        if "racingnsw" in url or "pidata" in url:
            self.all_requests.append({
                "url": url,
                "method": request.method,
                "type": request.resource_type,
            })

    async def on_response(self, response):
        url = response.url
        ct = response.headers.get("content-type", "")
        if ("racingnsw" in url and response.request.resource_type in ("xhr", "fetch", "document")) or "json" in ct:
            body = ""
            try:
                body = await response.text()
            except:
                pass
            self.all_responses.append({
                "url": url,
                "status": response.status,
                "content_type": ct,
                "body_len": len(body),
                "body_preview": body[:500] if body else "",
            })

    async def run(self):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                viewport={"width": 1920, "height": 1080},
            )
            page = await context.new_page()
            page.on("request", lambda r: asyncio.ensure_future(self.on_request(r)))
            page.on("response", lambda r: asyncio.ensure_future(self.on_response(r)))

            print("=== Step 1: Go to Racing NSW Calendar Results ===")
            await page.goto("https://racing.racingnsw.com.au/FreeFields/Calendar_Results.aspx", wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            content = await page.content()
            print(f"Page size: {len(content)}")

            links = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                    href: a.href,
                    text: a.textContent.trim().substring(0, 60)
                })).filter(l => l.href.includes('Results') || l.href.includes('result'))
                .slice(0, 30);
            }""")
            print(f"Result links found: {len(links)}")
            for l in links[:15]:
                print(f"  {l['text']}: {l['href'][:100]}")

            meeting_links = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('a[href*="Results_Meeting"]')).map(a => ({
                    href: a.href,
                    text: a.textContent.trim()
                })).slice(0, 10);
            }""")

            if not meeting_links:
                meeting_links = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a[href*="key="]')).map(a => ({
                        href: a.href,
                        text: a.textContent.trim()
                    })).slice(0, 10);
                }""")

            print(f"\nMeeting links: {len(meeting_links)}")
            for l in meeting_links[:10]:
                print(f"  {l['text']}: {l['href'][:120]}")

            if meeting_links:
                first_meeting = meeting_links[0]['href']
                print(f"\n=== Step 2: Visit first meeting: {first_meeting[:100]} ===")
                await page.goto(first_meeting, wait_until="networkidle", timeout=30000)
                await asyncio.sleep(2)

                race_links = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                        href: a.href,
                        text: a.textContent.trim().substring(0, 60)
                    })).filter(l => l.href.includes('Race') || l.href.includes('race') || l.href.includes('Result'))
                    .slice(0, 20);
                }""")
                print(f"Race links: {len(race_links)}")
                for l in race_links[:10]:
                    print(f"  {l['text']}: {l['href'][:120]}")

                result_race_links = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a[href*="Results_Race"]')).map(a => ({
                        href: a.href,
                        text: a.textContent.trim()
                    })).slice(0, 10);
                }""")

                if result_race_links:
                    first_race = result_race_links[0]['href']
                    print(f"\n=== Step 3: Visit race result: {first_race[:100]} ===")
                    await page.goto(first_race, wait_until="networkidle", timeout=30000)
                    await asyncio.sleep(2)

                    page_text = await page.evaluate("() => document.body.innerText")
                    if "sectional" in page_text.lower() or "speed" in page_text.lower():
                        print("FOUND sectional/speed references in race result page!")
                        for line in page_text.split('\n'):
                            if any(kw in line.lower() for kw in ['sectional', 'speed', '200m', '400m', '600m', 'split']):
                                print(f"  {line[:150]}")

                    all_links = await page.evaluate("""() => {
                        return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                            href: a.href,
                            text: a.textContent.trim().substring(0, 60)
                        })).filter(l => 
                            l.href.includes('sectional') || l.href.includes('replay') || 
                            l.href.includes('intelligence') || l.href.includes('speed') ||
                            l.href.includes('tracking') || l.href.includes('gps') ||
                            l.text.toLowerCase().includes('sectional') || l.text.toLowerCase().includes('replay')
                        ).slice(0, 10);
                    }""")
                    if all_links:
                        print(f"\nSectional/replay links: {len(all_links)}")
                        for l in all_links:
                            print(f"  {l['text']}: {l['href'][:120]}")

            print("\n=== Step 4: Try direct URLs for sectional data ===")
            test_urls = [
                "https://racing.racingnsw.com.au/FreeFields/RaceSearch.aspx",
                "https://racing.racingnsw.com.au/FreeServices/HorseSearch.aspx",
                "https://racing.racingnsw.com.au/InteractiveForm/TrackCondition.aspx",
                "https://racing.racingnsw.com.au/FreeFields/Sectionals.aspx",
                "https://racing.racingnsw.com.au/FreeFields/RaceTimes.aspx",
                "https://racing.racingnsw.com.au/api/sectionals",
                "https://racing.racingnsw.com.au/api/results",
                "http://pidata.racingnsw.com.au/",
            ]

            for url in test_urls:
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=10000)
                    status = resp.status if resp else "N/A"
                    title = await page.title()
                    print(f"  [{status}] {url[:80]} -> {title[:50]}")
                except Exception as e:
                    print(f"  [ERR] {url[:80]} -> {str(e)[:60]}")

            print("\n=== Step 5: Check race replays page ===")
            try:
                await page.goto("https://www.racingnsw.com.au/racing/race-replays/", wait_until="networkidle", timeout=20000)
                await asyncio.sleep(3)

                replay_content = await page.content()
                iframes = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('iframe')).map(f => ({
                        src: f.src, id: f.id, name: f.name
                    }));
                }""")
                print(f"Iframes on replay page: {len(iframes)}")
                for iframe in iframes:
                    print(f"  src={iframe['src'][:100]}")

                video_sources = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('video source, video[src]')).map(v => v.src || v.getAttribute('src'));
                }""")
                if video_sources:
                    print(f"Video sources: {video_sources}")

                replay_links = await page.evaluate("""() => {
                    return Array.from(document.querySelectorAll('a[href]')).map(a => ({
                        href: a.href,
                        text: a.textContent.trim().substring(0, 60)
                    })).filter(l => 
                        l.href.includes('replay') || l.href.includes('video') || 
                        l.href.includes('skyracing') || l.href.includes('akamai')
                    ).slice(0, 10);
                }""")
                for l in replay_links:
                    print(f"  Replay link: {l['text']}: {l['href'][:100]}")

            except Exception as e:
                print(f"  Error: {e}")

            print("\n=== Step 6: Try the Punter's Intelligence web app directly ===")
            pi_urls = [
                "https://puntersintelligence.racingnsw.com.au/",
                "https://pi.racingnsw.com.au/",
                "https://api.racingnsw.com.au/",
                "https://data.racingnsw.com.au/",
                "https://racetimeinfo.racingnsw.com.au/",
                "http://pidata.racingnsw.com.au/api/",
                "http://pidata.racingnsw.com.au/races",
            ]
            for url in pi_urls:
                try:
                    resp = await page.goto(url, wait_until="domcontentloaded", timeout=8000)
                    status = resp.status if resp else "N/A"
                    current = page.url
                    print(f"  [{status}] {url[:60]} -> {current[:80]}")
                except Exception as e:
                    print(f"  [ERR] {url[:60]} -> {str(e)[:50]}")

            print("\n" + "="*60)
            print("ALL CAPTURED REQUESTS")
            print("="*60)
            seen = set()
            for req in self.all_requests:
                parsed = urlparse(req["url"])
                key = f"{req['method']} {parsed.netloc}{parsed.path}"
                if key not in seen and req["type"] in ("xhr", "fetch", "document"):
                    seen.add(key)
                    print(f"  [{req['type']}] {req['method']} {req['url'][:120]}")

            print(f"\n{'='*60}")
            print("INTERESTING RESPONSES")
            print(f"{'='*60}")
            for resp in self.all_responses:
                if resp["body_len"] > 0 and resp["status"] == 200:
                    print(f"  [{resp['status']}] {resp['url'][:100]}")
                    print(f"    Type: {resp['content_type'][:50]}, Size: {resp['body_len']}")
                    if any(kw in resp["body_preview"].lower() for kw in ['sectional', 'speed', 'time', 'position', 'runner', 'horse']):
                        print(f"    INTERESTING PREVIEW: {resp['body_preview'][:300]}")

            await browser.close()


if __name__ == "__main__":
    sniffer = DeepSniffer()
    asyncio.run(sniffer.run())

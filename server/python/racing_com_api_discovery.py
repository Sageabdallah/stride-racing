#!/usr/bin/env python3
"""
Racing.com API discovery: uses Playwright to intercept network responses and identify
endpoints that serve race result data.
"""

import json
import sys
import os
import time
from playwright.sync_api import sync_playwright

CHROMIUM_PATH = "/nix/store/zi4f80l169xlmivz8vja8wlphq74qqk0-chromium-125.0.6422.141/bin/chromium"

SAMPLE_URLS = [
    "https://www.racing.com/racing/results/2026-02-14/flemington/race-1",
    "https://www.racing.com/racing/results/2026-02-14/caulfield/race-1",
    "https://www.racing.com/racing/results/2026-02-15/flemington/race-1",
]

def discover_api(url: str):
    """Navigate to a race result page and capture all network responses."""
    captured_responses = []
    
    def handle_response(response):
        try:
            content_type = response.headers.get("content-type", "")
            url_str = response.url
            status = response.status
            
            if any(skip in url_str for skip in ['.js', '.css', '.png', '.jpg', '.svg', '.woff', '.ico', 'analytics', 'google', 'facebook', 'doubleclick', 'gtm', 'tag-manager', 'fonts.', 'hotjar', 'cloudflareinsights']):
                return
            
            entry = {
                "url": url_str,
                "status": status,
                "content_type": content_type[:80],
                "size": 0,
                "preview": "",
                "has_sectional": False,
                "has_race_data": False,
            }
            
            if "json" in content_type or "graphql" in url_str:
                try:
                    body = response.text()
                    entry["size"] = len(body)
                    body_lower = body.lower()
                    
                    sectional_keywords = ["sectional", "split", "last200", "last_200", "last400", "last_400", 
                                         "last600", "last_600", "800m", "600m", "400m", "200m", 
                                         "section_time", "sectiontime", "sectionals"]
                    race_keywords = ["runner", "horse", "jockey", "barrier", "finish", "position", 
                                    "distance", "margin", "odds", "trainer"]
                    
                    entry["has_sectional"] = any(kw in body_lower for kw in sectional_keywords)
                    entry["has_race_data"] = any(kw in body_lower for kw in race_keywords)
                    
                    if entry["has_sectional"] or entry["has_race_data"]:
                        entry["preview"] = body[:2000]
                    else:
                        entry["preview"] = body[:500]
                except Exception as e:
                    entry["preview"] = f"Error reading body: {e}"
            
            captured_responses.append(entry)
        except Exception as e:
            pass
    
    print(f"\n{'='*60}")
    print(f"Navigating to: {url}")
    print(f"{'='*60}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=CHROMIUM_PATH,
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ]
        )
        
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        
        page = context.new_page()
        page.on("response", handle_response)
        
        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
            print(f"Page loaded. Waiting for dynamic content...")
            page.wait_for_timeout(5000)
            
            page_title = page.title()
            print(f"Page title: {page_title}")
            
            page_text = page.inner_text("body")
            if "sectional" in page_text.lower():
                print("*** 'sectional' found in page text! ***")
                sectional_idx = page_text.lower().find("sectional")
                print(f"Context: ...{page_text[max(0,sectional_idx-100):sectional_idx+200]}...")
            
            tab_elements = page.query_selector_all('[role="tab"], [data-tab], .tab, button:has-text("Sectional"), a:has-text("Sectional")')
            if tab_elements:
                print(f"\nFound {len(tab_elements)} tab-like elements")
                for el in tab_elements:
                    text = el.inner_text().strip()
                    if text:
                        print(f"  Tab: '{text}'")
                    if "sectional" in text.lower() or "split" in text.lower() or "time" in text.lower():
                        print(f"  >>> Clicking tab: '{text}'")
                        el.click()
                        page.wait_for_timeout(3000)
            
        except Exception as e:
            print(f"Navigation error: {e}")
        
        browser.close()
    
    print(f"\n{'='*60}")
    print(f"CAPTURED {len(captured_responses)} RESPONSES")
    print(f"{'='*60}")
    
    sectional_hits = [r for r in captured_responses if r["has_sectional"]]
    race_data_hits = [r for r in captured_responses if r["has_race_data"]]
    other_json = [r for r in captured_responses if not r["has_sectional"] and not r["has_race_data"] and r["preview"]]
    
    if sectional_hits:
        print(f"\n*** SECTIONAL DATA FOUND ({len(sectional_hits)} responses) ***")
        for r in sectional_hits:
            print(f"\n  URL: {r['url']}")
            print(f"  Status: {r['status']}, Size: {r['size']}, Type: {r['content_type']}")
            print(f"  Preview:\n{r['preview'][:1500]}")
    else:
        print("\n  No responses with sectional keywords found")
    
    if race_data_hits:
        print(f"\n--- Race data responses ({len(race_data_hits)}) ---")
        for r in race_data_hits:
            print(f"\n  URL: {r['url']}")
            print(f"  Status: {r['status']}, Size: {r['size']}, Type: {r['content_type']}")
            print(f"  Preview:\n{r['preview'][:1000]}")
    
    if other_json:
        print(f"\n--- Other JSON responses ({len(other_json)}) ---")
        for r in other_json:
            print(f"  URL: {r['url'][:120]}")
            print(f"  Status: {r['status']}, Size: {r['size']}")
    
    return captured_responses


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else SAMPLE_URLS[0]
    results = discover_api(url)
    
    with open("/tmp/racing_com_discovery.json", "w") as f:
        safe_results = []
        for r in results:
            safe_results.append({
                "url": r["url"],
                "status": r["status"],
                "content_type": r["content_type"],
                "size": r["size"],
                "has_sectional": r["has_sectional"],
                "has_race_data": r["has_race_data"],
                "preview": r["preview"][:500],
            })
        json.dump(safe_results, f, indent=2)
    print(f"\nFull results saved to /tmp/racing_com_discovery.json")

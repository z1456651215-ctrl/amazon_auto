#!/usr/bin/env python3
"""Drive front Google Chrome on macOS via AppleScript.
Open Amazon.de search page, visit each product, print title.

Usage:
  python3 amazon_titles_front.py --query "klein pillendose" --limit 10
"""

from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from urllib.parse import quote_plus


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Front Chrome Amazon title collector (macOS)")
    p.add_argument("--query", default="klein pillendose")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--wait", type=float, default=4.0, help="initial page wait seconds")
    p.add_argument("--step-wait", type=float, default=2.5, help="per product wait seconds")
    return p.parse_args()


def run_osascript(script: str) -> str:
    proc = subprocess.run(
        ["osascript", "-"],
        input=script,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "osascript failed")
    return proc.stdout.strip()


def js_string_literal(js: str) -> str:
    return js.replace("\\", "\\\\").replace('"', '\\"')


def chrome_exec_js(js: str) -> str:
    js_escaped = js_string_literal(js)
    script = f'''
    tell application "Google Chrome"
        execute active tab of front window javascript "{js_escaped}"
    end tell
    '''
    return run_osascript(script)


def chrome_open_url(url: str) -> None:
    script = f'''
    tell application "Google Chrome"
        activate
        if (count of windows) = 0 then make new window
        set URL of active tab of front window to "{url}"
    end tell
    '''
    run_osascript(script)


def maybe_accept_cookie() -> None:
    js = (
        "(function(){"
        "var ids=['sp-cc-accept','a-autoid-0-announce'];"
        "for(var i=0;i<ids.length;i++){var b=document.getElementById(ids[i]); if(b){b.click(); return 'clicked';}}"
        "return 'skip';"
        "})();"
    )
    try:
        chrome_exec_js(js)
    except Exception:
        pass


def collect_links(limit: int) -> list[str]:
    js = f"""
    (function() {{
      var arr = Array.from(document.querySelectorAll("div.s-main-slot div[data-component-type='s-search-result'] h2 a"))
        .map(function(a) {{ return new URL(a.getAttribute('href'), location.origin).href.split('?')[0]; }});
      var dedup = Array.from(new Set(arr)).slice(0, {int(limit)});
      return JSON.stringify(dedup);
    }})();
    """
    out = chrome_exec_js(js)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def read_title() -> str:
    js = (
        "(function(){"
        "var t=document.querySelector('#productTitle');"
        "if(t&&t.textContent) return t.textContent.trim();"
        "return document.title || '';"
        "})();"
    )
    return chrome_exec_js(js)


def main() -> int:
    if platform.system() != "Darwin":
        print("This script requires macOS (AppleScript + Google Chrome).", file=sys.stderr)
        return 2

    args = parse_args()
    query = args.query.strip()
    if not query:
        print("query is empty", file=sys.stderr)
        return 2

    search_url = f"https://www.amazon.de/s?k={quote_plus(query)}"
    chrome_open_url(search_url)
    time.sleep(max(args.wait, 1.0))

    maybe_accept_cookie()
    time.sleep(1.0)

    links = collect_links(args.limit)
    if not links:
        print("No product links found. Check if cookie/login/captcha is blocking.")
        return 1

    print(f"Found {len(links)} products")
    for i, url in enumerate(links, start=1):
        chrome_open_url(url)
        time.sleep(max(args.step_wait, 1.0))
        title = read_title().strip() or "(title not found)"
        print(f"{i}. {title}")
        print(f"   {url}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Open Amazon.de, search keywords, enter each product page, and print:
title, url, bullets, description

Usage:
  python3 amazon_titles.py --query "klein pillendose" --limit 10
"""

from __future__ import annotations

import argparse
import csv
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import List
from urllib.parse import quote_plus

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, SessionNotCreatedException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Amazon.de product scraper (GUI Chrome)")
    parser.add_argument("--query", default="klein pillendose", help="Primary search keyword")
    parser.add_argument("--query2", default="", help="Secondary search keyword")
    parser.add_argument("--per-keyword-limit", type=int, default=5, help="Products per keyword")
    parser.add_argument("--limit", type=int, default=10, help="Total fallback limit when only one keyword is used")
    parser.add_argument("--wait", type=int, default=20, help="Timeout seconds")
    parser.add_argument("--out", default="", help="Output CSV path, e.g. result.csv")
    parser.add_argument("--min-delay", type=float, default=2.0, help="Min step delay seconds")
    parser.add_argument("--max-delay", type=float, default=5.0, help="Max step delay seconds")
    parser.add_argument(
        "--mode",
        choices=["headless", "visible"],
        default="headless",
        help="Browser mode: headless (background) or visible",
    )
    return parser.parse_args()


def step_sleep(min_delay: float, max_delay: float) -> None:
    low = min(min_delay, max_delay)
    high = max(min_delay, max_delay)
    time.sleep(random.uniform(low, high))


def start_driver(mode: str) -> webdriver.Chrome:
    local_cache = Path(__file__).resolve().parent / ".selenium_cache"
    local_cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("SE_CACHE_PATH", str(local_cache))

    opts = Options()
    if mode == "headless":
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1920,1080")
    else:
        opts.add_argument("--start-maximized")
        opts.add_experimental_option("detach", True)

    return webdriver.Chrome(options=opts)


def accept_cookie_if_present(driver: webdriver.Chrome) -> None:
    selectors = [
        (By.ID, "sp-cc-accept"),
        (By.ID, "a-autoid-0-announce"),
        (By.XPATH, "//input[@name='accept']"),
        (By.XPATH, "//button[contains(., 'Akzeptieren') or contains(., 'Accept')]"),
    ]
    for by, value in selectors:
        try:
            btn = WebDriverWait(driver, 3).until(EC.element_to_be_clickable((by, value)))
            btn.click()
            time.sleep(0.7)
            return
        except Exception:
            continue


def collect_product_links(driver: webdriver.Chrome, limit: int) -> List[str]:
    page_text = driver.page_source.lower()
    if "validatecaptcha" in page_text or "enter the characters you see below" in page_text:
        raise RuntimeError("Amazon captcha page detected. Please solve captcha in browser, then rerun.")

    selectors = [
        "div.s-main-slot div[data-component-type='s-search-result'] h2 a",
        "div.s-main-slot [data-asin] h2 a[href*='/dp/']",
        "a.a-link-normal.s-no-outline[href*='/dp/']",
        "a[href*='/dp/']",
    ]

    anchors = []
    for i, selector in enumerate(selectors):
        try:
            if i == 0:
                WebDriverWait(driver, 8).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
            anchors = driver.find_elements(By.CSS_SELECTOR, selector)
            if anchors:
                break
        except Exception:
            continue

    links: List[str] = []
    seen = set()
    asin_re = re.compile(r"/dp/([A-Z0-9]{10})", re.IGNORECASE)

    for a in anchors:
        href = a.get_attribute("href")
        if not href:
            continue

        clean = href.split("?")[0]
        m = asin_re.search(clean)
        if not m:
            continue

        asin = m.group(1).upper()
        clean = f"https://www.amazon.de/dp/{asin}"

        if clean in seen:
            continue
        seen.add(clean)
        links.append(clean)

        if len(links) >= limit:
            break

    return links


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).strip()


def read_product_detail_on_page(driver: webdriver.Chrome, url: str, timeout: int) -> dict:
    driver.execute_script("window.open(arguments[0], '_blank');", url)
    driver.switch_to.window(driver.window_handles[-1])

    title = "(title not found)"
    bullets: List[str] = []
    description = ""

    try:
        WebDriverWait(driver, timeout).until(
            EC.any_of(
                EC.presence_of_element_located((By.ID, "productTitle")),
                EC.title_contains("Amazon"),
            )
        )
    except TimeoutException:
        pass

    try:
        el = driver.find_element(By.ID, "productTitle")
        text = el.text.strip()
        if text:
            title = text
    except Exception:
        fallback = driver.title.strip()
        if fallback:
            title = fallback

    bullet_selectors = [
        "#feature-bullets ul li span.a-list-item",
        "#feature-bullets li",
    ]
    for selector in bullet_selectors:
        nodes = driver.find_elements(By.CSS_SELECTOR, selector)
        for node in nodes:
            text = _normalize_text(node.text)
            if text and text not in bullets:
                bullets.append(text)
        if bullets:
            break

    desc_selectors = [
        "#productDescription p",
        "#productDescription",
        "#aplus_feature_div",
        "#bookDescription_feature_div",
    ]
    for selector in desc_selectors:
        nodes = driver.find_elements(By.CSS_SELECTOR, selector)
        merged = _normalize_text(" ".join([n.text for n in nodes if n.text.strip()]))
        if merged:
            description = merged
            break

    driver.close()
    driver.switch_to.window(driver.window_handles[0])

    return {
        "title": title,
        "url": url,
        "bullets": bullets[:5],
        "description": description or "(description not found)",
    }


def write_csv(path_value: str, records: List[dict]) -> Path:
    out_path = Path(path_value).expanduser()
    if not out_path.is_absolute():
        out_path = Path.cwd() / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with out_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow([
            "title",
            "url",
            "bullet_1",
            "bullet_2",
            "bullet_3",
            "bullet_4",
            "bullet_5",
            "description",
        ])
        for r in records:
            bullets = (r.get("bullets") or [])[:5]
            bullets += [""] * (5 - len(bullets))
            writer.writerow([
                r.get("title", ""),
                r.get("url", ""),
                bullets[0],
                bullets[1],
                bullets[2],
                bullets[3],
                bullets[4],
                r.get("description", ""),
            ])

    return out_path


def print_result_overview(records: List[dict]) -> None:
    print("\n输出结果如下：")

    for idx, r in enumerate(records, start=1):
        print(f"\n=== 产品 {idx} ===")
        print(f"标题：{r.get('title', '')}")
        print(f"链接：{r.get('url', '')}")

        print("五点：")
        bullets = (r.get("bullets") or [])[:5]
        if bullets:
            for i, b in enumerate(bullets, start=1):
                print(f"{i}. {b}")
        else:
            print("(not found)")

        print("产品详细描述：")
        print(r.get("description", "") or "(description not found)")


def main() -> int:
    args = parse_args()
    query1 = args.query.strip()
    query2 = args.query2.strip()

    if not query1:
        print("Query is empty.", file=sys.stderr)
        return 2

    queries = [query1]
    if query2:
        queries.append(query2)

    try:
        driver = start_driver(args.mode)
    except SessionNotCreatedException as e:
        print("Chrome session failed to start.", file=sys.stderr)
        print("Please close all Chrome windows and run again.", file=sys.stderr)
        print("If it still fails, update Chrome and rerun in a local terminal.", file=sys.stderr)
        print(f"Details: {e}", file=sys.stderr)
        return 3

    records = []

    try:
        for qi, query in enumerate(queries, start=1):
            limit = args.per_keyword_limit if len(queries) > 1 else args.limit
            search_url = f"https://www.amazon.de/s?k={quote_plus(query)}"

            print(f"\n=== Keyword {qi}/{len(queries)}: {query} ===")
            driver.get(search_url)
            step_sleep(args.min_delay, args.max_delay)

            if qi == 1:
                accept_cookie_if_present(driver)
                step_sleep(args.min_delay, args.max_delay)

            try:
                links = collect_product_links(driver, limit)
            except RuntimeError as e:
                print(str(e), file=sys.stderr)
                return 1

            if not links:
                print(f"No product links found for keyword: {query}")
                continue

            print(f"Found {len(links)} product links for keyword: {query}")
            if len(links) < limit:
                print(f"Warning: requested {limit}, got {len(links)}.")

            for idx, link in enumerate(links, start=1):
                step_sleep(args.min_delay, args.max_delay)
                item = read_product_detail_on_page(driver, link, args.wait)
                item["keyword"] = query
                print(f"{query} | {idx}. {item['title']}")
                records.append(item)

        if not records:
            print("No reference products collected for all keywords.", file=sys.stderr)
            return 1

        if args.out:
            out_path = write_csv(args.out, records)
            print(f"CSV saved: {out_path}")

        print_result_overview(records)
        print("\nDone.")
        return 0

    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
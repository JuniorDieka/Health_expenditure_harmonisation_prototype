"""Capture screenshots of the running Streamlit app for the README.

Usage: start `streamlit run app.py`, then `python docs/capture_screenshots.py`.
Saves PNGs into docs/screenshots/.
"""

import time
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8599"
OUT = Path(__file__).parent / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

TABS = [
    ("Overview", "01_overview"),
    ("Transactions", "02_transactions"),
    ("Review queue", "03_review_queue"),
    ("Data quality", "04_data_quality"),
    ("Record detail", "05_record_detail"),
]

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1500, "height": 950})
    page.goto(BASE, wait_until="networkidle")
    time.sleep(3)

    for label, name in TABS:
        page.click(f'button[role="tab"]:has-text("{label}")')
        page.wait_for_load_state("networkidle")
        time.sleep(2.5)

        if name == "05_record_detail":
            # pick the first record so the detail panels are populated
            page.wait_for_selector('[data-testid="stSelectbox"]:visible', timeout=15000)
            time.sleep(1)
            page.locator('[data-testid="stSelectbox"]:visible').first.click()
            page.wait_for_selector('[role="option"]', timeout=10000)
            page.locator('[role="option"]').first.click()
            try:
                page.wait_for_selector("text=Where did it come from?", timeout=30000)
            except Exception:
                pass
            time.sleep(4)

        page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
        print(f"saved {name}.png")

    browser.close()
print("done")

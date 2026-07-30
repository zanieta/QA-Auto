"""Quick visual smoke test — logs in, takes a screenshot, prints the path."""
import asyncio, base64, os, sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_URL  = os.environ["APP_BASE_URL"].rstrip("/")
USERNAME  = os.environ["APP_USERNAME"]
PASSWORD  = os.environ["APP_PASSWORD"]
OUT       = Path(__file__).resolve().parent.parent / "reports" / "smoke.png"


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        page    = await browser.new_page()

        print(f"[1/4] navigating to {BASE_URL}")
        await page.goto(BASE_URL, wait_until="domcontentloaded")
        await page.screenshot(path=str(OUT.with_stem("smoke_1_login")))

        print("[2/4] filling credentials")
        await page.fill('input[placeholder="Email address"]', USERNAME)
        await page.fill('input[type="password"]', PASSWORD)
        await page.screenshot(path=str(OUT.with_stem("smoke_2_filled")))

        print("[3/4] clicking Sign In")
        await page.click('button:has-text("Sign In")')
        await page.wait_for_url(lambda u: "/login" not in u, timeout=20_000)
        print(f"      landed at: {page.url}")

        print("[4/4] screenshot of dashboard")
        await page.screenshot(path=str(OUT.with_stem("smoke_3_dashboard")), full_page=False)

        await browser.close()
        print(f"\nDone. Screenshots in: {OUT.parent}")


asyncio.run(main())

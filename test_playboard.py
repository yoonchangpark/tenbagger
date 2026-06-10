import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="ko-KR",
            viewport={"width": 1920, "height": 1080}
        )
        print("Navigating to a specific channel videos...")
        await page.goto("https://playboard.co/channel/UCaJdckl6MBdDPDf75Ec_bJA/videos", wait_until="networkidle")
        await page.wait_for_timeout(5000)
        
        await page.screenshot(path="debug_screenshot.png")
        print("Screenshot saved to debug_screenshot.png")
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())

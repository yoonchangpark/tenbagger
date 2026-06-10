import asyncio
from playwright.async_api import async_playwright

async def get_recent_stock_videos():
    """주식/투자 인기 채널 Top 10의 최근 영상 제목을 반환합니다."""
    print("Scraping Playboard for Top 10 Stock/Investment Channels...")
    results = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        # Using a typical user agent
        page = await browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="ko-KR"
        )
        
        # 1. 인기 채널 랭킹으로 이동
        await page.goto("https://playboard.co/youtube-ranking/most-popular-stocks-channels-in-south-korea-daily", wait_until="networkidle")
        
        # 모든 채널 링크 추출
        channel_links = await page.evaluate('''() => {
            let links = Array.from(document.querySelectorAll('a[href^="/channel/"]'));
            // 중복 제거 및 상위 10개 추출
            let unique_hrefs = [...new Set(links.map(a => a.href))];
            return unique_hrefs.slice(0, 10);
        }''')
        
        print(f"Found Top 10 Channels. Extracting recent videos...")
        
        # 2. Extract videos via YouTube RSS using Channel ID
        import xml.etree.ElementTree as ET
        import urllib.request
        
        for link in channel_links:
            channel_id = link.split('/channel/')[1]
            rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            try:
                # Fetch RSS synchronously using urllib in the async task for simplicity
                req = urllib.request.Request(rss_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req) as response:
                    xml_data = response.read()
                    
                root = ET.fromstring(xml_data)
                
                # Namespaces
                ns = {'atom': 'http://www.w3.org/2005/Atom', 'yt': 'http://www.youtube.com/xml/schemas/2015'}
                
                entries = root.findall('atom:entry', ns)
                video_data = []
                for entry in entries[:3]: # 최신 영상 3개
                    title_elem = entry.find('atom:title', ns)
                    link_elem = entry.find('atom:link', ns)
                    
                    if title_elem is not None and link_elem is not None:
                        video_data.append({
                            "title": title_elem.text,
                            "url": link_elem.attrib['href']
                        })
                
                results.extend(video_data)
                print(f"[{channel_id}] -> {len(video_data)} videos collected via RSS")
            except Exception as e:
                print(f"Error accessing RSS for {channel_id}: {e}")
                
        await browser.close()
        
    return results

if __name__ == "__main__":
    import json
    videos = asyncio.run(get_recent_stock_videos())
    with open("scraped_videos.json", "w", encoding="utf-8") as f:
        json.dump(videos, f, ensure_ascii=False, indent=2)
    print("Scraping complete. Saved to scraped_videos.json")

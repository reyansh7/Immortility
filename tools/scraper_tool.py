from tools.browser_manager import BrowserManager


class ScraperTool:
    """Tool for extracting content from web pages."""

    @staticmethod
    async def scrape_page(url: str = "") -> dict:
        """Retrieve raw HTML from URL or current page."""
        manager = BrowserManager()
        page = await manager.get_page()

        if url:
            if not url.startswith("http"):
                url = "https://" + url
            await page.goto(url)
            await page.wait_for_timeout(2000)

        html = await page.content()
        await manager.sync_state()
        return {"status": "success", "length": len(html), "content": html, "url": page.url}

    @staticmethod
    async def get_page_text() -> dict:
        """Extract visible readable text from the current page."""
        manager = BrowserManager()
        page = await manager.get_page()
        text = await page.evaluate("document.body.innerText")
        await manager.sync_state()
        return {"status": "success", "length": len(text), "text": text, "url": page.url}

    # Backward-compatible aliases
    get_page_content = scrape_page
    extract_visible_text = get_page_text

from tools.browser_manager import BrowserManager
import os

class BrowserTool:
    """Provides general browser navigation and interaction capabilities."""
    
    @staticmethod
    async def open_url(url: str) -> dict:
        """Navigates the browser to the specified URL."""
        manager = BrowserManager()
        page = await manager.get_page()
        
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
            
        await page.goto(url)
        await page.wait_for_timeout(2000)
        await manager.sync_state()
        return {"status": "success", "url": url, "message": f"Successfully navigated to {url}"}

    @staticmethod
    async def click_element(selector: str) -> dict:
        """Clicks an element matching the given selector on the page."""
        manager = BrowserManager()
        page = await manager.get_page()
        await page.click(selector)
        await page.wait_for_timeout(1000)
        return {"status": "success", "message": f"Clicked element matching '{selector}'"}

    @staticmethod
    async def fill_input(selector: str, text: str) -> dict:
        """Fills a text input matching the given selector."""
        manager = BrowserManager()
        page = await manager.get_page()
        await page.fill(selector, text)
        return {"status": "success", "message": f"Filled '{text}' into '{selector}'"}

    @staticmethod
    async def take_screenshot(path: str) -> dict:
        """Takes a screenshot of the current page and saves it to path."""
        manager = BrowserManager()
        page = await manager.get_page()
        await page.screenshot(path=path)
        return {"status": "success", "message": f"Screenshot saved to {path}"}

    @staticmethod
    async def download_file(url: str, destination: str) -> dict:
        """Downloads a file by navigating to a URL that triggers a download."""
        manager = BrowserManager()
        page = await manager.get_page()
        
        async with page.expect_download() as download_info:
            await page.goto(url)
        download = await download_info.value
        await download.save_as(destination)
        
        return {"status": "success", "message": f"Downloaded file to {destination}"}

    @staticmethod
    async def get_current_url() -> dict:
        """Returns the URL of the currently active page."""
        manager = BrowserManager()
        page = await manager.get_page()
        url = page.url
        return {"status": "success", "url": url}

    @staticmethod
    async def get_page_title() -> dict:
        """Returns the title of the currently active page."""
        manager = BrowserManager()
        page = await manager.get_page()
        title = await page.title()
        return {"status": "success", "title": title}

    @staticmethod
    async def list_tabs() -> dict:
        """Returns a list of URLs of all open tabs."""
        manager = BrowserManager()
        context = await manager.get_context()
        tabs = []
        for i, p in enumerate(context.pages):
            tabs.append({"index": i, "url": p.url, "title": await p.title()})
        return {"status": "success", "tabs": tabs}

    @staticmethod
    async def switch_tab(index: int) -> dict:
        """Switches the active tab to the specified index."""
        manager = BrowserManager()
        context = await manager.get_context()
        if 0 <= index < len(context.pages):
            await manager.set_active_page(context.pages[index])
            return {"status": "success", "message": f"Switched to tab {index}"}
        return {"status": "error", "message": f"Invalid tab index: {index}"}

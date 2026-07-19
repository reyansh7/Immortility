import asyncio
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from core.agent_state import AgentState


class BrowserManager:
    """
    Singleton managing a persistent Playwright browser session.
    Supports CDP mode (existing browser on :9222) or standalone Chromium.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._playwright = None
            cls._instance._browser: Browser | None = None
            cls._instance._context: BrowserContext | None = None
            cls._instance._page: Page | None = None
            cls._instance._cdp_mode = False
        return cls._instance

    @property
    def browser(self) -> Browser | None:
        return self._browser

    @property
    def context(self) -> BrowserContext | None:
        return self._context

    @property
    def current_page(self) -> Page | None:
        return self._page

    @property
    def current_url(self) -> str:
        if self._page:
            try:
                return self._page.url
            except Exception:
                return ""
        return ""

    @property
    def open_tabs(self) -> list[dict]:
        if not self._context:
            return []
        return [{"index": i, "url": p.url} for i, p in enumerate(self._context.pages)]

    async def _is_page_alive(self) -> bool:
        if not self._page:
            return False
        try:
            await self._page.evaluate("1")
            return True
        except Exception:
            return False

    async def init(self, headless: bool = False) -> None:
        if self._playwright and await self._is_page_alive():
            return

        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

        self._playwright = await async_playwright().start()

        try:
            self._browser = await self._playwright.chromium.connect_over_cdp(
                "http://127.0.0.1:9222", timeout=3000
            )
            self._cdp_mode = True
            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
                pages = self._context.pages
                self._page = pages[0] if pages else await self._context.new_page()
            else:
                self._context = await self._browser.new_context()
                self._page = await self._context.new_page()
        except Exception:
            self._cdp_mode = False
            self._browser = await self._playwright.chromium.launch(headless=headless)
            self._context = await self._browser.new_context()
            self._page = await self._context.new_page()

        await self.sync_state()

    async def get_page(self) -> Page:
        if not await self._is_page_alive():
            self._playwright = None
            await self.init()
        return self._page

    async def get_context(self) -> BrowserContext:
        if not await self._is_page_alive():
            self._playwright = None
            await self.init()
        return self._context

    async def set_active_page(self, page: Page) -> None:
        self._page = page
        await self.sync_state()

    async def list_tabs(self) -> list[dict]:
        context = await self.get_context()
        tabs = []
        for i, p in enumerate(context.pages):
            try:
                title = await p.title()
            except Exception:
                title = ""
            tabs.append({"index": i, "url": p.url, "title": title})
        return tabs

    async def sync_state(self) -> None:
        """Persist browser snapshot into AgentState."""
        tabs = []
        if self._context:
            for i, p in enumerate(self._context.pages):
                try:
                    title = await p.title()
                except Exception:
                    title = ""
                tabs.append({"index": i, "url": p.url, "title": title})

        AgentState().update_browser_state({
            "current_url": self.current_url,
            "open_tabs": tabs,
        })

    async def is_cdp(self) -> bool:
        await self.init()
        return self._cdp_mode

    async def close(self) -> None:
        try:
            if self._browser and not self._cdp_mode:
                await self._browser.close()
            if self._playwright:
                await self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._playwright = None
        self._context = None
        self._page = None

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

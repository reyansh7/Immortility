from tools.browser_manager import BrowserManager
import urllib.parse

class SearchTool:
    """Tool for performing web searches via DuckDuckGo (HTML version) to avoid CAPTCHAs."""

    @staticmethod
    async def search_google(query: str) -> dict:
        """Search the web for the given query and return top results."""
        manager = BrowserManager()
        page = await manager.get_page()
        
        encoded_query = urllib.parse.quote(query)
        await page.goto(f"https://html.duckduckgo.com/html/?q={encoded_query}")
        await page.wait_for_timeout(2000)
        await manager.sync_state()
        
        results = await page.evaluate('''() => {
            const items = [];
            document.querySelectorAll('.result').forEach(el => {
                const titleEl = el.querySelector('.result__title a');
                const snippetEl = el.querySelector('.result__snippet');
                if (titleEl) {
                    // Extract the actual URL from DuckDuckGo's redirect format
                    let rawUrl = titleEl.getAttribute('href');
                    let actualUrl = rawUrl;
                    if (rawUrl && rawUrl.includes('uddg=')) {
                        try {
                            const params = new URLSearchParams(rawUrl.split('?')[1]);
                            if (params.has('uddg')) {
                                actualUrl = decodeURIComponent(params.get('uddg'));
                            }
                        } catch (e) {}
                    }
                    
                    items.push({
                        title: titleEl.innerText.trim(),
                        url: actualUrl,
                        snippet: snippetEl ? snippetEl.innerText.trim() : ""
                    });
                }
            });
            return items.slice(0, 5); // Return top 5
        }''')
        
        return {
            "status": "success",
            "query": query,
            "results": results
        }

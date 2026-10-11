from app.core.errors import ConfigurationError


class Serper:
    def __init__(self, settings, http, budget, trace, credit_preflight=None):
        self.settings, self.http, self.budget, self.trace = settings, http, budget, trace
        self.credit_preflight = credit_preflight
        self.credit_checked = False

    async def search(self, query: str, news: bool = False) -> list[dict]:
        key = self.settings.serper_api_key.get_secret_value()
        if not key:
            raise ConfigurationError("SERPER_API_KEY is not configured")
        if self.credit_preflight and not self.credit_checked:
            self.budget.check()
            await self.trace.reserve(self.budget)
            await self.credit_preflight()
            self.credit_checked = True
        self.budget.take_search()
        await self.trace.reserve(self.budget)
        async with self.trace.tool("news_search" if news else "web_search"):
            response = await self.http.post(
                "https://google.serper.dev/" + ("news" if news else "search"),
                headers={"X-API-KEY": key},
                json={
                    "q": query[:500],
                    "num": 10,
                    "gl": self.settings.serper_gl,
                    "hl": self.settings.serper_hl,
                },
            )
            response.raise_for_status()
            data = response.json()
            return [
                {
                    "url": r["link"],
                    "title": r.get("title", ""),
                    "published_at": r.get("date"),
                    "search_date": r.get("date"),
                    "snippet": r.get("snippet", ""),
                }
                for r in data.get("news" if news else "organic", [])
                if r.get("link")
            ]

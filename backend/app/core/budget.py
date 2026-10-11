import time
from dataclasses import dataclass, field

from app.core.config import Settings
from app.core.errors import BudgetExceeded


@dataclass
class Budget:
    settings: Settings
    deep: bool = False
    searches: int = 0
    pages: int = 0
    model_calls: int = 0
    tokens_reserved: int = 0
    cost_reserved: float = 0
    started: float = field(default_factory=time.monotonic)

    def check(self):
        if time.monotonic() - self.started > self.settings.run_timeout_seconds:
            raise BudgetExceeded("Run time limit reached")

    def take_search(self):
        self.check()
        limit = self.settings.deep_max_searches if self.deep else self.settings.max_searches
        if self.searches >= limit:
            raise BudgetExceeded("Search limit reached")
        self.searches += 1

    def take_page(self):
        self.check()
        limit = self.settings.deep_max_pages if self.deep else self.settings.max_pages
        if self.pages >= limit:
            raise BudgetExceeded("Page limit reached")
        self.pages += 1

    def reserve_model(self, input_text: str, output_tokens: int):
        self.check()
        # UTF-8 byte count is deliberately conservative for unknown tokenizers.
        tokens = len(input_text.encode("utf-8")) + output_tokens + 256
        cost = tokens * self.settings.model_price_ceiling_per_million / 1_000_000
        if (
            self.model_calls >= self.settings.max_model_calls
            or self.tokens_reserved + tokens > self.settings.max_total_tokens
            or self.cost_reserved + cost > self.settings.max_run_cost_usd
        ):
            raise BudgetExceeded("Model token/cost/call limit reached")
        self.model_calls += 1
        self.tokens_reserved += tokens
        self.cost_reserved += cost

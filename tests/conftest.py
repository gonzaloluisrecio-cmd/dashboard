import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import WidgetConfig  # noqa: E402
from app.widgets import Context  # noqa: E402

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


class FakeSummarizer:
    available = True

    def __init__(self):
        self.calls = []

    async def summarize(self, instructions, content):
        self.calls.append((instructions, content))
        return "- summary bullet"


@pytest.fixture
async def ctx():
    async with httpx.AsyncClient() as http:
        yield Context(http=http, tz=TZ, summarizer=FakeSummarizer())


def make(cls, ctx, **options):
    return cls(WidgetConfig(id="w", type=cls.type_name, title="W", options=options), ctx)

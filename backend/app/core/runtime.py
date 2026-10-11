import asyncio
import sys


def event_loop():
    # Psycopg async cannot use Windows' default Proactor loop.
    return asyncio.SelectorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()


def api_main():
    import uvicorn

    from app.core.config import Settings

    settings = Settings()
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        loop="app.core.runtime:event_loop",
        access_log=False,
    )

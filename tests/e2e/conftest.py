import socket
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import uvicorn

import analytics
import main
from tests.dashboard_fixtures import dashboard_db, use_local_desk_writes


@pytest.fixture(scope="session")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as driver:
        browser = driver.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def dashboard(monkeypatch):
    db = dashboard_db()
    use_local_desk_writes(db, monkeypatch)
    deliveries = []
    monkeypatch.setattr(main, "firestore_db", db)
    monkeypatch.setitem(main.app.dependency_overrides, main.get_db, lambda: db)
    monkeypatch.setattr(main.LLM, "get_provider", lambda: None)
    monkeypatch.setattr(main, "TWILIO_ACCOUNT_SID", "AC-test")
    monkeypatch.setattr(main, "TWILIO_AUTH_TOKEN", "test-only")
    monkeypatch.setattr(main, "TWILIO_WHATSAPP_NUMBER", "whatsapp:+15005550006")
    monkeypatch.setattr(main, "Client", lambda *args: SimpleNamespace(
        messages=SimpleNamespace(create=lambda **data: deliveries.append(data)),
    ))
    analytics.invalidate_snapshot()

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(main.app, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started, "Local UrbanOS fixture failed to start"
        yield SimpleNamespace(url=f"http://127.0.0.1:{port}", db=db, deliveries=deliveries)
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        analytics.invalidate_snapshot()


@pytest.fixture
def desktop(browser, dashboard):
    contexts = []
    errors = []
    assets = {"cdn.tailwindcss.com", "cdn.jsdelivr.net", "cdnjs.cloudflare.com", "unpkg.com", "fonts.googleapis.com", "fonts.gstatic.com"}

    def open_page(width=1440, height=810):
        context = browser.new_context(viewport={"width": width, "height": height}, service_workers="block",
                                      reduced_motion="reduce", ignore_https_errors=True)
        contexts.append(context)

        def isolate(route):
            request = route.request
            if request.url.startswith(dashboard.url + "/"):
                route.continue_()
            elif request.method == "GET" and urlsplit(request.url).hostname in assets:
                route.continue_()
            else:
                route.abort()

        context.route("**/*", isolate)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(dashboard.url, wait_until="domcontentloaded")
        return page

    yield open_page
    for context in contexts:
        context.close()
    assert not errors, "Browser errors: " + "; ".join(errors)

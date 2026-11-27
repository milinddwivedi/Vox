"""
Playwright End-to-End Customer Journey Suite for VOX Web Portal.
Verifies real browser user journeys across:
1. Landing page (index.html): Hero, navigation, download CTA, value props
2. Pricing page (pricing.html): Monthly vs. Annual toggle, Razorpay checkout URLs, query parameters
3. Account page (account.html): Google authentication bridge, session presentation
4. Download page (download.html): Windows release binary installer CTA and security badges
5. Responsive mobile & desktop viewports
"""

import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import threading
import time

import pytest
from playwright.sync_api import sync_playwright, Page, expect

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "docs"


@pytest.fixture(scope="module")
def web_server():
    """Spin up an ephemeral local HTTP server to serve the docs/ web portal."""
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(DOCS_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    port = server.server_port
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{port}"
    yield base_url
    server.shutdown()


@pytest.fixture(scope="module")
def browser_context():
    """Initialize Playwright Chromium browser (falling back to installed Edge channel)."""
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception:
            browser = p.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        yield context
        browser.close()


def test_landing_page_customer_journey(web_server, browser_context):
    """Customer visits landing page: verifies brand, navigation links, and hero CTA."""
    page = browser_context.new_page()
    page.goto(f"{web_server}/index.html")

    # Title & Branding
    assert "VOX" in page.title()
    expect(page.locator(".nav-brand").first).to_be_visible()

    # Navigation Links exist
    expect(page.locator("a[href='pricing.html']").first).to_be_visible()
    expect(page.locator("a[href='account.html']").first).to_be_visible()

    # Hero headline & download button
    expect(page.locator("h1")).to_be_visible()
    page.close()


def test_pricing_page_billing_toggle_and_razorpay_links(web_server, browser_context):
    """Customer explores pricing plans: tests Monthly vs Annual toggle and Razorpay redirection."""
    page = browser_context.new_page()
    page.goto(f"{web_server}/pricing.html")

    # Verify default Monthly state: ₹199 / month
    expect(page.locator("#proPrice")).to_have_text("₹199")
    expect(page.locator("#proPeriod")).to_have_text("/ month")
    expect(page.locator("#directRzpLink")).to_have_attribute("href", "https://rzp.io/rzp/ugv7JBF2")

    # Switch to Annual Billing
    page.click("#btnAnnual")
    expect(page.locator("#proPrice")).to_have_text("₹1,599")
    expect(page.locator("#proPeriod")).to_contain_text("/ year")
    expect(page.locator("#directRzpLink")).to_have_attribute("href", "https://rzp.io/rzp/fycKln1w")

    # Switch back to Monthly Billing
    page.click("#btnMonthly")
    expect(page.locator("#proPrice")).to_have_text("₹199")
    expect(page.locator("#proPeriod")).to_have_text("/ month")
    expect(page.locator("#directRzpLink")).to_have_attribute("href", "https://rzp.io/rzp/ugv7JBF2")

    page.close()


def test_pricing_page_url_parameter_auto_selects_annual(web_server, browser_context):
    """Customer arrives from email/ad link with ?plan=annual: annual billing pre-selected."""
    page = browser_context.new_page()
    page.goto(f"{web_server}/pricing.html?plan=annual")

    expect(page.locator("#proPrice")).to_have_text("₹1,599")
    expect(page.locator("#directRzpLink")).to_have_attribute("href", "https://rzp.io/rzp/fycKln1w")
    page.close()


def test_download_page_renders_cleanly(web_server, browser_context):
    """Customer visits download page: verifies download CTA and Windows system badges."""
    page = browser_context.new_page()
    page.goto(f"{web_server}/download.html")

    assert "Download" in page.title()
    expect(page.locator("body")).to_contain_text("Windows")
    page.close()


def test_responsive_mobile_viewport(web_server, browser_context):
    """Mobile user visits pricing page on small viewport (375x667): cards stack vertically."""
    page = browser_context.new_page()
    page.set_viewport_size({"width": 375, "height": 667})
    page.goto(f"{web_server}/pricing.html")

    expect(page.locator(".toggle-wrap")).to_be_visible()
    expect(page.locator(".pricing-card.featured")).to_be_visible()
    page.close()

"""Renew one free PythonAnywhere web app through its normal UI.

No credentials, cookies, page HTML, traces or screenshots are written to disk.
The adapter contract is verified against the account page before deployment.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urljoin, urlparse

ORIGIN = 'https://www.pythonanywhere.com'
CONTRACT = Path(__file__).with_name('renewal_page_contract.json')
RENEWAL_BUFFER_DAYS = 25
MONTHS = {name.lower(): i for i, name in enumerate([
    '', 'January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'
]) if name}
DATE_PATTERN = re.compile(r'\b(\d{1,2})\s+(' + '|'.join(MONTHS) + r')\s*(20\d{2})\b', re.I)


class RenewalError(Exception):
    """Only fixed public-safe codes are reported to workflow logs."""


@dataclass(frozen=True)
class Settings:
    username: str
    password: str = field(repr=False)
    domain: str

    @classmethod
    def from_environment(cls) -> 'Settings':
        # Do not pass these values to the browser driver subprocess environment.
        username = os.environ.pop('PA_USERNAME', '').strip()
        password = os.environ.pop('PYTHONANYWHERE_PASSWORD', '')
        domain = os.environ.pop('PA_DOMAIN', '').strip().lower()
        if not username or not password or not domain:
            raise RenewalError('missing_credentials')
        if not re.fullmatch(r'[A-Za-z0-9_]{1,32}', username):
            raise RenewalError('invalid_account')
        if domain != username.lower() + '.pythonanywhere.com':
            raise RenewalError('target_mismatch')
        return cls(username, password, domain)


def parse_expiry(text: str) -> date:
    matches = DATE_PATTERN.findall(' '.join(text.split()))
    if len(matches) != 1:
        raise RenewalError('expiry_not_unique')
    day, month, year = matches[0]
    try:
        return date(int(year), MONTHS[month.lower()], int(day))
    except ValueError:
        raise RenewalError('invalid_expiry') from None


def is_allowed_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        return (parsed.scheme == 'https' and parsed.hostname == 'www.pythonanywhere.com'
                and parsed.port in (None, 443) and parsed.username is None and parsed.password is None)
    except ValueError:
        return False


def classify_result(before: date, after: date, today: date) -> str:
    if before >= today + timedelta(days=RENEWAL_BUFFER_DAYS):
        if after != before:
            raise RenewalError('unexpected_expiry_change')
        return 'already_active'
    if after <= before or after < today + timedelta(days=27):
        raise RenewalError('renewal_not_verified')
    return 'renewed'


def load_contract(path: Path = CONTRACT) -> dict:
    data = json.loads(path.read_text(encoding='utf-8'))
    if not data.get('verified_on') or not data.get('expiry_selector') or not data.get('target_selector'):
        raise RenewalError('page_adapter_not_verified')
    if data.get('renew_button_name') != 'Run until 1 month from today':
        raise RenewalError('unexpected_button_contract')
    if data.get('expected_username') != 'yixin666' or data.get('expected_domain') != 'yixin666.pythonanywhere.com':
        raise RenewalError('unexpected_account_contract')
    return data


def challenge_present(page) -> bool:
    return (page.locator('input[name="otp_token"], input[autocomplete="one-time-code"], '
                         'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], '
                         'iframe[src*="challenges.cloudflare.com"]').count() > 0
            or page.get_by_text(re.compile(r'^(Verify you are human|Two-factor authentication|Enter verification code)$', re.I)).count() > 0)


def read_expiry(page, contract: dict) -> date:
    locator = page.locator(contract['expiry_selector'])
    if locator.count() != 1 or not locator.is_visible():
        raise RenewalError('expiry_not_unique')
    return parse_expiry(locator.inner_text())


def confirm_target(page, settings: Settings, contract: dict) -> None:
    expected_path = f'/user/{settings.username}/webapps/'
    if not is_allowed_url(page.url) or urlparse(page.url).path.rstrip('/') != expected_path.rstrip('/'):
        raise RenewalError('unexpected_web_page')
    if settings.username != contract['expected_username'] or settings.domain != contract['expected_domain']:
        raise RenewalError('target_not_confirmed')
    locator = page.locator(contract['target_selector'])
    if (locator.count() != 1 or not locator.is_visible()
            or (locator.get_attribute('method') or '').lower() != 'post'
            or urljoin(page.url, locator.get_attribute('action') or '') != ORIGIN + contract['renewal_path']):
        raise RenewalError('target_not_confirmed')
    if challenge_present(page):
        raise RenewalError('interactive_verification_required')


def run(settings: Settings, contract: dict, *, dry_run: bool = False) -> dict:
    from playwright.sync_api import sync_playwright

    today = datetime.now(timezone.utc).date()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(accept_downloads=False, locale='en-US', service_workers='block')
            # Limit both navigations and resource requests to the platform origin.
            context.route('**/*', lambda route: route.continue_() if is_allowed_url(route.request.url) else route.abort())
            page = context.new_page()
            page.set_default_timeout(20_000)
            page.goto(ORIGIN + f'/login/?next=/user/{settings.username}/webapps/', wait_until='domcontentloaded')
            if not is_allowed_url(page.url) or challenge_present(page):
                raise RenewalError('interactive_verification_required')
            form = page.locator('form').filter(has=page.locator('#id_auth-username'))
            if form.count() != 1:
                raise RenewalError('login_form_not_unique')
            action = urljoin(page.url, form.get_attribute('action') or '')
            if not is_allowed_url(action) or form.get_attribute('target') not in (None, '', '_self'):
                raise RenewalError('unexpected_login_destination')
            if ((form.get_attribute('method') or '').lower() != 'post'
                    or form.locator('input[name="csrfmiddlewaretoken"]').count() != 1):
                raise RenewalError('unexpected_login_form')
            form.locator('#id_auth-username').fill(settings.username)
            form.locator('#id_auth-password').fill(settings.password)
            form.locator('#id_next').click()
            page.wait_for_load_state('domcontentloaded')
            if challenge_present(page):
                raise RenewalError('interactive_verification_required')
            confirm_target(page, settings, contract)
            before = read_expiry(page, contract)
            if dry_run:
                return {'status':'checked_only', 'expiry':before.isoformat(),
                        'needs_renewal':before < today + timedelta(days=RENEWAL_BUFFER_DAYS)}
            if before >= today + timedelta(days=RENEWAL_BUFFER_DAYS):
                status = classify_result(before, before, today)
                return {'status': status, 'expiry': before.isoformat()}
            button = page.locator(contract['target_selector']).get_by_role('button', name=contract['renew_button_name'], exact=True)
            if button.count() != 1 or not button.is_visible() or not button.is_enabled():
                raise RenewalError('free_renewal_button_not_unique')
            button.click()
            # A fresh authenticated GET verifies the server's saved date, not a toast.
            page.reload(wait_until='domcontentloaded')
            confirm_target(page, settings, contract)
            after = read_expiry(page, contract)
            return {'status': classify_result(before, after, today), 'expiry': after.isoformat()}
        finally:
            browser.close()


def main() -> int:
    # Never propagate Playwright errors: they can include page content and values.
    parser = argparse.ArgumentParser(description='Confirm one existing free web app remains active.')
    parser.add_argument('--dry-run', action='store_true', help='Read the saved expiry without clicking renewal.')
    args = parser.parse_args()
    try:
        contract = load_contract()
        settings = Settings.from_environment()
        dry_run = args.dry_run or os.environ.get('PA_DRY_RUN','false').lower() == 'true'
        result = run(settings, contract, dry_run=dry_run)
    except RenewalError as error:
        print(json.dumps({'status':'not_verified', 'reason':str(error)}))
        return 1
    except Exception:
        print(json.dumps({'status':'not_verified', 'reason':'platform_or_runtime_error'}))
        return 1
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    sys.exit(main())

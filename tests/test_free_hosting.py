"""Offline safety rules; never starts a browser or reads real credentials."""
from datetime import date
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

path = Path(__file__).resolve().parents[1] / 'deploy/renew_free_hosting.py'
spec = importlib.util.spec_from_file_location('renew_free_hosting', path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class RenewalRulesTests(unittest.TestCase):
    def test_date_and_ambiguity(self):
        self.assertEqual(module.parse_expiry('Saturday 05 December2026'), date(2026,12,5))
        self.assertEqual(module.parse_expiry('Your web app will be disabled on Saturday 05 December 2026.'), date(2026,12,5))
        for text in ['no date', '05 December2026; 06 December2026', '31 February 2026']:
            with self.assertRaises(module.RenewalError):
                module.parse_expiry(text)

    def test_target_origin_guard(self):
        self.assertTrue(module.is_allowed_url('https://www.pythonanywhere.com/login/'))
        for url in ['http://www.pythonanywhere.com/', 'https://www.pythonanywhere.com.evil.test/',
                    'https://user@www.pythonanywhere.com/', 'https://www.pythonanywhere.com:444/',
                    'https://eu.pythonanywhere.com/', 'https://www.pythonanywhere.com:invalid/']:
            self.assertFalse(module.is_allowed_url(url))

    def test_server_date_must_extend_and_have_margin(self):
        today = date(2026,11,15)
        before = date(2026,12,5)
        self.assertEqual(module.classify_result(before,date(2026,12,15),today), 'renewed')
        for after in [before, date(2026,12,6)]:
            with self.assertRaises(module.RenewalError):
                module.classify_result(before,after,today)

    def test_already_active_does_not_require_click(self):
        today = date(2026,11,5)
        before = date(2026,12,5)
        self.assertEqual(module.classify_result(before,before,today), 'already_active')
        with self.assertRaises(module.RenewalError):
            module.classify_result(before,date(2026,12,6),today)

    def test_unverified_adapter_stops_before_login(self):
        with tempfile.TemporaryDirectory() as temporary:
            unverified = Path(temporary)/'contract.json'
            unverified.write_text('{"verified_on":null}')
            with self.assertRaisesRegex(module.RenewalError, 'page_adapter_not_verified'):
                module.load_contract(unverified)

    def test_contract_matches_observed_target(self):
        contract = module.load_contract()
        self.assertEqual(contract['renewal_path'], '/user/yixin666/webapps/yixin666.pythonanywhere.com/extend')
        settings = module.Settings('yixin666', 'not-a-real-password', 'yixin666.pythonanywhere.com')
        self.assertNotIn('not-a-real-password', repr(settings))


if __name__ == '__main__':
    unittest.main(verbosity=2)

"""Login recovery and CSRF boundaries without database side effects."""
import re
import secrets
import time
import unittest
from unittest.mock import patch

from bank.web import create_app


class LoginRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config['TESTING'] = True
        self.browser = self.app.test_client()
        self.password = secrets.token_urlsafe(24)
        self.auth = self.enterContext(patch('bank.web.client.authenticate', return_value=secrets.token_hex(32)))
        self.enterContext(patch('bank.web.client.session_user', return_value=None))

    def token(self, response):
        return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)

    def submit(self, token):
        return self.browser.post('/login', data={'email': 'login-test@example.test',
                                                 'password': self.password, 'csrf_token': token})

    def assert_recovery(self, response):
        self.assertEqual(response.status_code, 400)
        self.auth.assert_not_called()
        self.assertIn('value="login-test@example.test"', response.text)
        self.assertNotIn(self.password, response.text)
        self.assertIn('name="password"', response.text)
        retry = self.submit(self.token(response))
        self.assertEqual(retry.status_code, 302)
        self.assertEqual(retry.location, '/')
        self.auth.assert_called_once()

    def test_other_local_app_cookie_does_not_break_login(self):
        token = self.token(self.browser.get('/login'))
        # Cookies are shared across localhost ports; another app may use "session".
        self.browser.set_cookie('session', 'unrelated-local-app-cookie')
        response = self.submit(token)
        self.assertEqual(response.status_code, 302)
        self.auth.assert_called_once()

    def test_missing_session_cookie_recovers_on_login_page(self):
        token = self.token(self.browser.get('/login'))
        self.browser.delete_cookie(self.app.config['SESSION_COOKIE_NAME'])
        self.assert_recovery(self.submit(token))

    def test_expired_form_recovers_without_authenticating_stale_request(self):
        with patch('itsdangerous.timed.TimestampSigner.get_timestamp', return_value=int(time.time()) - 1801):
            token = self.token(self.browser.get('/login'))
        # Refresh the session cookie while retaining the old rendered form token.
        with self.browser.session_transaction() as state:
            state['csrf_token'] = 'current-session-token'
        self.assert_recovery(self.submit(token))

    def test_invalid_token_recovers_but_does_not_bypass_csrf(self):
        self.browser.get('/login')
        self.assert_recovery(self.submit('invalid-token'))

    def test_missing_token_stays_rejected_and_has_fresh_form(self):
        self.assert_recovery(self.submit(''))

    def test_financial_post_is_not_replayed_on_csrf_failure(self):
        with patch('bank.web.client.move_money') as move:
            response = self.browser.post('/accounts/1/transfer', data={'amount': '10', 'target': '2'})
        self.assertEqual(response.status_code, 400)
        move.assert_not_called()
        self.assertNotIn('name="password"', response.text)

"""End-to-end banking tests against real Db2 and the compiled COBOL rules."""
import json
import subprocess
import re
import secrets
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from support import execute, insert_id, query, transaction
from bank.client import BankError, BackendError
from werkzeug.security import generate_password_hash
from bank import client as backend
from bank.web import create_app


class ProtocolTests(unittest.TestCase):
    def test_invalid_requests_are_rejected_without_partial_output(self):
        requests = ('', '{} trailing', '[]', '{"command": 42}', '{"command":"SESSION","token":"x' +
                    '\u0000' + '"}', '{"command":"SESSION","token":"' + 'x' * 101 + '"}', 'x' * 16384)
        for payload in requests:
            with self.subTest(length=len(payload)):
                result = subprocess.run(['/usr/local/bin/bank-backend'], input=payload,
                                        text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(json.loads(result.stdout), {'ok': False, 'error': 'INVALID_REQUEST'})


class BankTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = secrets.token_urlsafe(24)
        cls.hashed = generate_password_hash(cls.password)
        cls.app = create_app()
        cls.app.config['TESTING'] = True

    def setUp(self):
        self.users = []
        self.accounts = []
        self.addCleanup(self.cleanup_data)
        self.prefix = uuid4().hex
        with transaction() as connection:
            for role in ('ADMIN', 'CUSTOMER', 'CUSTOMER'):
                index = len(self.users)
                user_id = insert_id(connection,
                                    'INSERT INTO BANK_USERS (NAME, EMAIL, PASSWORD_HASH, ROLE) VALUES (?, ?, ?, ?)',
                                    (f'Test {index}', f'{self.prefix}-{index}@example.test', self.hashed, role))
                self.users.append(user_id)
                if role == 'CUSTOMER':
                    self.accounts.append(insert_id(connection, 'INSERT INTO BANK_ACCOUNTS (USER_ID) VALUES (?)', (user_id,)))
        self.admin, self.alice, self.bob = self.users
        self.source, self.target = self.accounts
        self.admin_token, self.alice_token, self.bob_token = [
            backend.authenticate(f'{self.prefix}-{index}@example.test', self.password)
            for index in range(3)
        ]
        backend.move_money(self.admin_token, None, self.source, '100.00', str(uuid4()))

    def cleanup_data(self):
        # Cleanup is restricted to user IDs created by this test, never shared data.
        with transaction() as connection:
            for user_id in self.users:
                execute(connection, 'DELETE FROM BANK_ENTRIES WHERE ACCOUNT_ID IN '
                        '(SELECT ID FROM BANK_ACCOUNTS WHERE USER_ID = ?)', (user_id,))
            for user_id in self.users:
                execute(connection, 'DELETE FROM BANK_TRANSACTIONS WHERE ACTOR_ID = ?', (user_id,))
                execute(connection, 'DELETE FROM BANK_AUDIT WHERE ACTOR_ID = ?', (user_id,))
                execute(connection, 'DELETE FROM BANK_SESSIONS WHERE USER_ID = ?', (user_id,))
            for user_id in self.users:
                execute(connection, 'DELETE FROM BANK_NOTIFICATIONS WHERE USER_ID = ?', (user_id,))
                execute(connection, 'DELETE FROM BANK_ACCOUNT_RESOLUTIONS WHERE ACCOUNT_ID IN (SELECT ID FROM BANK_ACCOUNTS WHERE USER_ID = ?)', (user_id,))
                execute(connection, 'DELETE FROM BANK_ACCOUNT_APPROVALS WHERE ACCOUNT_ID IN (SELECT ID FROM BANK_ACCOUNTS WHERE USER_ID = ?)', (user_id,))
                execute(connection, 'DELETE FROM BANK_ACCOUNTS WHERE USER_ID = ?', (user_id,))
                execute(connection, 'DELETE FROM BANK_USERS WHERE ID = ?', (user_id,))

    def balances(self):
        return (backend.statement(self.alice_token, self.source)[0]['balance'],
                backend.statement(self.bob_token, self.target)[0]['balance'])

    def login_client(self, index=1):
        client = self.app.test_client()
        response = client.get('/login')
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
        response = client.post('/login', data={'email': f'{self.prefix}-{index}@example.test',
                                             'password': self.password, 'csrf_token': csrf})
        self.assertEqual(response.status_code, 302, response.text)
        return client

    def test_transfer_ledger_and_replay(self):
        key = str(uuid4())
        first = backend.move_money(self.alice_token, self.source, self.target, '25.30', key)
        self.assertEqual(first, backend.move_money(self.alice_token, self.source, self.target, '25.30', key))
        self.assertEqual(self.balances(), (Decimal('74.70'), Decimal('25.30')))
        entries = backend.statement(self.alice_token, self.source)[1]
        self.assertEqual(entries[0]['direction'], '-')
        self.assertEqual(entries[0]['transaction_id'], first)
        with self.assertRaises(BankError):
            backend.move_money(self.alice_token, self.source, self.target, '1', key)

    def test_authorization(self):
        for operation in (lambda: backend.statement(self.bob_token, self.source),
                          lambda: backend.move_money(self.bob_token, self.source, self.target, '1', str(uuid4())),
                          lambda: backend.move_money(self.alice_token, None, self.source, '1', str(uuid4())),
                          lambda: backend.set_account_status(self.alice_token, self.source, 'BLOCKED'),
                          lambda: backend.list_audit(self.alice_token)):
            with self.assertRaises(BankError):
                operation()
        self.assertEqual(self.balances(), (Decimal('100'), Decimal('0')))
        self.assertEqual([row['id'] for row in backend.list_accounts(self.bob_token)], [self.target])

    def test_blocked_and_insufficient_balance(self):
        with self.assertRaises(BankError):
            backend.move_money(self.alice_token, self.source, self.target, '100.01', str(uuid4()))
        backend.set_account_status(self.admin_token, self.target, 'BLOCKED')
        with self.assertRaises(BankError):
            backend.move_money(self.alice_token, self.source, self.target, '1', str(uuid4()))
        self.assertEqual(self.balances(), (Decimal('100'), Decimal('0')))
        backend.set_account_status(self.admin_token, self.target, 'ACTIVE')
        backend.move_money(self.alice_token, self.source, self.target, '1', str(uuid4()))

    def test_concurrent_transfers_do_not_overdraw(self):
        def attempt(_):
            try:
                backend.move_money(self.alice_token, self.source, self.target, '80', str(uuid4()))
                return True
            except BankError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(self.balances(), (Decimal('20'), Decimal('80')))

    def test_transaction_rolls_back_after_write_failure(self):
        trigger = 'TEST_FAIL_' + self.prefix[:16].upper()
        with transaction() as connection:
            execute(connection, f"CREATE TRIGGER {trigger} NO CASCADE BEFORE INSERT ON BANK_ENTRIES "
                    "REFERENCING NEW AS N FOR EACH ROW "
                    f"WHEN (N.ACCOUNT_ID = {self.target}) "
                    "SIGNAL SQLSTATE '75001' SET MESSAGE_TEXT = 'Test failure'")
        try:
            with self.assertRaises(BackendError):
                backend.move_money(self.alice_token, self.source, self.target, '20', str(uuid4()))
        finally:
            with transaction() as connection:
                execute(connection, f'DROP TRIGGER {trigger}')
        self.assertEqual(self.balances(), (Decimal('100'), Decimal('0')))
        self.assertEqual(len(backend.statement(self.alice_token, self.source)[1]), 1)

    def test_lockout_and_session_expiry(self):
        email = f'{self.prefix}-1@example.test'
        for _ in range(5):
            self.assertIsNone(backend.authenticate(email, secrets.token_urlsafe(16)))
        self.assertIsNone(backend.authenticate(email, self.password))
        token = backend.authenticate(f'{self.prefix}-2@example.test', self.password)
        self.assertEqual(backend.session_user(token)['id'], self.bob)
        with transaction() as connection:
            execute(connection, 'UPDATE BANK_SESSIONS SET EXPIRES_AT = CURRENT TIMESTAMP - 1 MINUTE WHERE USER_ID = ?', (self.bob,))
        self.assertIsNone(backend.session_user(token))

    def test_http_auth_csrf_and_pages(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get('/').status_code, 302)
        self.assertEqual(anonymous.post('/login', data={}).status_code, 400)
        client = self.login_client()
        self.assertEqual(client.get('/').status_code, 200)
        self.assertEqual(client.get(f'/accounts/{self.source}').status_code, 200)
        self.assertEqual(client.get(f'/accounts/{self.target}').status_code, 400)
        self.assertEqual(client.get('/admin/audit').status_code, 403)
        response = client.get(f'/accounts/{self.source}')
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
        key = re.search(r'name="request_key" value="([^"]+)"', response.text).group(1)
        data = {'target': str(self.target), 'amount': '12.50', 'request_key': key, 'csrf_token': csrf}
        self.assertEqual(client.post(f'/accounts/{self.source}/transfer', data=data).status_code, 302)
        self.assertEqual(client.post(f'/accounts/{self.source}/transfer', data=data).status_code, 302)
        self.assertEqual(self.balances(), (Decimal('87.50'), Decimal('12.50')))
        response = client.get('/')
        self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
        self.assertIn("script-src-attr 'none'", response.headers['Content-Security-Policy'])
        self.assertNotIn("'unsafe-inline'", response.headers['Content-Security-Policy'])
        self.assertEqual(client.post('/logout', data={'csrf_token': csrf}).status_code, 302)
        self.assertEqual(client.get('/').status_code, 302)
        admin = self.login_client(0)
        self.assertEqual(admin.get('/').status_code, 200)
        self.assertEqual(admin.get('/admin/audit').status_code, 200)
        self.assertEqual(admin.get(f'/accounts/{self.source}').status_code, 200)

    def test_onboarding_workflow_and_access(self):
        anonymous = self.app.test_client()
        self.assertEqual(anonymous.get('/admin/onboarding').status_code, 302)
        customer = self.login_client()
        self.assertEqual(customer.get('/admin/onboarding').status_code, 403)
        admin = self.login_client(0)
        dashboard = admin.get('/')
        self.assertNotIn('name="password"', dashboard.text)
        self.assertIn('Contas nesta página', dashboard.text)
        page = admin.get('/admin/onboarding')
        self.assertEqual(page.status_code, 200)
        self.assertIn('Criar acesso', page.text)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        response = admin.post('/admin/customers', data={
            'csrf_token': csrf, 'name': 'Preserved name',
            'email': f'{self.prefix}-invalid@example.test', 'password': 'short'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('value="Preserved name"', response.text)
        self.assertIn('role="alert"', response.text)
        self.assertNotIn('value="short"', response.text)
        self.assertIn('aria-current="page"', response.text)

    def test_customer_creation_and_output_escaping(self):
        email = self.prefix + '-new@example.test'
        user_id = backend.create_customer(self.admin_token, "O'Brien <script>alert(1)</script> João", email, self.password)
        self.users.append(user_id)
        token = backend.authenticate(email, self.password)
        self.assertEqual(backend.session_user(token)['id'], user_id)
        self.assertEqual(backend.list_accounts(token), [])
        second = backend.create_account(token)
        self.accounts.append(second)
        admin = self.login_client(0)
        response = admin.get('/')
        self.assertIn('&lt;script&gt;', response.text)
        self.assertIn('João', response.text)
        self.assertNotIn('<script>', response.text)
        with self.assertRaises(BankError):
            backend.create_customer(self.admin_token, 'Duplicate', email, self.password)
        with self.assertRaises(BankError):
            backend.create_first_admin('Other admin', self.prefix + '-admin@example.test', self.password)

    def test_native_validation_and_token_boundary(self):
        for value in ('0', '-1', '1.001', '10000000', '1e3', '1\nCREDIT'):
            with self.subTest(value=value), self.assertRaises(BankError):
                backend.move_money(self.alice_token, self.source, self.target, value, str(uuid4()))
        for token in ('invalid', '', self.bob_token):
            with self.assertRaises(BankError):
                backend.call_backend('MOVE', token=token, actor_id=self.alice,
                                     source=self.source, target=self.target, amount='1',
                                     request_key=str(uuid4()))
        backend.move_money(self.alice_token, self.source, self.target, '0.10', str(uuid4()))
        backend.move_money(self.alice_token, self.source, self.target, '0.20', str(uuid4()))
        self.assertEqual(self.balances(), (Decimal('99.70'), Decimal('0.30')))
        with transaction() as connection:
            execute(connection, 'UPDATE BANK_ACCOUNTS SET BALANCE = 9999999999999.99 WHERE ID = ?', (self.target,))
        with self.assertRaises(BankError):
            backend.move_money(self.alice_token, self.source, self.target, '0.01', str(uuid4()))
        self.assertEqual(self.balances(), (Decimal('99.70'), Decimal('9999999999999.99')))

    def test_connection_configuration_rejects_injection(self):
        from ledger.repository import connection_value
        import os
        with patch.dict(os.environ, {'DB2_HOST': 'db2;UID=other'}), self.assertRaises(ValueError):
            connection_value('DB2_HOST')
        with patch.dict(os.environ, {'DB2_HOST': 'db2;UID=other'}), self.assertRaises(BackendError):
            backend.call_backend('INIT')


if __name__ == '__main__':
    unittest.main()

"""Access identity, account quotas and administrative approval against real Db2."""
import re
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

from bank import client as backend
from bank.client import BankError, BackendError
from fixtures import IsolatedBankTestCase
from support import execute, insert_id, query, transaction


class AccountOpeningTests(IsolatedBankTestCase):
    def setUp(self):
        with transaction() as connection:
            for table in self.tables:
                execute(connection, f'DELETE FROM {table}')
            self.admin = insert_id(connection, 'INSERT INTO BANK_USERS (NAME, EMAIL, PASSWORD_HASH, ROLE) VALUES (?, ?, ?, ?)',
                                   ('Opening admin', 'admin@example.test', self.hashed, 'ADMIN'))
        self.admin_token = backend.authenticate('admin@example.test', self.password)
        self.owner = backend.create_customer(self.admin_token, 'Opening customer', 'customer@example.test', self.password)
        self.customer_token = backend.authenticate('customer@example.test', self.password)

    def test_access_has_no_bank_account_and_request_uses_authenticated_owner(self):
        self.assertEqual(backend.session_user(self.customer_token)['id'], self.owner)
        self.assertEqual(backend.list_accounts(self.customer_token), [])
        account = int(backend.call_backend('CREATE_ACCOUNT', token=self.customer_token,
                      email='admin@example.test', user_id=self.admin, account_type='CURRENT',
                      request_key=str(uuid4()))['id'])
        row = backend.statement(self.customer_token, account)[0]
        self.assertEqual(row['user_id'], self.owner)
        self.assertEqual(row['status'], 'PENDING')
        self.assertEqual(row['balance'], Decimal('0'))
        self.assertEqual([r['id'] for r in backend.pending_accounts(self.admin_token)], [account])
        with self.assertRaises(BankError):
            backend.pending_accounts(self.customer_token)

    def test_limits_include_pending_blocked_and_approved_accounts(self):
        current = [backend.create_account(self.customer_token) for _ in range(3)]
        savings = [backend.create_account(self.customer_token, account_type='SAVINGS') for _ in range(2)]
        for account in (current[0], savings[0]):
            backend.approve_account(self.admin_token, account)
            backend.set_account_status(self.admin_token, account, 'BLOCKED')
        for kind in ('CURRENT', 'SAVINGS'):
            with self.subTest(kind=kind), self.assertRaises(BankError) as error:
                backend.create_account(self.customer_token, account_type=kind)
            self.assertEqual(error.exception.code, 'ACCOUNT_LIMIT')
            with self.assertRaises(BankError):
                backend.create_account(self.admin_token, 'customer@example.test', kind)
        self.assertEqual(len(backend.list_accounts(self.customer_token)), 5)

    def test_concurrent_requests_cannot_exceed_quota_and_replay_is_safe(self):
        backend.create_account(self.customer_token)
        backend.create_account(self.customer_token)
        def attempt(_):
            try:
                return backend.create_account(self.customer_token)
            except BankError as error:
                self.assertEqual(error.code, 'ACCOUNT_LIMIT')
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        key = str(uuid4())
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: backend.create_account(self.customer_token,
                            account_type='SAVINGS', request_key=key), range(2)))
        self.assertEqual(ids[0], ids[1])
        with self.assertRaises(BankError) as error:
            backend.create_account(self.customer_token, account_type='CURRENT', request_key=key)
        self.assertEqual(error.exception.code, 'REQUEST_CONFLICT')

    def test_pending_accounts_cannot_move_receive_interest_or_bypass_approval(self):
        pending = backend.create_account(self.customer_token, account_type='SAVINGS')
        active = backend.create_account(self.customer_token)
        backend.approve_account(self.admin_token, active)
        backend.move_money(self.admin_token, None, active, '100', str(uuid4()))
        for operation in (
            lambda: backend.approve_account(self.customer_token, pending),
            lambda: backend.set_account_status(self.customer_token, pending, 'ACTIVE'),
            lambda: backend.set_account_status(self.admin_token, pending, 'ACTIVE'),
            lambda: backend.move_money(self.admin_token, None, pending, '10', str(uuid4())),
            lambda: backend.move_money(self.customer_token, active, pending, '10', str(uuid4())),
            lambda: backend.move_money(self.customer_token, pending, active, '1', str(uuid4()))):
            with self.assertRaises(BankError):
                operation()
        report = backend.run_report(self.admin_token, close_day=True)
        header, rows = backend.read_report(self.admin_token, report)
        self.assertEqual(header['status'], 'OK')
        row = next(row for row in rows if row['account_id'] == pending)
        self.assertEqual(row['account_status'], 'PENDING')
        self.assertEqual(row['interest_amount'], Decimal('0'))

    def test_approval_is_atomic_audited_and_idempotent(self):
        account = backend.create_account(self.customer_token, account_type='SAVINGS')
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: backend.approve_account(self.admin_token, account), range(2)))
        self.assertEqual(backend.statement(self.customer_token, account)[0]['status'], 'ACTIVE')
        with transaction() as connection:
            rows = query(connection, 'SELECT APPROVED_BY, APPROVED_AT FROM BANK_ACCOUNT_APPROVALS WHERE ACCOUNT_ID = ?', (account,))
            self.assertEqual(rows[0]['approved_by'], self.admin)
            self.assertIsNotNone(rows[0]['approved_at'])
            events = query(connection, "SELECT ID FROM BANK_AUDIT WHERE ACTION = 'ACCOUNT_APPROVED' AND SUBJECT_ID = ?", (account,))
            self.assertEqual(len(events), 1)
        backend.move_money(self.admin_token, None, account, '50', str(uuid4()))
        report = backend.run_report(self.admin_token, close_day=True)
        self.assertEqual(backend.read_report(self.admin_token, report)[0]['total_interest'], Decimal('0.01'))
        backend.set_account_status(self.admin_token, account, 'BLOCKED')
        backend.approve_account(self.admin_token, account)
        self.assertEqual(backend.statement(self.customer_token, account)[0]['status'], 'BLOCKED')

    def test_approval_failure_rolls_back_status_and_audit(self):
        account = backend.create_account(self.customer_token)
        with transaction() as connection:
            execute(connection, "CREATE TRIGGER TEST_APPROVAL_FAILURE NO CASCADE BEFORE UPDATE ON BANK_ACCOUNT_APPROVALS "
                    "FOR EACH ROW SIGNAL SQLSTATE '75001' SET MESSAGE_TEXT = 'Test failure'")
        try:
            with self.assertRaises(BackendError):
                backend.approve_account(self.admin_token, account)
        finally:
            with transaction() as connection:
                execute(connection, 'DROP TRIGGER TEST_APPROVAL_FAILURE')
        self.assertEqual(backend.statement(self.customer_token, account)[0]['status'], 'PENDING')
        with transaction() as connection:
            self.assertIsNone(query(connection, 'SELECT APPROVED_BY FROM BANK_ACCOUNT_APPROVALS WHERE ACCOUNT_ID = ?', (account,))[0]['approved_by'])
            self.assertEqual(query(connection, "SELECT ID FROM BANK_AUDIT WHERE ACTION = 'ACCOUNT_APPROVED'"), [])

    def login(self, email):
        browser = self.app.test_client()
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', browser.get('/login').text).group(1)
        response = browser.post('/login', data={'email': email, 'password': self.password, 'csrf_token': csrf})
        self.assertEqual(response.status_code, 302)
        return browser

    def test_customer_request_and_admin_approval_http_workflow(self):
        customer = self.login('customer@example.test')
        self.assertIn('Pedir nova conta', customer.get('/').text)
        page = customer.get('/accounts/new')
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        key = re.search(r'name="request_key" value="([^"]+)"', page.text).group(1)
        self.assertEqual(customer.post('/accounts', data={}).status_code, 400)
        response = customer.post('/accounts', data={'account_type': 'SAVINGS', 'request_key': key, 'csrf_token': csrf})
        self.assertEqual(response.status_code, 302)
        location = response.location
        detail = customer.get(location)
        self.assertIn('Pendente', detail.text)
        self.assertNotIn('name="amount"', detail.text)
        self.assertEqual(customer.get('/admin/approvals').status_code, 403)
        account = backend.list_accounts(self.customer_token)[0]['id']
        self.assertEqual(customer.post(f'/admin/accounts/{account}/approve', data={'csrf_token': csrf}).status_code, 403)
        admin = self.login('admin@example.test')
        page = admin.get('/admin/approvals')
        self.assertIn('Opening customer', page.text)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        self.assertEqual(admin.post(f'/admin/accounts/{account}/approve', data={}).status_code, 400)
        self.assertEqual(admin.post(f'/admin/accounts/{account}/approve', data={'csrf_token': csrf}).status_code, 302)
        self.assertIn('name="amount"', customer.get(location).text)
        self.assertNotIn('Opening customer', admin.get('/admin/approvals').text)

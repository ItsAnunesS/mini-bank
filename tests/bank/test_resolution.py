"""Account request decisions, authorization, concurrency and HTTP presentation."""
import re
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from bank import client as backend
from bank.client import BankError, BackendError
from fixtures import IsolatedBankTestCase
from support import execute, query, transaction


class AccountResolutionTests(IsolatedBankTestCase):
    def setUp(self):
        with transaction() as connection:
            for table in self.tables:
                execute(connection, f'DELETE FROM {table}')
        backend.call_backend('CREATE_ADMIN', name='Resolution admin', email='admin@example.test', password=self.password)
        self.admin = backend.authenticate('admin@example.test', self.password)
        backend.create_customer(self.admin, 'Request owner', 'owner@example.test', self.password)
        backend.create_customer(self.admin, 'Other customer', 'other@example.test', self.password)
        self.owner = backend.authenticate('owner@example.test', self.password)
        self.other = backend.authenticate('other@example.test', self.password)
        self.account = backend.create_account(self.owner)

    def test_rejection_reason_validation_and_replay(self):
        for reason in ('', '    ', 'éééé', 'x' * 241, 'é' * 121):
            with self.subTest(reason_length=len(reason)), self.assertRaises(BankError):
                backend.reject_account(self.admin, self.account, reason)
        self.assertEqual(backend.statement(self.owner, self.account)[0]['status'], 'PENDING')
        reason = "Documentação incompleta <script>alert('x')</script>"
        backend.reject_account(self.admin, self.account, reason)
        backend.reject_account(self.admin, self.account, reason)
        row = backend.statement(self.owner, self.account)[0]
        self.assertEqual((row['status'], row['rejection_reason']), ('REJECTED', reason))
        self.assertEqual(backend.list_accounts(self.owner)[0]['rejection_reason'], reason)
        with self.assertRaises(BankError):
            backend.reject_account(self.admin, self.account, 'Outro motivo')
        with transaction() as connection:
            rows = query(connection, 'SELECT * FROM BANK_ACCOUNT_RESOLUTIONS WHERE ACCOUNT_ID = ?', (self.account,))
            self.assertEqual(len(rows), 1)
            self.assertIsNotNone(rows[0]['resolved_at'])
            self.assertEqual(rows[0]['resolved_by'], backend.session_user(self.admin)['id'])
            self.assertEqual(len(query(connection, "SELECT ID FROM BANK_AUDIT WHERE ACTION = 'ACCOUNT_REJECTED'")), 1)
        self.assertEqual(backend.pending_accounts(self.admin), [])

    def test_permissions_final_states_and_monetary_boundaries(self):
        for action in (lambda: backend.reject_account(self.owner, self.account, 'Invalid request'),
                       lambda: backend.cancel_account(self.other, self.account),
                       lambda: backend.cancel_account(self.admin, self.account)):
            with self.assertRaises(BankError):
                action()
        backend.cancel_account(self.owner, self.account)
        backend.cancel_account(self.owner, self.account)
        with transaction() as connection:
            self.assertIsNone(query(connection, 'SELECT REASON FROM BANK_ACCOUNT_RESOLUTIONS')[0]['reason'])
            self.assertEqual(len(query(connection, "SELECT ID FROM BANK_AUDIT WHERE ACTION = 'ACCOUNT_CANCELLED'")), 1)
        rejected = backend.create_account(self.owner, account_type='SAVINGS')
        backend.reject_account(self.admin, rejected, 'Pedido incompleto')
        for account in (self.account, rejected):
            for action in (lambda: backend.approve_account(self.admin, account),
                           lambda: backend.set_account_status(self.admin, account, 'ACTIVE'),
                           lambda: backend.move_money(self.admin, None, account, '10', str(uuid4()))):
                with self.assertRaises(BankError):
                    action()
        report = backend.run_report(self.admin, close_day=True)
        header, rows = backend.read_report(self.admin, report)
        self.assertEqual(header['status'], 'OK')
        self.assertEqual({r['account_status'] for r in rows}, {'CANCELLED', 'REJECTED'})
        self.assertTrue(all(r['interest_amount'] == 0 for r in rows))
        active = backend.create_account(self.owner)
        backend.approve_account(self.admin, active)
        for action in (lambda: backend.cancel_account(self.owner, active),
                       lambda: backend.reject_account(self.admin, active, 'Not pending')):
            with self.assertRaises(BankError):
                action()

    def test_resolved_requests_release_slots_without_recreating_replayed_requests(self):
        for kind, limit in [('CURRENT', 3), ('SAVINGS', 2)]:
            accounts = [self.account] if kind == 'CURRENT' else []
            accounts += [backend.create_account(self.owner, account_type=kind) for _ in range(limit-len(accounts))]
            with self.assertRaises(BankError):
                backend.create_account(self.owner, account_type=kind)
            backend.reject_account(self.admin, accounts[0], 'Pedido incompleto')
            key = str(uuid4())
            replacement = backend.create_account(self.owner, account_type=kind, request_key=key)
            backend.cancel_account(self.owner, replacement)
            self.assertEqual(backend.create_account(self.owner, account_type=kind, request_key=key), replacement)
            self.assertEqual(backend.statement(self.owner, replacement)[0]['status'], 'CANCELLED')
            backend.create_account(self.owner, account_type=kind)
            with self.assertRaises(BankError):
                backend.create_account(self.owner, account_type=kind)

    def test_competing_decisions_preserve_one_atomic_outcome(self):
        def attempt(action):
            try:
                action()
                return True
            except BankError as error:
                self.assertEqual(error.code, 'NOT_PENDING')
                return False
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(attempt, [
                lambda: backend.approve_account(self.admin, self.account),
                lambda: backend.reject_account(self.admin, self.account, 'Pedido incompleto'),
                lambda: backend.cancel_account(self.owner, self.account)]))
        self.assertEqual(sum(results), 1)
        with transaction() as connection:
            self.assertEqual(len(query(connection, "SELECT ID FROM BANK_AUDIT WHERE ACTION IN ('ACCOUNT_APPROVED', 'ACCOUNT_REJECTED', 'ACCOUNT_CANCELLED')")), 1)
            status = query(connection, 'SELECT STATUS FROM BANK_ACCOUNTS WHERE ID = ?', (self.account,))[0]['status']
            decisions = query(connection, 'SELECT STATUS FROM BANK_ACCOUNT_RESOLUTIONS')
            approved = query(connection, 'SELECT APPROVED_BY FROM BANK_ACCOUNT_APPROVALS')[0]['approved_by']
            if status == 'ACTIVE':
                self.assertIsNotNone(approved)
                self.assertEqual(decisions, [])
            else:
                self.assertIsNone(approved)
                self.assertEqual(decisions, [{'status': status}])

    def test_resolution_write_failure_rolls_back_status_and_audit(self):
        with transaction() as connection:
            execute(connection, "CREATE TRIGGER TEST_RESOLUTION_FAILURE NO CASCADE BEFORE INSERT ON BANK_ACCOUNT_RESOLUTIONS FOR EACH ROW SIGNAL SQLSTATE '75001' SET MESSAGE_TEXT = 'Test failure'")
        try:
            for action in (lambda: backend.reject_account(self.admin, self.account, 'Pedido incompleto'),
                           lambda: backend.cancel_account(self.owner, self.account)):
                with self.assertRaises(BackendError):
                    action()
                self.assertEqual(backend.statement(self.owner, self.account)[0]['status'], 'PENDING')
        finally:
            with transaction() as connection:
                execute(connection, 'DROP TRIGGER TEST_RESOLUTION_FAILURE')
        with transaction() as connection:
            self.assertEqual(query(connection, 'SELECT * FROM BANK_ACCOUNT_RESOLUTIONS'), [])
            self.assertEqual(query(connection, "SELECT ID FROM BANK_AUDIT WHERE ACTION IN ('ACCOUNT_REJECTED', 'ACCOUNT_CANCELLED')"), [])

    def login(self, email):
        browser = self.app.test_client()
        token = self.csrf(browser.get('/login'))
        self.assertEqual(browser.post('/login', data={'email': email, 'password': self.password, 'csrf_token': token}).status_code, 302)
        return browser

    def csrf(self, response):
        return re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)

    def test_http_rejection_visibility_cancellation_and_csrf(self):
        owner = self.login('owner@example.test')
        admin = self.login('admin@example.test')
        path = f'/accounts/{self.account}'
        owner_csrf = self.csrf(owner.get(path))
        admin_csrf = self.csrf(admin.get(path))
        reject_path = f'/admin/accounts/{self.account}/reject'
        self.assertEqual(owner.post(reject_path, data={'reason': 'No access', 'csrf_token': owner_csrf}).status_code, 403)
        self.assertEqual(admin.post(reject_path, data={'reason': 'Incomplete'}).status_code, 400)
        reason = '<script>Rejected</script>'
        self.assertEqual(admin.post(reject_path, data={'reason': reason, 'csrf_token': admin_csrf}).status_code, 302)
        for page in (owner.get('/'), owner.get(path)):
            self.assertIn('Motivo da rejeição:', page.text)
            self.assertIn('&lt;script&gt;Rejected&lt;/script&gt;', page.text)
            self.assertNotIn(reason, page.text)
            self.assertNotIn('name="amount"', page.text)
        self.assertNotIn('Rejeitar pedido', admin.get(path).text)
        pending = backend.create_account(self.owner)
        cancel_path = f'/accounts/{pending}/cancel'
        self.assertEqual(owner.post(cancel_path, data={}).status_code, 400)
        self.assertIn('Cancelar pedido', owner.get(f'/accounts/{pending}').text)
        self.assertEqual(owner.post(cancel_path, data={'csrf_token': owner_csrf}).status_code, 302)
        self.assertIn('Cancelado', owner.get('/').text)
        self.assertNotIn('Cancelar pedido', owner.get(f'/accounts/{pending}').text)

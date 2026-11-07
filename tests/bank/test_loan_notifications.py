"""Loan decisions share the authenticated, transactional notification inbox."""
import re
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import uuid4

from bank import client as backend
from bank.client import BackendError, BankError
from fixtures import IsolatedBankTestCase
from support import execute, insert_id, query, transaction


class LoanNotificationTests(IsolatedBankTestCase):
    def setUp(self):
        with transaction() as c:
            for table in self.tables:
                execute(c, f'DELETE FROM {table}')
            for role, email in [('ADMIN', 'admin@example.test'), ('CUSTOMER', 'owner@example.test'), ('CUSTOMER', 'other@example.test')]:
                insert_id(c, 'INSERT INTO BANK_USERS (NAME, EMAIL, PASSWORD_HASH, ROLE) VALUES (?, ?, ?, ?)',
                          ('Notification tester', email, self.hashed, role))
        self.admin = backend.authenticate('admin@example.test', self.password)
        self.owner = backend.authenticate('owner@example.test', self.password)
        self.other = backend.authenticate('other@example.test', self.password)
        self.account = backend.create_account(self.owner)
        backend.approve_account(self.admin, self.account)
        backend.mark_all_notifications_read(self.owner)

    def loan(self):
        return int(backend.call_backend('REQUEST_LOAN', token=self.owner, target=self.account,
                                       amount='100', months=3, request_key=str(uuid4()))['id'])

    def decide(self, command, loan, reason=None):
        return backend.call_backend(command, token=self.admin, original=loan, reason=reason)

    def test_multiple_loans_share_account_without_duplicate_notifications(self):
        first, second, rejected = self.loan(), self.loan(), self.loan()
        for loan in (first, second):
            self.decide('APPROVE_LOAN', loan)
            self.decide('APPROVE_LOAN', loan)
        reason = 'Documentos em falta <script>test</script>'
        self.decide('REJECT_LOAN', rejected, reason)
        self.decide('REJECT_LOAN', rejected, reason)
        rows = backend.list_notifications(self.owner)
        self.assertEqual([(r['loan_id'], r['kind']) for r in rows],
                         [(rejected, 'LOAN_REJECTED'), (second, 'LOAN_APPROVED'), (first, 'LOAN_APPROVED'), (None, 'ACCOUNT_APPROVED')])
        self.assertEqual(rows[0]['reason'], reason)
        self.assertIsNone(rows[1]['reason'])
        self.assertEqual(backend.count_unread_notifications(self.owner), 3)
        self.assertEqual(backend.session_user(self.owner)['unread_count'], 3)
        self.assertEqual(backend.list_notifications(self.other), [])
        with transaction() as c:
            with self.assertRaises(Exception):
                execute(c, "INSERT INTO BANK_NOTIFICATIONS (USER_ID, ACCOUNT_ID, LOAN_ID, KIND) SELECT USER_ID, ACCOUNT_ID, ID, 'LOAN_APPROVED' FROM BANK_LOANS WHERE ID = ?", (first,))

    def test_notice_failure_rolls_back_approval_credit_and_rejection(self):
        loan = self.loan()
        with transaction() as c:
            execute(c, "CREATE TRIGGER FAIL_LOAN_NOTICE NO CASCADE BEFORE INSERT ON BANK_NOTIFICATIONS REFERENCING NEW AS N FOR EACH ROW WHEN (N.LOAN_ID IS NOT NULL) SIGNAL SQLSTATE '75001' SET MESSAGE_TEXT = 'test failure'")
        try:
            for command in ('APPROVE_LOAN', 'REJECT_LOAN'):
                with self.assertRaises(BackendError):
                    self.decide(command, loan, 'Incomplete documents')
                self.assertEqual(backend.call_backend('LOAN', token=self.owner, original=loan)['loan']['status'], 'PENDING')
                self.assertEqual(backend.statement(self.owner, self.account)[0]['balance'], 0)
                self.assertEqual(backend.count_unread_notifications(self.owner), 0)
                with transaction() as c:
                    self.assertEqual(query(c, "SELECT ID FROM BANK_AUDIT WHERE ACTION IN ('LOAN_APPROVED','LOAN_REJECTED')"), [])
        finally:
            with transaction() as c:
                execute(c, 'DROP TRIGGER FAIL_LOAN_NOTICE')
        self.decide('APPROVE_LOAN', loan)
        self.assertEqual(backend.count_unread_notifications(self.owner), 1)

    def test_concurrent_decisions_emit_one_notice(self):
        for command in ('APPROVE_LOAN', 'REJECT_LOAN'):
            loan = self.loan()
            def decide(_):
                try:
                    return self.decide(command, loan, 'Incomplete documents')['ok']
                finally:
                    backend.close_backend()
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(list(pool.map(decide, range(2))), [True, True])
        self.assertEqual(backend.count_unread_notifications(self.owner), 2)
        self.assertEqual(backend.statement(self.owner, self.account)[0]['balance'], 100)

    def test_shared_bell_reason_links_owner_and_bulk_read(self):
        loan = self.loan()
        self.decide('REJECT_LOAN', loan, '<script>Missing documents</script>')
        browser = self.app.test_client()
        browser.set_cookie('bank_auth', self.owner)
        page = browser.get('/')
        self.assertIn('Empréstimo rejeitado', page.text)
        feed = browser.get('/notifications/feed').json
        self.assertEqual(feed['unread_count'], 1)
        self.assertIn(f'/loans/{loan}', feed['html'])
        self.assertIn('&lt;script&gt;Missing documents&lt;/script&gt;', feed['html'])
        self.assertNotIn('<script>Missing documents</script>', feed['html'])
        notice = backend.list_notifications(self.owner)[0]['id']
        with self.assertRaises(BankError):
            backend.mark_notification_read(self.other, notice)
        self.assertEqual(browser.post(f'/notifications/{notice}/read').status_code, 400)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        self.assertEqual(browser.post(f'/notifications/{notice}/read', data={'csrf_token': csrf}).status_code, 302)
        self.assertEqual(browser.get('/notifications/feed?summary=1').json, {'unread_count': 0})
        next_loan = self.loan()
        self.decide('APPROVE_LOAN', next_loan)
        self.assertEqual(browser.get('/notifications/feed?summary=1').json, {'unread_count': 1})
        self.assertEqual(browser.post('/notifications/read-all', data={'csrf_token': csrf}, headers={'Accept': 'application/json'}).json, {'ok': True})
        self.assertEqual(browser.get('/notifications/feed?summary=1').json, {'unread_count': 0})
        browser.set_cookie('bank_auth', self.other)
        self.assertNotIn('Missing documents', browser.get('/notifications/feed').json['html'])

    def test_navigation_avoids_redundant_backend_calls_and_summary_omits_html(self):
        browser = self.app.test_client()
        browser.set_cookie('bank_auth', self.owner)
        for path, expected in [('/', ['SESSION','ACCOUNTS','NOTIFICATIONS']),
                               (f'/accounts/{self.account}', ['SESSION','STATEMENT']),
                               ('/notifications/feed?summary=1', ['SESSION']),
                               ('/notifications/feed', ['SESSION','NOTIFICATIONS'])]:
            with patch('bank.client.exchange', wraps=backend.exchange) as calls:
                response = browser.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertCountEqual([call.args[0]['command'] for call in calls.call_args_list], expected)
                if 'summary=1' in path:
                    self.assertNotIn('html', response.json)
        backend.revoke_session(self.owner)
        self.assertEqual(browser.get('/notifications/feed?summary=1').status_code, 302)

    def test_static_cache_is_versioned_and_private_responses_are_never_cached(self):
        browser = self.app.test_client()
        browser.set_cookie('bank_auth', self.owner)
        page = browser.get('/')
        self.assertEqual(page.headers['Cache-Control'], 'no-store')
        self.assertRegex(page.headers['Server-Timing'], r'^app;dur=\d+\.\d+$')
        css_url = re.search(r'href="([^"]*bank.css[^"]*)"', page.text).group(1).replace('&amp;', '&')
        self.assertIn('?v=', css_url)
        css = browser.get(css_url)
        self.addCleanup(css.close)
        self.assertIn('immutable', css.headers['Cache-Control'])
        with browser.get(css_url, headers={'If-None-Match': css.headers['ETag']}) as cached:
            self.assertEqual(cached.status_code, 304)
        with browser.get('/static/bank.css?v=old') as outdated:
            self.assertNotIn('immutable', outdated.headers['Cache-Control'])
        self.assertEqual(browser.get('/notifications/feed').headers['Cache-Control'], 'no-store')
        self.assertEqual(browser.get('/notifications/feed?summary=1').headers['Cache-Control'], 'no-store')

    def test_upgrade_preserves_existing_notifications_and_read_dates(self):
        import os
        from pathlib import Path
        class MigrationFixture(IsolatedBankTestCase):
            pass
        MigrationFixture.schema = 'BANKTEST_' + uuid4().hex[:16].upper()
        MigrationFixture.previous_schema = os.environ.get('DB2_SCHEMA')
        with transaction() as c:
            execute(c, f'CREATE SCHEMA {MigrationFixture.schema}')
        os.environ['DB2_SCHEMA'] = MigrationFixture.schema
        try:
            with transaction() as c:
                for version in range(2, 8):
                    if version in (3, 4):
                        table, field = ('BANK_TRANSACTIONS','KIND') if version == 3 else ('BANK_ACCOUNTS','STATUS')
                        constraint = query(c, 'SELECT CONSTNAME FROM SYSCAT.CHECKS WHERE TABSCHEMA = CURRENT SCHEMA AND TABNAME = ? AND TEXT LIKE ?', (table, '%' + field + '%'))[0]['constname']
                        execute(c, f'ALTER TABLE {table} DROP CONSTRAINT "{constraint}"')
                    path = next(Path('/app/db/migrations').glob(f'{version:03}_*.sql'))
                    for sql in path.read_text().split(';'):
                        if sql.strip():
                            execute(c, sql)
                uid = insert_id(c, "INSERT INTO BANK_USERS (NAME, EMAIL, PASSWORD_HASH, ROLE) VALUES (?, ?, ?, 'CUSTOMER')", ('Legacy', 'legacy@example.test', self.hashed))
                account = insert_id(c, 'INSERT INTO BANK_ACCOUNTS (USER_ID) VALUES (?)', (uid,))
                execute(c, "INSERT INTO BANK_NOTIFICATIONS (USER_ID, ACCOUNT_ID, KIND, READ_AT) VALUES (?, ?, 'ACCOUNT_APPROVED', CURRENT TIMESTAMP)", (uid, account))
                before = query(c, 'SELECT ID, USER_ID, ACCOUNT_ID, KIND, REASON, CREATED_AT, READ_AT FROM BANK_NOTIFICATIONS')
            backend.initialize_schema()
            backend.initialize_schema()
            with transaction() as c:
                self.assertEqual(query(c, 'SELECT ID, USER_ID, ACCOUNT_ID, KIND, REASON, CREATED_AT, READ_AT FROM BANK_NOTIFICATIONS'), before)
                self.assertIsNone(query(c, 'SELECT LOAN_ID FROM BANK_NOTIFICATIONS')[0]['loan_id'])
                with self.assertRaises(Exception):
                    execute(c, "INSERT INTO BANK_NOTIFICATIONS (USER_ID, ACCOUNT_ID, KIND) VALUES (?, ?, 'ACCOUNT_APPROVED')", (uid, account))
        finally:
            MigrationFixture.remove_schema()

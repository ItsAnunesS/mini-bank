"""Persistent customer notifications with atomic decisions and ownership checks."""
import re
from concurrent.futures import ThreadPoolExecutor

from bank import client as backend
from bank.client import BankError, BackendError
from fixtures import IsolatedBankTestCase
from support import execute, insert_id, query, transaction


class NotificationTests(IsolatedBankTestCase):
    def setUp(self):
        with transaction() as connection:
            for table in self.tables:
                execute(connection, f'DELETE FROM {table}')
        backend.create_first_admin('Notification admin', 'admin@example.test', self.password)
        self.admin = backend.authenticate('admin@example.test', self.password)
        self.owner_id = backend.create_customer(self.admin, 'Notification owner', 'owner@example.test', self.password)
        backend.create_customer(self.admin, 'Other customer', 'other@example.test', self.password)
        self.owner = backend.authenticate('owner@example.test', self.password)
        self.other = backend.authenticate('other@example.test', self.password)
        self.account = backend.create_account(self.owner)

    def test_decisions_create_one_notification_with_reason_and_no_cancellation_notice(self):
        self.assertEqual(backend.list_notifications(self.owner), [])
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: backend.approve_account(self.admin, self.account), range(2)))
        rejected = backend.create_account(self.owner)
        reason = 'Documentação incompleta <script>test</script>'
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: backend.reject_account(self.admin, rejected, reason), range(2)))
        cancelled = backend.create_account(self.owner)
        backend.cancel_account(self.owner, cancelled)
        rows = backend.list_notifications(self.owner)
        self.assertEqual(len(rows), 2)
        self.assertEqual([(r['account_id'], r['kind'], r['reason']) for r in rows],
                         [(rejected, 'ACCOUNT_REJECTED', reason), (self.account, 'ACCOUNT_APPROVED', None)])
        self.assertTrue(all(r['read_at'] is None for r in rows))
        self.assertEqual(backend.list_notifications(self.other), [])

    def test_read_is_owner_only_idempotent_and_persistent(self):
        backend.approve_account(self.admin, self.account)
        notification = backend.list_notifications(self.owner)[0]['id']
        for token in (self.other, self.admin):
            with self.assertRaises(BankError):
                backend.mark_notification_read(token, notification)
        with self.assertRaises(BankError):
            backend.list_notifications(self.admin)
        with self.assertRaises(BankError):
            backend.mark_notification_read(self.owner, 9223372036854775807)
        self.assertIsNone(backend.list_notifications(self.owner)[0]['read_at'])
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: backend.mark_notification_read(self.owner, notification), range(2)))
        read_at = backend.list_notifications(self.owner)[0]['read_at']
        self.assertIsNotNone(read_at)
        backend.mark_notification_read(self.owner, notification)
        new_session = backend.authenticate('owner@example.test', self.password)
        self.assertEqual(backend.list_notifications(new_session)[0]['read_at'], read_at)

    def test_notification_write_failure_rolls_back_entire_decision(self):
        with transaction() as connection:
            execute(connection, "CREATE TRIGGER TEST_NOTIFICATION_FAILURE NO CASCADE BEFORE INSERT ON BANK_NOTIFICATIONS FOR EACH ROW SIGNAL SQLSTATE '75001' SET MESSAGE_TEXT = 'Test failure'")
        try:
            for action in (lambda: backend.approve_account(self.admin, self.account),
                           lambda: backend.reject_account(self.admin, self.account, 'Pedido incompleto')):
                with self.assertRaises(BackendError):
                    action()
                self.assertEqual(backend.statement(self.owner, self.account)[0]['status'], 'PENDING')
                self.assertEqual(backend.list_notifications(self.owner), [])
                with transaction() as connection:
                    self.assertIsNone(query(connection, 'SELECT APPROVED_BY FROM BANK_ACCOUNT_APPROVALS')[0]['approved_by'])
                    self.assertEqual(query(connection, 'SELECT * FROM BANK_ACCOUNT_RESOLUTIONS'), [])
                    self.assertEqual(query(connection, "SELECT ID FROM BANK_AUDIT WHERE ACTION IN ('ACCOUNT_APPROVED', 'ACCOUNT_REJECTED')"), [])
        finally:
            with transaction() as connection:
                execute(connection, 'DROP TRIGGER TEST_NOTIFICATION_FAILURE')

    def test_notification_pagination_is_stable_after_marking_read(self):
        with transaction() as connection:
            for _ in range(51):
                account = insert_id(connection, 'INSERT INTO BANK_ACCOUNTS (USER_ID) VALUES (?)', (self.owner_id,))
                execute(connection, "INSERT INTO BANK_NOTIFICATIONS (USER_ID, ACCOUNT_ID, KIND) VALUES (?, ?, 'ACCOUNT_APPROVED')", (self.owner_id, account))
        first = backend.list_notifications(self.owner)
        self.assertEqual(len(first), 50)
        backend.mark_notification_read(self.owner, first[0]['id'])
        second = backend.list_notifications(self.owner, first[-1]['id'])
        self.assertEqual(len(second), 1)
        self.assertEqual(len({r['id'] for r in first + second}), 51)
        self.assertEqual([r['id'] for r in backend.list_notifications(self.owner)], [r['id'] for r in first])
        self.assertEqual(backend.list_notifications(self.other, first[-1]['id']), [])

    def login(self, email):
        browser = self.app.test_client()
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', browser.get('/login').text).group(1)
        self.assertEqual(browser.post('/login', data={'email': email, 'password': self.password, 'csrf_token': csrf}).status_code, 302)
        return browser

    def test_dashboard_reason_escaping_read_action_and_csrf(self):
        reason = '<script>Incomplete</script>'
        backend.reject_account(self.admin, self.account, reason)
        notification = backend.list_notifications(self.owner)[0]['id']
        owner = self.login('owner@example.test')
        other = self.login('other@example.test')
        page = owner.get('/')
        self.assertIn('Notificações', page.text)
        self.assertIn('Marcar como lida', page.text)
        self.assertIn('&lt;script&gt;Incomplete&lt;/script&gt;', page.text)
        self.assertNotIn(reason, page.text)
        self.assertNotIn('Incomplete', other.get('/').text)
        self.assertIsNone(backend.list_notifications(self.owner)[0]['read_at'])
        path = f'/notifications/{notification}/read'
        self.assertEqual(owner.post(path, data={}).status_code, 400)
        other_csrf = re.search(r'name="csrf_token" value="([^"]+)"', other.get('/').text).group(1)
        self.assertEqual(other.post(path, data={'csrf_token': other_csrf}).status_code, 400)
        self.assertIsNone(backend.list_notifications(self.owner)[0]['read_at'])
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        response = owner.post(path, data={'csrf_token': csrf}, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Lida em', response.text)
        self.assertNotIn('Marcar como lida', response.text)
        self.assertIsNotNone(backend.list_notifications(self.owner)[0]['read_at'])

    def test_global_unread_count_and_bulk_read_are_scoped_and_preserve_history(self):
        with transaction() as connection:
            for _ in range(51):
                account = insert_id(connection, 'INSERT INTO BANK_ACCOUNTS (USER_ID) VALUES (?)', (self.owner_id,))
                execute(connection, "INSERT INTO BANK_NOTIFICATIONS (USER_ID, ACCOUNT_ID, KIND) VALUES (?, ?, 'ACCOUNT_APPROVED')", (self.owner_id, account))
        other_account = backend.create_account(self.other)
        backend.approve_account(self.admin, other_account)
        self.assertEqual(backend.count_unread_notifications(self.owner), 51)
        first = backend.list_notifications(self.owner)[0]['id']
        backend.mark_notification_read(self.owner, first)
        first_read_at = backend.list_notifications(self.owner)[0]['read_at']
        self.assertEqual(backend.count_unread_notifications(self.owner), 50)
        with self.assertRaises(BankError):
            backend.mark_all_notifications_read(self.admin)
        with self.assertRaises(BankError):
            backend.count_unread_notifications(self.admin)
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: backend.mark_all_notifications_read(self.owner), range(2)))
        self.assertEqual(backend.count_unread_notifications(self.owner), 0)
        self.assertEqual(backend.count_unread_notifications(self.other), 1)
        rows = backend.list_notifications(self.owner)
        self.assertEqual(len(rows), 50)
        self.assertEqual(rows[0]['read_at'], first_read_at)
        self.assertTrue(all(row['read_at'] for row in rows))
        backend.approve_account(self.admin, self.account)
        self.assertEqual(backend.count_unread_notifications(self.owner), 1)

    def test_live_feed_and_bulk_http_authorization_csrf_and_fresh_updates(self):
        owner = self.login('owner@example.test')
        other = self.login('other@example.test')
        admin = self.login('admin@example.test')
        self.assertEqual(owner.get('/notifications/feed').json['unread_count'], 0)
        reason = '<script>Missing documents</script>'
        backend.reject_account(self.admin, self.account, reason)
        response = owner.get('/notifications/feed')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.json['unread_count'], 1)
        self.assertIn('&lt;script&gt;Missing documents&lt;/script&gt;', response.json['html'])
        self.assertNotIn(reason, response.json['html'])
        self.assertEqual(other.get('/notifications/feed').json['unread_count'], 0)
        self.assertNotIn('Missing documents', other.get('/notifications/feed').json['html'])
        self.assertEqual(admin.get('/notifications/feed').status_code, 403)
        self.assertEqual(self.app.test_client().get('/notifications/feed').status_code, 302)
        page = owner.get('/')
        self.assertIn('id="notification-bell"', page.text)
        self.assertIn('notifications.js', page.text)
        self.assertIn("script-src 'self'", page.headers['Content-Security-Policy'])
        self.assertNotIn("'unsafe-inline'", page.headers['Content-Security-Policy'])
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        self.assertEqual(owner.post('/notifications/read-all', headers={'Accept': 'application/json'}).status_code, 400)
        self.assertEqual(backend.count_unread_notifications(self.owner), 1)
        response = owner.post('/notifications/read-all', data={'csrf_token': csrf}, headers={'Accept': 'application/json'})
        self.assertEqual(response.json, {'ok': True})
        self.assertEqual(owner.get('/notifications/feed').json['unread_count'], 0)
        self.assertIn('Lida em', owner.get('/notifications/feed').json['html'])
        backend.revoke_session(self.owner)
        # The browser has a separate session; explicitly revoke its session via logout.
        owner.post('/logout', data={'csrf_token': csrf})
        self.assertEqual(owner.get('/notifications/feed').status_code, 302)

    def test_bulk_read_failure_rolls_back_and_html_fallback_works(self):
        backend.approve_account(self.admin, self.account)
        second = backend.create_account(self.owner)
        backend.reject_account(self.admin, second, 'Pedido incompleto')
        with transaction() as connection:
            execute(connection, "CREATE TRIGGER TEST_BULK_READ_FAILURE NO CASCADE BEFORE UPDATE ON BANK_NOTIFICATIONS FOR EACH ROW SIGNAL SQLSTATE '75001' SET MESSAGE_TEXT = 'Test failure'")
        try:
            with self.assertRaises(BackendError):
                backend.mark_all_notifications_read(self.owner)
            self.assertEqual(backend.count_unread_notifications(self.owner), 2)
        finally:
            with transaction() as connection:
                execute(connection, 'DROP TRIGGER TEST_BULK_READ_FAILURE')
        owner = self.login('owner@example.test')
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', owner.get('/').text).group(1)
        self.assertEqual(owner.post('/notifications/read-all', data={'csrf_token': csrf}).status_code, 302)
        self.assertEqual(backend.count_unread_notifications(self.owner), 0)

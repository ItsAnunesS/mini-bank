"""Product workflows run against real COBOL and an isolated Db2 schema."""
import re
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4

from bank import client as backend
from bank.client import BankError, BackendError
from fixtures import IsolatedBankTestCase
from support import execute, insert_id, query, transaction


class ProductTests(IsolatedBankTestCase):
    def setUp(self):
        with transaction() as c:
            for table in self.tables:
                execute(c, f'DELETE FROM {table}')
            for role, email in [('ADMIN', 'admin@example.test'), ('CUSTOMER', 'owner@example.test'), ('CUSTOMER', 'other@example.test')]:
                insert_id(c, 'INSERT INTO BANK_USERS (NAME, EMAIL, PASSWORD_HASH, ROLE) VALUES (?, ?, ?, ?)',
                          ('Product tester', email, self.hashed, role))
            self.month = query(c, "SELECT VARCHAR_FORMAT(CURRENT DATE, 'YYYY-MM') AS MONTH FROM SYSIBM.SYSDUMMY1")[0]['month']
        self.admin = backend.authenticate('admin@example.test', self.password)
        self.owner = backend.authenticate('owner@example.test', self.password)
        self.other = backend.authenticate('other@example.test', self.password)
        self.account = backend.create_account(self.owner)
        self.target = backend.create_account(self.other)
        backend.approve_account(self.admin, self.account)
        backend.approve_account(self.admin, self.target)

    def call(self, command, token=None, **fields):
        return backend.call_backend(command, token=token or self.owner, **fields)

    def deposit(self, amount='1000'):
        return backend.move_money(self.admin, None, self.account, amount, str(uuid4()))

    def balance(self):
        return backend.statement(self.owner, self.account)[0]['balance']

    def loan(self, amount='1000', months=3, key=None):
        return int(self.call('REQUEST_LOAN', target=self.account, amount=amount,
                             months=months, request_key=key or str(uuid4()))['id'])

    def read_loan(self, loan):
        return self.call('LOAN', original=loan)

    def test_monthly_statement_totals_boundaries_empty_and_ownership(self):
        self.deposit('100')
        with transaction() as c:
            execute(c, 'UPDATE BANK_ENTRIES SET CREATED_AT = CURRENT DATE - (DAY(CURRENT DATE) - 1) DAYS - 1 DAY WHERE ACCOUNT_ID = ?', (self.account,))
        self.deposit('20')
        backend.move_money(self.owner, self.account, self.target, '3', str(uuid4()))
        result = self.call('MONTHLY_STATEMENT', target=self.account, month=self.month)
        self.assertEqual(result['summary'], dict(opening_balance=Decimal('100'), credits=Decimal('20'), debits=Decimal('3'), closing_balance=Decimal('117')))
        self.assertEqual(len(result['rows']), 2)
        empty = self.call('MONTHLY_STATEMENT', target=self.account, month='1900-01')
        self.assertEqual(empty['summary']['closing_balance'], 0)
        self.assertEqual(empty['rows'], [])
        for month in ('2026-00', '2026-13', '9998-12', '202x-01', 'x'):
            with self.assertRaises(BankError):
                self.call('MONTHLY_STATEMENT', target=self.account, month=month)
        with self.assertRaises(BankError):
            self.call('MONTHLY_STATEMENT', token=self.other, target=self.account, month=self.month)

    def test_transfer_limits_validation_replay_and_daily_concurrency(self):
        self.deposit('10000')
        with self.assertRaises(BankError) as error:
            backend.move_money(self.owner, self.account, self.target, '1000.01', str(uuid4()))
        self.assertEqual(error.exception.code, 'TRANSFER_LIMIT')
        self.call('SET_POLICY', token=self.admin, target=self.account, amount='100', daily_limit='150', status='0')
        for token, daily in [(self.owner, '150'), (self.admin, '99')]:
            with self.assertRaises(BankError):
                self.call('SET_POLICY', token=token, target=self.account, amount='100', daily_limit=daily, status='0')
        key = str(uuid4())
        backend.move_money(self.owner, self.account, self.target, '100', key)
        backend.move_money(self.owner, self.account, self.target, '100', key)
        def transfer(_):
            try:
                backend.move_money(self.owner, self.account, self.target, '40', str(uuid4()))
                return 'OK'
            except BankError as error:
                return error.code
            finally:
                backend.close_backend()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(transfer, range(2)))
        self.assertCountEqual(outcomes, ['OK', 'DAILY_LIMIT'])
        self.assertEqual(self.balance(), 9860)

    def test_fees_exemptions_idempotency_and_reconciliation(self):
        self.deposit('10')
        self.call('CHARGE_FEE', token=self.admin, target=self.account)
        self.call('CHARGE_FEE', token=self.admin, target=self.account)
        self.assertEqual(self.balance(), 8)
        self.call('SET_POLICY', token=self.admin, target=self.target, amount='1000', daily_limit='5000', status='1')
        with self.assertRaises(BankError) as error:
            self.call('CHARGE_FEE', token=self.admin, target=self.target)
        self.assertEqual(error.exception.code, 'FEE_EXEMPT')
        savings = backend.create_account(self.owner, account_type='SAVINGS')
        backend.approve_account(self.admin, savings)
        with self.assertRaises(BankError) as error:
            self.call('CHARGE_FEE', token=self.admin, target=savings)
        self.assertEqual(error.exception.code, 'FEE_EXEMPT')
        with self.assertRaises(BankError):
            self.call('CHARGE_FEE', target=self.account)
        report = backend.run_report(self.admin)
        self.assertEqual(backend.read_report(self.admin, report)[0]['status'], 'OK')

    def test_fee_failure_is_atomic_and_retry_does_not_duplicate(self):
        with self.assertRaises(BankError):
            self.call('CHARGE_FEE', token=self.admin, target=self.account)
        self.deposit('10')
        with transaction() as c:
            execute(c, "CREATE TRIGGER FAIL_PRODUCT_FEE NO CASCADE BEFORE INSERT ON BANK_FEES REFERENCING NEW AS N FOR EACH ROW SIGNAL SQLSTATE '75001' SET MESSAGE_TEXT = 'test failure'")
        try:
            with self.assertRaises(BackendError):
                self.call('CHARGE_FEE', token=self.admin, target=self.account)
        finally:
            with transaction() as c:
                execute(c, 'DROP TRIGGER FAIL_PRODUCT_FEE')
        self.assertEqual(self.balance(), 10)
        self.call('CHARGE_FEE', token=self.admin, target=self.account)
        self.assertEqual(self.balance(), 8)

    def test_loan_schedule_rounding_approval_replay_and_full_payment(self):
        key = str(uuid4())
        loan = self.loan(key=key)
        self.assertEqual(self.loan(key=key), loan)
        result = self.read_loan(loan)
        self.assertEqual(result['loan']['total_interest'], Decimal('10.00'))
        self.assertEqual([row['capital'] for row in result['rows']], [Decimal('333.33'), Decimal('333.33'), Decimal('333.34')])
        self.assertEqual(result['rows'][-1]['remaining'], 0)
        self.assertEqual(self.balance(), 0)
        self.call('APPROVE_LOAN', token=self.admin, original=loan)
        self.call('APPROVE_LOAN', token=self.admin, original=loan)
        self.assertEqual(self.balance(), 1000)
        self.deposit('10')
        for _ in range(3):
            payment_key = str(uuid4())
            self.call('PAY_LOAN', original=loan, request_key=payment_key)
            self.call('PAY_LOAN', original=loan, request_key=payment_key)
        self.assertEqual(self.balance(), 0)
        self.assertEqual(self.read_loan(loan)['loan']['status'], 'PAID')
        report = backend.run_report(self.admin)
        self.assertEqual(backend.read_report(self.admin, report)[0]['status'], 'OK')
        self.call('CLOSE_ACCOUNT', target=self.account)
        self.assertEqual(backend.statement(self.owner, self.account)[0]['status'], 'CLOSED')

    def test_loan_boundaries_security_rejection_and_closure(self):
        for amount, months in [('99',3),('100001',3),('100',2),('100',61),('100','3x')]:
            with self.assertRaises(BankError):
                self.loan(amount, months)
        loan = self.loan('100', 60)
        result = self.read_loan(loan)
        self.assertEqual(len(result['rows']), 60)
        self.assertEqual(sum(row['capital'] for row in result['rows']), 100)
        with self.assertRaises(BankError):
            self.call('CLOSE_ACCOUNT', target=self.account)
        for command in ('LOAN', 'APPROVE_LOAN', 'REJECT_LOAN', 'PAY_LOAN'):
            with self.assertRaises(BankError):
                self.call(command, token=self.other, original=loan, reason='not authorized', request_key=str(uuid4()))
        with self.assertRaises(BankError):
            self.call('REJECT_LOAN', token=self.admin, original=loan, reason='no')
        self.call('REJECT_LOAN', token=self.admin, original=loan, reason='<script>declined</script>')
        self.assertEqual(self.read_loan(loan)['loan']['reason'], '<script>declined</script>')
        with self.assertRaises(BankError):
            self.call('APPROVE_LOAN', token=self.admin, original=loan)
        self.call('CLOSE_ACCOUNT', target=self.account)
        self.call('CLOSE_ACCOUNT', target=self.account)
        with self.assertRaises(BankError):
            backend.set_account_status(self.admin, self.account, 'ACTIVE')
        with self.assertRaises(BankError):
            self.deposit()
        self.assertEqual(self.call('MONTHLY_STATEMENT', target=self.account, month=self.month)['rows'], [])

    def test_loan_disbursement_failure_rolls_back_and_payment_needs_funds(self):
        loan = self.loan()
        with transaction() as c:
            execute(c, "CREATE TRIGGER FAIL_PRODUCT_LOAN NO CASCADE BEFORE UPDATE ON BANK_LOANS REFERENCING NEW AS N FOR EACH ROW WHEN (N.STATUS = 'ACTIVE') SIGNAL SQLSTATE '75001' SET MESSAGE_TEXT = 'test failure'")
        try:
            with self.assertRaises(BackendError):
                self.call('APPROVE_LOAN', token=self.admin, original=loan)
        finally:
            with transaction() as c:
                execute(c, 'DROP TRIGGER FAIL_PRODUCT_LOAN')
        self.assertEqual(self.balance(), 0)
        self.assertEqual(self.read_loan(loan)['loan']['status'], 'PENDING')
        self.call('APPROVE_LOAN', token=self.admin, original=loan)
        backend.move_money(self.owner, self.account, self.target, '1000', str(uuid4()))
        with self.assertRaises(BankError):
            self.call('PAY_LOAN', original=loan, request_key=str(uuid4()))
        self.assertTrue(all(row['paid_at'] is None for row in self.read_loan(loan)['rows']))
        with self.assertRaises(BankError):
            self.call('CLOSE_ACCOUNT', target=self.account)

    def test_closure_ownership_balance_and_released_slot(self):
        self.deposit('1')
        for token in (self.owner, self.other, self.admin):
            with self.assertRaises(BankError):
                self.call('CLOSE_ACCOUNT', token=token, target=self.account)
        backend.move_money(self.owner, self.account, self.target, '1', str(uuid4()))
        self.call('CLOSE_ACCOUNT', target=self.account)
        for _ in range(3):
            backend.create_account(self.owner)
        with self.assertRaises(BankError):
            backend.create_account(self.owner)

    def test_http_branding_views_csv_authorization_and_csrf(self):
        self.deposit('20')
        loan = self.loan()
        browser = self.app.test_client()
        browser.set_cookie('bank_auth', self.owner)
        paths = ['/', f'/accounts/{self.account}', f'/accounts/{self.account}/statements', f'/accounts/{self.account}/loans/new', '/loans', f'/loans/{loan}']
        for path in paths:
            response = browser.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn(b'MiniBank', response.data)
        csv = browser.get(f'/accounts/{self.account}/statements?month={self.month}&format=csv')
        self.assertEqual(csv.status_code, 200)
        self.assertIn('attachment', csv.headers['Content-Disposition'])
        self.assertIn('20.00', csv.text)
        self.assertEqual(browser.get('/static/minibank.svg').status_code, 200)
        self.assertEqual(browser.post(f'/accounts/{self.account}/close', data={'confirmed': 'yes'}).status_code, 400)
        page = browser.get(f'/loans/{loan}')
        # A valid CSRF token still cannot grant an admin operation to a customer.
        dashboard = browser.get('/')
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', dashboard.text).group(1)
        self.assertEqual(browser.post(f'/admin/loans/{loan}/approve', data={'csrf_token': csrf}).status_code, 403)
        browser.set_cookie('bank_auth', self.other)
        self.assertEqual(browser.get(f'/accounts/{self.account}/statements?format=csv').status_code, 400)
        browser.set_cookie('bank_auth', self.admin)
        self.assertEqual(browser.get(f'/loans/{loan}').status_code, 200)
        self.call('REJECT_LOAN', token=self.admin, original=loan, reason='<script>declined</script>')
        self.assertIn('&lt;script&gt;', browser.get(f'/loans/{loan}').text)

    def test_concurrent_approval_and_payment_with_same_key_are_idempotent(self):
        loan = self.loan()
        def approve(_):
            try:
                return self.call('APPROVE_LOAN', token=self.admin, original=loan)['ok']
            finally:
                backend.close_backend()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(approve, range(2))), [True, True])
        self.assertEqual(self.balance(), 1000)
        key = str(uuid4())
        def pay(_):
            try:
                return self.call('PAY_LOAN', original=loan, request_key=key)['ok']
            finally:
                backend.close_backend()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(pay, range(2))), [True, True])
        self.assertEqual(self.balance(), Decimal('661.67'))
        self.assertEqual(sum(row['paid_at'] is not None for row in self.read_loan(loan)['rows']), 1)

    def test_payment_failure_rolls_back_and_product_reversal_is_forbidden(self):
        loan = self.loan()
        self.call('APPROVE_LOAN', token=self.admin, original=loan)
        disbursement = self.read_loan(loan)['loan']['disbursement_id']
        with self.assertRaises(BankError) as error:
            backend.reverse_transaction(self.admin, disbursement, 'Cannot reverse product')
        self.assertEqual(error.exception.code, 'REVERSAL_NOT_ALLOWED')
        with transaction() as c:
            execute(c, "CREATE TRIGGER FAIL_PRODUCT_PAYMENT NO CASCADE BEFORE UPDATE ON BANK_INSTALLMENTS REFERENCING NEW AS N FOR EACH ROW WHEN (N.PAID_AT IS NOT NULL) SIGNAL SQLSTATE '75001' SET MESSAGE_TEXT = 'test failure'")
        key = str(uuid4())
        try:
            with self.assertRaises(BackendError):
                self.call('PAY_LOAN', original=loan, request_key=key)
        finally:
            with transaction() as c:
                execute(c, 'DROP TRIGGER FAIL_PRODUCT_PAYMENT')
        self.assertEqual(self.balance(), 1000)
        self.assertTrue(all(row['paid_at'] is None for row in self.read_loan(loan)['rows']))
        self.call('PAY_LOAN', original=loan, request_key=key)
        self.assertEqual(self.balance(), Decimal('661.67'))

    def test_concurrent_fees_charge_once(self):
        self.deposit('10')
        def charge(_):
            try:
                return self.call('CHARGE_FEE', token=self.admin, target=self.account)['ok']
            finally:
                backend.close_backend()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(charge, range(2))), [True, True])
        self.assertEqual(self.balance(), 8)

    def test_statement_overflow_is_rejected_without_partial_export(self):
        self.deposit('10')
        with transaction() as c:
            admin_id = query(c, "SELECT ID FROM BANK_USERS WHERE ROLE = 'ADMIN'")[0]['id']
            for _ in range(1000):
                tid = insert_id(c, "INSERT INTO BANK_TRANSACTIONS (ACTOR_ID, REQUEST_KEY, KIND, TARGET_ID, AMOUNT) VALUES (?, ?, 'DEPOSIT', ?, 1)", (admin_id, str(uuid4()), self.account))
                execute(c, "INSERT INTO BANK_ENTRIES (TRANSACTION_ID, ACCOUNT_ID, DIRECTION, AMOUNT, BALANCE_AFTER) VALUES (?, ?, '+', 1, 10)", (tid, self.account))
        with self.assertRaises(BankError) as error:
            self.call('MONTHLY_STATEMENT', target=self.account, month=self.month)
        self.assertEqual(error.exception.code, 'STATEMENT_LIMIT')
        browser = self.app.test_client()
        browser.set_cookie('bank_auth', self.owner)
        response = browser.get(f'/accounts/{self.account}/statements?format=csv')
        self.assertEqual(response.status_code, 400)
        self.assertNotIn('Content-Disposition', response.headers)

    def test_http_product_forms_complete_workflow(self):
        browser = self.app.test_client()
        def post(path, token, **data):
            browser.set_cookie('bank_auth', token)
            page = browser.get('/')
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
            return browser.post(path, data={'csrf_token': csrf, **data})
        self.deposit('10')
        response = post(f'/admin/accounts/{self.account}/policy', self.admin,
                        transfer_limit='2000', daily_limit='6000', fee_exempt='yes')
        self.assertEqual(response.status_code, 302)
        account = backend.statement(self.owner, self.account)[0]
        self.assertEqual(account['transfer_limit'], 2000)
        self.assertEqual(account['fee_exempt'], 1)
        post(f'/admin/accounts/{self.account}/policy', self.admin, transfer_limit='2000', daily_limit='6000')
        self.assertEqual(post(f'/admin/accounts/{self.account}/fee', self.admin).status_code, 302)
        response = post(f'/accounts/{self.account}/loans', self.owner, amount='100', months='3', request_key=str(uuid4()))
        self.assertEqual(response.status_code, 302)
        loan = int(response.headers['Location'].rsplit('/', 1)[1])
        self.assertEqual(post(f'/admin/loans/{loan}/approve', self.admin).status_code, 302)
        self.assertEqual(post(f'/loans/{loan}/pay', self.owner, request_key=str(uuid4())).status_code, 302)
        new_account = backend.create_account(self.owner)
        backend.approve_account(self.admin, new_account)
        self.assertEqual(post(f'/accounts/{new_account}/close', self.owner, confirmed='yes').status_code, 302)
        self.assertEqual(backend.statement(self.owner, new_account)[0]['status'], 'CLOSED')

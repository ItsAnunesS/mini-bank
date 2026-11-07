"""Daily operations use a disposable schema, never the user's banking records."""
import os
import re
import secrets
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from bank import client as backend
from bank.client import BankError, BackendError
from bank.web import create_app
from support import execute, insert_id, query, transaction
from werkzeug.security import generate_password_hash


from fixtures import IsolatedBankTestCase


class DailyOperationsTests(IsolatedBankTestCase):
    def setUp(self):
        with transaction() as connection:
            for table in self.tables:
                execute(connection, f'DELETE FROM {table}')
            self.admin = insert_id(connection, 'INSERT INTO BANK_USERS (NAME, EMAIL, PASSWORD_HASH, ROLE) VALUES (?, ?, ?, ?)',
                                   ('Test admin', 'admin@example.test', self.hashed, 'ADMIN'))
            self.owner = insert_id(connection, 'INSERT INTO BANK_USERS (NAME, EMAIL, PASSWORD_HASH, ROLE) VALUES (?, ?, ?, ?)',
                                   ('Test customer', 'customer@example.test', self.hashed, 'CUSTOMER'))
        self.admin_token = backend.authenticate('admin@example.test', self.password)
        self.customer_token = backend.authenticate('customer@example.test', self.password)
        self.current = backend.create_account(self.admin_token, 'customer@example.test')
        self.savings = backend.create_account(self.admin_token, 'customer@example.test', 'SAVINGS')
        backend.approve_account(self.admin_token, self.current)
        backend.approve_account(self.admin_token, self.savings)
        self.deposit_id = self.deposit(self.current, '100')
        self.savings_deposit_id = self.deposit(self.savings, '50')

    def deposit(self, account, amount):
        return backend.move_money(self.admin_token, None, account, amount, str(uuid4()))

    def balance(self, account):
        return backend.statement(self.customer_token, account)[0]['balance']

    def report(self, close=False):
        report_id = backend.run_report(self.admin_token, close_day=close)
        return report_id, *backend.read_report(self.admin_token, report_id)

    def test_close_rounding_concurrency_and_snapshot(self):
        self.assertEqual(backend.statement(self.customer_token, self.savings)[0]['account_type'], 'SAVINGS')
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: backend.run_report(self.admin_token, True), range(2)))
        self.assertEqual(ids[0], ids[1])
        header, rows = backend.read_report(self.admin_token, ids[0])
        self.assertEqual(header['status'], 'OK')
        self.assertEqual(header['total_interest'], Decimal('0.01'))
        self.assertEqual(self.balance(self.current), Decimal('100'))
        self.assertEqual(self.balance(self.savings), Decimal('50.01'))
        interest = backend.statement(self.customer_token, self.savings)[1][0]
        self.assertEqual(interest['kind'], 'INTEREST')
        self.deposit(self.savings, '20')
        self.assertEqual(backend.read_report(self.admin_token, ids[0])[1], rows)
        self.assertEqual(self.report()[1]['status'], 'OK')
        backend.reverse_transaction(self.admin_token, interest['transaction_id'], 'Correct interest posting')
        self.assertEqual(self.balance(self.savings), Decimal('70'))
        self.assertEqual(backend.run_report(self.admin_token, True), ids[0])
        self.assertEqual(self.balance(self.savings), Decimal('70'))

    def test_mismatch_blocks_interest_and_allows_retry(self):
        with transaction() as connection:
            execute(connection, 'UPDATE BANK_ACCOUNTS SET BALANCE = BALANCE + 1 WHERE ID = ?', (self.current,))
        failed_id, header, rows = self.report(True)
        self.assertEqual(header['status'], 'MISMATCH')
        self.assertEqual(header['mismatch_count'], 1)
        self.assertEqual(header['total_interest'], Decimal('0'))
        self.assertEqual(self.balance(self.savings), Decimal('50'))
        self.assertEqual(self.balance(self.current), Decimal('101'))
        self.assertEqual(next(row for row in rows if row['account_id'] == self.current)['difference'], Decimal('1'))
        with transaction() as connection:
            self.assertEqual(query(connection, 'SELECT * FROM BANK_DAILY_CLOSES'), [])
            execute(connection, 'UPDATE BANK_ACCOUNTS SET BALANCE = BALANCE - 1 WHERE ID = ?', (self.current,))
        success_id, header, _ = self.report(True)
        self.assertNotEqual(success_id, failed_id)
        self.assertEqual(header['status'], 'OK')
        self.assertEqual(backend.read_report(self.admin_token, failed_id)[0]['status'], 'MISMATCH')

    def test_ledger_chain_and_operation_shape(self):
        self.deposit(self.current, '1')
        with transaction() as connection:
            execute(connection, 'UPDATE BANK_ENTRIES SET BALANCE_AFTER = 99 WHERE TRANSACTION_ID = ?', (self.deposit_id,))
        _, header, rows = self.report()
        self.assertEqual(header['status'], 'MISMATCH')
        self.assertEqual(next(row for row in rows if row['account_id'] == self.current)['issue'], 'LEDGER_CHAIN')
        with transaction() as connection:
            execute(connection, 'UPDATE BANK_ENTRIES SET BALANCE_AFTER = 100 WHERE TRANSACTION_ID = ?', (self.deposit_id,))
            execute(connection, 'UPDATE BANK_TRANSACTIONS SET AMOUNT = 99 WHERE ID = ?', (self.deposit_id,))
        _, _, rows = self.report()
        self.assertEqual(next(row for row in rows if row['account_id'] == self.current)['issue'], 'TRANSACTION_MISMATCH')

    def test_reversal_is_compensating_and_unique(self):
        with self.assertRaises(BankError) as error:
            backend.reverse_transaction(self.admin_token, self.deposit_id, 'áéí')
        self.assertEqual(error.exception.code, 'INVALID_REASON')
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: backend.reverse_transaction(self.admin_token, self.deposit_id,
                                                                      'Duplicate simulated deposit'), range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(self.balance(self.current), Decimal('0'))
        original = backend.read_transaction(self.admin_token, self.deposit_id)
        self.assertEqual(original['kind'], 'DEPOSIT')
        self.assertEqual(original['amount'], Decimal('100'))
        self.assertEqual(original['reversed_by'], ids[0])
        with self.assertRaises(BankError):
            backend.reverse_transaction(self.admin_token, ids[0], 'Cannot reverse reversal')
        with self.assertRaises(BankError):
            backend.reverse_transaction(self.admin_token, self.deposit_id, 'Different reason')
        self.assertEqual(self.report()[1]['status'], 'OK')

    def test_transfer_reversal_checks_funds_and_blocked_accounts(self):
        original = backend.move_money(self.customer_token, self.current, self.savings, '100', str(uuid4()))
        backend.move_money(self.customer_token, self.savings, self.current, '120', str(uuid4()))
        with self.assertRaises(BankError) as error:
            backend.reverse_transaction(self.admin_token, original, 'Correct transfer destination')
        self.assertEqual(error.exception.code, 'INSUFFICIENT_FUNDS')
        self.deposit(self.savings, '70')
        backend.set_account_status(self.admin_token, self.current, 'BLOCKED')
        with self.assertRaises(BankError):
            backend.reverse_transaction(self.admin_token, original, 'Correct transfer destination')
        backend.set_account_status(self.admin_token, self.current, 'ACTIVE')
        backend.reverse_transaction(self.admin_token, original, 'Correct transfer destination')
        self.assertEqual(self.balance(self.current), Decimal('220'))
        self.assertEqual(self.balance(self.savings), Decimal('0'))
        self.assertEqual(self.report()[1]['status'], 'OK')

    def test_close_rolls_back_on_posting_failure(self):
        with transaction() as connection:
            execute(connection, "CREATE TRIGGER TEST_INTEREST_FAILURE NO CASCADE BEFORE INSERT ON BANK_ENTRIES "
                    "REFERENCING NEW AS N FOR EACH ROW "
                    f"WHEN (N.ACCOUNT_ID = {self.savings}) SIGNAL SQLSTATE '75001' SET MESSAGE_TEXT = 'Test failure'")
        try:
            with self.assertRaises(BackendError):
                backend.run_report(self.admin_token, True)
            with self.assertRaises(BackendError):
                backend.reverse_transaction(self.admin_token, self.savings_deposit_id, 'Rollback reversal verification')
            self.assertIsNone(backend.read_transaction(self.admin_token, self.savings_deposit_id)['reversed_by'])
        finally:
            with transaction() as connection:
                execute(connection, 'DROP TRIGGER TEST_INTEREST_FAILURE')
        self.assertEqual(self.balance(self.savings), Decimal('50'))
        with transaction() as connection:
            self.assertEqual(query(connection, 'SELECT ID FROM BANK_REPORTS'), [])
            self.assertEqual(query(connection, 'SELECT REPORT_ID FROM BANK_DAILY_CLOSES'), [])
            self.assertEqual(query(connection, "SELECT ID FROM BANK_TRANSACTIONS WHERE KIND = 'INTEREST'"), [])
        self.assertEqual(self.report(True)[1]['status'], 'OK')

    def test_blocked_savings_and_paginated_report(self):
        backend.set_account_status(self.admin_token, self.savings, 'BLOCKED')
        with transaction() as connection:
            for _ in range(51):
                execute(connection, 'INSERT INTO BANK_ACCOUNTS (USER_ID) VALUES (?)', (self.owner,))
        report_id, header, rows = self.report(True)
        self.assertEqual(header['account_count'], 53)
        self.assertEqual(header['total_interest'], Decimal('0'))
        self.assertEqual(len(rows), 50)
        _, remaining = backend.read_report(self.admin_token, report_id, rows[-1]['account_id'])
        self.assertEqual(len(remaining), 3)
        self.assertFalse({r['account_id'] for r in rows} & {r['account_id'] for r in remaining})

    def test_batch_limit_rolls_back_without_partial_report(self):
        with transaction() as connection:
            for _ in range(499):
                execute(connection, 'INSERT INTO BANK_ACCOUNTS (USER_ID) VALUES (?)', (self.owner,))
        with self.assertRaises(BankError) as error:
            backend.run_report(self.admin_token, True)
        self.assertEqual(error.exception.code, 'REPORT_LIMIT')
        self.assertEqual(self.balance(self.savings), Decimal('50'))
        with transaction() as connection:
            self.assertEqual(query(connection, 'SELECT ID FROM BANK_REPORTS'), [])

    def login(self, email):
        client = self.app.test_client()
        page = client.get('/login')
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        self.assertEqual(client.post('/login', data={'email': email, 'password': self.password,
                                                    'csrf_token': csrf}).status_code, 302)
        return client

    def test_authorization_csrf_and_admin_pages(self):
        for command, fields in (
            ('REPORTS', {}), ('RECONCILE', {}), ('CLOSE_DAY', {}), ('REPORT', {'target': '1'}),
            ('TRANSACTION', {'original': self.deposit_id}),
            ('REVERSE', {'original': self.deposit_id, 'reason': 'Unauthorized reversal'})):
            with self.subTest(command=command), self.assertRaises(BankError) as error:
                backend.call_backend(command, token=self.customer_token, **fields)
            self.assertEqual(error.exception.code, 'FORBIDDEN')
        customer = self.login('customer@example.test')
        self.assertEqual(customer.get('/admin/reports').status_code, 403)
        admin = self.login('admin@example.test')
        page = admin.get('/admin/reports')
        self.assertEqual(page.status_code, 200)
        self.assertEqual(admin.post('/admin/reports/close').status_code, 400)
        csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        response = admin.post('/admin/reports/reconcile', data={'csrf_token': csrf})
        self.assertEqual(response.status_code, 302)
        self.assertIn('Reconciliação por conta', admin.get(response.location).text)
        page = admin.get(f'/admin/transactions/{self.deposit_id}')
        self.assertIn('Estornar integralmente', page.text)
        response = admin.post(f'/admin/transactions/{self.deposit_id}/reverse',
                              data={'csrf_token': csrf, 'confirmed': 'yes', 'reason': 'HTTP reversal verification'})
        self.assertEqual(response.status_code, 302)
        self.assertIn('Estorno registado', admin.get(response.location).text)

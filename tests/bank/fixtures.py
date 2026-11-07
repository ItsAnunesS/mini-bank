"""Integration fixtures use a disposable schema, never the user's banking records."""
import os
import re
import secrets
import time
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


class IsolatedBankTestCase(unittest.TestCase):
    tables = ('BANK_NOTIFICATIONS', 'BANK_ACCOUNT_RESOLUTIONS', 'BANK_ACCOUNT_APPROVALS', 'BANK_DAILY_CLOSES', 'BANK_REPORT_ACCOUNTS', 'BANK_REPORTS',
              'BANK_REVERSALS', 'BANK_SAVINGS', 'BANK_ENTRIES', 'BANK_TRANSACTIONS',
              'BANK_AUDIT', 'BANK_SESSIONS', 'BANK_ACCOUNTS', 'BANK_USERS')

    @classmethod
    def setUpClass(cls):
        cls.schema = 'BANKTEST_' + uuid4().hex[:16].upper()
        cls.previous_schema = os.environ.get('DB2_SCHEMA')
        with transaction() as connection:
            execute(connection, f'CREATE SCHEMA {cls.schema}')
        os.environ['DB2_SCHEMA'] = cls.schema
        cls.addClassCleanup(cls.remove_schema)
        # Exercise upgrade of a populated v2 schema, including its unnamed check.
        with transaction() as connection:
            for statement in Path('/app/db/migrations/002_create_bank.sql').read_text().split(';'):
                if statement.strip():
                    execute(connection, statement)
            uid = insert_id(connection, 'INSERT INTO BANK_USERS (NAME, EMAIL, PASSWORD_HASH, ROLE) VALUES (?, ?, ?, ?)',
                            ('Migration fixture', 'migration@example.test', 'unused', 'CUSTOMER'))
            aid = insert_id(connection, 'INSERT INTO BANK_ACCOUNTS (USER_ID) VALUES (?)', (uid,))
        backend.initialize_schema()
        backend.initialize_schema()
        with transaction() as connection:
            assert query(connection, 'SELECT USER_ID, BALANCE FROM BANK_ACCOUNTS WHERE ID = ?', (aid,))[0] == {
                'user_id': uid, 'balance': Decimal('0')}
        cls.password = secrets.token_urlsafe(24)
        cls.hashed = generate_password_hash(cls.password)
        cls.app = create_app()
        cls.app.config['TESTING'] = True

    @classmethod
    def remove_schema(cls):
        backend.close_backend()
        # Concurrent test DDL can deadlock on the shared Db2 catalog.
        # Retry the whole rolled-back transaction, never an individual DROP.
        try:
            for attempt in range(3):
                try:
                    with transaction() as connection:
                        existing = {row['tabname'] for row in query(connection,
                                    'SELECT TABNAME FROM SYSCAT.TABLES WHERE TABSCHEMA = ?', (cls.schema,))}
                        for table in cls.tables:
                            if table in existing:
                                execute(connection, f'DROP TABLE "{cls.schema}"."{table}"')
                        execute(connection, f'DROP SCHEMA "{cls.schema}" RESTRICT')
                    break
                except Exception as error:
                    if 'SQLSTATE=40001' not in str(error) or attempt == 2:
                        raise
                    time.sleep(0.2 * (attempt + 1))
        finally:
            if cls.previous_schema is None:
                os.environ.pop('DB2_SCHEMA', None)
            else:
                os.environ['DB2_SCHEMA'] = cls.previous_schema

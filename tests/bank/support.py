"""Test-only Db2 fixtures and assertions; never imported by the web application."""
from contextlib import contextmanager
from decimal import Decimal
import ibm_db
import os
import re
from ledger.repository import open_connection


@contextmanager
def transaction():
    with open_connection() as connection:
        schema = os.environ.get('DB2_SCHEMA')
        if schema:
            if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,127}', schema):
                raise ValueError('Invalid test schema')
            statement = ibm_db.exec_immediate(connection, f'SET CURRENT SCHEMA "{schema}"')
            ibm_db.free_stmt(statement)
        ibm_db.autocommit(connection, ibm_db.SQL_AUTOCOMMIT_OFF)
        try:
            yield connection
            ibm_db.commit(connection)
        except Exception:
            ibm_db.rollback(connection)
            raise


def query(connection, sql, parameters=()):
    statement = ibm_db.prepare(connection, sql)
    try:
        ibm_db.execute(statement, parameters)
        rows = []
        while row := ibm_db.fetch_assoc(statement):
            rows.append({key.lower(): Decimal(str(value)) if key.lower() in
                         ('amount', 'balance', 'balance_after') and value is not None else value
                         for key, value in row.items()})
        return rows
    finally:
        ibm_db.free_stmt(statement)


def execute(connection, sql, parameters=()):
    statement = ibm_db.prepare(connection, sql)
    try:
        ibm_db.execute(statement, parameters)
        return ibm_db.num_rows(statement)
    finally:
        ibm_db.free_stmt(statement)


def insert_id(connection, sql, parameters):
    execute(connection, sql, parameters)
    return int(query(connection, 'SELECT IDENTITY_VAL_LOCAL() AS ID FROM SYSIBM.SYSDUMMY1')[0]['id'])

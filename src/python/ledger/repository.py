"""Db2 persistence with parameterized SQL and exact decimal amounts."""
import os
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

import ibm_db


def connection_value(name):
    value = os.environ[name]
    if not value or any(character in value for character in (';', '{', '}', '\0', '\n', '\r')):
        raise ValueError(f'{name} must be non-empty and cannot contain connection-string delimiters')
    return value


@contextmanager
def open_connection():
    # Uncataloged connections require credentials inside the connection string.
    dsn = (f"DATABASE={connection_value('DB2_DATABASE')};"
           f"HOSTNAME={connection_value('DB2_HOST')};PORT=50000;"
           f"UID={connection_value('DB2_USER')};"
           f"PWD={connection_value('DB2_PASSWORD')};"
           "PROTOCOL=TCPIP;CONNECTTIMEOUT=10;")
    connection = ibm_db.connect(dsn, '', '')
    try:
        yield connection
    finally:
        ibm_db.close(connection)


def execute(connection, sql, parameters=()):
    statement = ibm_db.prepare(connection, sql)
    ibm_db.execute(statement, parameters)
    return statement


class MovementRepository:
    def __init__(self, connection):
        self.connection = connection

    def initialize_schema(self):
        statement = execute(self.connection, "SELECT COUNT(*) FROM SYSCAT.TABLES "
                            "WHERE TABSCHEMA = CURRENT SCHEMA AND TABNAME = 'MOVIMENTOS'")
        if ibm_db.fetch_tuple(statement)[0] == 0:
            migration = Path('/app/db/migrations/001_create_movements.sql')
            execute(self.connection, migration.read_text())

    def add(self, kind, amount, description):
        if (not amount.is_finite() or not 0 < amount <= Decimal('9999999.99')
                or amount != amount.quantize(Decimal('.01'))
                or kind not in ('+', '-') or not description.strip()
                or len(description.encode('utf-8')) > 40):
            raise ValueError('Invalid movement')
        execute(self.connection,
                'INSERT INTO MOVIMENTOS (TIPO, VALOR, DESCRICAO) VALUES (?, ?, ?)',
                (kind, str(amount), description))

    def list_all(self):
        statement = execute(self.connection,
                            'SELECT TIPO, VALOR, DESCRICAO FROM MOVIMENTOS ORDER BY ID')
        while row := ibm_db.fetch_tuple(statement):
            kind, amount, description = row
            yield kind, Decimal(str(amount)), description

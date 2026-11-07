"""Exercise the COBOL application against a real Db2 database."""
import subprocess
import uuid
from decimal import Decimal

import ibm_db
from ledger.repository import open_connection, execute


def run_application(user_input):
    return subprocess.run(['/usr/local/bin/despesas'], input=user_input,
                          text=True, capture_output=True, timeout=60, check=True)


def main():
    marker = 'test-' + uuid.uuid4().hex[:12]
    revenue = marker + " d'agua"
    expense = marker + '-expense'
    try:
        result = run_application(
            f'1\n100.50\n{revenue}\n2\n20.25\n{expense}\n'
            '1\n-2\n1\n1.234\n3\n0\n')
        assert result.stdout.count('Movimento guardado no Db2.') == 2, result.stdout + result.stderr
        assert result.stdout.count('Valor invalido.') == 2, result.stdout
        assert revenue in result.stdout and expense in result.stdout, result.stdout
        # A separate connection verifies that both inserts were committed.
        with open_connection() as connection:
            statement = execute(connection,
                                'SELECT TIPO, VALOR FROM MOVIMENTOS '
                                'WHERE DESCRICAO IN (?, ?) ORDER BY ID', (revenue, expense))
            first = ibm_db.fetch_tuple(statement)
            second = ibm_db.fetch_tuple(statement)
            assert first[0] == '+' and Decimal(str(first[1])) == Decimal('100.50'), first
            assert second[0] == '-' and Decimal(str(second[1])) == Decimal('20.25'), second
            assert ibm_db.fetch_tuple(statement) is False
        result = run_application('3\n0\n')
        assert revenue in result.stdout and expense in result.stdout, result.stdout
        for user_input in ('', '1\n', '1\n12.50\n'):
            result = run_application(user_input)
            assert 'Movimento guardado' not in result.stdout
        print('PASS: COBOL -> Db2, decimals, quotes, validation, EOF and persistence.')
    finally:
        # Remove only rows created by this test run.
        with open_connection() as connection:
            execute(connection, 'DELETE FROM MOVIMENTOS WHERE DESCRICAO IN (?, ?)',
                    (revenue, expense))


if __name__ == '__main__':
    main()

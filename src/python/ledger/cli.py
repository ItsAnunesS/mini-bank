"""Fixed-command interface used by the COBOL repository adapter."""
import argparse
import os
import sys
from decimal import Decimal

from ledger.repository import MovementRepository, open_connection


def print_movements(movements):
    balance = Decimal('0.00')
    for kind, amount, description in movements:
        balance += amount if kind == '+' else -amount
        print(f'{kind} {amount:.2f} EUR | {description}')
    print(f'Saldo: {balance:.2f} EUR')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('init', 'add', 'list'))
    action = parser.parse_args().action
    try:
        with open_connection() as connection:
            repository = MovementRepository(connection)
            if action == 'init':
                repository.initialize_schema()
                print('Tabela MOVIMENTOS pronta.')
            elif action == 'add':
                repository.add(os.environ['MOVEMENT_KIND'],
                               Decimal(os.environ['MOVEMENT_AMOUNT']),
                               os.environ['MOVEMENT_DESCRIPTION'])
            else:
                print_movements(repository.list_all())
    except Exception as error:
        message = str(error).replace(os.environ.get('DB2_PASSWORD', '\0'), '[hidden]')
        print(f'Erro Db2: {message}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

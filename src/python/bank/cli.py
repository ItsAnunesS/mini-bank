"""Container-only schema and first-administrator commands."""
import argparse
from getpass import getpass
from bank.client import initialize_schema, create_first_admin, BankError, BackendError


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=('init', 'create-admin'))
    command = parser.parse_args().command
    if command == 'init':
        initialize_schema()
        print('Esquema bancário pronto.')
        return
    name = input('Nome do administrador: ')
    email = input('Email: ')
    password = getpass('Palavra-passe (mínimo 12 caracteres): ')
    if password != getpass('Confirmar palavra-passe: '):
        raise BankError('As palavras-passe não coincidem.')
    create_first_admin(name, email, password)
    print('Administrador criado. Podes iniciar sessão na aplicação web.')


if __name__ == '__main__':
    try:
        main()
    except (BankError, BackendError, EOFError, KeyboardInterrupt) as error:
        raise SystemExit(str(error) or 'Operação cancelada.')

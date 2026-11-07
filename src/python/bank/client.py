"""Web transport adapter. Banking decisions and SQL live in bank-backend."""
from bank.transport import exchange, close_backend
from uuid import uuid4
from datetime import datetime
from decimal import Decimal


MESSAGES = {
    'ACCOUNT_LIMIT': 'Atingiste o limite: até 3 contas à ordem e 2 poupanças, incluindo contas pendentes e bloqueadas.',
    'NOTIFICATION_UNAVAILABLE': 'Notificação indisponível.',
    'NOT_PENDING': 'Esta conta não tem um pedido pendente de aprovação.',
    'INVALID_ACCOUNT_TYPE': 'Escolhe conta à ordem ou poupança.',
    'INVALID_REASON': 'Indica um motivo com pelo menos 5 caracteres e até 240 bytes.',
    'TRANSACTION_NOT_FOUND': 'Operação não encontrada.',
    'REVERSAL_NOT_ALLOWED': 'Não é permitido estornar um estorno.',
    'ALREADY_REVERSED': 'Esta operação já foi estornada.',
    'REPORT_NOT_FOUND': 'Relatório não encontrado.',
    'REPORT_LIMIT': 'O processamento excedeu o limite desta versão (500 contas ou capacidade decimal).',
    'INVALID_REQUEST': 'Pedido inválido. Verifica os dados introduzidos.',
    'INVALID_EMAIL': 'Introduz um email válido.',
    'INVALID_NAME': 'O nome é obrigatório e aceita até 120 bytes.',
    'INVALID_PASSWORD': 'A palavra-passe deve ter entre 12 e 128 caracteres.',
    'INVALID_AMOUNT': 'Introduz um valor entre 0.01 e 9999999.99, com até duas casas decimais.',
    'INVALID_STATUS': 'Estado inválido.',
    'INVALID_REQUEST_KEY': 'Pedido inválido. Abre novamente o formulário.',
    'UNAUTHENTICATED': 'A sessão terminou. Inicia sessão novamente.',
    'FORBIDDEN': 'Não tens permissão para esta operação.',
    'ADMIN_EXISTS': 'O administrador inicial já existe.',
    'EMAIL_EXISTS': 'Já existe um utilizador com este email.',
    'CUSTOMER_NOT_FOUND': 'Cliente não encontrado.',
    'ACCOUNT_UNAVAILABLE': 'Conta indisponível.',
    'ACCOUNT_BLOCKED': 'Uma das contas não está ativa. Verifica o estado do pedido ou da conta.',
    'SAME_ACCOUNT': 'Escolhe uma conta de destino diferente.',
    'INSUFFICIENT_FUNDS': 'Saldo insuficiente.',
    'BALANCE_LIMIT': 'A conta de destino atingiu o limite de saldo.',
    'REQUEST_CONFLICT': 'Este pedido já foi utilizado para outra operação.',
}


class BankError(Exception):
    """An expected backend rejection translated for the interface."""

    def __init__(self, code):
        self.code = code
        super().__init__(MESSAGES.get(code, code))


class BackendError(Exception):
    """An unavailable or invalid native backend response, without sensitive detail."""


def decode_row(row):
    for key, value in row.items():
        if value is None:
            continue
        if key in ('amount', 'balance', 'balance_after', 'balance_before', 'ledger_balance',
                   'last_balance', 'difference', 'interest_amount', 'total_interest'):
            row[key] = Decimal(value)
        elif key in ('created_at', 'expires_at', 'locked_until', 'read_at'):
            row[key] = datetime.fromisoformat(value)
    return row


def call_backend(command, **fields):
    payload = {'command': command, **{key: str(value) for key, value in fields.items() if value is not None}}
    try:
        response = exchange(payload)
    except (OSError, ValueError) as error:
        raise BackendError('Backend indisponível.') from error
    if not response.get('ok'):
        code = response.get('error', 'DATABASE_ERROR')
        if code == 'DATABASE_ERROR':
            raise BackendError('Não foi possível concluir a operação na base de dados.')
        raise BankError(code)
    response['rows'] = [decode_row(row) for row in response.get('rows', [])]
    for key in ('account', 'report', 'transaction'):
        if key in response:
            response[key] = decode_row(response[key])
    return response


def initialize_schema():
    call_backend('INIT')


def create_first_admin(name, email, password):
    return int(call_backend('CREATE_ADMIN', name=name, email=email, password=password)['id'])


def authenticate(email, password):
    return call_backend('LOGIN', email=email, password=password).get('token')


def session_user(token):
    rows = call_backend('SESSION', token=token)['rows'] if token else []
    return rows[0] if rows else None


def revoke_session(token):
    call_backend('LOGOUT', token=token)


def create_customer(token, name, email, password):
    return int(call_backend('CREATE_CUSTOMER', token=token, name=name, email=email, password=password)['id'])


def create_account(token, email=None, account_type="CURRENT", request_key=None):
    return int(call_backend('CREATE_ACCOUNT', token=token, email=email, account_type=account_type,
                            request_key=str(uuid4()) if request_key is None else request_key)['id'])


def set_account_status(token, account_id, status):
    call_backend('SET_STATUS', token=token, target=account_id, status=status)


def list_accounts(token, before=9223372036854775807):
    return call_backend('ACCOUNTS', token=token, before=before)['rows']


def statement(token, account_id, before=9223372036854775807):
    response = call_backend('STATEMENT', token=token, target=account_id, before=before)
    return response['account'], response['rows']


def list_audit(token, before=9223372036854775807):
    return call_backend('AUDIT', token=token, before=before)['rows']


def move_money(token, source_id, target_id, amount_text, request_key):
    return int(call_backend('MOVE', token=token, source=source_id, target=target_id,
                            amount=amount_text, request_key=request_key)['id'])


def run_report(token, close_day=False):
    return int(call_backend('CLOSE_DAY' if close_day else 'RECONCILE', token=token)['id'])


def list_reports(token, before=9223372036854775807):
    return call_backend('REPORTS', token=token, before=before)['rows']


def read_report(token, report_id, before=9223372036854775807):
    response = call_backend('REPORT', token=token, target=report_id, before=before)
    return response['report'], response['rows']


def read_transaction(token, transaction_id):
    return call_backend('TRANSACTION', token=token, original=transaction_id)['transaction']


def reverse_transaction(token, transaction_id, reason):
    return int(call_backend('REVERSE', token=token, original=transaction_id, reason=reason)['id'])


def approve_account(token, account_id):
    call_backend('APPROVE_ACCOUNT', token=token, target=account_id)


def pending_accounts(token, before=9223372036854775807):
    return call_backend('PENDING_ACCOUNTS', token=token, before=before)['rows']


def reject_account(token, account_id, reason):
    call_backend('REJECT_ACCOUNT', token=token, target=account_id, reason=reason)


def cancel_account(token, account_id):
    call_backend('CANCEL_ACCOUNT', token=token, target=account_id)


def list_notifications(token, before=9223372036854775807):
    return call_backend('NOTIFICATIONS', token=token, before=before)['rows']


def mark_notification_read(token, notification_id):
    call_backend('READ_NOTIFICATION', token=token, target=notification_id)


def count_unread_notifications(token):
    return int(call_backend('UNREAD_NOTIFICATIONS', token=token)['rows'][0]['unread_count'])


def mark_all_notifications_read(token):
    call_backend('READ_ALL_NOTIFICATIONS', token=token)

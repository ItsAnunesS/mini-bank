"""Server-rendered banking interface with CSRF and server-side authorization."""
import os
import secrets
from datetime import timedelta
from functools import wraps
from uuid import uuid4

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFProtect, CSRFError
from werkzeug.exceptions import HTTPException
from bank import client
from bank.client import BankError
from bank.products import register_product_routes


def create_app():
    if len(os.environ.get('BANK_SECRET_KEY', '')) < 32:
        raise RuntimeError('BANK_SECRET_KEY must contain at least 32 characters')
    app = Flask(__name__)
    app.config.update(SECRET_KEY=os.environ['BANK_SECRET_KEY'], MAX_CONTENT_LENGTH=16384,
                      SESSION_COOKIE_NAME='conta_clara_session',
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_SECURE=os.environ.get('BANK_HTTPS') == '1',
                      PERMANENT_SESSION_LIFETIME=timedelta(minutes=30),
                      WTF_CSRF_TIME_LIMIT=1800,
                      TRUSTED_HOSTS=['localhost', '127.0.0.1'])
    CSRFProtect(app)

    def protected(role=None):
        def decorate(view):
            @wraps(view)
            def wrapped(*args, **kwargs):
                if not g.user:
                    return redirect(url_for('login'))
                if role and g.user['role'] != role:
                    abort(403)
                return view(*args, **kwargs)
            return wrapped
        return decorate

    @app.before_request
    def load_user():
        g.user = None
        if request.endpoint not in ('static', 'health'):
            g.user = client.session_user(request.cookies.get('bank_auth'))

    @app.after_request
    def secure_response(response):
        response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; script-src-attr 'none'; img-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.template_filter('account_status')
    def format_account_status(value):
        return {'ACTIVE': 'Ativa', 'BLOCKED': 'Bloqueada', 'PENDING': 'Pendente',
                'REJECTED': 'Rejeitado', 'CANCELLED': 'Cancelado', 'CLOSED': 'Encerrada', 'PAID': 'Liquidado'}.get(value, value)

    @app.template_filter('money')
    def format_money(value):
        return f'{value:,.2f}'.replace(',', '_').replace('.', ',').replace('_', ' ') + ' €'

    @app.template_filter('account_number')
    def format_account(value):
        return f'MB-{int(value):08d}' if value else '—'

    @app.context_processor
    def shared_context():
        notification_count = 0
        if getattr(g, 'user', None) and g.user['role'] == 'CUSTOMER':
            if not hasattr(g, 'notification_count'):
                g.notification_count = client.count_unread_notifications(request.cookies.get('bank_auth'))
            notification_count = g.notification_count
        return {'new_request_key': lambda: str(uuid4()), 'notification_count': notification_count}

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        if g.user:
            return redirect(url_for('dashboard'))
        error = None
        if request.method == 'POST':
            token = client.authenticate(request.form.get('email', ''), request.form.get('password', ''))
            if token:
                session.clear()
                session.permanent = True
                response = redirect(url_for('dashboard'))
                response.set_cookie('bank_auth', token, max_age=1800, httponly=True,
                                    samesite='Strict', secure=app.config['SESSION_COOKIE_SECURE'])
                return response
            error = 'Não foi possível entrar. Verifica os dados ou tenta novamente mais tarde.'
        return render_template('login.html', error=error,
                               email=request.form.get('email', '')[:254]), (401 if error else 200)

    @app.post('/logout')
    @protected()
    def logout():
        client.revoke_session(request.cookies.get('bank_auth', ''))
        session.clear()
        response = redirect(url_for('login'))
        response.delete_cookie('bank_auth')
        return response

    @app.get('/')
    @protected()
    def dashboard():
        token = request.cookies.get('bank_auth')
        notifications = client.list_notifications(token, request.args.get('notification_before', '9223372036854775807')) if g.user['role'] == 'CUSTOMER' else []
        return render_template('dashboard.html', accounts=client.list_accounts(token, page_cursor()),
                               notifications=notifications)

    @app.post('/notifications/<int:notification_id>/read')
    @protected('CUSTOMER')
    def read_notification(notification_id):
        client.mark_notification_read(request.cookies.get('bank_auth'), notification_id)
        if request.accept_mimetypes.best == 'application/json':
            return {'ok': True}
        flash('Notificação marcada como lida.', 'success')
        return redirect(url_for('dashboard', _anchor='notifications'))

    @app.get('/notifications/feed')
    @protected('CUSTOMER')
    def notification_feed():
        token = request.cookies.get('bank_auth')
        notifications = client.list_notifications(token, request.args.get('notification_before', '9223372036854775807'))
        g.notification_count = client.count_unread_notifications(token)
        return {'unread_count': g.notification_count,
                'html': render_template('notifications.html', notifications=notifications)}

    @app.post('/notifications/read-all')
    @protected('CUSTOMER')
    def read_all_notifications():
        client.mark_all_notifications_read(request.cookies.get('bank_auth'))
        if request.accept_mimetypes.best == 'application/json':
            return {'ok': True}
        flash('Todas as notificações foram marcadas como lidas.', 'success')
        return redirect(url_for('dashboard', _anchor='notifications'))

    @app.get('/admin/onboarding')
    @protected('ADMIN')
    def onboarding():
        return render_template('onboarding.html', values={}, mode='customer')

    @app.post('/admin/customers')
    @protected('ADMIN')
    def create_customer():
        client.create_customer(request.cookies.get('bank_auth'), request.form.get('name', ''),
                               request.form.get('email', ''), request.form.get('password', ''))
        flash('Acesso criado. O utilizador já pode entrar e pedir as suas contas bancárias.', 'success')
        return redirect(url_for('onboarding'))

    @app.post('/admin/accounts')
    @protected('ADMIN')
    def create_account():
        account_id = client.create_account(request.cookies.get('bank_auth'), request.form.get('email', ''),
                                           request.form.get('account_type', 'CURRENT'),
                                           request.form.get('request_key', ''))
        flash('Pedido registado. A conta aguarda aprovação.', 'success')
        return redirect(url_for('account_detail', account_id=account_id))

    @app.get('/accounts/new')
    @protected('CUSTOMER')
    def new_account():
        return render_template('request_account.html')

    @app.post('/accounts')
    @protected('CUSTOMER')
    def request_account():
        account_id = client.create_account(request.cookies.get('bank_auth'),
                                           account_type=request.form.get('account_type', 'CURRENT'),
                                           request_key=request.form.get('request_key', ''))
        flash('Pedido enviado. A conta ficará pendente até aprovação de um administrador.', 'success')
        return redirect(url_for('account_detail', account_id=account_id))

    @app.get('/admin/approvals')
    @protected('ADMIN')
    def approvals():
        return render_template('approvals.html', accounts=client.pending_accounts(
            request.cookies.get('bank_auth'), page_cursor()))

    @app.post('/admin/accounts/<int:account_id>/approve')
    @protected('ADMIN')
    def approve_account(account_id):
        client.approve_account(request.cookies.get('bank_auth'), account_id)
        flash('Aprovação registada.', 'success')
        return redirect(url_for('account_detail', account_id=account_id))

    @app.post('/admin/accounts/<int:account_id>/reject')
    @protected('ADMIN')
    def reject_account(account_id):
        client.reject_account(request.cookies.get('bank_auth'), account_id, request.form.get('reason', ''))
        flash('Pedido rejeitado. O titular pode consultar o motivo no painel.', 'success')
        return redirect(url_for('account_detail', account_id=account_id))

    @app.post('/accounts/<int:account_id>/cancel')
    @protected('CUSTOMER')
    def cancel_account(account_id):
        client.cancel_account(request.cookies.get('bank_auth'), account_id)
        flash('Pedido cancelado. Podes pedir uma nova conta.', 'success')
        return redirect(url_for('dashboard'))

    @app.get('/accounts/<int:account_id>')
    @protected()
    def account_detail(account_id):
        account, entries = client.statement(request.cookies.get('bank_auth'), account_id, page_cursor())
        return render_template('account.html', account=account, entries=entries)

    @app.post('/admin/accounts/<int:account_id>/status')
    @protected('ADMIN')
    def update_status(account_id):
        client.set_account_status(request.cookies.get('bank_auth'), account_id, request.form.get('status', ''))
        flash('Estado da conta atualizado.', 'success')
        return redirect(url_for('account_detail', account_id=account_id))

    @app.post('/admin/accounts/<int:account_id>/deposit')
    @protected('ADMIN')
    def deposit(account_id):
        client.move_money(request.cookies.get('bank_auth'), None, account_id, request.form.get('amount', ''), request.form.get('request_key', ''))
        flash('Depósito simulado registado.', 'success')
        return redirect(url_for('account_detail', account_id=account_id))

    @app.post('/accounts/<int:account_id>/transfer')
    @protected('CUSTOMER')
    def transfer(account_id):
        target = request.form.get('target', '').strip().upper().removeprefix('MB-')
        client.move_money(request.cookies.get('bank_auth'), account_id, target, request.form.get('amount', ''), request.form.get('request_key', ''))
        flash('Transferência concluída.', 'success')
        return redirect(url_for('account_detail', account_id=account_id))

    @app.get('/admin/audit')
    @protected('ADMIN')
    def audit_log():
        return render_template('audit.html', events=client.list_audit(request.cookies.get('bank_auth'), page_cursor()))

    @app.get('/admin/reports')
    @protected('ADMIN')
    def reports():
        return render_template('reports.html', reports=client.list_reports(
            request.cookies.get('bank_auth'), page_cursor()))

    @app.post('/admin/reports/reconcile')
    @protected('ADMIN')
    def reconcile():
        report_id = client.run_report(request.cookies.get('bank_auth'))
        return redirect(url_for('report_detail', report_id=report_id))

    @app.post('/admin/reports/close')
    @protected('ADMIN')
    def close_day():
        report_id = client.run_report(request.cookies.get('bank_auth'), close_day=True)
        return redirect(url_for('report_detail', report_id=report_id))

    @app.get('/admin/reports/<int:report_id>')
    @protected('ADMIN')
    def report_detail(report_id):
        report, rows = client.read_report(request.cookies.get('bank_auth'), report_id, page_cursor())
        return render_template('report.html', report=report, rows=rows)

    @app.get('/admin/transactions/<int:transaction_id>')
    @protected('ADMIN')
    def transaction_detail(transaction_id):
        return render_template('transaction.html', transaction=client.read_transaction(
            request.cookies.get('bank_auth'), transaction_id))

    @app.post('/admin/transactions/<int:transaction_id>/reverse')
    @protected('ADMIN')
    def reverse_transaction(transaction_id):
        if request.form.get('confirmed') != 'yes':
            raise BankError('INVALID_REQUEST')
        reversal_id = client.reverse_transaction(request.cookies.get('bank_auth'),
                                                  transaction_id, request.form.get('reason', ''))
        flash('Estorno registado. A operação original foi preservada.', 'success')
        return redirect(url_for('transaction_detail', transaction_id=reversal_id))

    @app.errorhandler(BankError)
    def business_error(error):
        if request.endpoint == 'request_account':
            return render_template('request_account.html', error=str(error)), 400
        if request.endpoint in ('create_customer', 'create_account'):
            return render_template('onboarding.html', error=str(error),
                                   values={'name': request.form.get('name', ''),
                                           'email': request.form.get('email', ''),
                                           'account_type': request.form.get('account_type', 'CURRENT')},
                                   mode='customer' if request.endpoint == 'create_customer' else 'account'), 400
        return render_template('error.html', message=str(error), title='Operação não concluída'), 400

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        if request.endpoint == 'login':
            # Reject the stale POST, then offer a fresh, protected login form.
            # Never authenticate or replay credentials from an invalid request.
            session.pop('csrf_token', None)
            g.pop('csrf_token', None)
            return render_template('login.html',
                                   error='O formulário de acesso foi renovado. Introduz novamente a palavra-passe para entrar.',
                                   email=request.form.get('email', '')[:254]), 400
        return render_template('error.html', message='O formulário expirou. Volta à página e tenta novamente.', title='Atualiza a página'), 400

    @app.errorhandler(Exception)
    def unexpected_error(error):
        if isinstance(error, HTTPException):
            return render_template('error.html', title='Página indisponível',
                                   message='Não foi possível abrir esta página ou não tens permissão.'), error.code
        if app.testing:
            raise error
        reference = secrets.token_hex(4)
        # Driver exceptions can contain SQL or credentials; never log their message.
        app.logger.error('Request failed: reference=%s type=%s', reference, type(error).__name__)
        return render_template('error.html', title='Serviço temporariamente indisponível',
                               message=f'Tenta novamente mais tarde. Referência: {reference}'), 503

    register_product_routes(app, protected, page_cursor)
    return app


def page_cursor():
    value = request.args.get('before', '9223372036854775807')
    if not value.isascii() or not value.isdigit() or not 0 < int(value) <= 9223372036854775807:
        abort(400)
    return int(value)

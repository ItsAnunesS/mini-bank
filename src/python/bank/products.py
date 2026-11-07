"""Product views and exports; calculations and persistence remain in COBOL."""
import csv
import io
from datetime import datetime

from flask import Response, flash, redirect, render_template, request, url_for
from bank import client
from bank.client import BankError


def register_product_routes(app, protected, page_cursor):
    def call(command, **fields):
        return client.call_backend(command, token=request.cookies.get('bank_auth'), **fields)

    @app.get('/accounts/<int:account_id>/statements')
    @protected()
    def monthly_statement(account_id):
        month = request.args.get('month', datetime.now().strftime('%Y-%m'))
        result = call('MONTHLY_STATEMENT', target=account_id, month=month)
        if request.args.get('format') == 'csv':
            output = io.StringIO(newline='')
            writer = csv.writer(output, delimiter=';')
            writer.writerow(['MiniBank', f'MB-{account_id:08d}', month])
            writer.writerow(['Saldo inicial', 'Entradas', 'Saídas', 'Saldo final'])
            writer.writerow([result['summary'][key] for key in ('opening_balance', 'credits', 'debits', 'closing_balance')])
            writer.writerow(['Operação', 'Data', 'Tipo', 'Sentido', 'Valor EUR', 'Saldo EUR'])
            for row in result['rows']:
                writer.writerow([row['transaction_id'], row['created_at'].isoformat(), row['kind'],
                                 'Crédito' if row['direction'] == '+' else 'Débito', row['amount'], row['balance_after']])
            return Response('\ufeff' + output.getvalue(), content_type='text/csv; charset=utf-8',
                            headers={'Content-Disposition': f'attachment; filename="MiniBank-{account_id}-{month}.csv"'})
        return render_template('monthly_statement.html', account_id=account_id, month=month,
                               summary=result['summary'], entries=result['rows'])

    @app.post('/admin/accounts/<int:account_id>/policy')
    @protected('ADMIN')
    def account_policy(account_id):
        call('SET_POLICY', target=account_id, amount=request.form.get('transfer_limit', ''),
             daily_limit=request.form.get('daily_limit', ''), status='1' if request.form.get('fee_exempt') == 'yes' else '0')
        flash('Limites e isenção atualizados.', 'success')
        return redirect(url_for('account_detail', account_id=account_id, _anchor='products'))

    @app.post('/admin/accounts/<int:account_id>/fee')
    @protected('ADMIN')
    def charge_fee(account_id):
        call('CHARGE_FEE', target=account_id)
        flash('Comissão do mês registada. Uma cobrança já existente não é repetida.', 'success')
        return redirect(url_for('account_detail', account_id=account_id))

    @app.post('/accounts/<int:account_id>/close')
    @protected('CUSTOMER')
    def close_account(account_id):
        if request.form.get('confirmed') != 'yes':
            raise BankError('INVALID_REQUEST')
        call('CLOSE_ACCOUNT', target=account_id)
        flash('Conta encerrada. O histórico foi preservado.', 'success')
        return redirect(url_for('account_detail', account_id=account_id))

    @app.get('/loans')
    @protected()
    def loans():
        return render_template('loans.html', loans=call('LOANS', before=page_cursor())['rows'])

    @app.get('/accounts/<int:account_id>/loans/new')
    @protected('CUSTOMER')
    def new_loan(account_id):
        account, _ = client.statement(request.cookies.get('bank_auth'), account_id)
        return render_template('new_loan.html', account=account)

    @app.post('/accounts/<int:account_id>/loans')
    @protected('CUSTOMER')
    def request_loan(account_id):
        result = call('REQUEST_LOAN', target=account_id, amount=request.form.get('amount', ''),
                      months=request.form.get('months', ''), request_key=request.form.get('request_key', ''))
        flash('Pedido enviado. Consulta o plano; o crédito só é disponibilizado após aprovação.', 'success')
        return redirect(url_for('loan_detail', loan_id=int(result['id'])))

    @app.get('/loans/<int:loan_id>')
    @protected()
    def loan_detail(loan_id):
        result = call('LOAN', original=loan_id)
        return render_template('loan.html', loan=result['loan'], installments=result['rows'])

    @app.post('/admin/loans/<int:loan_id>/approve')
    @protected('ADMIN')
    def approve_loan(loan_id):
        call('APPROVE_LOAN', original=loan_id)
        flash('Empréstimo aprovado e capital creditado na conta.', 'success')
        return redirect(url_for('loan_detail', loan_id=loan_id))

    @app.post('/admin/loans/<int:loan_id>/reject')
    @protected('ADMIN')
    def reject_loan(loan_id):
        call('REJECT_LOAN', original=loan_id, reason=request.form.get('reason', ''))
        flash('Empréstimo rejeitado. O titular pode consultar o motivo.', 'success')
        return redirect(url_for('loan_detail', loan_id=loan_id))

    @app.post('/loans/<int:loan_id>/pay')
    @protected('CUSTOMER')
    def pay_loan(loan_id):
        call('PAY_LOAN', original=loan_id, request_key=request.form.get('request_key', ''))
        flash('Prestação paga. O saldo e o plano foram atualizados.', 'success')
        return redirect(url_for('loan_detail', loan_id=loan_id))

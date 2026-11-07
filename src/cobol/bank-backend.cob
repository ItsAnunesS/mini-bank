identification division.
program-id. bank-backend is initial.
data division.
working-storage section.
copy "bank/data.cpy".
procedure division.
run-request.
    call static "bank_input" returning native-status end-call
    if native-status not = zero
        move "INVALID_REQUEST" to error-code
        perform finish-request
    end-if
    perform read-request
    call static "bank_db_open" returning native-status end-call
    perform check-database
    move 1 to database-opened
    evaluate request-command
        when "INIT" perform initialize-schema
        when "CREATE_ADMIN" perform create-admin
        when "LOGIN" perform authenticate-user
        when "SESSION" perform read-session
        when "LOGOUT" perform revoke-session
        when other
            perform authorize-session
            evaluate request-command
                when "CREATE_CUSTOMER" perform create-customer
                when "CREATE_ACCOUNT" perform create-account
                when "APPROVE_ACCOUNT" perform approve-account
                when "REJECT_ACCOUNT" perform resolve-account-request
                when "CANCEL_ACCOUNT" perform resolve-account-request
                when "PENDING_ACCOUNTS"
                    perform require-admin perform list-accounts
                when "SET_STATUS" perform change-account-status
                when "ACCOUNTS" perform list-accounts
                when "NOTIFICATIONS" perform list-notifications
                when "READ_NOTIFICATION" perform mark-notification-read
                when "UNREAD_NOTIFICATIONS" perform count-unread-notifications
                when "READ_ALL_NOTIFICATIONS" perform mark-all-notifications-read
                when "STATEMENT" perform list-statement
                when "AUDIT" perform list-audit
                when "MOVE" perform move-money
                when "REVERSE" perform reverse-movement
                when "CLOSE_DAY"
                    move "CLOSE" to report-mode perform run-report
                when "RECONCILE"
                    move "RECONCILE" to report-mode perform run-report
                when "REPORTS" perform list-reports
                when "REPORT" perform read-report
                when "TRANSACTION" perform read-transaction
                when other
                    move "INVALID_REQUEST" to error-code
            end-evaluate
    end-evaluate
    perform finish-request.

copy "bank/database.cpy".
copy "bank/authentication.cpy".
copy "bank/customers.cpy".
copy "bank/accounts.cpy".
copy "bank/notifications.cpy".
copy "bank/transfers.cpy".
copy "bank/reversals.cpy".
copy "bank/reports.cpy".
end program bank-backend.

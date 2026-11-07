lock-owned-account.
    move request-target to numeric-value perform validate-id
    move request-target to target-id lock-id
    perform lock-account
    if actor-role not = "ADMIN" and
        function numval(target-owner) not = function numval(actor-id)
        move "ACCOUNT_UNAVAILABLE" to error-code perform finish-request
    end-if.
require-active-account.
    if target-state not = "ACTIVE"
        move "ACCOUNT_BLOCKED" to error-code perform finish-request
    end-if.
validate-month.
    if request-month(1:4) is not numeric or
        request-month(5:1) not = "-" or
        request-month(6:2) is not numeric or
        function numval(request-month(1:4)) < 1900 or
        function numval(request-month(1:4)) > 9998 or
        function numval(request-month(6:2)) < 1 or > 12
        move "INVALID_MONTH" to error-code perform finish-request
    end-if
    move spaces to month-start
    string request-month "-01" into month-start end-string
    perform reset-query
    move "SELECT CASE WHEN CAST(? AS DATE) > CURRENT DATE THEN 1 ELSE 0 END FROM SYSIBM.SYSDUMMY1" to query-sql
    move month-start to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    move 1 to parameter-index perform read-column
    if function numval(column-value) = 1
        move "INVALID_MONTH" to error-code perform finish-request
    end-if.
monthly-statement.
    perform validate-month
    *> Serialize with account writers for consistent totals and rows.
    perform lock-owned-account
    perform reset-query
    string "SELECT COALESCE(SUM(CASE WHEN CREATED_AT < CAST(? AS DATE) THEN "
        "CASE WHEN DIRECTION = '+' THEN AMOUNT ELSE -AMOUNT END ELSE 0 END),0) AS OPENING_BALANCE, "
        "COALESCE(SUM(CASE WHEN CREATED_AT >= CAST(? AS DATE) AND DIRECTION = '+' THEN AMOUNT ELSE 0 END),0) AS CREDITS, "
        "COALESCE(SUM(CASE WHEN CREATED_AT >= CAST(? AS DATE) AND DIRECTION = '-' THEN AMOUNT ELSE 0 END),0) AS DEBITS, "
        "COALESCE(SUM(CASE WHEN DIRECTION = '+' THEN AMOUNT ELSE -AMOUNT END),0) AS CLOSING_BALANCE "
        "FROM BANK_ENTRIES WHERE ACCOUNT_ID = ? AND CREATED_AT < CAST(? AS DATE) + 1 MONTH"
        into query-sql end-string
    move month-start to parameter-value perform add-parameter
    perform add-parameter perform add-parameter
    move target-id to parameter-value perform add-parameter
    move month-start to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    call static "bank_result_row" using z"summary" end-call
    perform reset-query
    string "SELECT E.*, T.KIND FROM BANK_ENTRIES E JOIN BANK_TRANSACTIONS T ON T.ID = E.TRANSACTION_ID "
        "WHERE E.ACCOUNT_ID = ? AND E.CREATED_AT >= CAST(? AS DATE) AND E.CREATED_AT < CAST(? AS DATE) + 1 MONTH "
        "ORDER BY E.ID FETCH FIRST 1001 ROWS ONLY" into query-sql end-string
    move target-id to parameter-value perform add-parameter
    move month-start to parameter-value perform add-parameter
    perform add-parameter
    perform execute-query
    move 1000 to row-limit
    move zero to row-count
    perform fetch-row
    perform until fetch-status = 2
        add 1 to row-count end-add
        if row-count > row-limit
            move "STATEMENT_LIMIT" to error-code perform finish-request
        end-if
        call static "bank_result_row" using x"00" end-call
        perform fetch-row
    end-perform.
set-account-policy.
    perform require-admin
    perform lock-owned-account
    if target-state not = "ACTIVE" and not = "BLOCKED"
        move "ACCOUNT_BLOCKED" to error-code perform finish-request
    end-if
    call "validate-amount" using request-amount transfer-limit operation-result end-call
    if operation-result not = zero
        move "INVALID_AMOUNT" to error-code perform finish-request
    end-if
    call "validate-amount" using request-daily-limit daily-limit operation-result end-call
    if operation-result not = zero or daily-limit < transfer-limit
        move "INVALID_LIMIT" to error-code perform finish-request
    end-if
    if request-status not = "0" and not = "1"
        move "INVALID_REQUEST" to error-code perform finish-request
    end-if
    perform reset-query
    move "UPDATE BANK_ACCOUNTS SET TRANSFER_LIMIT = ?, DAILY_LIMIT = ?, FEE_EXEMPT = ? WHERE ID = ?" to query-sql
    move transfer-limit to amount-text
    move amount-text to parameter-value perform add-parameter
    move daily-limit to amount-text
    move amount-text to parameter-value perform add-parameter
    move request-status to parameter-value perform add-parameter
    move target-id to parameter-value perform add-parameter
    perform execute-query
    move "POLICY_CHANGED" to audit-action
    move target-id to audit-subject perform write-audit.
enforce-transfer-limits.
    *> The source account lock serializes the daily aggregate and new transfer.
    perform reset-query
    string "SELECT A.TRANSFER_LIMIT, A.DAILY_LIMIT, COALESCE((SELECT SUM(T.AMOUNT) "
        "FROM BANK_TRANSACTIONS T WHERE T.SOURCE_ID = A.ID AND T.KIND = 'TRANSFER' "
        "AND T.CREATED_AT >= CURRENT DATE AND T.CREATED_AT < CURRENT DATE + 1 DAY),0) "
        "FROM BANK_ACCOUNTS A WHERE A.ID = ?" into query-sql end-string
    move source-id to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    move 1 to parameter-index perform read-column
    compute transfer-limit = function numval(column-value) end-compute
    move 2 to parameter-index perform read-column
    compute daily-limit = function numval(column-value) end-compute
    move 3 to parameter-index perform read-column
    compute daily-used = function numval(column-value) end-compute
    if movement-amount > transfer-limit
        move "TRANSFER_LIMIT" to error-code perform finish-request
    end-if
    if daily-used + movement-amount > daily-limit
        move "DAILY_LIMIT" to error-code perform finish-request
    end-if.
charge-monthly-fee.
    perform require-admin
    perform lock-owned-account
    perform reset-query
    move "SELECT CHAR(CURRENT DATE - (DAY(CURRENT DATE) - 1) DAYS) FROM SYSIBM.SYSDUMMY1" to query-sql
    perform execute-query perform fetch-row
    move 1 to parameter-index perform read-column
    move column-value(1:10) to month-start
    perform reset-query
    move "SELECT TRANSACTION_ID FROM BANK_FEES WHERE ACCOUNT_ID = ? AND FEE_MONTH = ?" to query-sql
    move target-id to parameter-value perform add-parameter
    move month-start to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    if fetch-status = zero exit paragraph end-if
    perform require-active-account
    perform reset-query
    string "SELECT A.FEE_EXEMPT, (SELECT COUNT(*) FROM BANK_SAVINGS S WHERE S.ACCOUNT_ID = A.ID) "
        "FROM BANK_ACCOUNTS A WHERE A.ID = ?" into query-sql end-string
    move target-id to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    move 1 to parameter-index perform read-column
    compute fee-exempt = function numval(column-value) end-compute
    move 2 to parameter-index perform read-column
    if fee-exempt = 1 or function numval(column-value) > zero
        move "FEE_EXEMPT" to error-code perform finish-request
    end-if
    move 2 to movement-amount
    move "FEE" to movement-kind
    move "-" to entry-direction
    perform generate-internal-key
    perform post-single-entry
    perform reset-query
    move "INSERT INTO BANK_FEES (ACCOUNT_ID, FEE_MONTH, TRANSACTION_ID) VALUES (?, ?, ?)" to query-sql
    move target-id to parameter-value perform add-parameter
    move month-start to parameter-value perform add-parameter
    move transaction-id to parameter-value perform add-parameter
    perform execute-query.
generate-internal-key.
    call static "bank_random" using session-token by value 100 returning native-status end-call
    perform check-database
    move session-token(1:36) to request-key.
post-single-entry.
    *> Caller holds the account lock; ledger and product state commit together.
    move spaces to source-id
    if entry-direction = "-"
        if target-balance < movement-amount
            move "INSUFFICIENT_FUNDS" to error-code perform finish-request
        end-if
        compute updated-target = target-balance - movement-amount end-compute
    else
        compute updated-target = target-balance + movement-amount
            on size error move "BALANCE_LIMIT" to error-code perform finish-request
        end-compute
    end-if
    perform insert-movement
    move target-id to account-id
    move updated-target to balance-text
    perform write-entry
    move movement-kind to audit-action
    move transaction-id to audit-subject perform write-audit.
close-account.
    if actor-role not = "CUSTOMER"
        move "FORBIDDEN" to error-code perform finish-request
    end-if
    perform lock-owned-account
    if target-state = "CLOSED" exit paragraph end-if
    perform require-active-account
    if target-balance not = zero
        move "CLOSURE_BALANCE" to error-code perform finish-request
    end-if
    perform reset-query
    move "SELECT COUNT(*) FROM BANK_LOANS WHERE ACCOUNT_ID = ? AND STATUS IN ('PENDING','ACTIVE')" to query-sql
    move target-id to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    move 1 to parameter-index perform read-column
    if function numval(column-value) > zero
        move "CLOSURE_LOAN" to error-code perform finish-request
    end-if
    perform reset-query
    move "UPDATE BANK_ACCOUNTS SET STATUS = 'CLOSED', CLOSED_AT = CURRENT TIMESTAMP WHERE ID = ?" to query-sql
    move target-id to parameter-value perform add-parameter
    perform execute-query
    move "ACCOUNT_CLOSED" to audit-action
    move target-id to audit-subject perform write-audit.

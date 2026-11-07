run-report.
    perform require-admin
    *> Serialize report runs, then freeze balances while reading the ledger.
    perform reset-query
    move "LOCK TABLE BANK_REPORTS IN EXCLUSIVE MODE" to query-sql
    perform execute-query
    perform reset-query
    move "SELECT CHAR(CURRENT DATE) FROM SYSIBM.SYSDUMMY1" to query-sql
    perform execute-query perform fetch-row
    move 1 to parameter-index perform read-column
    move column-value(1:10) to report-date
    if report-mode = "CLOSE"
        perform reset-query
        move "SELECT REPORT_ID FROM BANK_DAILY_CLOSES WHERE CLOSE_DATE = ?"
            to query-sql
        move report-date to parameter-value perform add-parameter
        perform execute-query perform fetch-row
        if fetch-status = zero
            move 1 to parameter-index perform read-column
            move column-value(1:19) to report-id
            call static "bank_text" using z"id" report-id by value 19
            end-call
            exit paragraph
        end-if
    end-if
    perform reset-query
    move "LOCK TABLE BANK_ACCOUNTS IN EXCLUSIVE MODE" to query-sql
    perform execute-query
    perform reset-query
    move "LOCK TABLE BANK_TRANSACTIONS IN SHARE MODE" to query-sql
    perform execute-query
    perform reset-query
    move "LOCK TABLE BANK_ENTRIES IN SHARE MODE" to query-sql
    perform execute-query
    perform reset-query
    move "LOCK TABLE BANK_SAVINGS IN SHARE MODE" to query-sql
    perform execute-query
    perform reset-query
    move "LOCK TABLE BANK_REVERSALS IN SHARE MODE" to query-sql
    perform execute-query
    perform reset-query
    move "INSERT INTO BANK_REPORTS (ACTOR_ID, MODE, REPORT_DATE, STATUS) VALUES (?, ?, ?, 'OK')"
        to query-sql
    move actor-id to parameter-value perform add-parameter
    move report-mode to parameter-value perform add-parameter
    move report-date to parameter-value perform add-parameter
    perform execute-query perform last-identity
    move column-value(1:19) to report-id
    move zero to batch-count mismatch-count total-interest
    move "0" to batch-last-id
    move 1 to batch-remaining
    perform until batch-remaining = zero
        perform next-batch-account
        if batch-remaining = 1
            add 1 to batch-count end-add
            if batch-count > 500
                move "REPORT_LIMIT" to error-code perform finish-request
            end-if
            perform reconcile-account
            perform save-account-snapshot
        end-if
    end-perform
    move "OK" to report-status
    if mismatch-count > zero
        move "MISMATCH" to report-status
    else
        if report-mode = "CLOSE"
            perform credit-savings-interest
            perform reset-query
            move "INSERT INTO BANK_DAILY_CLOSES (CLOSE_DATE, REPORT_ID) VALUES (?, ?)"
                to query-sql
            move report-date to parameter-value perform add-parameter
            move report-id to parameter-value perform add-parameter
            perform execute-query
        end-if
    end-if
    perform reset-query
    move "UPDATE BANK_REPORTS SET STATUS = ?, ACCOUNT_COUNT = ?, MISMATCH_COUNT = ?, TOTAL_INTEREST = ? WHERE ID = ?"
        to query-sql
    move report-status to parameter-value perform add-parameter
    move batch-count to count-text
    move count-text to parameter-value perform add-parameter
    move mismatch-count to count-text
    move count-text to parameter-value perform add-parameter
    move total-interest to total-text
    move total-text to parameter-value perform add-parameter
    move report-id to parameter-value perform add-parameter
    perform execute-query
    if report-mode = "CLOSE" move "CLOSE_DAY" to audit-action
    else move "RECONCILE" to audit-action end-if
    move report-id to audit-subject perform write-audit
    call static "bank_text" using z"id" report-id by value 19 end-call.
next-batch-account.
    perform reset-query
    string "SELECT A.ID, A.STATUS, A.BALANCE, "
        "CASE WHEN S.ACCOUNT_ID IS NULL THEN 'CURRENT' ELSE 'SAVINGS' END "
        "FROM BANK_ACCOUNTS A LEFT JOIN BANK_SAVINGS S ON S.ACCOUNT_ID = A.ID "
        "WHERE A.ID > ? ORDER BY A.ID FETCH FIRST 1 ROW ONLY"
        into query-sql end-string
    move batch-last-id to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    if fetch-status = 2
        move zero to batch-remaining exit paragraph
    end-if
    move 1 to parameter-index perform read-column
    move column-value(1:19) to account-id batch-last-id
    move 2 to parameter-index perform read-column
    move column-value(1:10) to batch-state
    move 3 to parameter-index perform read-column
    compute batch-balance = function numval(column-value) end-compute
    move 4 to parameter-index perform read-column
    move column-value(1:10) to batch-type.
reconcile-account.
    move "OK" to batch-issue
    move zero to batch-interest
    perform reset-query
    string "SELECT COALESCE(SUM(DELTA),0), "
        "COALESCE(MAX(CASE WHEN RN = 1 THEN BALANCE_AFTER END),0), "
        "COALESCE(SUM(CASE WHEN BALANCE_AFTER <> RUNNING_BALANCE THEN 1 ELSE 0 END),0) "
        "FROM (SELECT BALANCE_AFTER, "
        "CASE WHEN DIRECTION = '+' THEN AMOUNT ELSE -AMOUNT END AS DELTA, "
        "SUM(CASE WHEN DIRECTION = '+' THEN AMOUNT ELSE -AMOUNT END) "
        "OVER (ORDER BY ID ROWS UNBOUNDED PRECEDING) AS RUNNING_BALANCE, "
        "ROW_NUMBER() OVER (ORDER BY ID DESC) AS RN "
        "FROM BANK_ENTRIES WHERE ACCOUNT_ID = ?) X"
        into query-sql end-string
    move account-id to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    move 1 to parameter-index perform read-column
    compute ledger-balance = function numval(column-value)
        on size error
            move "REPORT_LIMIT" to error-code perform finish-request
    end-compute
    move 2 to parameter-index perform read-column
    compute last-balance = function numval(column-value) end-compute
    move 3 to parameter-index perform read-column
    compute structural-errors = function numval(column-value)
    end-compute
    compute batch-difference = batch-balance - ledger-balance
        on size error
            move "REPORT_LIMIT" to error-code perform finish-request
    end-compute
    if batch-difference not = zero
        move "BALANCE_MISMATCH" to batch-issue
    else
        if structural-errors > zero or last-balance not = batch-balance
            move "LEDGER_CHAIN" to batch-issue
        end-if
    end-if
    perform check-transaction-structure
    if structural-errors > zero and batch-issue = "OK"
        move "TRANSACTION_MISMATCH" to batch-issue
    end-if
    if report-mode = "CLOSE" and batch-type = "SAVINGS"
        and batch-state = "ACTIVE" and batch-issue = "OK"
        perform calculate-daily-interest
        if batch-interest > 9999999.99
            move "INTEREST_LIMIT" to batch-issue
        end-if
        compute updated-target = batch-balance + batch-interest
            on size error move "BALANCE_LIMIT" to batch-issue
        end-compute
    end-if
    if batch-issue not = "OK"
        add 1 to mismatch-count end-add
    end-if.
check-transaction-structure.
    perform reset-query
    string "SELECT COUNT(*) FROM BANK_TRANSACTIONS T "
        "WHERE (T.TARGET_ID = ? OR T.SOURCE_ID = ?) AND ("
        "(SELECT COUNT(*) FROM BANK_ENTRIES E WHERE E.TRANSACTION_ID = T.ID) "
        "<> CASE WHEN T.SOURCE_ID IS NULL THEN 1 ELSE 2 END "
        "OR EXISTS (SELECT 1 FROM BANK_ENTRIES E WHERE E.TRANSACTION_ID = T.ID "
        "AND (E.AMOUNT <> T.AMOUNT OR NOT ("
        "(E.ACCOUNT_ID = T.TARGET_ID AND E.DIRECTION = "
        "CASE WHEN T.KIND IN ('FEE','PAYMENT') OR (T.KIND = 'REVERSAL' AND T.SOURCE_ID IS NULL) THEN '-' ELSE '+' END) "
        "OR (T.SOURCE_ID IS NOT NULL AND E.ACCOUNT_ID = T.SOURCE_ID AND E.DIRECTION = '-')))) "
        "OR (T.KIND IN ('DEPOSIT','INTEREST','LOAN','FEE','PAYMENT') AND T.SOURCE_ID IS NOT NULL) "
        "OR (T.KIND = 'TRANSFER' AND (T.SOURCE_ID IS NULL OR T.SOURCE_ID = T.TARGET_ID)) "
        "OR (T.KIND = 'REVERSAL' AND NOT EXISTS ("
        "SELECT 1 FROM BANK_REVERSALS R JOIN BANK_TRANSACTIONS O ON O.ID = R.ORIGINAL_ID "
        "WHERE R.TRANSACTION_ID = T.ID AND O.AMOUNT = T.AMOUNT AND ("
        "(O.KIND IN ('DEPOSIT','INTEREST') AND T.SOURCE_ID IS NULL AND T.TARGET_ID = O.TARGET_ID) "
        "OR (O.KIND = 'TRANSFER' AND T.SOURCE_ID = O.TARGET_ID AND T.TARGET_ID = O.SOURCE_ID)))))"
        into query-sql end-string
    move account-id to parameter-value perform add-parameter
    move account-id to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    move 1 to parameter-index perform read-column
    compute structural-errors = function numval(column-value)
    end-compute.
save-account-snapshot.
    perform reset-query
    string "INSERT INTO BANK_REPORT_ACCOUNTS (REPORT_ID, ACCOUNT_ID, "
        "ACCOUNT_TYPE, ACCOUNT_STATUS, BALANCE_BEFORE, LEDGER_BALANCE, "
        "LAST_BALANCE, DIFFERENCE, ISSUE, BALANCE_AFTER) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        into query-sql end-string
    move report-id to parameter-value perform add-parameter
    move account-id to parameter-value perform add-parameter
    move batch-type to parameter-value perform add-parameter
    move batch-state to parameter-value perform add-parameter
    move batch-balance to balance-text
    move balance-text to parameter-value perform add-parameter
    move ledger-balance to signed-text
    move function trim(signed-text) to parameter-value
    perform add-parameter
    move last-balance to balance-text
    move balance-text to parameter-value perform add-parameter
    move batch-difference to signed-text
    move function trim(signed-text) to parameter-value
    perform add-parameter
    move batch-issue to parameter-value perform add-parameter
    move batch-balance to balance-text
    move balance-text to parameter-value perform add-parameter
    perform execute-query.
credit-savings-interest.
    move "0" to batch-last-id
    move 1 to batch-remaining
    perform until batch-remaining = zero
        perform next-batch-account
        if batch-remaining = 1 and batch-type = "SAVINGS"
            and batch-state = "ACTIVE"
            perform calculate-daily-interest
            compute movement-amount = batch-interest
                on size error
                    move "BALANCE_LIMIT" to error-code
                    perform finish-request
            end-compute
            if movement-amount > zero perform post-interest end-if
        end-if
    end-perform.
post-interest.
    move account-id to target-id
    move spaces to source-id
    move "INTEREST" to movement-kind
    compute updated-target = batch-balance + movement-amount end-compute
    call static "bank_random" using session-token by value 100
        returning native-status end-call
    perform check-database
    move session-token(1:36) to request-key
    perform insert-movement
    move target-id to account-id
    move "+" to entry-direction
    move updated-target to balance-text
    perform write-entry
    move "INTEREST" to audit-action
    move transaction-id to audit-subject perform write-audit
    add movement-amount to total-interest end-add
    perform reset-query
    move "UPDATE BANK_REPORT_ACCOUNTS SET INTEREST_AMOUNT = ?, BALANCE_AFTER = ? WHERE REPORT_ID = ? AND ACCOUNT_ID = ?"
        to query-sql
    move amount-text to parameter-value perform add-parameter
    move balance-text to parameter-value perform add-parameter
    move report-id to parameter-value perform add-parameter
    move account-id to parameter-value perform add-parameter
    perform execute-query.
list-reports.
    perform require-admin
    perform reset-query
    string "SELECT R.*, U.NAME AS ACTOR_NAME FROM BANK_REPORTS R "
        "JOIN BANK_USERS U ON U.ID = R.ACTOR_ID WHERE R.ID < ? "
        "ORDER BY R.ID DESC FETCH FIRST 50 ROWS ONLY"
        into query-sql end-string
    move request-cursor to parameter-value perform add-parameter
    perform execute-query perform append-rows.
read-report.
    perform require-admin
    move request-target to numeric-value perform validate-id
    perform reset-query
    string "SELECT R.*, U.NAME AS ACTOR_NAME FROM BANK_REPORTS R "
        "JOIN BANK_USERS U ON U.ID = R.ACTOR_ID WHERE R.ID = ?"
        into query-sql end-string
    move request-target to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    if fetch-status = 2
        move "REPORT_NOT_FOUND" to error-code perform finish-request
    end-if
    call static "bank_result_row" using z"report" end-call
    perform reset-query
    string "SELECT * FROM BANK_REPORT_ACCOUNTS WHERE REPORT_ID = ? "
        "AND ACCOUNT_ID < ? ORDER BY ACCOUNT_ID DESC FETCH FIRST 50 ROWS ONLY"
        into query-sql end-string
    move request-target to parameter-value perform add-parameter
    move request-cursor to parameter-value perform add-parameter
    perform execute-query perform append-rows.

calculate-daily-interest.
    *> Fixed demonstration rate; no carry of sub-cent amounts between days.
    compute batch-interest rounded = batch-balance * 3.65 / 100 / 365
    end-compute.

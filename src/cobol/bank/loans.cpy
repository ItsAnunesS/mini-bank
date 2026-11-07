require-customer.
    if actor-role not = "CUSTOMER"
        move "FORBIDDEN" to error-code perform finish-request
    end-if.
request-loan.
    perform require-customer
    perform lock-owned-account
    perform require-active-account
    call "validate-amount" using request-amount loan-principal operation-result end-call
    if operation-result not = zero or loan-principal < 100 or > 100000
        move "INVALID_LOAN" to error-code perform finish-request
    end-if
    if function trim(request-months) is not numeric
        move "INVALID_LOAN" to error-code perform finish-request
    end-if
    compute loan-months = function numval(request-months) end-compute
    if loan-months < 3 or > 60
        move "INVALID_LOAN" to error-code perform finish-request
    end-if
    perform validate-request-key
    perform reset-query
    move "SELECT ID, ACCOUNT_ID, PRINCIPAL, MONTHS FROM BANK_LOANS WHERE USER_ID = ? AND REQUEST_KEY = ?" to query-sql
    move actor-id to parameter-value perform add-parameter
    move request-key to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    if fetch-status = zero
        move 1 to parameter-index perform read-column
        move column-value(1:19) to loan-id
        move 2 to parameter-index perform read-column
        if function numval(column-value) not = function numval(target-id)
            move "REQUEST_CONFLICT" to error-code perform finish-request
        end-if
        move 3 to parameter-index perform read-column
        if function numval(column-value) not = loan-principal
            move "REQUEST_CONFLICT" to error-code perform finish-request
        end-if
        move 4 to parameter-index perform read-column
        if function numval(column-value) not = loan-months
            move "REQUEST_CONFLICT" to error-code perform finish-request
        end-if
        perform return-loan-id
        exit paragraph
    end-if
    perform reset-query
    move "INSERT INTO BANK_LOANS (ACCOUNT_ID, USER_ID, REQUEST_KEY, PRINCIPAL, MONTHS) VALUES (?, ?, ?, ?, ?)" to query-sql
    move target-id to parameter-value perform add-parameter
    move actor-id to parameter-value perform add-parameter
    move request-key to parameter-value perform add-parameter
    move loan-principal to amount-text
    move amount-text to parameter-value perform add-parameter
    move loan-months to count-text
    move count-text to parameter-value perform add-parameter
    perform execute-query perform last-identity
    move column-value(1:19) to loan-id
    perform build-installments
    move "LOAN_REQUESTED" to audit-action
    move loan-id to audit-subject perform write-audit
    perform return-loan-id.
return-loan-id.
    call static "bank_text" using z"id" loan-id by value 19 end-call.
build-installments.
    move loan-principal to loan-remaining
    *> Truncate regular capital to cents; the final installment clears the remainder.
    compute installment-capital = loan-principal / loan-months end-compute
    perform varying installment-number from 1 by 1 until installment-number > loan-months
        compute installment-interest rounded = loan-remaining * 6 / 100 / 12 end-compute
        if installment-number = loan-months
            move loan-remaining to installment-capital
        end-if
        subtract installment-capital from loan-remaining end-subtract
        compute movement-amount = installment-capital + installment-interest end-compute
        perform reset-query
        string "INSERT INTO BANK_INSTALLMENTS (LOAN_ID, NUMBER, DUE_DATE, CAPITAL, INTEREST, AMOUNT, REMAINING) "
            "VALUES (?, ?, ADD_MONTHS(CURRENT DATE, CAST(? AS INTEGER)), ?, ?, ?, ?)" into query-sql end-string
        move loan-id to parameter-value perform add-parameter
        move installment-number to count-text
        move count-text to parameter-value perform add-parameter perform add-parameter
        move installment-capital to amount-text
        move amount-text to parameter-value perform add-parameter
        move installment-interest to amount-text
        move amount-text to parameter-value perform add-parameter
        move movement-amount to amount-text
        move amount-text to parameter-value perform add-parameter
        move loan-remaining to amount-text
        move amount-text to parameter-value perform add-parameter
        perform execute-query
    end-perform.
lock-loan.
    move request-original to numeric-value perform validate-id
    move request-original to loan-id
    perform reset-query
    move "SELECT ACCOUNT_ID FROM BANK_LOANS WHERE ID = ?" to query-sql
    move loan-id to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    if fetch-status = 2
        move "LOAN_UNAVAILABLE" to error-code perform finish-request
    end-if
    move 1 to parameter-index perform read-column
    move column-value(1:19) to request-target
    *> Account before loan is the shared lock order for closure and repayments.
    perform lock-owned-account
    perform reset-query
    move "SELECT STATUS, PRINCIPAL, MONTHS FROM BANK_LOANS WHERE ID = ? WITH RS USE AND KEEP UPDATE LOCKS" to query-sql
    move loan-id to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    move 1 to parameter-index perform read-column
    move column-value(1:10) to loan-state
    move 2 to parameter-index perform read-column
    compute loan-principal = function numval(column-value) end-compute
    move 3 to parameter-index perform read-column
    compute loan-months = function numval(column-value) end-compute.
approve-loan.
    perform require-admin
    perform lock-loan
    if loan-state = "ACTIVE" or = "PAID" exit paragraph end-if
    if loan-state not = "PENDING"
        move "LOAN_NOT_PENDING" to error-code perform finish-request
    end-if
    perform require-active-account
    move loan-principal to movement-amount
    move "LOAN" to movement-kind
    move "+" to entry-direction
    perform generate-internal-key
    perform post-single-entry
    perform reset-query
    move "UPDATE BANK_LOANS SET STATUS = 'ACTIVE', APPROVED_AT = CURRENT TIMESTAMP, DECIDED_BY = ?, DISBURSEMENT_ID = ? WHERE ID = ?" to query-sql
    move actor-id to parameter-value perform add-parameter
    move transaction-id to parameter-value perform add-parameter
    move loan-id to parameter-value perform add-parameter
    perform execute-query
    perform reset-query
    move "UPDATE BANK_INSTALLMENTS SET DUE_DATE = ADD_MONTHS(CURRENT DATE, NUMBER) WHERE LOAN_ID = ?" to query-sql
    move loan-id to parameter-value perform add-parameter
    perform execute-query
    move "LOAN_APPROVED" to audit-action
    move loan-id to audit-subject perform write-audit.
reject-loan.
    perform require-admin
    perform validate-reason
    perform lock-loan
    if loan-state = "REJECTED"
        perform reset-query
        move "SELECT REASON FROM BANK_LOANS WHERE ID = ?" to query-sql
        move loan-id to parameter-value perform add-parameter
        perform execute-query perform fetch-row
        move 1 to parameter-index perform read-column
        if column-value(1:240) not = request-reason
            move "REQUEST_CONFLICT" to error-code perform finish-request
        end-if
        exit paragraph
    end-if
    if loan-state not = "PENDING"
        move "LOAN_NOT_PENDING" to error-code perform finish-request
    end-if
    perform reset-query
    move "UPDATE BANK_LOANS SET STATUS = 'REJECTED', REASON = ?, DECIDED_BY = ? WHERE ID = ?" to query-sql
    move request-reason to parameter-value perform add-parameter
    move actor-id to parameter-value perform add-parameter
    move loan-id to parameter-value perform add-parameter
    perform execute-query
    move "LOAN_REJECTED" to audit-action
    move loan-id to audit-subject perform write-audit.
pay-loan.
    perform require-customer
    perform validate-request-key
    perform lock-loan
    perform reset-query
    string "SELECT I.LOAN_ID FROM BANK_TRANSACTIONS T LEFT JOIN BANK_INSTALLMENTS I ON I.TRANSACTION_ID = T.ID "
        "WHERE T.ACTOR_ID = ? AND T.REQUEST_KEY = ?" into query-sql end-string
    move actor-id to parameter-value perform add-parameter
    move request-key to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    if fetch-status = zero
        move 1 to parameter-index perform read-column
        if function numval(column-value) not = function numval(loan-id)
            move "REQUEST_CONFLICT" to error-code perform finish-request
        end-if
        exit paragraph
    end-if
    if loan-state not = "ACTIVE"
        move "LOAN_NOT_ACTIVE" to error-code perform finish-request
    end-if
    perform require-active-account
    perform reset-query
    move "SELECT NUMBER, AMOUNT FROM BANK_INSTALLMENTS WHERE LOAN_ID = ? AND PAID_AT IS NULL ORDER BY NUMBER FETCH FIRST 1 ROW ONLY" to query-sql
    move loan-id to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    if fetch-status = 2
        move "LOAN_NOT_ACTIVE" to error-code perform finish-request
    end-if
    move 1 to parameter-index perform read-column
    compute installment-number = function numval(column-value) end-compute
    move 2 to parameter-index perform read-column
    compute movement-amount = function numval(column-value) end-compute
    move "PAYMENT" to movement-kind
    move "-" to entry-direction
    perform post-single-entry
    perform reset-query
    move "UPDATE BANK_INSTALLMENTS SET PAID_AT = CURRENT TIMESTAMP, TRANSACTION_ID = ? WHERE LOAN_ID = ? AND NUMBER = ?" to query-sql
    move transaction-id to parameter-value perform add-parameter
    move loan-id to parameter-value perform add-parameter
    move installment-number to count-text
    move count-text to parameter-value perform add-parameter
    perform execute-query
    if installment-number = loan-months
        perform reset-query
        move "UPDATE BANK_LOANS SET STATUS = 'PAID' WHERE ID = ?" to query-sql
        move loan-id to parameter-value perform add-parameter
        perform execute-query
    end-if.
list-loans.
    perform reset-query
    string "SELECT L.*, U.NAME AS OWNER_NAME, "
        "(SELECT COALESCE(SUM(CAPITAL),0) FROM BANK_INSTALLMENTS I WHERE I.LOAN_ID = L.ID AND I.PAID_AT IS NULL) AS OUTSTANDING "
        "FROM BANK_LOANS L JOIN BANK_USERS U ON U.ID = L.USER_ID "
        "WHERE L.ID < ? AND (CAST(? AS VARCHAR(10)) = 'ADMIN' OR L.USER_ID = ?) ORDER BY L.ID DESC FETCH FIRST 50 ROWS ONLY"
        into query-sql end-string
    move request-cursor to parameter-value perform add-parameter
    move actor-role to parameter-value perform add-parameter
    move actor-id to parameter-value perform add-parameter
    perform execute-query perform append-rows.
read-loan.
    perform lock-loan
    perform reset-query
    string "SELECT L.*, U.NAME AS OWNER_NAME, "
        "(SELECT SUM(INTEREST) FROM BANK_INSTALLMENTS I WHERE I.LOAN_ID = L.ID) AS TOTAL_INTEREST, "
        "(SELECT SUM(AMOUNT) FROM BANK_INSTALLMENTS I WHERE I.LOAN_ID = L.ID) AS TOTAL_REPAYMENT, "
        "(SELECT COALESCE(SUM(CAPITAL),0) FROM BANK_INSTALLMENTS I WHERE I.LOAN_ID = L.ID AND I.PAID_AT IS NULL) AS OUTSTANDING "
        "FROM BANK_LOANS L JOIN BANK_USERS U ON U.ID = L.USER_ID WHERE L.ID = ?" into query-sql end-string
    move loan-id to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    call static "bank_result_row" using z"loan" end-call
    perform reset-query
    move "SELECT * FROM BANK_INSTALLMENTS WHERE LOAN_ID = ? ORDER BY NUMBER" to query-sql
    move loan-id to parameter-value perform add-parameter
    perform execute-query
    move 60 to row-limit
    perform append-rows.

load-original.
    move request-original to numeric-value perform validate-id
    move request-original to original-id
    perform reset-query
    move "SELECT KIND, SOURCE_ID, TARGET_ID, AMOUNT FROM BANK_TRANSACTIONS WHERE ID = ?"
        to query-sql
    move original-id to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    if fetch-status = 2
        move "TRANSACTION_NOT_FOUND" to error-code perform finish-request
    end-if
    move 1 to parameter-index perform read-column
    move column-value(1:10) to original-kind
    move 2 to parameter-index perform read-column
    move column-value(1:19) to original-source
    move 3 to parameter-index perform read-column
    move column-value(1:19) to original-target
    move 4 to parameter-index perform read-column
    compute movement-amount = function numval(column-value) end-compute.
reverse-movement.
    perform require-admin
    perform validate-reason
    perform load-original
    if original-kind = "REVERSAL"
        move "REVERSAL_NOT_ALLOWED" to error-code perform finish-request
    end-if
    move original-target to target-id
    move spaces to source-id
    if original-kind = "TRANSFER"
        move original-target to source-id
        move original-source to target-id
    end-if
    perform lock-movement-accounts
    *> Account locks serialize competing reversals of the same original.
    perform reset-query
    move "SELECT TRANSACTION_ID, REASON FROM BANK_REVERSALS WHERE ORIGINAL_ID = ?"
        to query-sql
    move original-id to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    if fetch-status = zero
        move 1 to parameter-index perform read-column
        move column-value(1:19) to transaction-id
        move 2 to parameter-index perform read-column
        if column-value(1:240) not = request-reason
            move "ALREADY_REVERSED" to error-code perform finish-request
        end-if
        call static "bank_text" using z"id" transaction-id by value 19
        end-call
        exit paragraph
    end-if
    if target-state not = "ACTIVE" or
        (source-id not = spaces and source-state not = "ACTIVE")
        move "ACCOUNT_BLOCKED" to error-code perform finish-request
    end-if
    if source-id = spaces
        if target-balance < movement-amount
            move "INSUFFICIENT_FUNDS" to error-code perform finish-request
        end-if
        compute updated-target = target-balance - movement-amount
        end-compute
    else
        if source-balance < movement-amount
            move "INSUFFICIENT_FUNDS" to error-code perform finish-request
        end-if
        compute updated-source = source-balance - movement-amount
        end-compute
        compute updated-target = target-balance + movement-amount
            on size error
                move "BALANCE_LIMIT" to error-code perform finish-request
        end-compute
    end-if
    move "REVERSAL" to movement-kind
    move spaces to request-key
    string "REVERSAL:" function trim(original-id) into request-key
    end-string
    perform insert-movement
    perform reset-query
    move "INSERT INTO BANK_REVERSALS (ORIGINAL_ID, TRANSACTION_ID, REASON) VALUES (?, ?, ?)"
        to query-sql
    move original-id to parameter-value perform add-parameter
    move transaction-id to parameter-value perform add-parameter
    move request-reason to parameter-value perform add-parameter
    perform execute-query
    move target-id to account-id
    move updated-target to balance-text
    if source-id = spaces move "-" to entry-direction
    else move "+" to entry-direction end-if
    perform write-entry
    if source-id not = spaces
        move source-id to account-id
        move updated-source to balance-text
        move "-" to entry-direction
        perform write-entry
    end-if
    move "REVERSAL" to audit-action
    move transaction-id to audit-subject
    perform write-audit
    call static "bank_text" using z"id" transaction-id by value 19
    end-call.
read-transaction.
    perform require-admin
    move request-original to numeric-value perform validate-id
    perform reset-query
    string "SELECT T.*, R.TRANSACTION_ID AS REVERSED_BY, "
        "V.ORIGINAL_ID, V.REASON FROM BANK_TRANSACTIONS T "
        "LEFT JOIN BANK_REVERSALS R ON R.ORIGINAL_ID = T.ID "
        "LEFT JOIN BANK_REVERSALS V ON V.TRANSACTION_ID = T.ID "
        "WHERE T.ID = ?" into query-sql
    end-string
    move request-original to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    if fetch-status = 2
        move "TRANSACTION_NOT_FOUND" to error-code perform finish-request
    end-if
    call static "bank_result_row" using z"transaction" end-call.

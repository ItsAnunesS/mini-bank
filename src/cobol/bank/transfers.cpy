move-money.
    move request-target to numeric-value perform validate-id
    move request-target to target-id
    move request-source to source-id
    if source-id = spaces
        perform require-admin
        move "DEPOSIT" to movement-kind
    else
        if actor-role not = "CUSTOMER"
            move "FORBIDDEN" to error-code
            perform finish-request
        end-if
        move source-id to numeric-value perform validate-id
        if function numval(source-id) = function numval(target-id)
            move "SAME_ACCOUNT" to error-code
            perform finish-request
        end-if
        move "TRANSFER" to movement-kind
    end-if
    call "validate-amount" using request-amount movement-amount
        operation-result end-call
    if operation-result not = zero
        move "INVALID_AMOUNT" to error-code
        perform finish-request
    end-if
    perform validate-request-key
    perform lock-movement-accounts
    if source-id not = spaces and
        function numval(source-owner) not = function numval(actor-id)
        move "FORBIDDEN" to error-code perform finish-request
    end-if
    perform find-previous-movement
    if transaction-id not = spaces
        call static "bank_text" using z"id" transaction-id by value 19
        end-call
        exit paragraph
    end-if
    if target-state not = "ACTIVE" or
        (source-id not = spaces and source-state not = "ACTIVE")
        move "ACCOUNT_BLOCKED" to error-code
        perform finish-request
    end-if
    if source-id not = spaces
        if source-balance < movement-amount
            move "INSUFFICIENT_FUNDS" to error-code
            perform finish-request
        end-if
        compute updated-source = source-balance - movement-amount
        end-compute
    end-if
    compute updated-target = target-balance + movement-amount
        on size error
            move "BALANCE_LIMIT" to error-code
            perform finish-request
    end-compute
    perform insert-movement
    move target-id to account-id
    move "+" to entry-direction
    move updated-target to balance-text
    perform write-entry
    if source-id not = spaces
        move source-id to account-id
        move "-" to entry-direction
        move updated-source to balance-text
        perform write-entry
    end-if
    move movement-kind to audit-action
    move transaction-id to audit-subject
    perform write-audit
    call static "bank_text" using z"id" transaction-id by value 19
    end-call.
validate-request-key.
    move function lower-case(request-key) to request-key
    if function length(function trim(request-key)) not = 36
        move "INVALID_REQUEST_KEY" to error-code
        perform finish-request
    end-if
    perform varying character-index from 1 by 1
        until character-index > 36
        evaluate character-index
            when 9 when 14 when 19 when 24
                if request-key(character-index:1) not = "-"
                    move "INVALID_REQUEST_KEY" to error-code
                end-if
            when other
                if not (request-key(character-index:1) >= "0" and <= "9")
                    and not (request-key(character-index:1) >= "a" and <= "f")
                    move "INVALID_REQUEST_KEY" to error-code
                end-if
        end-evaluate
    end-perform
    if error-code not = spaces perform finish-request end-if.
lock-account.
    perform reset-query
    move "SELECT ID, USER_ID, STATUS, BALANCE FROM BANK_ACCOUNTS WHERE ID = ? WITH RS USE AND KEEP UPDATE LOCKS"
        to query-sql
    move lock-id to parameter-value perform add-parameter
    perform execute-query
    perform fetch-row
    if fetch-status = 2
        move "ACCOUNT_UNAVAILABLE" to error-code
        perform finish-request
    end-if
    move 2 to parameter-index perform read-column
    if function numval(lock-id) = function numval(target-id)
        move column-value(1:19) to target-owner
    else
        move column-value(1:19) to source-owner
    end-if
    move 3 to parameter-index perform read-column
    if function numval(lock-id) = function numval(target-id)
        move column-value(1:10) to target-state
    else
        move column-value(1:10) to source-state
    end-if
    move 4 to parameter-index perform read-column
    if function numval(lock-id) = function numval(target-id)
        compute target-balance = function numval(column-value)
        end-compute
    else
        compute source-balance = function numval(column-value)
        end-compute
    end-if.
find-previous-movement.
    move spaces to transaction-id
    perform reset-query
    move "SELECT ID, SOURCE_ID, TARGET_ID, AMOUNT FROM BANK_TRANSACTIONS WHERE ACTOR_ID = ? AND REQUEST_KEY = ?"
        to query-sql
    move actor-id to parameter-value perform add-parameter
    move request-key to parameter-value perform add-parameter
    perform execute-query
    perform fetch-row
    if fetch-status = 2 exit paragraph end-if
    move 1 to parameter-index perform read-column
    move column-value(1:19) to transaction-id
    move 2 to parameter-index perform read-column
    move column-value(1:19) to saved-source
    move 3 to parameter-index perform read-column
    move column-value(1:19) to saved-target
    move 4 to parameter-index perform read-column
    compute saved-amount = function numval(column-value) end-compute
    if function numval(saved-source) not = function numval(source-id)
        or function numval(saved-target) not = function numval(target-id)
        or saved-amount not = movement-amount
        move "REQUEST_CONFLICT" to error-code
        perform finish-request
    end-if.
insert-movement.
    perform reset-query
    move "INSERT INTO BANK_TRANSACTIONS (ACTOR_ID, REQUEST_KEY, KIND, SOURCE_ID, TARGET_ID, AMOUNT) VALUES (?, ?, ?, ?, ?, ?)"
        to query-sql
    move actor-id to parameter-value perform add-parameter
    move request-key to parameter-value perform add-parameter
    move movement-kind to parameter-value perform add-parameter
    move source-id to parameter-value perform add-parameter
    if source-id = spaces move 1 to parameter-null(parameter-count) end-if
    move target-id to parameter-value perform add-parameter
    move movement-amount to amount-text
    move amount-text to parameter-value perform add-parameter
    perform execute-query
    perform last-identity
    move column-value(1:19) to transaction-id.
write-entry.
    perform reset-query
    move "UPDATE BANK_ACCOUNTS SET BALANCE = ? WHERE ID = ?" to query-sql
    move balance-text to parameter-value perform add-parameter
    move account-id to parameter-value perform add-parameter
    perform execute-query
    call static "bank_db_rows" returning native-status end-call
    if native-status not = 1
        move "DATABASE_ERROR" to error-code
        perform finish-request
    end-if
    perform reset-query
    move "INSERT INTO BANK_ENTRIES (TRANSACTION_ID, ACCOUNT_ID, DIRECTION, AMOUNT, BALANCE_AFTER) VALUES (?, ?, ?, ?, ?)"
        to query-sql
    move transaction-id to parameter-value perform add-parameter
    move account-id to parameter-value perform add-parameter
    move entry-direction to parameter-value perform add-parameter
    move amount-text to parameter-value perform add-parameter
    move balance-text to parameter-value perform add-parameter
    perform execute-query.

lock-movement-accounts.
    *> All monetary workflows acquire account locks in the same order.
    if source-id = spaces
        move target-id to lock-id perform lock-account
    else
        if function numval(source-id) < function numval(target-id)
            move source-id to lock-id perform lock-account
            move target-id to lock-id perform lock-account
        else
            move target-id to lock-id perform lock-account
            move source-id to lock-id perform lock-account
        end-if
    end-if.

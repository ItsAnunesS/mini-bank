create-admin.
    perform validate-profile
    perform reset-query
    move "LOCK TABLE BANK_USERS IN EXCLUSIVE MODE" to query-sql
    perform execute-query
    perform reset-query
    move "SELECT ID FROM BANK_USERS WHERE ROLE = 'ADMIN' FETCH FIRST 1 ROW ONLY"
        to query-sql
    perform execute-query
    perform fetch-row
    if fetch-status = zero
        move "ADMIN_EXISTS" to error-code
        perform finish-request
    end-if
    move "ADMIN" to actor-role
    perform insert-user
    move user-id to actor-id audit-subject
    move "ADMIN_CREATED" to audit-action
    perform write-audit
    call static "bank_text" using z"id" user-id by value 19 end-call.
create-customer.
    perform require-admin
    perform validate-profile
    perform reset-query
    move "SELECT ID FROM BANK_USERS WHERE EMAIL = ?" to query-sql
    move request-email to parameter-value perform add-parameter
    perform execute-query
    perform fetch-row
    if fetch-status = zero
        move "EMAIL_EXISTS" to error-code
        perform finish-request
    end-if
    move "CUSTOMER" to actor-role
    perform insert-user
    move "CUSTOMER_CREATED" to audit-action
    move user-id to audit-subject
    perform write-audit
    call static "bank_text" using z"id" user-id by value 19 end-call.
insert-user.
    perform reset-query
    move "INSERT INTO BANK_USERS (NAME, EMAIL, PASSWORD_HASH, ROLE) VALUES (?, ?, ?, ?)"
        to query-sql
    move request-name to parameter-value perform add-parameter
    move request-email to parameter-value perform add-parameter
    move password-hash to parameter-value perform add-parameter
    move actor-role to parameter-value perform add-parameter
    perform execute-query
    perform last-identity
    move column-value(1:19) to user-id.
insert-account.
    if request-account-type not = "CURRENT" and not = "SAVINGS"
        move "INVALID_ACCOUNT_TYPE" to error-code perform finish-request
    end-if
    perform reset-query
    move "INSERT INTO BANK_ACCOUNTS (USER_ID, STATUS) VALUES (?, 'PENDING')" to query-sql
    move user-id to parameter-value perform add-parameter
    perform execute-query
    perform last-identity
    move column-value(1:19) to account-id
    if request-account-type = "SAVINGS"
        perform reset-query
        move "INSERT INTO BANK_SAVINGS (ACCOUNT_ID) VALUES (?)"
            to query-sql
        move account-id to parameter-value perform add-parameter
        perform execute-query
    end-if.
create-account.
    if actor-role = "ADMIN"
        perform normalize-email
        perform reset-query
        move "SELECT ID FROM BANK_USERS WHERE EMAIL = ? AND ROLE = 'CUSTOMER' WITH RS USE AND KEEP UPDATE LOCKS"
            to query-sql
        move request-email to parameter-value perform add-parameter
    else
        if actor-role not = "CUSTOMER"
            move "FORBIDDEN" to error-code perform finish-request
        end-if
        perform reset-query
        move "SELECT ID FROM BANK_USERS WHERE ID = ? AND ROLE = 'CUSTOMER' WITH RS USE AND KEEP UPDATE LOCKS"
            to query-sql
        move actor-id to parameter-value perform add-parameter
    end-if
    *> Serialize all requests for the same owner, including different account types.
    perform execute-query perform fetch-row
    if fetch-status = 2
        move "CUSTOMER_NOT_FOUND" to error-code perform finish-request
    end-if
    move 1 to parameter-index perform read-column
    move column-value(1:19) to user-id
    evaluate request-account-type
        when "CURRENT" move 3 to account-limit
        when "SAVINGS" move 2 to account-limit
        when other
            move "INVALID_ACCOUNT_TYPE" to error-code perform finish-request
    end-evaluate
    perform validate-request-key
    perform reset-query
    string "SELECT A.ID, A.USER_ID, CASE WHEN S.ACCOUNT_ID IS NULL "
        "THEN 'CURRENT' ELSE 'SAVINGS' END FROM BANK_ACCOUNT_APPROVALS P "
        "JOIN BANK_ACCOUNTS A ON A.ID = P.ACCOUNT_ID "
        "LEFT JOIN BANK_SAVINGS S ON S.ACCOUNT_ID = A.ID "
        "WHERE P.REQUESTED_BY = ? AND P.REQUEST_KEY = ?"
        into query-sql end-string
    move actor-id to parameter-value perform add-parameter
    move request-key to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    if fetch-status = zero
        move 1 to parameter-index perform read-column
        move column-value(1:19) to account-id
        move 2 to parameter-index perform read-column
        if function numval(column-value) not = function numval(user-id)
            move "REQUEST_CONFLICT" to error-code perform finish-request
        end-if
        move 3 to parameter-index perform read-column
        if column-value(1:10) not = request-account-type
            move "REQUEST_CONFLICT" to error-code perform finish-request
        end-if
        call static "bank_text" using z"id" account-id by value 19
        end-call
        exit paragraph
    end-if
    perform reset-query
    string "SELECT COUNT(*) FROM BANK_ACCOUNTS A "
        "LEFT JOIN BANK_SAVINGS S ON S.ACCOUNT_ID = A.ID "
        "WHERE A.USER_ID = ? AND A.STATUS IN ('ACTIVE', 'BLOCKED', 'PENDING') AND "
        "CASE WHEN S.ACCOUNT_ID IS NULL THEN 'CURRENT' ELSE 'SAVINGS' END = ?"
        into query-sql end-string
    move user-id to parameter-value perform add-parameter
    move request-account-type to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    move 1 to parameter-index perform read-column
    compute existing-account-count = function numval(column-value)
    end-compute
    if existing-account-count >= account-limit
        move "ACCOUNT_LIMIT" to error-code perform finish-request
    end-if
    perform insert-account
    perform reset-query
    move "INSERT INTO BANK_ACCOUNT_APPROVALS (ACCOUNT_ID, REQUESTED_BY, REQUEST_KEY) VALUES (?, ?, ?)"
        to query-sql
    move account-id to parameter-value perform add-parameter
    move actor-id to parameter-value perform add-parameter
    move request-key to parameter-value perform add-parameter
    perform execute-query
    move "ACCOUNT_REQUESTED" to audit-action
    move account-id to audit-subject perform write-audit
    call static "bank_text" using z"id" account-id by value 19 end-call.
approve-account.
    perform require-admin
    move request-target to numeric-value perform validate-id
    perform reset-query
    move "SELECT STATUS FROM BANK_ACCOUNTS WHERE ID = ? WITH RS USE AND KEEP UPDATE LOCKS"
        to query-sql
    move request-target to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    if fetch-status = 2
        move "ACCOUNT_UNAVAILABLE" to error-code perform finish-request
    end-if
    move 1 to parameter-index perform read-column
    move column-value(1:10) to approval-state
    perform reset-query
    move "SELECT APPROVED_BY FROM BANK_ACCOUNT_APPROVALS WHERE ACCOUNT_ID = ?"
        to query-sql
    move request-target to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    if fetch-status = 2
        move "NOT_PENDING" to error-code perform finish-request
    end-if
    move 1 to parameter-index perform read-column
    move column-value(1:19) to approved-by
    *> Replayed approvals must never reactivate a subsequently blocked account.
    if approved-by not = spaces exit paragraph end-if
    if approval-state not = "PENDING"
        move "NOT_PENDING" to error-code perform finish-request
    end-if
    perform reset-query
    move "UPDATE BANK_ACCOUNTS SET STATUS = 'ACTIVE' WHERE ID = ?"
        to query-sql
    move request-target to parameter-value perform add-parameter
    perform execute-query
    perform reset-query
    move "UPDATE BANK_ACCOUNT_APPROVALS SET APPROVED_BY = ?, APPROVED_AT = CURRENT TIMESTAMP WHERE ACCOUNT_ID = ?"
        to query-sql
    move actor-id to parameter-value perform add-parameter
    move request-target to parameter-value perform add-parameter
    perform execute-query
    move "ACCOUNT_APPROVED" to audit-action
    move request-target to audit-subject perform write-audit
    perform create-account-notification.
change-account-status.
    perform require-admin
    move request-target to numeric-value perform validate-id
    if request-status not = "ACTIVE" and not = "BLOCKED"
        move "INVALID_STATUS" to error-code
        perform finish-request
    end-if
    perform reset-query
    move "UPDATE BANK_ACCOUNTS SET STATUS = ? WHERE ID = ? AND STATUS IN ('ACTIVE', 'BLOCKED')" to query-sql
    move request-status to parameter-value perform add-parameter
    move request-target to parameter-value perform add-parameter
    perform execute-query
    call static "bank_db_rows" returning native-status end-call
    if native-status not = 1
        move "ACCOUNT_UNAVAILABLE" to error-code
        perform finish-request
    end-if
    move spaces to audit-action
    string "ACCOUNT_" function trim(request-status)
        into audit-action end-string
    move request-target to audit-subject
    perform write-audit.
resolve-account-request.
    if request-command = "REJECT_ACCOUNT"
        perform require-admin
        perform validate-reason
        move "REJECTED" to request-status
    else
        if actor-role not = "CUSTOMER"
            move "FORBIDDEN" to error-code perform finish-request
        end-if
        move "CANCELLED" to request-status
    end-if
    move request-target to numeric-value perform validate-id
    perform reset-query
    move "SELECT USER_ID, STATUS FROM BANK_ACCOUNTS WHERE ID = ? WITH RS USE AND KEEP UPDATE LOCKS"
        to query-sql
    move request-target to parameter-value perform add-parameter
    perform execute-query perform fetch-row
    if fetch-status = 2
        move "ACCOUNT_UNAVAILABLE" to error-code perform finish-request
    end-if
    move 1 to parameter-index perform read-column
    if actor-role = "CUSTOMER" and
        function numval(column-value) not = function numval(actor-id)
        move "ACCOUNT_UNAVAILABLE" to error-code perform finish-request
    end-if
    move 2 to parameter-index perform read-column
    move column-value(1:10) to approval-state
    *> Serialize competing decisions with approval and preserve the first outcome.
    if approval-state = request-status
        if request-status = "REJECTED"
            perform reset-query
            move "SELECT REASON FROM BANK_ACCOUNT_RESOLUTIONS WHERE ACCOUNT_ID = ?"
                to query-sql
            move request-target to parameter-value perform add-parameter
            perform execute-query perform fetch-row
            move 1 to parameter-index perform read-column
            if column-value(1:240) not = request-reason
                move "REQUEST_CONFLICT" to error-code perform finish-request
            end-if
        end-if
        exit paragraph
    end-if
    if approval-state not = "PENDING"
        move "NOT_PENDING" to error-code perform finish-request
    end-if
    perform reset-query
    move "UPDATE BANK_ACCOUNTS SET STATUS = ? WHERE ID = ?" to query-sql
    move request-status to parameter-value perform add-parameter
    move request-target to parameter-value perform add-parameter
    perform execute-query
    perform reset-query
    move "INSERT INTO BANK_ACCOUNT_RESOLUTIONS (ACCOUNT_ID, STATUS, REASON, RESOLVED_BY) VALUES (?, ?, ?, ?)"
        to query-sql
    move request-target to parameter-value perform add-parameter
    move request-status to parameter-value perform add-parameter
    move request-reason to parameter-value perform add-parameter
    if request-status = "CANCELLED" move 1 to parameter-null(3) end-if
    move actor-id to parameter-value perform add-parameter
    perform execute-query
    move spaces to audit-action
    string "ACCOUNT_" function trim(request-status) into audit-action
    end-string
    move request-target to audit-subject perform write-audit
    if request-status = "REJECTED" perform create-account-notification end-if.

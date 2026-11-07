create-account-notification.
    *> The decision and its notification commit or roll back together.
    perform reset-query
    move "INSERT INTO BANK_NOTIFICATIONS (USER_ID, ACCOUNT_ID, KIND, REASON) SELECT USER_ID, ID, ?, ? FROM BANK_ACCOUNTS WHERE ID = ?"
        to query-sql
    move audit-action to parameter-value perform add-parameter
    move request-reason to parameter-value perform add-parameter
    if audit-action = "ACCOUNT_APPROVED" move 1 to parameter-null(2) end-if
    move request-target to parameter-value perform add-parameter
    perform execute-query.
require-notification-owner.
    if actor-role not = "CUSTOMER"
        move "FORBIDDEN" to error-code perform finish-request
    end-if.
list-notifications.
    perform require-notification-owner
    perform reset-query
    move "SELECT ID, ACCOUNT_ID, KIND, REASON, CREATED_AT, READ_AT FROM BANK_NOTIFICATIONS WHERE USER_ID = ? AND ID < ? ORDER BY ID DESC FETCH FIRST 50 ROWS ONLY"
        to query-sql
    move actor-id to parameter-value perform add-parameter
    move request-cursor to parameter-value perform add-parameter
    perform execute-query perform append-rows.
mark-notification-read.
    perform require-notification-owner
    move request-target to numeric-value perform validate-id
    perform reset-query
    move "UPDATE BANK_NOTIFICATIONS SET READ_AT = COALESCE(READ_AT, CURRENT TIMESTAMP) WHERE ID = ? AND USER_ID = ?"
        to query-sql
    move request-target to parameter-value perform add-parameter
    move actor-id to parameter-value perform add-parameter
    perform execute-query
    call static "bank_db_rows" returning native-status end-call
    if native-status not = 1
        move "NOTIFICATION_UNAVAILABLE" to error-code perform finish-request
    end-if.
count-unread-notifications.
    perform require-notification-owner
    perform reset-query
    move "SELECT COUNT_BIG(*) AS UNREAD_COUNT FROM BANK_NOTIFICATIONS WHERE USER_ID = ? AND READ_AT IS NULL"
        to query-sql
    move actor-id to parameter-value perform add-parameter
    perform execute-query perform append-rows.
mark-all-notifications-read.
    perform require-notification-owner
    perform reset-query
    move "UPDATE BANK_NOTIFICATIONS SET READ_AT = CURRENT TIMESTAMP WHERE USER_ID = ? AND READ_AT IS NULL"
        to query-sql
    move actor-id to parameter-value perform add-parameter
    perform execute-query.

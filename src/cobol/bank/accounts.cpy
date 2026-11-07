list-accounts.
    perform reset-query
    if actor-role = "ADMIN"
        string "SELECT A.*, U.NAME AS OWNER_NAME, U.EMAIL, R.REASON AS REJECTION_REASON, "
            "CASE WHEN S.ACCOUNT_ID IS NULL THEN 'CURRENT' ELSE 'SAVINGS' END AS ACCOUNT_TYPE "
            "FROM BANK_ACCOUNTS A JOIN BANK_USERS U ON U.ID = A.USER_ID "
            "LEFT JOIN BANK_SAVINGS S ON S.ACCOUNT_ID = A.ID "
            "LEFT JOIN BANK_ACCOUNT_RESOLUTIONS R ON R.ACCOUNT_ID = A.ID "
            "WHERE A.ID < ? AND (? <> 'PENDING_ACCOUNTS' OR A.STATUS = 'PENDING') ORDER BY A.ID DESC FETCH FIRST 50 ROWS ONLY"
            into query-sql end-string
        move request-cursor to parameter-value perform add-parameter
        move request-command to parameter-value perform add-parameter
    else
        string "SELECT A.*, U.NAME AS OWNER_NAME, U.EMAIL, R.REASON AS REJECTION_REASON, "
            "CASE WHEN S.ACCOUNT_ID IS NULL THEN 'CURRENT' ELSE 'SAVINGS' END AS ACCOUNT_TYPE "
            "FROM BANK_ACCOUNTS A JOIN BANK_USERS U ON U.ID = A.USER_ID "
            "LEFT JOIN BANK_SAVINGS S ON S.ACCOUNT_ID = A.ID "
            "LEFT JOIN BANK_ACCOUNT_RESOLUTIONS R ON R.ACCOUNT_ID = A.ID "
            "WHERE A.ID < ? AND A.USER_ID = ? "
            "ORDER BY A.ID DESC FETCH FIRST 50 ROWS ONLY"
            into query-sql end-string
        move request-cursor to parameter-value perform add-parameter
        move actor-id to parameter-value perform add-parameter
    end-if
    perform execute-query
    perform append-rows.
list-statement.
    move request-target to numeric-value perform validate-id
    perform reset-query
    string "SELECT A.ID, A.USER_ID, A.STATUS, A.BALANCE, A.CREATED_AT, "
        "U.NAME AS OWNER_NAME, U.EMAIL, R.REASON AS REJECTION_REASON, "
        "CASE WHEN S.ACCOUNT_ID IS NULL THEN 'CURRENT' ELSE 'SAVINGS' END AS ACCOUNT_TYPE "
        "FROM BANK_ACCOUNTS A "
        "JOIN BANK_USERS U ON U.ID = A.USER_ID "
        "LEFT JOIN BANK_SAVINGS S ON S.ACCOUNT_ID = A.ID "
        "LEFT JOIN BANK_ACCOUNT_RESOLUTIONS R ON R.ACCOUNT_ID = A.ID WHERE A.ID = ?"
        into query-sql end-string
    move request-target to parameter-value perform add-parameter
    perform execute-query
    perform fetch-row
    if fetch-status = 2
        move "ACCOUNT_UNAVAILABLE" to error-code
        perform finish-request
    end-if
    move 2 to parameter-index perform read-column
    if actor-role not = "ADMIN" and
        function numval(column-value) not = function numval(actor-id)
        move "ACCOUNT_UNAVAILABLE" to error-code
        perform finish-request
    end-if
    call static "bank_result_row" using z"account" end-call
    perform reset-query
    string "SELECT E.*, T.KIND, T.SOURCE_ID, T.TARGET_ID, R.TRANSACTION_ID AS REVERSED_BY, V.ORIGINAL_ID "
        "FROM BANK_ENTRIES E JOIN BANK_TRANSACTIONS T "
        "ON T.ID = E.TRANSACTION_ID "
        "LEFT JOIN BANK_REVERSALS R ON R.ORIGINAL_ID = T.ID "
        "LEFT JOIN BANK_REVERSALS V ON V.TRANSACTION_ID = T.ID "
        "WHERE E.ACCOUNT_ID = ? AND E.ID < ? "
        "ORDER BY E.ID DESC FETCH FIRST 50 ROWS ONLY"
        into query-sql end-string
    move request-target to parameter-value perform add-parameter
    move request-cursor to parameter-value perform add-parameter
    perform execute-query
    perform append-rows.
list-audit.
    perform require-admin
    perform reset-query
    string "SELECT A.*, U.NAME FROM BANK_AUDIT A "
        "JOIN BANK_USERS U ON U.ID = A.ACTOR_ID WHERE A.ID < ? "
        "ORDER BY A.ID DESC FETCH FIRST 50 ROWS ONLY"
        into query-sql end-string
    move request-cursor to parameter-value perform add-parameter
    perform execute-query
    perform append-rows.

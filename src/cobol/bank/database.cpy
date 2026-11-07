read-request.
    call static "bank_field" using z"month" request-month
        by value 7 returning native-status end-call
    perform check-field
    call static "bank_field" using z"daily_limit" request-daily-limit
        by value 50 returning native-status end-call
    perform check-field
    call static "bank_field" using z"months" request-months
        by value 3 returning native-status end-call
    perform check-field
    call static "bank_field" using z"command" request-command
        by value 32 returning native-status end-call
    perform check-field
    call static "bank_field" using z"token" request-token
        by value 100 returning native-status end-call
    perform check-field
    call static "bank_field" using z"name" request-name
        by value 120 returning native-status end-call
    perform check-field
    call static "bank_field" using z"email" request-email
        by value 254 returning native-status end-call
    perform check-field
    call static "bank_field" using z"password" request-password
        by value 512 returning native-status end-call
    perform check-field
    move native-status to password-length
    call static "bank_field" using z"source" request-source
        by value 19 returning native-status end-call
    perform check-field
    call static "bank_field" using z"target" request-target
        by value 19 returning native-status end-call
    perform check-field
    call static "bank_field" using z"before" request-cursor
        by value 19 returning native-status end-call
    perform check-field
    if request-cursor = spaces
        move "9223372036854775807" to request-cursor
    end-if
    move request-cursor to numeric-value
    perform validate-id
    call static "bank_field" using z"amount" request-amount
        by value 50 returning native-status end-call
    perform check-field
    call static "bank_field" using z"request_key" request-key
        by value 36 returning native-status end-call
    perform check-field
    call static "bank_field" using z"status" request-status
        by value 10 returning native-status end-call
    perform check-field
    call static "bank_field" using z"account_type" request-account-type
        by value 10 returning native-status end-call
    perform check-field
    if request-account-type = spaces
        move "CURRENT" to request-account-type
    end-if
    call static "bank_field" using z"original" request-original
        by value 19 returning native-status end-call
    perform check-field
    call static "bank_field" using z"reason" request-reason
        by value 240 returning native-status end-call
    perform check-field.
check-field.
    if native-status < zero
        move "INVALID_REQUEST" to error-code
        perform finish-request
    end-if.
validate-id.
    if numeric-value = spaces or
        function trim(numeric-value) is not numeric
        move "INVALID_REQUEST" to error-code
        perform finish-request
    end-if
    if function numval(numeric-value) < 1 or
        function numval(numeric-value) > 9223372036854775807
        move "INVALID_REQUEST" to error-code
        perform finish-request
    end-if.
reset-query.
    move spaces to query-sql
    move zero to parameter-count
    initialize query-parameters.
add-parameter.
    add 1 to parameter-count end-add
    if parameter-count > 12
        move "DATABASE_ERROR" to error-code
        perform finish-request
    end-if
    move parameter-value to parameter-text(parameter-count).
execute-query.
    call static "bank_db_prepare" using query-sql by value 4096
        returning native-status end-call
    perform check-database
    perform varying parameter-index from 1 by 1
        until parameter-index > parameter-count
        move parameter-text(parameter-index) to parameter-value
        call static "bank_db_bind" using
            by value parameter-index
            by reference parameter-value
            by value 512 parameter-null(parameter-index)
            returning native-status
        end-call
        perform check-database
    end-perform
    call static "bank_db_execute" returning native-status end-call
    perform check-database.
fetch-row.
    call static "bank_db_fetch" returning fetch-status end-call
    if fetch-status = 1
        move "DATABASE_ERROR" to error-code
        perform finish-request
    end-if.
read-column.
    call static "bank_db_column" using by value parameter-index
        by reference column-value by value 512
        returning native-status
    end-call
    perform check-database.
check-database.
    if native-status not = zero
        move "DATABASE_ERROR" to error-code
        perform finish-request
    end-if.
last-identity.
    perform reset-query
    move "SELECT IDENTITY_VAL_LOCAL() AS ID FROM SYSIBM.SYSDUMMY1"
        to query-sql
    perform execute-query
    perform fetch-row
    if fetch-status not = zero
        move "DATABASE_ERROR" to error-code
        perform finish-request
    end-if
    move 1 to parameter-index
    perform read-column.
append-rows.
    move zero to row-count
    perform fetch-row
    perform until fetch-status = 2
        add 1 to row-count end-add
        if row-count > row-limit
            move "DATABASE_ERROR" to error-code
            perform finish-request
        end-if
        call static "bank_result_row" using x"00" end-call
        perform fetch-row
    end-perform.
write-audit.
    perform reset-query
    move "INSERT INTO BANK_AUDIT (ACTOR_ID, ACTION, SUBJECT_ID) VALUES (?, ?, ?)"
        to query-sql
    move actor-id to parameter-value perform add-parameter
    move audit-action to parameter-value perform add-parameter
    move audit-subject to parameter-value perform add-parameter
    perform execute-query.
initialize-schema.
    perform reset-query
    move "SELECT TABNAME FROM SYSCAT.TABLES WHERE TABSCHEMA = CURRENT SCHEMA AND TABNAME = 'BANK_USERS'"
        to query-sql
    perform execute-query
    perform fetch-row
    if fetch-status = 2
        move 2 to migration-version
        perform apply-migration
    end-if
    perform reset-query
    move "SELECT TABNAME FROM SYSCAT.TABLES WHERE TABSCHEMA = CURRENT SCHEMA AND TABNAME = 'BANK_DAILY_CLOSES'"
        to query-sql
    perform execute-query
    perform fetch-row
    if fetch-status = 2 perform upgrade-daily-schema end-if
    perform upgrade-account-schema
    perform reset-query
    move "SELECT TABNAME FROM SYSCAT.TABLES WHERE TABSCHEMA = CURRENT SCHEMA AND TABNAME = 'BANK_ACCOUNT_RESOLUTIONS'"
        to query-sql
    perform execute-query perform fetch-row
    if fetch-status = 2
        move 5 to migration-version perform apply-migration
    end-if
    perform reset-query
    move "SELECT TABNAME FROM SYSCAT.TABLES WHERE TABSCHEMA = CURRENT SCHEMA AND TABNAME = 'BANK_NOTIFICATIONS'"
        to query-sql
    perform execute-query perform fetch-row
    if fetch-status = 2
        move 6 to migration-version perform apply-migration
    end-if
    perform reset-query
    move "SELECT TABNAME FROM SYSCAT.TABLES WHERE TABSCHEMA = CURRENT SCHEMA AND TABNAME = 'BANK_LOANS'" to query-sql
    perform execute-query perform fetch-row
    if fetch-status = 2
        move 7 to migration-version perform apply-migration
    end-if.
upgrade-daily-schema.
    *> Replace the original unnamed kind constraint in the same DDL transaction.
    perform reset-query
    move "SELECT CONSTNAME FROM SYSCAT.CHECKS WHERE TABSCHEMA = CURRENT SCHEMA AND TABNAME = 'BANK_TRANSACTIONS' AND TEXT LIKE '%KIND%'"
        to query-sql
    perform execute-query
    perform fetch-row
    if fetch-status not = zero
        move "DATABASE_ERROR" to error-code perform finish-request
    end-if
    move 1 to parameter-index perform read-column
    move column-value(1:128) to constraint-name
    perform reset-query
    string 'ALTER TABLE BANK_TRANSACTIONS DROP CONSTRAINT "'
        function trim(constraint-name) '"' into query-sql
    end-string
    perform execute-query
    move 3 to migration-version
    perform apply-migration.
upgrade-account-schema.
    perform reset-query
    move "SELECT TABNAME FROM SYSCAT.TABLES WHERE TABSCHEMA = CURRENT SCHEMA AND TABNAME = 'BANK_ACCOUNT_APPROVALS'"
        to query-sql
    perform execute-query perform fetch-row
    if fetch-status = zero exit paragraph end-if
    perform reset-query
    move "SELECT CONSTNAME FROM SYSCAT.CHECKS WHERE TABSCHEMA = CURRENT SCHEMA AND TABNAME = 'BANK_ACCOUNTS' AND TEXT LIKE '%STATUS%'"
        to query-sql
    perform execute-query perform fetch-row
    if fetch-status not = zero
        move "DATABASE_ERROR" to error-code perform finish-request
    end-if
    move 1 to parameter-index perform read-column
    move column-value(1:128) to constraint-name
    perform reset-query
    string 'ALTER TABLE BANK_ACCOUNTS DROP CONSTRAINT "'
        function trim(constraint-name) '"' into query-sql
    end-string
    perform execute-query
    move 4 to migration-version perform apply-migration.
apply-migration.
    move zero to native-status
    perform until native-status = 2
        perform reset-query
        call static "bank_migration" using by value migration-version
            by reference query-sql by value 4096
            returning native-status end-call
        if native-status = 1 perform check-database end-if
        if native-status = zero and query-sql not = spaces
            perform execute-query
        end-if
    end-perform.
finish-request.
    if database-opened = 1
        if error-code = spaces
            call static "bank_db_end" using by value 1
                returning native-status end-call
            if native-status not = zero
                move "DATABASE_ERROR" to error-code
            end-if
        else
            call static "bank_db_end" using by value 0 end-call
        end-if
        if error-code = "DATABASE_ERROR"
            call static "bank_db_discard" end-call
        else
            call static "bank_db_close" end-call
        end-if
    end-if
    call static "bank_finish" using error-code by value 40
        returning return-code end-call
    goback.
validate-reason.
    move function trim(request-reason) to request-reason
    compute reason-length = function length(function trim(request-reason))
    end-compute
    call static "bank_utf8_length" using request-reason
        by value reason-length returning reason-characters end-call
    if reason-characters < 5
        move "INVALID_REASON" to error-code perform finish-request
    end-if.

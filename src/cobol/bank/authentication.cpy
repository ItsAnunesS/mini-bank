normalize-email.
    move function lower-case(function trim(request-email))
        to request-email
    compute email-length = function length(function trim(request-email))
    end-compute
    move zero to at-count email-at
    perform varying character-index from 1 by 1
        until character-index > email-length
        if request-email(character-index:1) = "@"
            add 1 to at-count end-add
            move character-index to email-at
        end-if
        if request-email(character-index:1) <= space
            move "INVALID_EMAIL" to error-code
        end-if
    end-perform
    if at-count not = 1 or email-at < 2 or
        email-at >= email-length
        move "INVALID_EMAIL" to error-code
    end-if
    move zero to at-count
    if error-code = spaces
        perform varying character-index from email-at by 1
            until character-index > email-length
            if request-email(character-index:1) = "." and
                character-index > email-at + 1 and
                character-index < email-length
                add 1 to at-count end-add
            end-if
        end-perform
        if at-count = zero move "INVALID_EMAIL" to error-code end-if
    end-if
    if error-code not = spaces perform finish-request end-if.
validate-profile.
    perform normalize-email
    move function trim(request-name) to request-name
    if request-name = spaces
        move "INVALID_NAME" to error-code
        perform finish-request
    end-if
    call static "bank_utf8_length" using request-password
        by value password-length returning password-characters end-call
    if password-characters < 12 or password-characters > 128
        move "INVALID_PASSWORD" to error-code
        perform finish-request
    end-if
    call static "bank_password_hash" using request-password
        by value password-length by reference password-hash
        returning native-status end-call
    perform check-database.
lookup-session.
    move 2 to fetch-status
    if request-token = spaces exit paragraph end-if
    call static "bank_sha256" using request-token by value 100
        by reference session-hash returning native-status end-call
    perform check-database
    perform reset-query
    string "SELECT U.ID, U.NAME, U.EMAIL, U.ROLE FROM BANK_USERS U "
        "JOIN BANK_SESSIONS S ON S.USER_ID = U.ID "
        "WHERE S.TOKEN_HASH = ? AND S.EXPIRES_AT > CURRENT TIMESTAMP"
        into query-sql
    end-string
    move session-hash to parameter-value perform add-parameter
    perform execute-query
    perform fetch-row.
read-session.
    perform lookup-session
    if fetch-status = zero
        move 1 to parameter-index perform read-column
        move column-value(1:19) to actor-id
        move 4 to parameter-index perform read-column
        move column-value(1:10) to actor-role
        call static "bank_result_row" using x"00" end-call
        if actor-role = "CUSTOMER"
            perform load-unread-count
            move 1 to parameter-index perform read-column
            call static "bank_text" using z"unread_count" column-value by value 512 end-call
        end-if
    end-if.
authorize-session.
    perform lookup-session
    if fetch-status not = zero
        move "UNAUTHENTICATED" to error-code
        perform finish-request
    end-if
    move 1 to parameter-index perform read-column
    move column-value(1:19) to actor-id
    move 4 to parameter-index perform read-column
    move column-value(1:10) to actor-role.
require-admin.
    if actor-role not = "ADMIN"
        move "FORBIDDEN" to error-code
        perform finish-request
    end-if.
revoke-session.
    if request-token = spaces exit paragraph end-if
    call static "bank_sha256" using request-token by value 100
        by reference session-hash returning native-status end-call
    perform check-database
    perform reset-query
    move "DELETE FROM BANK_SESSIONS WHERE TOKEN_HASH = ?" to query-sql
    move session-hash to parameter-value perform add-parameter
    perform execute-query.
authenticate-user.
    call static "bank_utf8_length" using request-password
        by value password-length returning password-characters end-call
    if password-characters < 1 or password-characters > 128
        exit paragraph
    end-if
    move function lower-case(function trim(request-email))
        to request-email
    perform reset-query
    string "SELECT ID, PASSWORD_HASH, CASE WHEN LOCKED_UNTIL > "
        "CURRENT TIMESTAMP THEN 1 ELSE 0 END AS LOCKED "
        "FROM BANK_USERS WHERE EMAIL = ? "
        "WITH RS USE AND KEEP UPDATE LOCKS" into query-sql
    end-string
    move request-email to parameter-value perform add-parameter
    perform execute-query
    perform fetch-row
    if fetch-status = 2
        *> Preserve password derivation cost for unknown user names.
        call static "bank_password_hash" using request-password
            by value password-length by reference password-hash
            returning native-status end-call
        perform check-database
        exit paragraph
    end-if
    move 1 to parameter-index perform read-column
    move column-value(1:19) to user-id
    move 2 to parameter-index perform read-column
    move column-value(1:256) to password-hash
    move 3 to parameter-index perform read-column
    if function numval(column-value) = 1 exit paragraph end-if
    call static "bank_password_verify" using request-password
        by value password-length by reference password-hash
        returning native-status end-call
    if native-status not = zero
        perform reset-query
        string "UPDATE BANK_USERS SET FAILED_LOGINS = FAILED_LOGINS + 1, "
            "LOCKED_UNTIL = CASE WHEN FAILED_LOGINS >= 4 "
            "THEN CURRENT TIMESTAMP + 15 MINUTES ELSE NULL END "
            "WHERE ID = ?" into query-sql end-string
        move user-id to parameter-value perform add-parameter
        perform execute-query
        exit paragraph
    end-if
    perform reset-query
    move "UPDATE BANK_USERS SET FAILED_LOGINS = 0, LOCKED_UNTIL = NULL WHERE ID = ?"
        to query-sql
    move user-id to parameter-value perform add-parameter
    perform execute-query
    perform reset-query
    move "DELETE FROM BANK_SESSIONS WHERE EXPIRES_AT < CURRENT TIMESTAMP"
        to query-sql
    perform execute-query
    call static "bank_random" using session-token by value 100
        returning native-status end-call
    perform check-database
    call static "bank_sha256" using session-token by value 100
        by reference session-hash returning native-status end-call
    perform check-database
    perform reset-query
    move "INSERT INTO BANK_SESSIONS (TOKEN_HASH, USER_ID, EXPIRES_AT) VALUES (?, ?, CURRENT TIMESTAMP + 30 MINUTES)"
        to query-sql
    move session-hash to parameter-value perform add-parameter
    move user-id to parameter-value perform add-parameter
    perform execute-query
    call static "bank_text" using z"token" session-token by value 100
    end-call.

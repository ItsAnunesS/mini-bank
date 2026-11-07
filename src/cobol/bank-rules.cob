identification division.
program-id. bank-rules.
data division.
working-storage section.
01 amount-input                 pic x(50).
01 validated-amount             pic 9(7)v99.
01 action-input                 pic x(8).
01 balance-input                pic x(30).
01 current-balance              pic 9(13)v99.
01 updated-balance              pic 9(13)v99.
01 amount-output                pic 9(7).99.
01 balance-output               pic 9(13).99.
copy "operation-result.cpy".
procedure division.
process-request.
    accept action-input end-accept
    accept amount-input end-accept
    call "validate-amount" using amount-input validated-amount
        operation-result
    end-call
    if not operation-succeeded
        display "INVALID_AMOUNT" end-display
        move 1 to return-code
        stop run
    end-if
    if action-input = "VALIDATE"
        move validated-amount to amount-output
        display amount-output end-display
        move 0 to return-code
        stop run
    end-if
    accept balance-input end-accept
    if function test-numval(balance-input) not = zero
        display "INVALID_BALANCE" end-display
        move 1 to return-code
        stop run
    end-if
    if function numval(balance-input) < zero or
        function numval(balance-input) > 9999999999999.99
        display "INVALID_BALANCE" end-display
        move 1 to return-code
        stop run
    end-if
    compute current-balance = function numval(balance-input)
    end-compute
    evaluate action-input
        when "DEBIT"
            if current-balance < validated-amount
                display "INSUFFICIENT_FUNDS" end-display
                move 1 to return-code
                stop run
            end-if
            compute updated-balance = current-balance - validated-amount
            end-compute
        when "CREDIT"
            compute updated-balance = current-balance + validated-amount
                on size error
                    display "BALANCE_LIMIT" end-display
                    move 1 to return-code
                    stop run
            end-compute
        when other
            display "INVALID_ACTION" end-display
            move 1 to return-code
            stop run
    end-evaluate
    move updated-balance to balance-output
    display balance-output end-display
    move 0 to return-code
    stop run.
end program bank-rules.

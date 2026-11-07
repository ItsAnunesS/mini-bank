identification division.
program-id. test-amount.
data division.
working-storage section.
01 amount-input                 pic x(50).
01 validated-amount             pic 9(7)v99.
01 expected-amount              pic 9(7)v99.
01 failure-count                usage binary-long value 0.
copy "operation-result.cpy".
procedure division.
run-tests.
    move "12.50" to amount-input
    move 12.50 to expected-amount
    perform expect-valid
    move "9999999.99" to amount-input
    move 9999999.99 to expected-amount
    perform expect-valid
    move "0.01" to amount-input
    move 0.01 to expected-amount
    perform expect-valid
    move " 42 " to amount-input
    move 42 to expected-amount
    perform expect-valid
    move "1.2" to amount-input
    move 1.20 to expected-amount
    perform expect-valid
    move spaces to amount-input
    perform expect-invalid
    move "0" to amount-input
    perform expect-invalid
    move "-1" to amount-input
    perform expect-invalid
    move "10000000" to amount-input
    perform expect-invalid
    move "1.234" to amount-input
    perform expect-invalid
    move "1.230" to amount-input
    perform expect-invalid
    move "1,50" to amount-input
    perform expect-invalid
    move "1.2.3" to amount-input
    perform expect-invalid
    move "1 2" to amount-input
    perform expect-invalid
    move "abc" to amount-input
    perform expect-invalid
    move ".5" to amount-input
    perform expect-invalid
    move "1." to amount-input
    perform expect-invalid
    *> Reuse the same program after failures to detect leaked state.
    move "2.75" to amount-input
    move 2.75 to expected-amount
    perform expect-valid
    if failure-count > zero
        display "FAIL: " failure-count " amount validation cases" end-display
        move 1 to return-code
    else
        display "PASS: 18 amount validation cases" end-display
        move zero to return-code
    end-if
    stop run.
expect-valid.
    call "validate-amount" using amount-input validated-amount
        operation-result
    end-call
    if not operation-succeeded or validated-amount not = expected-amount
        display "FAIL valid input: " function trim(amount-input) end-display
        add 1 to failure-count end-add
    end-if.
expect-invalid.
    call "validate-amount" using amount-input validated-amount
        operation-result
    end-call
    if operation-succeeded
        display "FAIL invalid input: " function trim(amount-input) end-display
        add 1 to failure-count end-add
    end-if.
end program test-amount.

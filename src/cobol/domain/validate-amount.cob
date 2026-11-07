identification division.
program-id. validate-amount.
data division.
working-storage section.
01 character-index              usage binary-long.
01 input-length                 usage binary-long.
01 whole-digit-count            usage binary-long.
01 decimal-digit-count          usage binary-long.
01 decimal-point-seen           pic 9.
   88 has-decimal-point         value 1.
01 current-character            pic x.
01 normalized-input             pic x(50).
linkage section.
01 amount-input                 pic x(50).
01 validated-amount             pic 9(7)v99.
copy "operation-result.cpy".
procedure division using amount-input validated-amount operation-result.
validate-input.
    set operation-failed to true
    move zero to validated-amount whole-digit-count
        decimal-digit-count decimal-point-seen
    move function trim(amount-input) to normalized-input
    compute input-length = function length(
        function trim(normalized-input))
    end-compute
    if input-length = zero
        goback
    end-if

    perform varying character-index from 1 by 1
        until character-index > input-length
        move normalized-input(character-index:1) to current-character
        evaluate true
            when current-character >= "0" and <= "9"
                if has-decimal-point
                    add 1 to decimal-digit-count end-add
                else
                    add 1 to whole-digit-count end-add
                end-if
            when current-character = "." and not has-decimal-point
                move 1 to decimal-point-seen
            when other
                goback
        end-evaluate
    end-perform

    if whole-digit-count = zero or whole-digit-count > 7
        or decimal-digit-count > 2
        or (has-decimal-point and decimal-digit-count = zero)
        goback
    end-if
    compute validated-amount = function numval(normalized-input)
    end-compute
    if validated-amount = zero
        goback
    end-if
    set operation-succeeded to true
    goback.
end program validate-amount.

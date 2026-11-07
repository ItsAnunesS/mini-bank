identification division.
program-id. movement-repository.
data division.
working-storage section.
01 amount-text                  pic 9(7).99.
01 process-status               usage binary-long.
linkage section.
01 repository-action            pic x(4).
copy "movement.cpy".
copy "operation-result.cpy".
procedure division using repository-action movement-record operation-result.
execute-operation.
    set operation-failed to true
    evaluate repository-action
        when "ADD "
            perform insert-movement
        when "LIST"
            call "SYSTEM" using
                z"/opt/venv/bin/python -m ledger.cli list"
                returning process-status
            end-call
        when other
            goback
    end-evaluate
    if process-status = zero
        set operation-succeeded to true
    end-if
    goback.
insert-movement.
    *> User input is passed as data, never interpolated into shell commands.
    move movement-amount to amount-text
    set environment "MOVEMENT_KIND" to movement-kind
    set environment "MOVEMENT_AMOUNT" to amount-text
    set environment "MOVEMENT_DESCRIPTION" to
        function trim(movement-description)
    call "SYSTEM" using
        z"/opt/venv/bin/python -m ledger.cli add"
        returning process-status
    end-call.
end program movement-repository.

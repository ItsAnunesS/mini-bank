identification division.
program-id. bank-worker.
data division.
working-storage section.
01 input-status usage binary-long.
01 request-result usage binary-long.
procedure division.
    call static "bank_worker_next" returning input-status end-call
    perform until input-status = 2
        call static "bank-backend" returning request-result end-call
        call static "bank_worker_next" returning input-status end-call
    end-perform
    call static "bank_db_discard" end-call
    move zero to return-code
    stop run.
end program bank-worker.

identification division.
program-id. expense-ledger.
data division.
working-storage section.
01 menu-choice                  pic x(10).
01 validated-amount             pic 9(7)v99.
01 amount-input                 pic x(50).
01 description-input            pic x(256).
01 repository-action            pic x(4).
01 input-ended                  pic 9 value 0.
   88 end-of-input               value 1.
copy "movement.cpy".
copy "operation-result.cpy".
procedure division.
run-application.
    display "GESTOR DE DESPESAS - COBOL + DB2" end-display
    perform until menu-choice = "0" or end-of-input
        perform display-menu
        accept menu-choice
            on exception move 1 to input-ended
        end-accept
        if not end-of-input
            perform handle-menu-choice
        end-if
    end-perform
    move zero to return-code
    stop run.
display-menu.
    display " " end-display
    display "1 - Registar receita" end-display
    display "2 - Registar despesa" end-display
    display "3 - Consultar movimentos e saldo" end-display
    display "0 - Sair" end-display.
handle-menu-choice.
    evaluate function trim(menu-choice)
        when "1"
            set movement-is-income to true
            perform register-movement
        when "2"
            set movement-is-expense to true
            perform register-movement
        when "3"
            perform list-movements
        when "0" continue
        when other display "Opcao invalida." end-display
    end-evaluate.
register-movement.
    display "Valor (ex.: 12.50; maximo 9999999.99):" end-display
    accept amount-input
        on exception move 1 to input-ended
    end-accept
    if end-of-input
        exit paragraph
    end-if
    call "validate-amount" using amount-input validated-amount
        operation-result
    end-call
    if not operation-succeeded
        display "Valor invalido. Use um valor positivo com ate 2 decimais."
        end-display
        exit paragraph
    end-if
    move validated-amount to movement-amount
    perform read-description
    if end-of-input or not operation-succeeded
        exit paragraph
    end-if
    move "ADD" to repository-action
    call "movement-repository" using repository-action
        movement-record operation-result
    end-call
    if operation-succeeded
        display "Movimento guardado no Db2." end-display
    else
        display "Nao foi possivel guardar o movimento." end-display
    end-if.
read-description.
    set operation-failed to true
    display "Descricao (ate 40 bytes em UTF-8):" end-display
    accept description-input
        on exception move 1 to input-ended
    end-accept
    if end-of-input
        exit paragraph
    end-if
    move function trim(description-input) to description-input
    if description-input = spaces
        display "A descricao e obrigatoria." end-display
        exit paragraph
    end-if
    if function length(function trim(description-input)) > 40
        display "Descricao demasiado longa. Maximo: 40 bytes." end-display
        exit paragraph
    end-if
    move description-input(1:40) to movement-description
    set operation-succeeded to true.
list-movements.
    move "LIST" to repository-action
    call "movement-repository" using repository-action
        movement-record operation-result
    end-call
    if not operation-succeeded
        display "Nao foi possivel consultar o Db2." end-display
    end-if.
end program expense-ledger.

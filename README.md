# MiniBank — mini banco com COBOL e Db2

Sistema bancário **de demonstração**, com interface web local. Administradores criam acessos e aprovam pedidos de contas; clientes pedem e consultam as próprias contas e transferem valores simulados. O gestor de despesas original continua disponível, sem alteração dos seus dados.

## Abrir a aplicação web

Tudo corre em Docker. Não é necessário instalar Python, COBOL ou Db2 no computador.

```sh
docker compose up -d --build bank
```

Abre [http://localhost:8088](http://localhost:8088). A porta está publicada apenas em `127.0.0.1`; a base de dados permanece na rede interna Docker.

### Criar o primeiro administrador

```sh
docker compose --profile tools run --build --rm bank-admin
```

O comando pede nome, email e palavra-passe com entrada oculta. A palavra-passe deve ter entre 12 e 128 caracteres. Não existem credenciais predefinidas. O comando recusa criar outro administrador se já existir um; o teste de existência e a criação são protegidos pela mesma transação.

Depois de entrar:

1. Como administrador, em **Criar acesso**, regista o cliente. Isto cria apenas o acesso, sem conta bancária automática.
2. Entra como cliente e escolhe **Pedir nova conta** no painel: **À ordem** ou **Poupança**. O pedido fica **Pendente**, com saldo zero.
3. Como administrador, abre **Aprovar contas**, consulta o pedido e aprova-o. Para rejeitar, abre **Analisar / rejeitar** e indica o motivo.
4. Depois da aprovação, o administrador pode registar um **depósito simulado** na conta.
5. O cliente escolhe uma das suas contas no painel, consulta o extrato ou transfere para outra conta ativa pelo número `MB-...`.

Cada cliente pode ter até **3 contas à ordem e 2 poupanças**. Contas pendentes, ativas e bloqueadas contam para estes limites. A validação é feita em COBOL, incluindo pedidos simultâneos e pedidos administrativos. Reenviar o mesmo pedido não duplica a conta. Contas pendentes não permitem movimentos nem recebem juros; apenas o administrador pode aprová-las.

O administrador pode **rejeitar um pedido pendente**, indicando um motivo com pelo menos 5 caracteres e até 240 bytes. O titular vê o motivo no painel e no detalhe. No detalhe de um pedido pendente, o titular pode escolher **Cancelar pedido**. Pedidos rejeitados ou cancelados mantêm o histórico, deixam de ocupar vagas e não podem ser ativados; o cliente pode enviar um novo pedido. A primeira decisão confirmada prevalece se houver ações simultâneas.

O painel do cliente inclui **Notificações** de novas aprovações e rejeições de contas e empréstimos, com ligação ao respetivo detalhe e motivo da rejeição. **Marcar como lida** guarda a data de leitura sem apagar o aviso. O sino mostra o total de não lidas, incluindo outras páginas. A lista tem páginas de 50 notificações. O contador e a lista atualizam automaticamente a cada 15 segundos com a aba visível e ao regressar à aba, com novas tentativas mais espaçadas em caso de falha. **Marcar todas como lidas** abrange todas as notificações não lidas do titular no momento da operação, sem apagar o histórico nem alterar datas já registadas. Novos avisos após essa operação continuam por ler. Os formulários também funcionam sem JavaScript. A migração não gera avisos retroativos para decisões anteriores.

A palavra-passe inicial do cliente deve ser entregue por um canal privado. Esta versão ainda não inclui recuperação ou alteração de palavra-passe pela interface, criação de administradores adicionais, levantamentos, cartões ou integração com bancos reais.

### Extratos, limites e comissões

No detalhe de cada conta, **Serviços da conta → Extratos mensais** permite escolher um mês e consultar saldo inicial, entradas, saídas e saldo final. O CSV inclui todos os movimentos do mês, em ordem de registo. A impressão do navegador permite guardar PDF. O mês atual é provisório; consultas futuras são recusadas. Nesta versão, um extrato com mais de 1.000 movimentos é recusado integralmente, sem exportação parcial.

As transferências têm limites por conta de **1.000 € por operação e 5.000 € por dia**, alteráveis pelo administrador em **Limites e isenção**. O limite diário deve ser igual ou superior ao individual. O dia segue a data do Db2; transferências estornadas continuam a consumir o limite, enquanto depósitos, juros, comissões e prestações não o consomem. Operações concorrentes validam o mesmo saldo e limite sob bloqueio.

A comissão mensal simulada é **2 € por conta à ordem**. O administrador cobra explicitamente o mês atual no detalhe da conta: não há cobrança automática nem retroativa. A mesma conta/mês só pode ser cobrada uma vez. Poupanças e contas com isenção individual não pagam comissão. Contas não ativas e contas sem saldo suficiente não são cobradas; não há acumulação de dívida. Alterar a isenção não devolve comissões anteriores.

### Empréstimos simulados

O cliente abre uma conta ativa e escolhe **Pedir empréstimo**: capital de **100 a 100.000 €**, prazo de **3 a 60 meses** e taxa fixa de **6% ao ano**. COBOL gera o plano com capital constante, juros mensais de 0,5% sobre o capital em dívida e arredondamento dos juros ao cêntimo. A última prestação absorve os cêntimos remanescentes do capital.

Em **Empréstimos**, o administrador consulta o plano e aprova ou rejeita com motivo. A aprovação credita o capital uma única vez e fixa as datas: cada vencimento é calculado a partir da data de aprovação, com ajuste de fim do mês. As datas anteriores à aprovação são indicativas. O titular vê a decisão e o motivo no detalhe do pedido.

O pagamento é manual, por ordem das prestações, com débito na conta associada. Pode ser antecipado, mas **não recalcula os juros nem o calendário** desta simulação. Não existem multas de atraso, seguros, amortizações extraordinárias ou cobranças automáticas. Saldo insuficiente e conta bloqueada impedem o pagamento sem alterar o plano. Repetir o mesmo pedido de pagamento não debita outra prestação.

### Encerrar uma conta

O titular pode encerrar uma conta ativa com **saldo zero e sem empréstimos pendentes ou ativos**, através de confirmação explícita em Serviços da conta. A conta fica definitivamente encerrada, liberta a vaga no limite de contas e conserva todos os movimentos, pedidos e extratos. Não pode ser reativada nem receber dinheiro. Contas bloqueadas precisam primeiro de intervenção administrativa.

A migração `007_banking_products.sql` adiciona os produtos e limites sem apagar utilizadores, contas ou movimentos. As contas existentes recebem os limites predefinidos; nenhuma comissão ou operação de crédito é lançada pela migração. Comissões, créditos de empréstimos e pagamentos integram o extrato e a reconciliação, mas não admitem estorno genérico nesta versão.

A migração `008_loan_notifications.sql` preserva os avisos existentes e acrescenta a ligação a empréstimos. Aprovação/rejeição, crédito do empréstimo e notificação partilham a mesma transação. Repetições não duplicam avisos e não são criadas notificações retroativas.

### Desempenho da navegação

O contador inicial do sino acompanha a validação da sessão, evitando uma chamada adicional ao backend. A atualização automática começa após 15 segundos: o HTML inicial já contém dados atuais. Fora do painel, o sino pede apenas o contador, sem renderizar/descarregar a lista completa. CSS, JavaScript e logótipo usam URLs com versão do conteúdo e cache do navegador; uma alteração gera outra URL. Páginas, extratos, sessões e notificações continuam com `Cache-Control: no-store`.

O cabeçalho `Server-Timing` indica o tempo de processamento da aplicação, em milissegundos, para distinguir processamento do servidor de espera na rede/navegador. Não inclui conteúdo de pedidos nem identificadores pessoais.

### Configuração

Neste ambiente, `.env` já contém `DB2_PASSWORD` e `BANK_SECRET_KEY` gerados aleatoriamente. Não publiques esse ficheiro. Numa cópia nova, usa `.env.example` apenas como modelo e substitui ambos os valores por segredos próprios. Uma chave de sessão deve ter pelo menos 32 bytes aleatórios. Alterar a password do Db2 no `.env` não altera automaticamente a password de uma instância já existente.

A aplicação usa HTTP apenas no endereço local. Para qualquer publicação externa seriam necessários HTTPS, `BANK_HTTPS=1` (cookies Secure), configuração explícita de hosts permitidos, gestão de segredos e uma conta Db2 com permissões limitadas. O projeto não faz essa publicação.

O painel destaca as contas e os seus estados, com acesso ao pedido de novas contas. A administração separa **Criar acesso** de **Aprovar contas**; cada conta tem atalhos para operação e extrato. Os resumos referem-se apenas à página apresentada.

## Organização do mini banco

O percurso de cada pedido é **navegador → Python/Flask → executável COBOL → driver nativo IBM → Db2**. O Python comunica com um processo COBOL persistente por thread, sem shell, usando uma linha JSON por pedido/resposta. O processo reutiliza a ligação ao Db2; cada pedido continua a ter autorização e commit/rollback próprios, sem cache de saldos ou permissões. O primeiro pedido de cada thread estabelece a ligação e pode demorar mais; os seguintes reutilizam-na.

- `src/cobol/bank-backend.cob`: entrada do backend e despacho dos comandos, com estado reinicializado por pedido.
- `src/cobol/bank-worker.cob`: ciclo de pedidos do processo persistente.
- `src/python/bank/transport.py`: transporte por thread, prazo de resposta e recuperação do processo, sem repetir operações automaticamente.
- `src/cobol/bank/`: autenticação, sessões, autorização, clientes, contas, transferências, SQL parametrizado e controlo de commit/rollback, separados por responsabilidade.
- `src/cobol/domain/validate-amount.cob`: validação decimal partilhada com o terminal.
- `src/native/bank_bridge.c`: ponte técnica para IBM CLI, JSON e OpenSSL; não contém SQL nem decisões bancárias.
- `src/python/bank/web.py`: rotas, formulários, cookies, CSRF e apresentação HTML.
- `src/python/bank/client.py`: transporte dos pedidos/respostas e tradução dos erros do COBOL.
- `src/python/bank/templates/` e `static/`: interface responsiva, com JavaScript local para notificações e sem recursos externos.
- `db/migrations/002_create_bank.sql`: tabelas bancárias, separadas de `MOVIMENTOS`.
- `tests/bank/`: testes com Db2 e COBOL reais; o acesso Python à base neste diretório serve apenas para preparar e verificar os testes.

O backend bancário não usa Python para consultar ou alterar o Db2. O COBOL chama a ponte C, que executa as instruções através da biblioteca nativa IBM. O driver é reutilizado do pacote já presente na imagem; o gestor de despesas original mantém o seu adaptador Python.

Flask, Gunicorn, Flask-WTF, compilador e bibliotecas nativas são instalados exclusivamente na imagem Docker. As migrações preservam os dados, os hashes de palavras-passe e as sessões existentes.

## Integridade e segurança

- O COBOL valida o token de sessão em cada operação e confirma o perfil e, quando aplicável, a titularidade da conta no servidor.
- As transferências bloqueiam as contas por ordem de ID e confirmam débito, crédito, extrato e auditoria numa única transação. Falhas provocam rollback.
- A chave única por utilizador/pedido impede que reenviar o mesmo formulário movimente dinheiro duas vezes. Reutilizar a chave com dados diferentes é rejeitado.
- Contas bloqueadas ou pendentes não recebem nem enviam movimentos; o extrato continua acessível ao titular.
- As palavras-passe são guardadas apenas como hashes scrypt. Após cinco erros, o login fica bloqueado por 15 minutos. As sessões expiram ao fim de 30 minutos e são revogadas no logout.
- Cookies HttpOnly e SameSite, sessão web `conta_clara_session` isolada de outras aplicações locais, proteção CSRF, escape HTML automático e SQL parametrizado. Formulários de login expirados ou sem sessão válida são renovados na própria página; volta a introduzir a palavra-passe. Pedidos inválidos nunca são autenticados ou repetidos automaticamente.
- Valores em EUR: até `9999999.99` por movimento, saldo `DECIMAL(15,2)`, nunca ponto flutuante. O primeiro administrador pode criar depósitos fictícios; não representam entrada de dinheiro real.
- Listagens limitadas a 50 registos por página, com paginação por ID.
- O contentor web corre sem root, sem capacidades Linux adicionais e com `no-new-privileges`.

## Poupança, fecho diário e reconciliação

Como cliente, em **Pedir nova conta**, escolhe **Poupança** e aguarda aprovação. Depósitos e transferências funcionam em ambos os tipos após aprovação; os juros são exclusivos da poupança.

A taxa anual nominal é **simulada e fixa em 3,65%**, base de 365 dias: `saldo × 3,65 / 100 / 365`. O COBOL arredonda ao cêntimo por conta e por execução (metade arredonda para cima). Por exemplo, 50,00 € produzem 0,01 €; 1 000,00 € produzem 0,10 €. Só contas ativas recebem juros. Não há remuneração retroativa, rateio por horas nem acumulação de frações de cêntimo.

O administrador abre **Fecho e reconciliação**:
- **Reconciliar agora** verifica todas as contas e guarda um novo relatório, sem alterar saldos.
- **Executar fecho de hoje** reconcilia e, se não houver divergências, credita juros. O dia é a data corrente do Db2. Apenas um fecho concluído por dia; pedidos repetidos ou concorrentes devolvem o mesmo relatório.
- Com divergências, guarda o relatório e não credita juros em nenhuma conta. É possível repetir o fecho depois de resolver a origem das divergências; os relatórios anteriores permanecem disponíveis.
- Cada relatório contém saldos antes/depois, soma dos lançamentos, diferenças, tipo/estado das contas e juros efetivamente creditados. A consulta tem páginas de 50 contas.

**Semântica desta demonstração:** o fecho é uma fotografia consistente no momento da execução. Não bloqueia o resto do dia, não fecha um calendário contabilístico e não impede movimentos posteriores. Um novo fecho na mesma data não recalcula os juros; usa reconciliação para verificar operações posteriores. O processamento é manual, com limite de 500 contas por execução. Durante a fotografia, escritas concorrentes aguardam os bloqueios do Db2.

A reconciliação verifica a soma de débitos/créditos contra o saldo, todos os saldos intermédios do extrato e a composição das operações (contas, direções, montantes e número de lançamentos). Não corrige dados automaticamente.

## Estornar uma operação

No extrato, o administrador abre **Operação #… · Ver detalhes**, indica o motivo e confirma o estorno integral. Pode estornar depósitos simulados, transferências e juros. Não é possível estornar um estorno.

O COBOL cria uma operação `REVERSAL` e lançamentos compensatórios, associados ao original. Nunca apaga ou reescreve a operação original. O estorno exige contas ativas e saldo suficiente na conta a debitar; respeita também o limite do saldo de destino. Apenas um estorno por operação, mesmo com pedidos concorrentes. Repetir o mesmo original/motivo devolve o estorno existente; um motivo diferente é rejeitado.

Estornar juros não reabre o fecho nem altera o relatório já guardado. O movimento compensatório aparece no extrato e numa reconciliação posterior.

## Atualizar uma instalação existente

`docker compose up -d --build bank` executa o inicializador COBOL antes de iniciar a web. A migração `003_daily_operations.sql` acrescenta poupanças, ligações de estorno, relatórios e o registo de fechos; expande a restrição de tipos de operação numa única transação. Não converte contas existentes nem altera os seus saldos.

A migração `004_account_approval.sql` acrescenta o estado pendente e os registos de pedido/aprovação. Os acessos, contas, estados e movimentos existentes são preservados; não precisam de nova aprovação. Contas antigas acima dos limites não são removidas, mas impedem novos pedidos desse tipo. A migração `005_account_resolution.sql` acrescenta rejeição e cancelamento, com motivo, autor e data, sem alterar pedidos ou contas existentes.

A migração `006_notifications.sql` cria as notificações persistentes, sem alterar contas, decisões ou movimentos existentes.

No protocolo, `CREATE_CUSTOMER` passa a devolver o ID do utilizador e já não cria uma conta; a abertura passa por `CREATE_ACCOUNT` e `APPROVE_ACCOUNT`.

## Testar o mini banco

```sh
docker compose --profile test run --build --rm bank-tests
```

Os testes criam utilizadores e contas exclusivos de cada execução e removem apenas esses dados. Cobrem validação COBOL, autorização, concorrência, saldo insuficiente, bloqueios, repetição de pedidos, rollback provocado por uma falha real de escrita no Db2, login, CSRF, sessões e escape HTML. Os testes diários usam um esquema aleatório isolado, incluindo atualização de uma base anterior, concorrência, arredondamento, estornos, divergências e rollback; removem esse esquema ao terminar. Chamadas diretas ao backend também verificam que um ID de utilizador enviado pelo cliente não substitui a identidade da sessão.

Os testes de abertura também usam um esquema isolado e cobrem acesso sem contas, limites por tipo, pedidos e aprovações concorrentes, repetição segura, rollback da aprovação, restrições das contas pendentes e o percurso HTTP completo de pedido/aprovação.

Para parar a interface sem parar o Db2:

```sh
docker compose stop bank
```

---

## Gestor de despesas original (terminal)

Aplicação de terminal para registar receitas e despesas e consultar movimentos e saldo. O menu e a validação estão em GnuCOBOL; um adaptador separado usa o driver IBM para guardar os dados no Db2.

## Requisitos e arranque

O computador precisa apenas de Docker e Docker Compose. Compilador, Python, driver e base de dados são instalados exclusivamente nos contentores. A imagem Db2 é `amd64` e o serviço tem um limite de 6 GB de memória; reserva memória adicional para Docker e compilação.

Este ambiente já tem uma password gerada no ficheiro `.env`, excluído do Git. Numa cópia nova, copia `.env.example` para `.env`, substitui a password de exemplo e restringe o acesso ao ficheiro. Não publiques `.env`. A configuração rejeita `;`, chavetas e quebras de linha nas credenciais para impedir a introdução de opções adicionais na ligação.

```sh
docker compose up -d db2
docker compose run --build --rm despesas
```

O primeiro arranque descarrega a imagem IBM e inicializa a base. O Compose espera por uma ligação bem-sucedida e executa o inicializador da tabela antes de abrir o menu.

Usa valores como `12.50`: positivos, com ponto decimal, até duas casas decimais e no máximo `9999999.99`. A descrição aceita até 40 bytes UTF-8; caracteres acentuados podem ocupar mais de um byte. A opção `0` termina a aplicação. O fim da entrada também termina sem guardar movimentos incompletos.

## Estrutura

```text
src/
  cobol/
    main.cob                    Menu e coordenação dos casos de uso
    domain/validate-amount.cob  Validação pura de valores
    adapters/db2-repository.cob Adaptador de persistência
    copybooks/                 Contratos de registos e resultados
  python/ledger/
    cli.py                     Interface do adaptador e apresentação
    repository.py              Ligações e SQL parametrizado
db/migrations/                 SQL versionado do esquema
tests/cobol/                   Testes independentes da base
tests/integration/             Testes reais COBOL → Db2
docs/architecture.md           Contratos e convenções
```

## Testes

```sh
# Compilar com avisos tratados como erros e testar 18 casos de validação.
docker build --target unit-tests -t cobol-unit-tests .

# Testar a aplicação contra o Db2; requer a base saudável.
docker compose --profile test run --build --rm integration-tests
```

A compilação normal também executa os testes COBOL. Os testes de integração verificam receitas, despesas, precisão decimal, descrições com apóstrofos, rejeição de entradas inválidas, fim da entrada e leitura numa nova ligação/processo. Apagam apenas os registos identificados como pertencentes à execução do teste.

## Dados e operação

A base `DESPESAS` guarda os movimentos em `DB2INST1.MOVIMENTOS`, com valores `DECIMAL(9,2)`, identificador automático e data/hora. O volume `db2-data` mantém os dados quando se recria o contentor.

```sh
# Estado e diagnóstico
docker compose ps
docker compose logs --tail=80 db2

# Parar sem apagar os dados
docker compose stop

# Consultar os registos diretamente
docker compose exec db2 su - db2inst1 -c 'db2 connect to DESPESAS && db2 "select ID, TIPO, VALOR, DESCRICAO from MOVIMENTOS order by ID"'
```

`docker compose down -v` apaga os volumes. O antigo volume `movimentos` foi preservado; os dados da versão em ficheiro não são migrados automaticamente.

## Segurança e limitações

- A aplicação corre sem root e a porta do Db2 não é publicada no computador.
- O SQL usa parâmetros; os comandos de sistema não incluem texto introduzido pelo utilizador.
- As credenciais ficam fora do controlo de versões e são ocultadas nos erros do adaptador.
- O Db2 usa apenas as capacidades adicionais `IPC_OWNER` e `SYS_RESOURCE`, sem `privileged`. A compatibilidade de arranque deve ser verificada no ambiente Docker utilizado.
- Este é um ambiente local de aprendizagem: a aplicação usa a conta da instância. Produção requer um utilizador dedicado com permissões limitadas, gestão de segredos, TLS e cópias de segurança.
- No gestor de despesas legado, cada operação abre um processo/ligação de curta duração. O mini banco web reutiliza processos COBOL e ligações Db2 por thread. A listagem lê sequencialmente e mantém apenas um saldo decimal em memória. Maior volume justificaria reutilizar ligações e paginar resultados.

Consulta a [arquitetura](docs/architecture.md) e o [changelog](CHANGELOG.md).

## Referências

- [Instalação IBM Db2 em Docker](https://ibm.github.io/db2developerextension-about/docs/tips-and-tricks/manual-db2-installation.html)
- [Driver Python da IBM](https://github.com/ibmdb/python-ibmdb)
- [Capacidades utilizadas pelo Db2](https://www.ibm.com/docs/en/software-hub/5.4.x?topic=security-db2-scc-capabilities)

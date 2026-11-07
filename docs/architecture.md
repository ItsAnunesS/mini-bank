# Arquitetura e convenções

## Responsabilidades

`expense-ledger` gere o terminal e coordena o registo e a consulta. Não contém SQL nem credenciais.

`validate-amount` é um subprograma COBOL puro, sem entrada/saída. Valida texto decimal, rejeita formatos ambíguos e casas decimais em excesso e reinicializa o estado em cada chamada.

`movement-repository` é a fronteira de persistência. A implementação Db2 traduz um registo partilhado para um comando fixo do adaptador. Outra implementação pode ser ligada ao mesmo contrato sem alterar o menu ou a validação.

A interface Python trata da configuração e apresentação. `MovementRepository` trata do acesso à base e valida a sua fronteira de entrada. As restrições SQL protegem os valores e o tipo de movimento independentemente do menu.

## Contratos COBOL

| Programa | Parâmetros por referência | Resultado |
| --- | --- | --- |
| `validate-amount` | Entrada `PIC X(50)`, saída `PIC 9(7)V99`, `operation-result` | `0`: válido; `1`: inválido. Ignorar o valor de saída quando falha. |
| `movement-repository` | Ação `PIC X(4)`, `movement-record`, `operation-result` | `ADD ` guarda; `LIST` apresenta movimentos e saldo. `0`: sucesso; `1`: falha. |

Os copybooks `movement.cpy` e `operation-result.cpy` definem os formatos partilhados. Quem chama deve respeitar os tamanhos e verificar o resultado antes de anunciar sucesso. O contrato não expõe o driver nem a configuração da ligação.

## KISS, DRY e SOLID

- **KISS:** três programas COBOL e um pequeno adaptador; sem framework, ORM genérico ou hierarquia de classes desnecessária.
- **DRY:** copybooks partilhados, um único fluxo para receita/despesa e centralização da ligação e execução parametrizada.
- **Responsabilidade única:** interação, validação pura, transporte, SQL e esquema separados.
- **Aberto/fechado e inversão de dependências:** nomes de programas e contratos estáveis permitem substituir a implementação da persistência sem alterar a validação.
- **Segregação de interfaces:** a validação não conhece métodos da base; a persistência expõe só as operações utilizadas pela aplicação.
- **Substituição:** outra implementação deve preservar formatos, resultados, precisão decimal e semântica. Não se introduz herança apenas para declarar cumprimento de SOLID.

A validação no adaptador e as restrições SQL são intencionais: protegem fronteiras de confiança distintas.

## Convenções

- GnuCOBOL em formato livre, indentação de quatro espaços nas instruções e terminadores de âmbito explícitos.
- Identificadores em inglês: nomes com hífen no COBOL e snake_case no Python.
- Comentários em inglês para explicar intenção, restrições e decisões não óbvias.
- Condições nomeadas com nível 88, saídas antecipadas e parágrafos focados.
- Valores monetários decimais de precisão fixa, nunca ponto flutuante.
- Ponto final no fim de parágrafos; evitar pontos dentro de estruturas de controlo.
- `GOBACK` nos subprogramas; `STOP RUN` apenas no programa executável.
- Mensagens ao utilizador e documentação em português.
- Compilação com `-Wall -Wextra -Werror` e execução dos testes afetados dentro do Docker.

## Persistência

O inicializador cria a tabela apenas se não existir. O SQL versionado é a base inicial, não um sistema genérico de migrações. Alterações futuras devem acrescentar migrações explícitas e revistas.

Cada inserção é uma instrução com autocommit. O menu só anuncia sucesso depois da conclusão do adaptador. Uma falha é comunicada; não há armazenamento alternativo em ficheiro que possa divergir da base.

A listagem percorre os registos por ID e calcula o saldo com precisão decimal, sem carregar todos os movimentos em memória. Exportações que precisem de uma fotografia consistente durante alterações concorrentes exigem uma política explícita de isolamento.

## Segurança

Os comandos fixos nunca interpolam entradas do utilizador. Os campos passam por variáveis de ambiente dentro do contentor e depois por parâmetros SQL. Não há montagem do socket Docker nem do código do computador no contentor da aplicação.

O `.env` e a conta da instância são escolhas para desenvolvimento local. Não substituem gestão de segredos e permissões limitadas em produção. Não ativar `privileged` automaticamente para contornar falhas: investigar o diagnóstico e obter autorização para acesso mais amplo.


## Mini banco web

O pacote `bank` é independente do gestor de despesas e preserva os contratos do terminal. Python serve a interface; o executável COBOL é responsável pelos casos de uso e pela persistência bancária.

### Percurso de uma operação

1. Flask-WTF valida o CSRF e a rota envia um comando fixo, os campos do formulário e o token opaco da sessão.
2. `bank.client` usa `bank.transport` para trocar uma linha JSON com `bank-worker`, sem shell. Cada thread mantém o seu processo e a sua ligação Db2.
3. O COBOL valida os campos, resolve a sessão no Db2 e confirma o perfil e a titularidade. Nunca aceita um ID de autor indicado pelo cliente.
4. O COBOL bloqueia as contas por ID crescente, valida o valor e calcula os saldos com precisão decimal.
5. O COBOL executa SQL parametrizado para saldos, operação, lançamentos e auditoria; confirma a transação apenas se todas as etapas terminarem.
6. O Python converte a resposta para apresentação. Os valores decimais viajam como texto e são apresentados com `Decimal`.

Uma falha provoca rollback e elimina qualquer resposta parcial. O token de login só é entregue depois do commit. Repetir uma transferência com a mesma chave e os mesmos dados devolve a operação anterior.

### Fronteiras e protocolo

`bank-backend.cob` reúne parágrafos de `bank/authentication.cpy`, `customers.cpy`, `accounts.cpy`, `transfers.cpy` e `database.cpy`; `data.cpy` declara os campos. O mesmo processo pode tratar vários pedidos sequenciais numa ligação Db2. `bank-backend` usa `IS INITIAL` e `GOBACK`: a memória COBOL de cada pedido é reinicializada antes da próxima chamada, sem herdar utilizador, permissões, parâmetros ou resultados. O executável de pedido único continua disponível para chamadas diretas e testes do protocolo. Esta divisão mantém as responsabilidades explícitas sem introduzir uma hierarquia de classes num programa procedural.

Os comandos públicos de acesso e contas são `LOGIN`, `SESSION`, `LOGOUT`, `CREATE_CUSTOMER`, `CREATE_ACCOUNT`, `PENDING_ACCOUNTS`, `APPROVE_ACCOUNT`, `REJECT_ACCOUNT`, `CANCEL_ACCOUNT`, `SET_STATUS`, `ACCOUNTS`, `STATEMENT`, `AUDIT` e `MOVE`. Os comandos de relatórios e estornos são descritos abaixo. `INIT` e `CREATE_ADMIN` só são expostos pelos comandos locais de administração, nunca por rotas HTTP.

Os campos JSON são strings com limites validados no COBOL. A resposta contém `ok`, `rows` e, conforme o comando, `id`, `account` ou `token`; erros contêm apenas `ok: false` e `error`. O limite de entrada é 16 KiB, cada troca com o subprocesso tem timeout de 25 segundos e as chamadas SQL têm timeout de 10 segundos. O encerramento do processo/ligação reverte uma transação ainda não confirmada. Um timeout após commit pode deixar o resultado incerto para o cliente; movimentos devem ser repetidos com a mesma chave.

A ponte `src/native/bank_bridge.c` fornece operações técnicas: JSON com json-c; ligação, parâmetros, cursor e transação com IBM CLI; aleatoriedade, SHA-256 e scrypt com OpenSSL. Não contém SQL, perfis ou limites monetários. O COBOL decide quando chamar cada operação. Os hashes scrypt existentes permanecem compatíveis. O banco não importa `ibm_db` em Python; apenas o terminal legado e as fixtures dos testes usam esse módulo.

A autorização nas rotas melhora a navegação, mas o backend COBOL repete a verificação para proteger chamadas diretas. A integração não aceita SQL, caminhos de executáveis ou comandos shell fornecidos pelos formulários.

### Reutilização de ligações

`bank-worker.cob` chama o backend sequencialmente; a ponte C mantém apenas a ligação entre pedidos, liberta os objetos JSON e fecha cada cursor. Commit/rollback ocorre antes de devolver cada resposta. Não se partilham transações ou estado COBOL entre pedidos; a autenticação/autorização continua a consultar o Db2 em cada comando. A semântica de reinicialização segue o [guia oficial do GnuCOBOL](https://gnucobol.sourceforge.io/HTML/gnucobpg.html).

`bank.transport` mantém um processo privado por thread e reinicia-o quando muda a configuração Db2, quando termina ou depois de falha de comunicação. Os dois processos Gunicorn com duas threads mantêm até quatro ligações, abertas à medida que forem necessárias. Um processo herdado por fork nunca é usado nem terminado pelo filho. Erros Db2 descartam a ligação para o pedido seguinte; erros de domínio mantêm-na depois do rollback. Timeouts/saídas inesperadas não repetem pedidos, pois o commit pode já ter ocorrido. O pedido seguinte pode criar um novo processo. Não existe cache de saldos, sessões ou permissões.

### KISS, DRY e SOLID no banco

- Uma camada HTTP, um transporte, um backend COBOL e uma ponte técnica; sem ORM ou serviço adicional.
- Validação monetária, parâmetros SQL, leitura de resultados e auditoria partilhados.
- Autenticação, gestão de contas e movimentos em parágrafos focados; contratos JSON isolam a interface do driver.
- A substituição de uma implementação deve preservar os contratos, a autorização, a precisão e a atomicidade. SOLID orienta a separação de responsabilidades; não exige classes em COBOL.

### Modelo de dados

`BANK_USERS` guarda identidade, perfil e hash; `BANK_SESSIONS` guarda apenas hashes dos tokens e prazos. `BANK_ACCOUNTS` identifica titular, estado e saldo. `BANK_TRANSACTIONS` é o registo da operação e da chave de idempotência. `BANK_ENTRIES` regista cada débito/crédito e saldo posterior. `BANK_AUDIT` identifica o autor de ações administrativas e monetárias.

Não existem edições diretas de saldo na interface. O depósito simulado é uma operação administrativa auditada. Uma transferência cria dois lançamentos ligados à mesma operação. Chaves estrangeiras e restrições monetárias protegem a integridade; o teste de concorrência confirma que duas transferências não gastam o mesmo saldo.

A migração bancária cria as tabelas numa transação e é executada pelo serviço `bank-init`, antes do servidor HTTP. Não migra despesas antigas para contas bancárias. O driver devolve decimais como texto; o adaptador normaliza-os para `Decimal` antes de os expor à aplicação.

### Acesso e aprovação de contas

`CREATE_CUSTOMER` cria apenas `BANK_USERS` e devolve o ID do utilizador. Não existe registo público de acessos: o administrador continua a criá-los. O cliente autenticado pede uma conta com `CREATE_ACCOUNT`, `account_type` e `request_key`; o titular vem da sessão, ignorando identidades enviadas pelo cliente. O comando administrativo pode indicar o email do titular e segue os mesmos limites e estado inicial.

O COBOL bloqueia a linha do titular antes de contar contas e inserir o pedido. O limite é 3 à ordem e 2 poupanças, contando contas pendentes, ativas e bloqueadas. Pedidos rejeitados e cancelados não ocupam vagas. Uma chave única em `BANK_ACCOUNT_APPROVALS` identifica o pedido por autor; repetir a chave com o mesmo titular/tipo devolve a conta existente. Criação, pedido e auditoria são atómicos.

`PENDING_ACCOUNTS` e `APPROVE_ACCOUNT` exigem administrador. Aprovar bloqueia a conta e grava estado ativo, aprovador, data e auditoria na mesma transação. Repetir uma aprovação não duplica o evento nem reativa uma conta bloqueada posteriormente. `SET_STATUS` não pode ativar contas pendentes. As regras monetárias e de juros só aceitam contas ativas.

A migração 004 substitui a restrição de estados e cria `BANK_ACCOUNT_APPROVALS` numa transação. Não altera contas anteriores: conservam estado, tipo, saldo e histórico, mesmo acima dos novos limites. Apenas novas aberturas são sujeitas ao fluxo de aprovação.

`REJECT_ACCOUNT` exige administrador e motivo válido; `CANCEL_ACCOUNT` exige o cliente titular. Ambos bloqueiam a linha da conta, aceitam apenas pedidos pendentes e gravam estado, auditoria e `BANK_ACCOUNT_RESOLUTIONS` numa transação. A decisão conserva autor/data e, para rejeição, motivo. O bloqueio é partilhado com o fluxo de aprovação: só uma decisão concorrente vence. Repetir a mesma decisão não duplica o evento; mudar o motivo de uma rejeição já confirmada é rejeitado. Pedidos resolvidos não podem ser reativados nem movimentados, e a chave original de abertura continua a devolver o pedido existente.

A migração 005 amplia os estados e cria a tabela de decisões numa transação. As consultas autorizadas de contas devolvem `rejection_reason`; o HTML escapa o texto do motivo no painel e no detalhe. A validação UTF-8 do motivo é partilhada com os estornos.

### Notificações

`bank/notifications.cpy` cria uma notificação para o titular na mesma transação de cada nova aprovação ou rejeição. `BANK_NOTIFICATIONS` conserva conta, tipo, motivo, criação e leitura. A chave única por conta/tipo e as decisões idempotentes impedem duplicação. Cancelamentos não geram notificações administrativas. A migração 006 não cria avisos retroativos.

`NOTIFICATIONS` e `READ_NOTIFICATION` exigem perfil de cliente e filtram por titular autenticado no COBOL. A leitura usa paginação por ID (50 por página); marcar como lida preserva a primeira data, inclusive em pedidos repetidos ou concorrentes. O POST web tem CSRF; motivos são escapados no HTML. `UNREAD_NOTIFICATIONS` conta todas as não lidas do titular, sem o limite da página; `READ_ALL_NOTIFICATIONS` atualiza apenas as suas linhas ainda não lidas numa transação. A leitura em lote é idempotente e preserva as datas existentes.

`GET /notifications/feed` devolve contador e fragmento HTML escapado. O JavaScript local consulta-o a cada 15 segundos com a aba visível, pausa em segundo plano e atualiza ao regressar; evita pedidos sobrepostos, limita pedidos a 10 segundos e espaça tentativas até 60 segundos em caso de falha. Ações de leitura usam POST com CSRF, sem repetição automática. A sessão expirada interrompe a atualização. A CSP permite apenas scripts da mesma origem, sem scripts inline ou handlers HTML. Sem JavaScript, os formulários mantêm o percurso normal de POST e redirecionamento.

### Operações diárias

`bank/reports.cpy` executa `RECONCILE`, `CLOSE_DAY`, `REPORTS` e `REPORT`. Uma primeira passagem reconcilia e guarda as linhas do relatório; só se todas forem válidas uma segunda passagem credita juros de poupança. A fórmula e o arredondamento estão no parágrafo `calculate-daily-interest`. As únicas decisões no Python são rotas, apresentação e transporte.

O processamento serializa execuções com bloqueio exclusivo em `BANK_REPORTS`, seguido de bloqueio exclusivo em `BANK_ACCOUNTS` e partilhado em `BANK_TRANSACTIONS`, `BANK_ENTRIES`, `BANK_SAVINGS` e `BANK_REVERSALS`. Todos os fluxos monetários bloqueiam contas antes de escrever operações. Os bloqueios, juros, relatório, auditoria e marcador diário pertencem à mesma transação. Uma falha de escrita reverte tudo; uma divergência de negócio confirma apenas o relatório de diagnóstico.

`BANK_SAVINGS` distingue a poupança sem converter contas antigas. `BANK_REPORTS` guarda o resumo e `BANK_REPORT_ACCOUNTS` os valores por conta no momento da execução. `BANK_DAILY_CLOSES` tem chave primária na data do Db2 e só recebe registo após fecho sem divergências. Relatórios são imutáveis pela aplicação; a base continua administrável pelo seu proprietário.

A leitura usa um cursor por ID com memória constante, limitado a 500 contas. A composição das operações é validada por SQL parametrizado; a decisão sobre divergências e o cálculo monetário pertencem ao COBOL. Relatórios e detalhes são paginados a 50 linhas.

`bank/reversals.cpy` implementa `TRANSACTION` e `REVERSE`. Uma transferência compensatória troca origem e destino. Um estorno de depósito ou juro tem origem nula, conta associada no destino e um lançamento negativo. `BANK_REVERSALS` liga original, compensação e motivo; a chave do original impede duplicação. O original permanece intacto. A preparação da compensação e os bloqueios reutilizam os mesmos parágrafos dos movimentos normais.

A migração 003 substitui a restrição de tipos da transação e cria as novas tabelas na mesma unidade de trabalho. O nome da restrição anterior é obtido do catálogo Db2, não de entrada do utilizador. O leitor de migrações aceita apenas os caminhos fixos das versões conhecidas. O inicializador reconhece as migrações completas e pode ser repetido.

`DB2_SCHEMA` é uma configuração opcional da ligação nativa, com caracteres restritos, usada pelos testes para isolar os seus dados. Não é um campo do protocolo nem é exposta nos formulários. O Compose normal mantém o esquema da instância.

### Limites deliberados

É uma aplicação local de aprendizagem, não um produto bancário pronto para produção. Não implementa compensação, requisitos regulatórios, contabilidade geral de dupla entrada, MFA, recuperação de acesso, recuperação de desastres ou integração de pagamentos. A autorização da aplicação não substitui permissões mínimas na base: por compatibilidade com o ambiente existente, a ligação ainda usa a conta da instância Db2.

A auditoria não pode ser alterada pela interface, mas não é um arquivo inviolável contra um administrador da base. O bloqueio de login é por conta; uma publicação externa exigiria também limitação de pedidos e proteção contra abuso no ponto de entrada.

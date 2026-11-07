# Changelog

Alterações relevantes do projeto, com datas no formato AAAA-MM-DD.

## [Não lançado]

### Adicionado

- Notificações de aprovação/rejeição de empréstimos no sino existente, com motivo escapado, ligação ao pedido, leitura individual/em lote e gravação atómica com a decisão.
- Migração 008 preserva notificações e datas de leitura anteriores; unicidade por decisão suporta vários empréstimos na mesma conta.

- Extratos mensais por conta, com saldos de abertura/fecho, totais e exportação CSV completa até 1.000 movimentos.
- Limites de transferência por operação/dia configuráveis pelo administrador, com validação concorrente em COBOL.
- Comissão mensal simulada de 2 €, cobrança idempotente e isenções por conta e para poupanças.
- Empréstimos simulados com pedidos, aprovação/rejeição, plano de capital constante a 6% ao ano e pagamento manual de prestações.
- Encerramento definitivo de contas elegíveis, preservando histórico e libertando vagas.
- Identidade MiniBank com logótipo vetorial e favicon; navegação para serviços da conta e empréstimos.
- Migração 007 e integração dos novos movimentos na reconciliação.

- Sino com total de notificações não lidas, atualização automática sem recarregar e leitura de todas as notificações do titular; polling apenas com a aba visível, proteção CSRF e alternativa sem JavaScript.

- Notificações persistentes de aprovação/rejeição no painel do cliente, com motivo, paginação e opção de marcar como lida; autorização por titular, proteção CSRF e gravação atómica com a decisão em COBOL/Db2.

- Rejeição administrativa de pedidos com motivo visível ao titular e cancelamento pelo titular enquanto pendente; decisões auditadas e protegidas contra concorrência.
- Pedidos rejeitados/cancelados preservam o histórico sem ocupar vagas; migração 005 mantém contas e pedidos anteriores.

- Pedidos de contas à ordem/poupança pendentes de aprovação administrativa, com auditoria e migração que preserva as contas existentes.
- Limites por cliente de 3 contas à ordem e 2 poupanças, validados em COBOL com proteção contra concorrência e duplicação.
- Poupança com taxa simulada fixa de 3,65% ao ano e juros diários calculados em COBOL.
- Fecho diário idempotente, reconciliação de saldos/lançamentos/operações e relatórios persistentes por conta.
- Estornos integrais com motivo, operação compensatória, ligação ao original e proteção contra duplicação.
- Páginas administrativas de execução/consulta de relatórios e detalhe/estorno de operações.
- Migração transacional para instalações existentes e testes diários em esquema Db2 isolado.

- Interface web Conta Clara com áreas de administrador e cliente, executada apenas em Docker/local.
- Criação de clientes e contas, bloqueio/ativação, depósitos simulados, transferências, extratos e auditoria.
- Regras monetárias COBOL reutilizáveis pelo backend web.
- Transferências atómicas, bloqueios ordenados e idempotência de pedidos.
- Login com scrypt, sessões revogáveis no Db2, proteção CSRF e criação interativa do primeiro administrador.
- Testes integrados de concorrência, rollback, autorização e segurança HTTP.

- Serviço IBM Db2 Community, volume persistente e verificação de disponibilidade.
- Inicialização do esquema com valores decimais, identificadores e timestamps.
- Adaptador IBM com SQL parametrizado e testes de integração isolados.
- Dezoito testes COBOL de validação independentes da base.
- Documentação da arquitetura, convenções em inglês e contratos partilhados em copybooks.

### Alterado

- Navegação reutiliza o contador recebido com a sessão; polling fora do painel descarrega só o total e não repete a consulta imediatamente após carregar a página.
- CSS, JavaScript e logótipo com cache versionado por conteúdo; dados pessoais continuam sem cache. Cabeçalho Server-Timing permite diagnosticar a duração no servidor.

- Backend web reutiliza processos COBOL e ligações Db2 por thread, eliminando a abertura repetida de ligações em cada consulta. Estado, autorização e transação continuam isolados por pedido; falhas não provocam repetição automática de movimentos.

- Criação de cliente passa a criar apenas o acesso; o painel permite escolher contas existentes ou pedir uma nova. `CREATE_CUSTOMER` devolve agora o ID do utilizador.
- Navegação com áreas separadas para criar acessos, pedir contas e aprovar pedidos; contas pendentes não permitem operações nem juros.
- Cookie de sessão web com nome exclusivo para evitar colisões entre aplicações em localhost.
- Login com recuperação de formulário CSRF inválido/expirado na própria página, mantendo o email e exigindo nova submissão protegida.

- Frontend com painel de contas, abertura de cliente/conta numa área dedicada, navegação contextual e atalhos no detalhe.
- Erros de criação apresentados junto aos formulários, preservando nome e email sem devolver palavras-passe.

- Regras bancárias, autenticação, autorização, SQL e transações migrados para COBOL, preservando dados e credenciais existentes.
- Python limitado à interface HTTP e ao transporte JSON do banco; ponte C técnica para IBM CLI, json-c e OpenSSL.
- Teste de rollback passa a provocar uma falha real no Db2; testes de chamadas diretas verificam tokens e autorização no COBOL.

- Separação do programa original em menu, validação de domínio e adaptador de persistência.
- Persistência em Db2 em substituição do ficheiro; volume antigo preservado, sem migração automática.
- Avisos de compilação tratados como erros e terminadores de âmbito explícitos.
- Rejeição de casas decimais em excesso, descrições longas e entradas incompletas.
- Credenciais fora do Git e permissões limitadas para a aplicação.

### Verificação

- Migração 008 aplicada com comparação de todas as colunas preexistentes: dados e datas de leitura preservados.
- 77 testes da aplicação aprovados em Docker/Db2 isolado. Incluem notificações de empréstimos concorrentes, rollback do crédito quando o aviso falha, preservação de notificações anteriores, autorização, escape HTML e cache exclusivo de recursos públicos.
- Aprovação/rejeição e leitura em lote verificadas no navegador: o sino atualiza sem recarregar e o motivo permanece visível após leitura.
- Medição HTTP com Gunicorn real: medianas locais de cerca de 8–12 ms nas páginas autenticadas; não foi reproduzida uma demora de vários segundos. Reduzidas chamadas por navegação (detalhe: 3 → 2) e pelo sino fora do painel (3 → 1); cache estático evita downloads repetidos.

- Migração 007 aplicada com comparação dos valores de todas as colunas preexistentes: utilizadores, contas, saldos, movimentos, sessões e relatórios preservados.
- 70 testes da aplicação aprovados em Docker/Db2 isolado, incluindo 14 testes de produtos: limites concorrentes, duplicação de cobranças/aprovações/pagamentos, arredondamento, rollback, CSV, CSRF, autorização e encerramento. Extratos, empréstimos, identidade e administração verificados no navegador.

- 56 testes da aplicação aprovados em Docker, incluindo reutilização de processos, isolamento de sessões/threads, fork, timeout, rollback e recuperação após falha. Dados de teste em esquemas isolados.
- Comparação local com Flask test client e Db2 aquecido, mediana de cinco pedidos: painel 154 → 2,59 ms; detalhe da conta 116,86 → 1,30 ms; notificações 112,96 → 1,18 ms. Mede processamento no servidor, sem rede/renderização; o primeiro pedido ainda estabelece a ligação.
- 48 testes da aplicação e 18 casos COBOL aprovados para o sino, contador global e leitura em lote. Atualização sem recarregar e preservação do foco confirmadas no navegador com dados isolados.
- Limpeza de esquemas de teste repete a transação em caso de deadlock transitório no catálogo Db2; o grupo inicialmente afetado foi repetido com sucesso.
- 45 testes da aplicação e 18 casos COBOL aprovados em Docker com notificações; incluem isolamento por titular, CSRF, escape do motivo, paginação, leitura persistente/idempotente e rollback da decisão quando a notificação falha.
- Migração 006 aplicada sem alterar contas, decisões ou movimentos existentes.
- 40 testes da aplicação e 18 casos de validação COBOL aprovados em Docker após a adição de rejeição/cancelamento; verificados motivo obrigatório, escape HTML, autorização, CSRF, decisões concorrentes, libertação de vagas e rollback real no Db2.
- Migração 005 aplicada com contas, pedidos e movimentos anteriores inalterados.
- 34 testes de acesso, contas, aprovação, operações diárias e protocolo aprovados em Docker; integração executada em esquemas Db2 isolados. Incluem limites concorrentes, aprovação atómica, CSRF e regressão do login.
- Migração 004 aplicada com confirmação de que contas e movimentos existentes ficaram inalterados; páginas administrativas verificadas no navegador.
- Nove testes de operações diárias e doze testes bancários/protocolo aprovados no Db2 real, em esquemas isolados.
- Concorrência de fechos e estornos, arredondamento, limite de lote, divergências, rollback real, paginação e autorização administrativa verificados.

- Onze testes bancários e de protocolo aprovados com Db2 e COBOL reais, incluindo concorrência, rollback provocado no Db2, segurança HTTP e autorização por token.
- Ecrã de login revisto no navegador; regressão do gestor de despesas original aprovada.

- Compilação estrita sem avisos e 18 testes COBOL aprovados dentro do Docker.
- Teste integrado COBOL → Db2 aprovado: receitas, despesas, precisão decimal, apóstrofos, validação, fim da entrada e leitura numa nova ligação/processo.
- Persistência confirmada após reiniciar o Db2; registo temporário removido.
- Arranque Db2 confirmado sem modo privilegiado, com IPC_OWNER e SYS_RESOURCE.
- Corrigida a configuração da ligação não catalogada conforme o contrato do driver IBM.

## [0.1.0] - 2026-11-06

### Adicionado

- Menu GnuCOBOL para receitas, despesas e saldo.
- Compilação Docker em etapas e volume persistente para ficheiro de movimentos.
- Validação de entrada e dependências executadas apenas nos contentores.

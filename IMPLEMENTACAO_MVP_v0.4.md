# Observatório NDQ — v0.4: persistência e coleta experimental

Implementação iniciada em 27/10/2026 e validação v0.4.1 posteriormente informada
pelo responsável pelo projeto. PostgreSQL é uma camada opcional de trabalho
interno. O site e seus três dossiês continuam gerados pelos mesmos JSONs e
templates da v0.3. A fundação v0.4.1 não consulta órgãos nem publica páginas.
A extensão v0.4.2 descrita ao final consulta a API da Câmara e prepara propostas
locais de revisão, sem ingestão no banco ou publicação automática.

## Arquitetura e fluxo

Arquivos desta fase: `tools/persistence/{__init__.py,__main__.py,contract.py,db.py,repository.py}`,
`tools/persistence/migrations/001_foundation.sql`, `tools/test_persistence.py`,
`tools/test_persistence_postgres.py`, `requirements-db.txt`, `.env.example`,
`.gitignore` e este documento. O `README.md` recebeu uma seção aditiva; os
arquivos anteriores da v0.3 e as alterações locais existentes foram preservados.

```text
registros/*.json + fixtures legadas (contrato/regras da v0.3)
                 |
        adaptador fechado + validação offline
                 |
        importação transacional com psycopg
                 |
        PostgreSQL local ou Neon
        identidades estáveis + revisões imutáveis + evidências
                 |
        inspeção histórica / relatório privado

registros/*.json + editorial/*.json -> build_site.py -> HTML estático
```

O fluxo público permanece independente do banco. Não existe exportação automática
de uma revisão do banco para os arquivos publicados. `read_record()` reconstitui
um registro para inspeção e verifica seu SHA-256; não escreve conteúdo no site.

Decisões:

- Python 3.10+ e psycopg 3, sem ORM, servidor web, extensão PostgreSQL ou serviço
  de filas. A dependência do banco é opcional e carregada apenas ao conectar.
- Schema isolado `ndq`, migração SQL incremental e tabela de controle
  `public.ndq_schema_migrations`. Migrações são transacionais, serializadas por
  advisory lock e verificadas por checksum com quebras de linha normalizadas.
  Migração já aplicada não deve ser editada: acrescentar `002_*.sql`, etc.
- `dossier_id` existente é a identidade interna, independente do slug. Reservas
  históricas de slug impedem reatribuição silenciosa. Não há fusão automática.
- Identidade oficial é a tupla **instituição + namespace + valor**, reservada para
  um único dossiê. Cada associação tem observação e referência documental. Duas
  identidades do piloto Fundepar são preservadas porque já estão documentadas
  juntas no registro; semelhança de título não cria identidade compartilhada.
- Cada alteração gera uma revisão completa, ligada à anterior. SHA-256 compara
  somente com a última revisão: `A -> B -> A` mantém três revisões. Reimportar a
  última entrada idêntica não cria eventos, revisão ou nova execução.
- Tabelas factuais têm colunas relacionais para pesquisa e restrições. JSONB
  conserva metadados estruturados do contrato provisório, sem perder `null`,
  `NOT_VERIFIED`, campos ausentes, listas ou rótulos. Não há armazenamento de
  PDFs, XLSX, HTML editorial ou respostas brutas de APIs. O adaptador usa listas
  de campos permitidos e limita textos; não é um coletor universal.
- Todas as tabelas de domínio bloqueiam `UPDATE`, `DELETE` e `TRUNCATE` por
  triggers. Correções entram como novas revisões/observações. Isso protege o
  caminho normal da aplicação, não substitui permissões contra o dono do banco.
- `observed_at` é a data informada no registro; `recorded_at` é quando o banco
  registrou a revisão; uma importação não equivale a nova coleta oficial.

## Tabelas e relações

| Tabelas | Responsabilidade |
|---|---|
| `source_systems`, `collection_runs`, `run_sources` | Hosts das referências, execuções e fontes envolvidas; importações usam `fixture_import`, sem alegar coleta ao vivo |
| `dossiers`, `dossier_slugs`, `dossier_revisions` | Identidade interna, rotas reservadas, envelope estruturado e cadeia de revisões |
| `document_references` | Referência oficial por revisão, URL, tipo, data observada, estado de acesso e localizadores/metadados |
| `observations`, `observation_evidence` | Chave local, categoria, data disponível, dados normalizados e citações da mesma revisão |
| `official_identities`, `identity_observations` | Identificador oficial único e sua associação documentada ao dossiê |
| `facts` | Predicado, valor JSON tipado, estado original de verificação e vigência quando informada |
| `events` | Tipo, ocorrência, publicação, observação, previsão e qualificação de fuso; ocorrência e previsão são estados distintos |
| `contextual_milestones` | Marcos institucionais, observações NDQ e datas apenas previstas; precisão mensal ou diária |
| `status_observations` | Estado oficial observado, sem inferir efeito jurídico |
| `actors`, `actor_roles` | Pessoas com identidade ainda não resolvida e papéis documentados no dossiê |
| `official_acts`, `relationships` | Relação com outro dossiê ou ato: tipo, origem documental/proposta editorial, evidências e revisão independente; aprovação/rejeição exige revisor e data |
| `documented_changes` | Valores anterior/novo, atributo, detecção e evidências da mudança oficial documentada |
| `editorial_reviews`, `editorial_basis` | Avaliação original, revisão interna pendente e ligação aos fatos que a embasam |
| `publication_states` | Estado independente; nesta fase aceita somente `private_preview_only` ou `withheld` |
| `scheduled_checks`, `check_results` | Agenda interna idempotente e resultados históricos; indisponibilidade não vira conclusão negativa |

`latest_revisions` é uma view interna. Para consultar fatos atuais, filtrar/juntar
pela revisão retornada por ela; contar todas as revisões não é contar fatos atuais.

Uma observação exige uma referência primária por FK; `observation_evidence`
preserva todas as citações e sua ordem. A referência primária é a primeira da
lista, sem atribuir maior força à evidência. As tabelas tipadas só aceitam sua
categoria correspondente. Isso impede inserir um marco contextual como evento
formal e impede relações ou fatos sem proveniência. A API de importação conserva
os campos originais junto às projeções relacionais e verifica a reconstituição.

Hosts são derivados das URLs, não de um cadastro inventado de instituições.
Senado e FNDE não recebem registros vazios que poderiam sugerir cobertura.
Nomes de atores recebem chaves locais restritas ao dossiê e
`name_only_unresolved`: nomes iguais não identificam automaticamente a mesma pessoa.

## Datas e incerteza

Datas oficiais usam um domínio textual validado: `YYYY-MM-DD` ou ISO 8601 com
`T`, minutos, segundos opcionais e offset opcional. Isso evita inventar meia-noite
ou converter horários da Câmara sem fuso conhecido para UTC. Datas com offset
continuam com o offset original. Marcos mensais ficam em `period = YYYY-MM`;
o mês não ganha um dia na persistência. `published_at = null` continua desconhecido.

Eventos importados precisam de ocorrência documentada, não posterior ao **dia**
da observação. A comparação é deliberadamente por dia: a observação dos pilotos
tem precisão diária e não prova a hora de coleta. Não se infere ordenação dentro
do mesmo dia nem entre fusos distintos. Eventos futuros devem entrar por um
adaptador futuro como `scheduled`, sem `occurred_at`; esse estado já tem restrição
SQL. A data Fundepar de `2026-06-11T08:30` permanece um marco previsto, mesmo tendo
passado no calendário. Não há inferência de que a sessão ocorreu.

Listas vazias significam **não carregadas**, nunca uma constatação de ausência.
Remover uma entrada numa revisão não apaga sua observação anterior e não gera
automaticamente um fato negativo. O diff do relatório mostra seções alteradas,
sem classificá-las como mudanças oficiais verificadas. `documented_changes`
mantém as alterações já documentadas no registro de origem.

## Configuração e instalação local

Na raiz do repositório, PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-db.txt
```

O build estático e os testes offline continuam sem dependências externas.
`.env.example` documenta as variáveis. Os comandos de banco e o verificador de
conexão carregam `.env` da raiz com `python-dotenv` e `override=False`; uma
variável explícita do ambiente tem prioridade. O dry-run e o build não leem
credenciais. Configure `.env` localmente ou use um gerenciador de segredos.
Credenciais e arquivos `.env` não devem entrar no Git.

| Variável | Uso |
|---|---|
| `DATABASE_URL` | Conexão lida do ambiente ou `.env` por `migrate`, importação com escrita e verificador; nunca lida pelo build ou dry-run |
| `NDQ_TEST_DATABASE_URL` | Somente testes de integração; host loopback explícito e banco `ndq_test` vazio |

Para verificar uma conexão Neon sem alterar dados ou schema, depois de instalar
`requirements-db.txt` e configurar o `.env` local, execute:

```powershell
.\.venv\Scripts\python.exe -B -m tools.persistence.check_connection
```

O verificador carrega o `.env` da raiz, preserva `DATABASE_URL` já definida no
ambiente, exige TLS e inicia a sessão em modo somente leitura antes das consultas.
O relatório não exibe URL, senha nem hostname. Essa verificação não executa
migrações nem importa registros.

O campo `tls_active` reflete `conn.pgconn.ssl_in_use`, observado pelo libpq no
cliente. `pg_stat_ssl` pode refletir outra parte da rota até o backend Neon e
não determina o TLS entre cliente e endpoint. Se o atributo do cliente não
estiver disponível, `tls_active` fica `null` e a verificação sai com código 1.
`sslmode_required` mostra a exigência usada na conexão; ela não é apresentada
como prova independente de validação de certificado. `tls_protocol` só aparece
quando o wrapper libpq expõe esse atributo e seu valor passa na validação do
formato. `certificate_validation` permanece `not_independently_observed`.

Use PostgreSQL 14+ instalado localmente, com ferramentas `createdb`/`psql` no
PATH. Crie um banco de desenvolvimento sob uma conta local autorizada:

```powershell
createdb -h localhost -U postgres ndq_dev
# Usa autenticação local configurada / pgpass; a URL não precisa conter senha.
$env:DATABASE_URL = 'postgresql://postgres@localhost:5432/ndq_dev'
.\.venv\Scripts\python.exe -B -m tools.persistence migrate
.\.venv\Scripts\python.exe -B -m tools.persistence import-pilots --dry-run
.\.venv\Scripts\python.exe -B -m tools.persistence import-pilots --report import-report-local.json
```

Alternativa para quem já tem Docker, sem volume permanente e com senha temporária
escolhida localmente:

```powershell
$env:POSTGRES_PASSWORD = Read-Host 'Senha temporária do PostgreSQL de desenvolvimento'
docker run --name ndq-postgres-dev --rm -d -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD -e POSTGRES_DB=ndq_dev postgres:16
# Configure DATABASE_URL usando a porta 55432 e autenticação escolhida.
# Aguarde pg_isready antes de executar as migrações.
docker exec ndq-postgres-dev pg_isready -U postgres -d ndq_dev
```

Esses comandos são instruções para execução local: esta entrega não cria conta,
banco, contêiner ou conexão externa automaticamente. Não há comando de rollback
destrutivo. O histórico de migrações falha se houver checksum divergente, lacunas
ou migrações do banco ausentes no checkout; resolver por migração aditiva revisada.

## Neon

Em uma **branch de desenvolvimento** do Neon, copie a connection string do painel
Connect para `DATABASE_URL` por meio do seu gerenciador de segredos. Prefira o
endpoint direto para migrações e este importador de curta duração; não é preciso
pool para os visitantes do site estático. O driver também desativa preparação
automática de statements para reduzir dependências de estado de sessão.

Mantenha os parâmetros TLS da connection string, incluindo `sslmode=require`
ou mais estrito e `channel_binding=require` quando fornecido pelo painel. Não
retire TLS para fazer a conexão funcionar. Use as mesmas duas operações:

```powershell
# DATABASE_URL já definida com a conexão da branch de desenvolvimento autorizada.
.\.venv\Scripts\python.exe -B -m tools.persistence migrate
.\.venv\Scripts\python.exe -B -m tools.persistence import-pilots --report import-report-neon-dev.json
```

Referências de configuração: [conexão do Neon](https://neon.com/docs/connect/connect-from-any-app),
[transações do psycopg](https://www.psycopg.org/psycopg3/docs/basic/transactions.html) e
[prepared statements](https://www.psycopg.org/psycopg3/docs/advanced/prepare.html).
Uma conta Neon não é necessária para os testes offline. A conexão de desenvolvimento
foi verificada com TLS ativo observado pelo libpq; isso não demonstra, por si só,
validação independente do certificado. As validações com escrita descritas abaixo
ocorreram apenas nas branches descartável e de desenvolvimento informadas pelo
responsável, sem escrita em produção.

## Validação isolada no Neon: procedimento e resultado v0.4.1

`tools.persistence.validate_disposable_neon` é um procedimento separado dos testes
`tools.test_persistence_postgres`, que continuam restritos a `localhost/ndq_test`.
Ele não usa `DATABASE_URL` nem `NDQ_TEST_DATABASE_URL` como destino. A URL de
desenvolvimento em `.env` é consultada apenas para impedir coincidência de endpoint.

1. No console Neon, crie uma branch nova chamada `ndq-validation-...`, filha da
   branch persistente de desenvolvimento. Confira que é uma branch distinta da
   produção e do desenvolvimento. No painel da nova branch, anote o **branch ID**,
   o **endpoint ID** do compute direto e copie a URL direta de `neondb`. Anote
   também os branch IDs da branch persistente de desenvolvimento e da produção,
   além do endpoint ID da produção. Não cole a URL em chat, issue ou
   comando registrado no histórico do shell.
2. Crie, localmente, `.env.ndq-validation` na raiz com estas seis chaves
   (arquivo coberto por `.env.*` no `.gitignore`):

   ```dotenv
   NDQ_DISPOSABLE_DATABASE_URL=postgresql://USUARIO:SENHA@ENDPOINT/neondb?sslmode=require
   NDQ_DISPOSABLE_BRANCH_ID=br-IDENTIFICADOR
   NDQ_DISPOSABLE_ENDPOINT_ID=ep-IDENTIFICADOR
   NDQ_DEVELOPMENT_BRANCH_ID=br-IDENTIFICADOR-DO-DESENVOLVIMENTO
   NDQ_PRODUCTION_BRANCH_ID=br-IDENTIFICADOR-DA-PRODUCAO
   NDQ_PRODUCTION_ENDPOINT_ID=ep-IDENTIFICADOR-DA-PRODUCAO
   ```

   Preserve `channel_binding` e outros parâmetros de segurança fornecidos pelo
   Neon. Não altere `.env` nem exporte a URL descartável como `DATABASE_URL`.
3. Rode a pré-verificação sem escrita:

   ```powershell
   .\.venv\Scripts\python.exe -B -m tools.persistence.validate_disposable_neon
   ```

   O comando exige TLS confirmado pelo libpq, URL direta com `sslmode` explícito,
   banco `neondb`, endpoint igual ao informado para a branch de teste, branch ID
   diferente dos IDs informados para produção e desenvolvimento, endpoint diferente
   dos endpoints de produção informado e desenvolvimento conhecido, e ausência de
   schema/ledger NDQ. **Compare o branch ID e endpoint ID no console Neon antes
   de autorizar a etapa seguinte.** SQL sozinho não comprova a vinculação entre
   endpoint e branch; o relatório marca `branch_id_verified_by_neon_api: false`.
4. Somente após a comparação no console, aplique e valide na branch descartável:

   ```powershell
   .\.venv\Scripts\python.exe -B -m tools.persistence.validate_disposable_neon --apply --confirm-branch-id br-IDENTIFICADOR
   ```

   O comando revalida o destino antes da primeira escrita, aplica `001_foundation.sql`,
   confirma que reaplicá-la não muda o ledger, importa os três pilotos, repete a
   importação e exige que a segunda seja inalterada. Confere contagens, uma revisão
   inicial por dossiê, hashes e reconstrução exata dos registros, identidades,
   referências de fonte, evidências, estado editorial/publicação e FKs validadas.
   O JSON de saída omite URL, senha, hostname, IDs de documentos e mensagens brutas
   do driver. Em caso de erro após `--apply`, mudanças parciais podem ter sido
   confirmadas, pois migração e importação são transações separadas; inspecione a
   branch antes de tentar novamente.

Esse procedimento não testa negativas com `DELETE`/`TRUNCATE`, concorrência,
coleta externa, atualização de fatos oficiais nem publicação. Ele não substitui
a suíte de integração local. A branch descartável só deve ser removida pelo
usuário após revisar o resultado; este comando nunca a remove.

**Resultado confirmado pelo responsável na branch descartável:** a migração
`001_foundation.sql` foi aplicada; os **três dossiês piloto** foram importados;
a repetição da importação não criou novas revisões; **36 chaves estrangeiras**
constaram como validadas; e **três reconstruções exatas** dos registros passaram.
As conferências de identidades, referências de fontes, evidências e estados
editoriais também passaram. Esses resultados verificam o primeiro lote de pilotos
naquela branch; não demonstram cobertura futura, concorrência ou todos os cenários
de restrições e correções históricas.

## Inicialização controlada da branch persistente de desenvolvimento (v0.4.1)

Após a validação real na branch descartável **informada pelo responsável**, o comando
`tools.persistence.initialize_development_neon` prepara a mesma migração e os
mesmos três pilotos para a branch persistente de desenvolvimento. Não cria outro
schema, não usa a URL descartável e não altera o site público. A suíte
`tools.test_persistence_postgres` continua restrita a `localhost/ndq_test`.

1. No console Neon, confira o ID da branch persistente de desenvolvimento, seu
   endpoint **direto** e os IDs de branch/endpoint de produção e da branch
   descartável. Mantenha a URL de desenvolvimento apenas no `DATABASE_URL` do
   `.env` da raiz. Não coloque URL ou credenciais no arquivo de guarda.
2. Crie o arquivo ignorado `.env.ndq-development` com estes seis identificadores:

   ```dotenv
   NDQ_DEVELOPMENT_BRANCH_ID=br-ID-DO-DESENVOLVIMENTO
   NDQ_DEVELOPMENT_ENDPOINT_ID=ep-ID-DO-DESENVOLVIMENTO
   NDQ_PRODUCTION_BRANCH_ID=br-ID-DA-PRODUCAO
   NDQ_PRODUCTION_ENDPOINT_ID=ep-ID-DA-PRODUCAO
   NDQ_DISPOSABLE_BRANCH_ID=br-ID-DO-DESCARTAVEL
   NDQ_DISPOSABLE_ENDPOINT_ID=ep-ID-DO-DESCARTAVEL
   ```

   O `DATABASE_URL` da sessão PowerShell, se definido, precisa ser exatamente
   igual ao do `.env` da raiz; um valor diferente aborta antes de conectar.
3. Execute somente a pré-verificação, em modo leitura:

   ```powershell
   .\.venv\Scripts\python.exe -B -m tools.persistence.initialize_development_neon --preflight
   ```

   O comando recusa IDs de branch/endpoint iguais aos de produção ou da branch
   descartável, URL que não indique o endpoint direto de desenvolvimento e
   `neondb`, modo TLS fraco, overrides de destino libpq, TLS não confirmado pelo
   cliente e presença de schema/ledger NDQ. A sessão inicia com transações
   somente leitura. **Confirme no console que o endpoint realmente pertence à
   branch de desenvolvimento**: a conexão SQL não prova esse vínculo e o campo
   `branch_id_verified_by_neon_api` permanece `false`.
4. Após revisar o preflight e obter autorização explícita para escrita, execute:

   ```powershell
   .\.venv\Scripts\python.exe -B -m tools.persistence.initialize_development_neon --apply --confirm-branch-id br-ID-DO-DESENVOLVIMENTO
   ```

   A execução abre uma nova conexão, revalida o alvo vazio antes da primeira
   escrita, aplica `001_foundation.sql` pela função `migrate()`, importa os três
   pilotos pela função `import_pilots()` e repete a importação. Reutiliza as
   verificações da branch descartável para contagens, três revisões iniciais,
   hashes, reconstrução exata, identidades, referências de fonte, evidências,
   estados editoriais e FKs. Não roda testes destrutivos. Se falhar após iniciar
   `--apply`, alterações parciais podem ter sido confirmadas: migração e importação
   são transações separadas. Inspecione o banco antes de qualquer nova tentativa.

**Resultado posterior confirmado pelo responsável:** a inicialização da branch
persistente de desenvolvimento terminou com `success=true`. O procedimento
aplicou a mesma migração e importação dos três pilotos, com nova verificação de
destino antes da escrita. Não há aqui um relatório independente com as contagens
da branch de desenvolvimento; os números **36 FKs** e **três reconstruções exatas**
acima são os resultados informados para a branch descartável. O inicializador
exige schema e ledger ausentes, portanto não deve ser reexecutado na branch já
inicializada apenas para consultar o estado atual.

O relatório omite URL, senha, hostname e mensagens brutas do driver. Qualquer
nova escrita na branch persistente requer autorização e verificação do alvo.

## Importação, relatório e falhas

```powershell
py -3 -B -m tools.persistence import-pilots --dry-run --report import-report-dry-run.json
```

O dry-run valida somente arquivos locais; não compara com banco nem promete que
uma futura conexão/importação terá sucesso. Os registros passam pelo validador
v0.3 existente e por verificações adicionais de formato/proveniência. Arquivos
legados são lidos para conferir o contrato já aprovado, sem importar seu HTML.

Uma execução com escrita valida o lote inteiro primeiro, serializa importadores
cooperantes, verifica identidades/slugs e grava tudo numa transação. Falha em
qualquer dossiê reverte o lote inteiro. Não há atualização parcial ou exclusão.
O relatório inclui contagens de entrada, identidades estáveis, hashes, revisões
criadas, predecessor, seções alteradas e dossiês inalterados. Execuções bem-sucedidas
com alterações guardam relatório resumido no banco; tentativas inválidas ficam
no relatório de erro local. As mensagens de conexão não são impressas, pois
podem carregar credenciais. O tipo de erro é exposto; erros do contrato incluem
detalhes locais. O processo retorna 0 em sucesso, 1 em falha e 2 para uso inválido.

Se apenas a gravação do arquivo de relatório falhar após uma importação concluída,
o resultado real permanece no stdout, com `report_file_error` e saída 1. Isso não
reverte um lote já confirmado; reexecutá-lo continua idempotente.

Todo dossiê importado entra em `pending_review` e `private_preview_only`. O estado
`prototype_approved_for_private_integration` fica preservado em
`origin_review_status`/avaliação original; não vira nova revisão editorial nem
aprovação pública. Não se fabrica revisor. Alterações futuras precisarão de um
fluxo de ações de revisão com autor/data; as tabelas atuais não são uma UI de revisão.

O adaptador é deliberadamente fechado para `ndq-dossier-0.2` usado pela v0.3 e
seus três tipos. Campos desconhecidos, corpos brutos, identidades conflitantes,
datas inválidas e proveniência ausente falham. `dossier_relations`, `deadlines`,
`coverage_evidence`, propostas vinculadas e eventos de execução de contratação
ainda não têm um contrato populado no piloto: valores não vazios são **rejeitados**,
não descartados. A estrutura SQL para relações e agenda está pronta para um
adaptador futuro aprovado. `type_data.notice_versions` e `item_requirements`
continuam JSONB estruturado validado, com suas referências locais preservadas.

## Testes

Sem conexão com banco ou rede, após instalar `requirements-db.txt`:

```powershell
py -3 -B -m unittest discover -s tools -p 'test_*.py' -v
py -3 -B tools/validate_dossiers.py
py -3 -B tools/build_site.py
py -3 -B tools/check_site.py
```

Para integração, crie um **banco local vazio dedicado**, sem rodar `migrate` nele:

```powershell
createdb -h localhost -U postgres ndq_test
$env:NDQ_TEST_DATABASE_URL = 'postgresql://postgres@localhost:5432/ndq_test'
.\.venv\Scripts\python.exe -B -m unittest tools.test_persistence_postgres -v
Remove-Item Env:NDQ_TEST_DATABASE_URL
```

Os testes aplicam a migração e importam os pilotos dentro de uma transação externa
revertida ao final; cada caso usa savepoint e rollback. Não executam DROP, DELETE
ou migração destrutiva para preparar o banco. O teste de bloqueio tenta DELETE e
TRUNCATE dentro de savepoints e exige sua rejeição. Bancos com schema NDQ existente,
hosts externos e nomes diferentes de `ndq_test` são recusados. Sem a variável, os
testes exibem skip. Com ela definida, driver ausente, conexão indisponível,
migração inválida ou asserção falha são **erros**, nunca skips silenciosos.

Cobertura: contrato inválido/sem suporte; identidades e conflitos; importação
idempotente e reconstrução exata; revisões e reversão de conteúdo; ausência de
evidência; datas e fusos; contexto versus ocorrência; proveniência de relações
com dossiês/atos; retificação; revisão/publicação; agenda e resultados.

Checkpoint offline v0.4.1 informado e reproduzido na atualização do README:
**91 testes aprovados**, **20 testes PostgreSQL localhost-only em skip explícito**.
No fechamento desta documentação, `tools/build_site.py` gerou o índice e os três
dossiês sem alterar seus quatro HTMLs (comparação byte a byte), e
`tools/check_site.py` aprovou as seis páginas HTML, três fixtures legadas e três
registros estruturados. Esses resultados são separados dos testes de banco.
O arquivo local `import-report-dry-run.json`, quando presente, contém
apenas um relatório offline e é ignorado pelo Git; não comprova uma importação.

## Limitações e próximos passos

- A migração e a importação foram validadas em uma branch descartável do Neon, e
  a inicialização da branch persistente de desenvolvimento foi informada como
  bem-sucedida. Os **20 testes de integração reservados a PostgreSQL local**
  continuam sem execução no checkpoint offline; não foram apontados ao Neon.
  Validação da branch descartável não equivale a ensaio de todos os caminhos SQL,
  de concorrência ou de atualização de revisões futuras.
- A fundação v0.4.1 não inclui descoberta semanal, coletores, retentativas de
  coleta, detecção remota de versões ou workers. A extensão de leitura v0.4.2
  abaixo acrescenta o primeiro coletor e retentativas HTTP; `official_collection`
  continua sem ingestão implementada nas tabelas de execução.
- Agenda não gera D+1 automaticamente. `schedule_check()` exige data operacional
  com fuso explícito, motivo, observação base e chave de idempotência. Falta
  definir fuso, dias úteis/corridos, cancelamentos, remarcações e cobertura das
  fontes antes de automatizar. Resultado `source_unavailable` não significa
  ausência de atos ou mudanças.
- Relações futuras exigem definir direção/tipo/identidade do alvo e documentação
  da associação. Não há descoberta ou fusão por similaridade. Cadastro de atores
  permanece local e não resolvido.
- Revisão editorial, permissões de usuários/serviços, backup/retenção, concorrência
  fora do importador e publicação supervisionada continuam sem produto operacional.
  Triggers de imutabilidade não são um sistema de autorização.
- As projeções relacionais são gravadas somente pelo repositório suportado;
  metadados JSONB não são um contrato universal para escritas SQL externas. Uma
  mudança no contrato exige atualizar adapter, migração e testes em conjunto.

**Roteiro registrado ao fechar a v0.4.1 — v0.4.2:** definir o contrato de observação e implementar
um coletor experimental da Câmara dos Deputados com normalização e proveniência.
A v0.4.3 prevê detecção de mudanças e relações documentadas; a v0.4.4, descoberta
semanal e verificações D+1; e a v0.4.5, revisão editorial e publicação supervisionada.
A suíte de integração local ainda pode ampliar a cobertura SQL antes dessas etapas.
A fase 1 desse coletor está implementada e descrita a seguir.

## v0.4.2 — Coletor Câmara, fase 1: observação e proposta privada

Esta extensão implementa consultas reais ao Dados Abertos da Câmara v2,
normalização por campos permitidos, comparação somente leitura e relatório
privado. O ID da proposição é argumento do CLI; `2498778` é o piloto configurado
no exemplo, não uma constante do coletor. Nenhuma migração ou escrita no banco
faz parte desse fluxo. Os arquivos de registro, editorial e HTML permanecem
independentes da proposta e não são alterados.

### Arquivos e fluxo

- `tools/collectors/http.py`: cliente JSON reutilizável com timeout, ritmo mínimo
  de 0,5 s (padrão 1 s), até duas retentativas por padrão e backoff exponencial
  limitado. Retenta timeout/rede, HTTP 408/429 e 500/502/503/504; outros status
  encerram o endpoint com diagnóstico. Não segue redirecionamentos nem lê corpos
  de erros HTTP. Limites: 2 MB por resposta, 10 mil itens e 30 páginas por padrão.
- `tools/collectors/camara/normalize.py`: projeções validadas, datas, URI oficial
  de autor não vinculada ao ator legado e fingerprints de conteúdo. Não conserva
  `texto`, `justificativa`, PDFs nem o envelope bruto da API.
- `tools/collectors/camara/baseline.py`: busca identidade exata no JSON e em
  `ndq.official_identities`; lê até 20 revisões, valida reconstrução/hash pela
  função existente `read_record()` e relata se o histórico lido foi limitado.
- `tools/collectors/camara/compare.py`: findings `unchanged`, `new`, `changed`,
  `conflicting`, `uncertain` e `retrieval_incomplete`, sempre para revisão.
- `tools/collectors/camara/service.py` e `__main__.py`: composição, CLI e relatório
  `ndq-camara-proposal-0.4.2`, gravado somente em `.ndq-private/camara/` (ignorado
  pelo Git). `tools/collectors/camara/ingestion_plan.py` calcula uma decisão
  idempotente **offline**, sem adaptador SQL. `tools/test_camara_collector.py` usa
  fixtures sintéticas e, quando disponível, o relatório privado existente como
  fixture local; nenhuma requisição HTTP/SQL é feita durante os testes.

O cliente inspeciona `dados` e `links`, exige `self` coerente e percorre `next`.
Aceita paginação HTTPS apenas no mesmo host e caminho, com `pagina`/`itens`
numéricos; rejeita filtros adicionais, saltos, ciclos, alteração não comprovada
do tamanho da página e limites excedidos. Uma falha preserva os itens já obtidos
em memória, mas marca a cobertura como incompleta. Concluir as páginas retornadas
não prova história institucional exaustiva nem uma fotografia atômica dos quatro
endpoints. Outros formatos de paginação devem ser revistos antes de ampliar o
cliente; são recusados de forma explícita, sem declarar completude.

### Fontes e mapeamentos

Os quatro endpoints foram exercitados ao vivo para o piloto:

| Endpoint sob `https://dadosabertos.camara.leg.br/api/v2` | Campos normalizados e limites |
|---|---|
| `/proposicoes/{id}` | `id`, `uri`, tipo, número, ano, ementa, apresentação, órgão numerador e status oficial. `statusProposicao.dataHora` fica como data do registro de status, sem virar publicação ou vigência. Não se afirma que a proposição é lei. |
| `/proposicoes/{id}/tramitacoes` | `dataHora`, `sequencia`, órgão/sigla/URI, códigos e descrições, despacho e referências oficiais disponíveis, incluindo `uriUltimoRelator`. Nenhum PDF é buscado. |
| `/proposicoes/{id}/autores` | URI, nome, tipo, código, ordem e indicador `proponente` quando fornecidos. Não infere relatoria nem equivalência de pessoa por nome. |
| `/proposicoes/{id}/relacionadas` | Identificadores e resumos das proposições retornadas, como candidatos `returned_as_related_unspecified`, sem tipo jurídico ou efeito inferido. O piloto devolveu lista vazia; respostas não vazias foram exercitadas somente com fixtures sintéticas. |

Referências: [documentação oficial da API](https://dadosabertos.camara.leg.br/swagger/api.html)
e [explicação oficial de relacionadas](https://dadosabertos.camara.leg.br/news/noticias/2017-11-09-versao0_2_8.html).
A seleção de campos foi conferida nas respostas reais. Campos opcionais ausentes
não são fabricados; os campos originais selecionados distinguem ausência de chave
e `null`. A política `camara-selected-fields-v1` retém somente `PROP_FIELDS`,
`STATUS_FIELDS`, `EVENT_FIELDS`, `AUTHOR_FIELDS` e `RELATED_FIELDS` definidos em
`normalize.py`. Todos esses campos selecionados participam da comparação entre
duas observações oficiais; os demais são descartados, sem guardar nomes, valores,
documentos ou envelope bruto. `coverage` registra, **por endpoint**, listas de
campos retidos/comparados, quantidade de ocorrências de campos descartados, número
de linhas retornadas e escopo `selected_fields_only_not_complete_change_detection`.
O inventário de campos descartados de um relatório antigo reconstruído somente de
sua projeção é desconhecido (`null`), não zero. Mudanças exclusivamente em campos
descartados permanecem indetectáveis até revisão explícita da política/versionamento.

Datas de fonte preservam texto, precisão e offset quando informado. `dataHora` de
tramitação é mantido como **data/hora procedimental informada pela fonte**; inclusive
`00:00` não prova ocorrência exatamente à meia-noite. Sem prova independente da
semântica de instante, `occurred_at`, `published_at` e `scheduled_for` ficam nulos.
A data de
recuperação é UTC, separada das datas oficiais. `retrieved_at` do conjunto, dos
itens e dos findings representa o fim da coleta (`retrieval_time_scope` explícito);
os instantes de recebimento de cada página ficam em `coverage.*.pages`.
Mês não recebe dia inventado;
datas inválidas ou sem precisão suportada ficam incertas. `published_at` e
`scheduled_for` permanecem desconhecidos quando o endpoint não os fornece com
essa semântica. Uma data futura em `dataHora` fica como
`future_source_date_unconfirmed`, sem afirmar ocorrência ou agendamento. Datas
com offset podem ser comparadas ao instante UTC; sem fuso, a comparação é apenas
por dia, sem inventar ordenação dentro do dia.

### Identidade, comparação e lacunas do modelo

A identidade do dossiê é a tupla existente `camara / proposicoes / {id}`; não há
fusão por título ou similaridade textual. Como não foi observado um ID oficial
único de tramitação, o candidato `camara-compound-v1` usa SHA-256 de
`[proposition_id, sequencia, dataHora, siglaOrgao]`. Descrições ficam fora da chave,
permitindo comparar correções literais. A ordem da resposta não muda a chave.
Campos insuficientes e múltiplas linhas sob a mesma chave, mesmo idênticas, ficam
incertos ou conflitantes. Uma alteração da data/órgão com sequência já conhecida
é candidata a correção, sem ser automaticamente um evento novo.

`new` significa novo em relação à baseline explicitada, não um novo ato do governo.
Uma observação já encontrada em revisão histórica não é anunciada como nova sem
qualificação. Itens não retornados nunca geram exclusão, cancelamento ou conclusão
de ocorrência; com paginação parcial, o resultado é `retrieval_incomplete`.
`unchanged` só se aplica aos campos comparados com cobertura suficiente.

Os despachos literais não existiam na baseline curada v0.3. O relatório guarda
`editorial_representations` com **resumo curatorial** e **despacho literal** em
campos separados, sem classificá-los como mudança oficial nem gerar sete alertas
recorrentes. `compare_record()` mantém comparações contextuais com o dossiê;
mudanças literais de fonte são classificadas apenas por `compare_snapshots()` contra
uma observação oficial anterior. `--previous-proposal` permite essa comparação;
ela aponta o campo selecionado que mudou, não afirma detecção integral. Chaves,
datas e URLs são revalidadas ao ler o relatório anterior. Esse arquivo não é uma
evidência assinada. A ausência ou limpeza de campo opcional gera incerteza.

A URI de autor fica em `official_identity` com instituição, namespace, valor e URL
do endpoint, estado de vínculo legado `unresolved` e papel
`author_as_returned_not_rapporteur`. Coincidência de nome não funde pessoas nem
atribui relatoria. Uma resposta completa com zero linhas de `relacionadas` tem
`returned_count=0` e não produz incerteza recorrente; isso não prova ausência
global de relações ou que o endpoint detecta todas as modalidades jurídicas.

O schema atual ainda não tem um adaptador aprovado para ingerir essas observações
v0.4.2: o importador dos pilotos rejeita relações populadas, nomes não resolvem
identidades oficiais de autores e o endpoint de relacionadas não estabelece um
tipo jurídico. As lacunas aparecem em `model_gaps`; nenhum contorno modifica o
schema ou converte a proposta para `import_pilots()`.

### Uso e proteção do destino

Na raiz, com `requirements-db.txt` instalado, o comando padrão já é dry-run:

```powershell
.\.venv\Scripts\python.exe -B -m tools.collectors.camara --proposition-id 2498778 --dry-run
```

O coletor usa o guard existente de `.env.ndq-development`, com a URL de
desenvolvimento lida apenas do `.env` da raiz. IDs de produção/descartável iguais
ao alvo, endpoint divergente, URL de sessão conflitante ou override libpq são
recusados antes da conexão. O vínculo entre branch e endpoint continua dependendo
dos IDs conferidos no console; não há prova independente pela API Neon.
A conexão começa com `default_transaction_read_only=on`, exige TLS observado
pelo libpq e abre transação `REPEATABLE READ, READ ONLY`. Apenas `SELECT`, `SHOW`
e configuração transacional são executados; não há coleta persistida no banco.
Erros de conexão expõem somente tipo e SQLSTATE válido, nunca mensagens do driver.

Para consultar a API comparando somente com os JSONs, sem carregar configuração
de banco, use:

```powershell
.\.venv\Scripts\python.exe -B -m tools.collectors.camara --proposition-id 2498778 --dry-run --no-database
```

Para uma observação posterior, substitua o caminho abaixo pelo relatório local
anterior emitido pelo CLI:

```powershell
.\.venv\Scripts\python.exe -B -m tools.collectors.camara --proposition-id 2498778 --previous-proposal .ndq-private\camara\RELATORIO-ANTERIOR.json
```

`--timeout`, `--retries` e `--max-pages` têm limites validados. Não existe `--apply`.
O relatório contém `observation` (projeções e cobertura), `baselines` (IDs, hashes,
revisões e estados conhecidos), `comparisons` (antes/depois com fonte e motivo),
`finding_counts`, `model_gaps` e itens sugeridos para revisão. Ele é uma proposta
`pending_review`, com `publication_approved=false` e `database_writes=false`.
Saída 0 significa execução completa no escopo declarado, não aprovação factual
ou editorial; saída 1 indica resultado parcial ou falha. O diretório local
ignorado não é uma solução de controle de acesso e não deve ser publicado.

### Resultado ao vivo e cobertura de testes

Na execução de `2026-10-27T23:56:38Z`, os quatro endpoints retornaram uma página
cada: uma proposição, **sete tramitações**, **um autor** e **zero relacionadas**.
A tupla oficial encontrou `ndq:camara:proposicao:2498778`, revisão 1, na branch
Neon de desenvolvimento. A reconstrução do banco e o registro JSON tinham o
mesmo SHA-256; a sessão confirmou somente leitura e TLS ativo no cliente.
Os estados lidos permaneceram `pending_review` e `private_preview_only`.

O **relatório original** da execução apresentou, por baseline (JSON e banco),
10 findings inalterados e nove incertezas. Após revisão editorial, sete dessas
incertezas eram somente diferenças entre resumo curatorial e despacho literal;
a lista vazia de relacionadas também não exigia incerteza recorrente. Esses números
documentam o relatório histórico, **não** a interpretação atual do código. Nenhuma
nova consulta ao vivo foi feita para recalcular contagens. O relatório original
permanece privado e ignorado pelo Git. Nenhuma resposta bruta foi salva e nenhum
banco foi alterado nesta revisão.

Os testes offline cobrem proposição válida, paginação e falhas parciais, limites,
timeout/retentativas, 4xx/5xx, JSON inválido, reordenação e repetição, eventos novos,
mudança de despacho/status, campos opcionais, colisões, datas, relações candidatas,
falha/identidade do banco e preservação dos estados. Paginação real com várias
páginas, relações não vazias, correções futuras e concorrência ainda não foram
validadas ao vivo. Os testes PostgreSQL localhost-only continuam separados.

Checkpoint anterior: **33 testes do coletor aprovados**; suíte completa com
**124 aprovados, zero falhas e 20 skips PostgreSQL locais** (144 casos).
O build e a checagem das seis páginas passaram; os quatro HTMLs gerados permaneceram
idênticos byte a byte. Os demais arquivos públicos não tiveram alterações no Git.

### Revisão editorial e contrato de ingestão somente projetado

Esta revisão acrescentou testes offline baseados no relatório privado do piloto,
sem copiá-lo para o Git. O conteúdo da fonte, os resumos editoriais e a publicação
continuam separados. A chave `source_content_key` é SHA-256 determinístico do ID
oficial, versão da projeção e campos oficiais selecionados nos quatro endpoints,
com linhas ordenadas por conteúdo. O momento da busca, a ordem retornada e os
campos descartados não entram na chave. Assim, uma busca semanal idêntica tem a
mesma chave; `plan_source_ingestion()` marca `already_observed`, não propõe nova
revisão factual nem alerta editorial. Um resultado incompleto não vira baseline
factual. Uma diferença em campo selecionado produz apenas candidato de mudança
para revisão; não cria proposta editorial, decisão de aprovação ou revisão publicada.
O plano é **simulação local**, não persistência nem proteção contra concorrência.
Nesta revisão, a suíte offline executou **153 casos: 133 aprovados, zero falhas e
20 skips PostgreSQL restritos a localhost**. Os testes privados são ignorados quando
o relatório local não está disponível; os testes sintéticos continuam portáveis.
`validate_dossiers.py` e `check_site.py` também passaram. Não houve nova consulta
ao Neon nem à API nesta etapa.

No checkpoint v0.4.2b, antes de um teste de ingestão em branch Neon
**descartável**, havia sido especificada uma migração 002, ainda não criada
naquele momento, que deveria acrescentar pelo menos:

O schema 001 não basta: `ndq.observations` depende de `dossier_revisions`,
`ndq.events` exige ocorrência ou agendamento, e `ndq.actors.identity_state`
aceita apenas `name_only_unresolved`. Forçar a observação `dataHora=00:00` nesses
campos fabricaria uma ocorrência ou uma revisão editorial.

1. `source_snapshots` imutáveis com identidade da proposição, versão da projeção,
   hash do conteúdo selecionado, data da observação e cobertura por endpoint;
   unicidade por `(institution, namespace, official_id, projection_version,
   content_hash)`. A unicidade precisa ser garantida em transação SQL, não só no
   verificador Python.
2. `source_snapshot_items` com endpoint, locator, campos permitidos, fingerprint
   e data procedimental original. Colisões da chave composta de tramitação devem
   ser conservadas como linhas distintas e sinalizadas; a chave composta não é
   identidade oficial global. A URI de autor exige identidade de fonte separada
   dos atores legados `name_only_unresolved`.
3. Um vínculo entre cada tentativa/execução (`collection_runs`) e o snapshot
   deduplicado, inclusive para respostas vazias, falhas e rechecagens sem mudança.
   A execução pode ter histórico próprio, sem multiplicar fatos ou alertas.
4. Entidades separadas para diferenças entre snapshots, propostas editoriais e
   decisões de aprovação. `dossier_revisions` e `publication_states` seguem como
   histórico/publicação e não devem ser produzidos pela ingestão de fonte. Chaves
   de idempotência devem impedir alertas repetidos para o mesmo par de snapshots,
   caminho de campo e classificação.

A migração precisava impor append-only também nas novas tabelas de evidência, chaves
estrangeiras para a fonte/execução e validação de JSONB selecionado. No checkpoint
v0.4.2b, o SQL, o adaptador de escrita e testes de corrida/rollback não existiam. Um teste em
branch descartável dependerá de aprovação do contrato, alvo conferido e migração
versionada; não se deve apontar a suíte PostgreSQL restrita a `localhost/ndq_test`
para o Neon. Nenhuma mudança de schema havia sido aplicada naquele checkpoint.

**Próxima ingestão:** revisar um contrato versionado para observações de fonte,
identidade dos atores, evidência de correções e tipos de relação antes de propor
qualquer escrita. Exercitar o contrato em PostgreSQL local/branch descartável e
obter autorização específica para ingestão em desenvolvimento. O relatório desta
fase não deve ser aplicado automaticamente nem alterar aprovação/publicação.

## v0.4.2c — persistência de observações de fonte preparada

`002_source_observations.sql` foi criada sem alterar a migração 001. **Não foi
aplicada a nenhum banco nesta tarefa.** A coleta normal continua exclusivamente
em dry-run. A função `write_local_test_observation()` não é chamada pelo CLI e
exige `allow_local_test=True`, host loopback e banco `ndq_test`; recusa o Neon,
inclusive as branches descartável, de desenvolvimento e produção. Um modo de
escrita remota exigirá implementação e autorização específicas posteriores.

### Modelo e limites de armazenamento

| Objeto novo | Conteúdo e limite |
|---|---|
| `source_snapshots` | Identidade oficial da proposição, versão da projeção, hash dos campos oficiais selecionados, primeiro momento de observação e cobertura dos quatro endpoints. Restrição única da identidade de conteúdo. Nenhum estado editorial. |
| `source_snapshot_items` | Linhas selecionadas, categoria, posição determinística, chave candidata, locator, fingerprint, URL de evidência, data procedimental original e URI de autor quando disponível. Chaves compostas colidentes podem ocupar linhas diferentes. |
| `source_collection_attempts` | Uma linha por tentativa, inclusive conteúdo repetido, falha ou resultado parcial. Guarda a cobertura por endpoint, snapshot anterior válido, snapshot retornado e transição (`initial`, `unchanged`, `changed`, `returned`, `incomplete`, `failed`). Falhas/parciais não apontam para snapshot atual. |
| `source_attempt_items` | Campos selecionados válidos de uma tentativa incompleta, inclusive linhas com chave de evento ausente. São evidência da tentativa, sem criar snapshot factual nem substituir o último baseline completo. Linhas rejeitadas pela normalização deixam códigos de problema, não payload bruto. |
| `source_change_candidates` | Diferenças entre dois snapshots oficiais, com classificação, campo, valores antes/depois, URL de evidência e chave única de alerta. Não são eventos procedimentais. FKs exigem mesma identidade oficial e vínculo ao par da tentativa que primeiro identificou a diferença. |
| `source_editorial_proposals`, `source_editorial_decisions` | Estruturas separadas para uma etapa humana futura; o writer de fonte não insere nelas. Decisões são registros append-only, sem criação automática de `dossier_revisions` ou `publication_states`. |

As tabelas novas recebem o trigger de imutabilidade de 001; não permitem UPDATE,
DELETE nem TRUNCATE. A função SQL `source_fields_valid` restringe as chaves JSONB
dos itens à política `camara-selected-fields-v1`, inclusive o status aninhado.
`source_coverage_valid` exige as quatro categorias, metadados de completude,
contagens, páginas e problemas com limites. O writer revalida a projeção pela
normalização, compara chaves/fingerprints e conserva somente campos selecionados,
URLs oficiais, locators e códigos de erro sanitizados. Corpos brutos da API e
documentos não entram nessas tabelas. A URI de autor é identidade de fonte, sem
fusão com `ndq.actors` legados e sem inferir relatoria.

`dataHora` permanece texto original e semântica `procedural_source_date`. Não
alimenta `ndq.events.occurred_at`; `00:00` não é interpretado como instante exato.
`completed_at` é o fim informado da coleta; `recorded_at` é a gravação no banco.
As páginas dos quatro endpoints são buscadas antes da transação SQL e não compõem
uma fotografia atômica da instituição. O modelo preserva essa limitação.

### Transações, deduplicação e transições

O writer inicia **uma transação** para ler o último snapshot completo, inserir ou
reutilizar o snapshot, seus itens, a tentativa e os candidatos. Um advisory lock
transacional por identidade oficial ordena os writers cooperantes; a restrição
única `(institution, namespace, official_id, projection_version, content_hash)`
é a proteção final contra duplicação. `ON CONFLICT DO NOTHING` reutiliza conteúdo
idêntico. A chave do candidato inclui o par antes/depois, seção, item, campo,
classificação e motivo; a unicidade do banco impede repetir o mesmo alerta.
Falha de qualquer INSERT reverte todo o conjunto dessa tentativa; exceções do
driver são reduzidas a uma mensagem genérica sem DSN ou senha.

- **A→A:** duas tentativas, um snapshot, nenhuma nova revisão factual ou alerta.
- **A→B:** duas tentativas, dois snapshots e candidatos somente para campos
  selecionados que mudaram.
- **A→B→A:** a terceira tentativa aponta para o snapshot A já existente, marca
  `returned` e pode criar candidatos B→A; não apaga B. Repetir o mesmo par depois
  não duplica seus candidatos.
- **Completo→incompleto:** a tentativa parcial mantém cobertura e problemas,
  mas o último baseline completo continua vigente.
- **Incompleto→completo:** o novo resultado é comparado ao último completo,
  sem transformar itens omitidos na coleta parcial em exclusões.
- **Identidade de evento ausente/colidente:** linhas e fingerprints ficam separados;
  o resultado é incerto/conflitante. Mudança de data ou órgão com sequência
  conhecida gera possível correção para revisão, sem fusão automática.

O adaptador não grava `dossier_revisions`, `publication_states`, propostas ou
decisões editoriais. A tabela `source_collection_attempts` é independente de
`collection_runs` de 001 porque o coletor ainda não mede início exato da coleta;
inventar `started_at` violaria a proveniência. Integrar as duas trilhas exigirá
um contrato temporal explícito. A API é consultada fora da transação; a gravação
do relatório privado em arquivo também não participa dela.

### Testes e próximo ensaio isolado

Os testes offline verificam projeção, deduplicação de chave, A→B→A, endpoint
vazio, resultado parcial, recusa de alvo não local, erro sanitizado, preflight
somente leitura e o pin da migração 001 nos validadores antigos. Os testes de
integração adicionados à suíte **existente e restrita a `localhost/ndq_test`**
cobrem escrita, repetição, retorno de conteúdo, correção de despacho, evento novo,
data/órgão ambíguos, evento sem chave preservado na tentativa, colisão,
recuperação de parcial, rollback, FKs, triggers
append-only e ausência de efeitos editoriais. Continuaram em skip porque não há
banco PostgreSQL local configurado nesta sessão; portanto esses comportamentos
SQL **não foram executados nem validados**. Corrida real entre duas conexões e
ingestão Neon também permanecem sem ensaio. Há um teste adicional de corrida
real entre duas conexões, opt-in via `NDQ_SOURCE_CONCURRENCY_TEST_URL`: ele exige
`localhost/ndq_test` **já migrado até 002** e deixa apenas registros sintéticos
nesse banco local descartável. Deve rodar separado da suíte rollback-only,
com `NDQ_TEST_DATABASE_URL` indefinida. Não foi executado nesta sessão.

Checkpoint desta tarefa: `unittest discover` executou **171 casos: 144 aprovados,
zero falhas e 27 skips** (20 testes locais anteriores, seis novos rollback-only e
um teste de concorrência opt-in).
`build_site.py` e `check_site.py` passaram; os quatro HTMLs gerados permaneceram
idênticos byte a byte. Nenhuma conexão à API ou ao Neon foi feita.

Para a **próxima tarefa** de validação em Neon descartável:

1. No console Neon, criar uma branch **nova e descartável** a partir da branch
   persistente de desenvolvimento que contenha somente a migração 001 e os três
   pilotos. Conferir manualmente seu ID e endpoint; ambos devem diferir dos de
   produção e desenvolvimento. Não usar a URL do `.env` da raiz.
2. Preencher o arquivo ignorado `.env.ndq-validation` com
   `NDQ_DISPOSABLE_DATABASE_URL`, `NDQ_DISPOSABLE_BRANCH_ID`,
   `NDQ_DISPOSABLE_ENDPOINT_ID`, `NDQ_DEVELOPMENT_BRANCH_ID`,
   `NDQ_PRODUCTION_BRANCH_ID` e `NDQ_PRODUCTION_ENDPOINT_ID`. A URL deve ser do
   endpoint direto, banco `neondb` e TLS explícito. Nunca colá-la em relatórios.
3. Executar **somente o preflight de leitura** na raiz:

   ```powershell
   .\.venv\Scripts\python.exe -B -m tools.persistence.preflight_source_disposable_neon
   ```

   Ele exige TLS observado pelo libpq, transação read-only, hash de 001 correto,
   schema NDQ existente e 002 ainda ausente. O guard confere os IDs/endpoint
   informados localmente, mas **não verifica o ID da branch pela API Neon**.
4. Revisar um executor descartável ainda não habilitado, com confirmação manual
   do ID da branch antes de qualquer escrita. Só então, em tarefa autorizada,
   aplicar 002, ingerir uma observação de fonte selecionada e repetir A→A,
   A→B→A, parcial/recuperação, FKs e rollback. Conferir que os três dossiês,
   seus estados editoriais e páginas públicas não mudaram. No checkpoint anterior,
   o writer público bloqueava destinos Neon e não havia comando de aplicação remota.

O preflight é executável, mas **não foi executado contra Neon agora**. A prontidão
para escrita real depende de executar os testes SQL locais, corrigir eventuais
falhas, ensaiar concorrência em banco descartável e aprovar o guard de escrita
remota. Não dirigir a suíte local `ndq_test` à branch Neon.

### Executor descartável protegido — implementação posterior

`tools/persistence/validate_source_disposable_neon.py` implementa o ensaio
controlado previsto acima. **Não foi executado contra Neon nesta tarefa.** O modo
padrão é preflight read-only; `--apply` exige `--confirm-branch-id` com o ID `br-...`
efetivo da branch selecionada. O CLI da Câmara continua dry-run, e
`write_local_test_observation()` continua limitado a `localhost/ndq_test`. Apenas
o corpo transacional validado foi extraído para uma função interna reutilizada
pelo executor depois de guardas específicos; não há writer remoto público geral.

O destino vem somente de `.env.ndq-validation`, lido pelo guard existente com
interpolação desativada. A URL do `.env` da raiz é consultada apenas para recusar
coincidência de endpoint, nunca usada para conectar. O executor exige também o
guard ignorado `.env.ndq-development` para conferir separação do endpoint de
desenvolvimento e consistência dos identificadores protegidos. Recusa overrides
libpq `PGHOSTADDR`, `PGSERVICE` e `PGOPTIONS`, endpoint pooler, banco diferente de
`neondb`, TLS ausente e host efetivo libpq diferente do endpoint direto configurado.
O ID da branch não pode ser provado pela conexão PostgreSQL; sua confirmação no
console Neon continua uma verificação humana obrigatória.

Antes de escrever, uma conexão read-only confirma TLS, transação somente leitura,
checksum da migração 001 e ausência de 002/tabelas de fonte. O estado dos três
pilotos é capturado: IDs, identidades oficiais, hash e número da revisão, estado
`pending_review` e publicação `private_preview_only`. Uma **nova conexão** repete
os guardas e compara o estado dos pilotos. Sob advisory lock da migração, executa
somente o SQL de 002 e registra seu checksum no ledger em **uma transação**. Não
invoca o CLI genérico de migração nem altera 001.

Depois do commit de 002, o executor roda cenários selecionados de proposições
**sintéticas**, em IDs separados dos pilotos, com `synthetic_fixture=true` nas
tabelas de snapshots e tentativas. Verifica A→A, A→B, A→B→A, retorno repetido sem
duplicar candidato, parcial/recuperação, evento novo, data/órgão possivelmente
corrigidos, colisão da chave composta, rollback por falha injetada, FKs validadas
e rejeição SQL de UPDATE/DELETE/TRUNCATE. Duas conexões simultâneas competem pela
mesma identidade sintética e devem registrar um snapshot, duas tentativas e zero
candidatos. Por fim, uma sessão de leitura confirma apenas 001+002 no ledger e
que os três pilotos mantêm exatamente os mesmos registros e estados.

As requisições HTTP não participam do ensaio: ele usa somente dados sintéticos,
sem consulta à API oficial nem afirmação de fato público. A migração é atômica,
mas os cenários são **transações independentes** para permitir a corrida entre
conexões. Se o processo falhar após 002, a branch descartável pode conter 002 e
parte dos dados sintéticos; o relatório sinaliza `partial_changes_possible` e
não promete reversão global. Erros do driver são reduzidos a tipo e SQLSTATE
válido, sem URL, senha ou hostname. O relatório não contém textos sintéticos
completos nem DSN.

Comandos a executar **somente em tarefa posterior autorizada**, após conferir o
arquivo de configuração ignorado e o ID da branch no console Neon:

```powershell
.\.venv\Scripts\python.exe -B -m tools.persistence.validate_source_disposable_neon
$disposableBranchId = Read-Host 'ID br-... da branch descartável conferido no console Neon'
.\.venv\Scripts\python.exe -B -m tools.persistence.validate_source_disposable_neon --apply --confirm-branch-id $disposableBranchId
```

Após o ensaio, conferir o relatório, o ID da branch e descartá-la **pelo console
Neon**; não executar DROP no banco de desenvolvimento ou produção. A branch
descartável fica com 002 e dados sintéticos append-only enquanto existir.
O executor não apaga esses registros nem altera o site. A primeira aplicação real
de 002, os INSERTs, as restrições e a concorrência no Neon continuam **sem teste**
até essa execução autorizada. Um resultado de preflight não comprova sozinho o ID
da branch pela API Neon nem valida concorrência.

Checkpoint offline do executor: **180 casos, 153 aprovados, zero falhas e 27
skips PostgreSQL locais**. Os nove novos testes do executor exercitam confirmação
antes de conexão, destino efetivo/TLS, modo padrão read-only, seleção de 002,
mudança de estado da migração, baseline dos pilotos, variantes sintéticas e
saída sanitizada. `build_site.py` e `check_site.py` passaram; os quatro HTMLs
gerados mantiveram os mesmos bytes. Nenhum desses testes prova execução SQL no
Neon. O branch ID não é verificado por uma API do provedor.

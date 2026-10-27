# Observatório NDQ — v0.4.1: fundação de persistência privada

Implementação iniciada em 27/10/2026 e validação v0.4.1 posteriormente informada
pelo responsável pelo projeto. PostgreSQL é uma camada opcional de trabalho
interno. O site e seus três dossiês continuam gerados pelos mesmos JSONs e
templates da v0.3. Nada nesta fase consulta órgãos, publica páginas ou aprova
conteúdo automaticamente.

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
- Não existem descoberta semanal, coletores, retentativas, detecção remota de
  versões, armazenamento temporário de documentos ou workers. `official_collection`
  é uma opção de esquema, não uma coleta já implementada.
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

**Próximo marco planejado — v0.4.2:** definir o contrato de observação e implementar
um coletor experimental da Câmara dos Deputados com normalização e proveniência.
A v0.4.3 prevê detecção de mudanças e relações documentadas; a v0.4.4, descoberta
semanal e verificações D+1; e a v0.4.5, revisão editorial e publicação supervisionada.
A suíte de integração local ainda pode ampliar a cobertura SQL antes dessas etapas.

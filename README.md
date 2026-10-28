# Observatório NDQ

O **Observatório NDQ** é um serviço gratuito de interesse público associado à marca brasileira **Não de Queijo®**. Acompanha documentos oficiais sobre atividade legislativa, regulatória e de compras públicas que possa afetar a **inclusão alimentar** no Brasil. Esse tema abrange necessidades médicas e alimentares, como alergias, intolerâncias e doença celíaca, e escolhas éticas, filosóficas e culturais, incluindo o veganismo. “Plant-based” não equivale automaticamente a certificação vegana ou à ausência de alérgenos, glúten ou lactose.

O Observatório tem finalidade informativa e editorial própria; a página institucional da Não de Queijo permanece a apresentação da marca. O projeto defende a inclusão alimentar, sem alinhamento partidário. Afirmações devem ter fontes, datas e contexto verificáveis; lacunas e incertezas devem ficar explícitas. Um projeto de lei não é uma lei vigente, um edital não prova contratação ou entrega, e uma data prevista não prova que o evento ocorreu. A publicação de interpretações exige revisão humana.

> **Estado editorial:** o repositório contém o site estático e três dossiês piloto. Os registros são recortes documentados, sem coleta contínua. Conteúdo em revisão não deve ser publicado automaticamente; `noindex` não torna uma página privada.

## Estado do projeto

| Etapa | Entrega |
|---|---|
| **MVP v0.1** | Site estático integrado: home institucional preservada, seção Não de Queijo e portal do Observatório com três dossiês. |
| **MVP v0.2** | Registros estruturados com identidades, referências, fatos e eventos dos pilotos. |
| **MVP v0.3** | Renderização de seções factuais dos dossiês a partir dos registros estruturados; parte da prosa contextual continua editorial. |
| **MVP v0.4.1** | Fundação de persistência PostgreSQL, migração SQL versionada e importação idempotente dos três pilotos, validadas em uma branch descartável do Neon. A inicialização da branch persistente de desenvolvimento foi posteriormente reportada com `success=true`. |
| **MVP v0.4.2 — fase 1** | Coletor experimental da Câmara com normalização, comparação somente leitura e proposta privada. Uma execução real do piloto foi comparada ao JSON e à baseline Neon de desenvolvimento. A ingestão dos resultados ainda não foi implementada. |
| **MVP v0.4.2c — validação preparada** | Migração 002, writer de observações e executor de integração sintética com guard para branch Neon descartável. O CLI de coleta continua dry-run; o executor ainda não foi executado no Neon. |

A próxima etapa é definir o contrato de ingestão das observações e suas evidências, mantendo revisão humana antes de alterações no conteúdo.

## Tecnologias

- **Python 3.10+** para validação, geração estática e persistência; **Psycopg 3** e `python-dotenv` para os comandos de banco.
- **PostgreSQL 18 / Neon** no ambiente de banco informado para a validação; a migração usa SQL PostgreSQL sem depender de serviços exclusivos do provedor.
- **HTML, CSS e JavaScript** sem framework no site público.
- **Git e GitHub** para versionamento; **GitHub Pages** para servir o site estático.
- **GitHub Actions** é a solução planejada para agendamento e verificações futuras; não há rotina de coleta agendada implementada nesta etapa.

## Arquitetura e limites atuais

| Camada | Estado atual |
|---|---|
| **Coleta de fontes oficiais** | O CLI experimental da Câmara consulta quatro endpoints e prepara propostas locais. Ainda não existe agendamento, ingestão automática ou promessa de cobertura completa. |
| **Registros e persistência** | `observatorio/conteudo/registros/*.json` é a entrada estruturada dos pilotos. O adaptador valida esses arquivos e os importa para o schema privado `ndq`, com migrações em SQL. O banco não alimenta automaticamente o site. |
| **Histórico e relações** | Revisões, observações, identidades e evidências têm estruturas próprias e preservam proveniência. A migração 002 propõe snapshots imutáveis, tentativas separadas e candidatos de mudança. O writer público segue restrito ao banco de teste local; um executor isolado foi preparado somente para ensaio sintético na branch Neon descartável. Ingestão operacional e relações tipadas aprovadas continuam pendentes. |
| **Revisão editorial e publicação** | A importação marca os pilotos como `pending_review` e `private_preview_only`. Não há fluxo operacional de aprovação/publicação; avaliações anteriores não se tornam aprovação nova. |
| **Geração do site** | `tools/build_site.py` gera HTML estático a partir dos registros e de `observatorio/conteudo/editorial/*.json`. O navegador público não usa credenciais nem conexão com PostgreSQL. |

O dossiê é um objeto histórico em evolução, distinto de uma notícia isolada. Observação pelo NDQ, ocorrência oficial, publicação, vigência e data agendada são conceitos diferentes. Listas vazias nos pilotos indicam dados não carregados, não prova de ausência.

## Estrutura do repositório

```text
index.html, styles.css, assets/       Home institucional e identidade da Não de Queijo
nao-de-queijo/                        Seção da marca em construção
observatorio/index.html               Portal estático, busca e filtros
observatorio/dossies/<slug>/          Páginas geradas dos três dossiês piloto
observatorio/assets/                  CSS e JavaScript do portal e dos dossiês
observatorio/conteudo/registros/      Dados estruturados dos pilotos
observatorio/conteudo/editorial/      Prosa e templates editoriais
observatorio/conteudo/dossies/        Fixtures legadas usadas para regressão
tools/build_site.py                   Gerador do site estático
tools/render_dossiers.py             Renderização das seções factuais
tools/validate_dossiers.py           Validação dos registros
tools/check_site.py                   Checagem local de páginas e links
tools/persistence/                   Contrato, conexão, importador e verificadores
tools/persistence/migrations/         Migrações SQL versionadas; início em 001_foundation.sql
tools/persistence/source_observations.py Writer de fonte restrito a localhost/ndq_test
tools/persistence/validate_source_disposable_neon.py  Executor sintético descartável, preflight por padrão
tools/collectors/camara/              Observação da Câmara e comparação sem ingestão
tools/collectors/http.py              Cliente JSON com retentativas e paginação restrita
tools/test_*.py                       Testes offline e integração PostgreSQL opcional
requirements-db.txt                  Dependências dos comandos de banco
.env.example                         Modelo de configuração sem credenciais
```

As rotas são `/` (marca), `/nao-de-queijo/` (seção em construção), `/observatorio/` (portal) e `/observatorio/dossies/<slug>/` (dossiês). A seção da marca não anuncia retomada de produção comercial. Os fixtures em `observatorio/conteudo/dossies/` preservam regressões editoriais; não são a fonte atual das cronologias geradas.

## Desenvolvimento local

Execute os comandos a partir da raiz do repositório, em **PowerShell**. O ambiente virtual usa Python 3.10 ou superior:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-db.txt
```

Para rodar a suíte **sem banco**, deixe `NDQ_TEST_DATABASE_URL` indefinida. Os testes PostgreSQL de integração exigem explicitamente um banco local `ndq_test` em loopback e permanecem em *skip* sem essa variável; não os direcione ao Neon.

```powershell
Remove-Item Env:NDQ_TEST_DATABASE_URL -ErrorAction SilentlyContinue
.\.venv\Scripts\python.exe -B -m unittest discover -s tools -p 'test_*.py' -v
.\.venv\Scripts\python.exe -B tools/validate_dossiers.py
```

Para gerar e conferir o site localmente, depois servi-lo em `http://localhost:8000/`:

```powershell
.\.venv\Scripts\python.exe -B tools/build_site.py
.\.venv\Scripts\python.exe -B tools/check_site.py
.\.venv\Scripts\python.exe -B -m http.server 8000
```

O build atualiza os arquivos HTML gerados em `observatorio/`, mas não publica o site. Revise o conteúdo e os arquivos alterados antes de qualquer publicação. A home institucional, seu domínio (`CNAME`), logo e configuração de medição devem ser preservados.

### Conexão e comandos de banco

Com as dependências instaladas e `DATABASE_URL` de um ambiente autorizado configurada na raiz do repositório, o verificador faz consultas somente de leitura e relata o estado de TLS observado pelo libpq:

```powershell
.\.venv\Scripts\python.exe -B -m tools.persistence.check_connection
```

O CLI existente oferece validação local sem conexão, migração e importação dos pilotos. **`migrate` aplica todas as migrações pendentes, agora incluindo 002, e `import-pilots` sem `--dry-run` escreve no banco indicado por `DATABASE_URL`**; confira a branch e as permissões antes de executá-los. Uma variável de ambiente explícita tem prioridade sobre o `.env` da raiz nesses comandos. Os validadores históricos de inicialização permanecem limitados à migração 001.

```powershell
.\.venv\Scripts\python.exe -B -m tools.persistence import-pilots --dry-run
.\.venv\Scripts\python.exe -B -m tools.persistence migrate
.\.venv\Scripts\python.exe -B -m tools.persistence import-pilots
```

Para a inicialização controlada de uma branch persistente vazia, `tools.persistence.initialize_development_neon` oferece `--preflight` somente de leitura e `--apply` com confirmação do ID da branch. Os passos e as limitações estão em [IMPLEMENTACAO_MVP_v0.4.md](IMPLEMENTACAO_MVP_v0.4.md). Após a inicialização reportada, esse comando **não** serve para uma nova verificação da mesma branch: ele exige ausência do schema e da tabela de migrações.

### Coletor experimental da Câmara

Com o guard de desenvolvimento configurado, este comando consulta a API e compara o resultado com o registro local e o histórico PostgreSQL em sessão somente leitura:

```powershell
.\.venv\Scripts\python.exe -B -m tools.collectors.camara --proposition-id 2498778 --dry-run
```

Dry-run é o único modo. `--no-database` consulta a API sem carregar credenciais e compara apenas com os JSONs. Os relatórios são propostas `pending_review` em `.ndq-private/camara/`, ignoradas pelo Git; não atualizam banco, registro, editorial ou HTML. `--previous-proposal` compara **campos oficiais selecionados** com uma observação oficial anterior; o resumo curatorial e o despacho literal permanecem separados. O plano de ingestão no relatório é apenas uma simulação idempotente offline. A nota v0.4 documenta a política de retenção por endpoint, a cobertura limitada de comparação, a migração 002 ainda não aplicada e o preflight somente leitura de uma branch Neon descartável.

### Executor sintético na branch Neon descartável

O executor v0.4.2c usa exclusivamente a configuração ignorada `.env.ndq-validation`. Seu modo padrão é somente leitura:

```powershell
.\.venv\Scripts\python.exe -B -m tools.persistence.validate_source_disposable_neon
```

Após conferir **manualmente** o ID da branch descartável no console Neon e autorizar o ensaio, o modo explícito aplica somente a migração 002 e grava cenários sintéticos isolados dos três pilotos:

```powershell
$disposableBranchId = Read-Host 'ID br-... da branch descartável conferido no console Neon'
.\.venv\Scripts\python.exe -B -m tools.persistence.validate_source_disposable_neon --apply --confirm-branch-id $disposableBranchId
```

Esse comando **não foi executado nesta etapa**. Exige endpoint direto, TLS ativo, banco `neondb`, migração 001 íntegra, 002 ausente, IDs de produção/desenvolvimento distintos e a confirmação exata da branch. Não usa a URL do `.env` da raiz como destino. Uma execução bem-sucedida deixa a migração 002 e linhas marcadas como sintéticas na branch descartável; após revisar o relatório, descarte **somente essa branch** pelo console Neon, conferindo novamente seu ID. Não execute a suíte PostgreSQL restrita a `localhost/ndq_test` no Neon. Detalhes de transações e limitações estão em [IMPLEMENTACAO_MVP_v0.4.md](IMPLEMENTACAO_MVP_v0.4.md).

## Ambientes de banco e segredos

| Branch no Neon | Uso previsto |
|---|---|
| **Produção** | Ambiente protegido. Não usar para testes, migrações ou importações sem procedimento e autorização próprios. |
| **Desenvolvimento persistente** | Trabalho interno e histórico da v0.4.1; inicialização reportada como concluída. |
| **Validação descartável** | Testar migração e importações isoladamente; foi usada na validação inicial da v0.4.1. |

Essas são **branches de banco do Neon**: cada uma representa um estado de dados e schema. Uma **branch Git** versiona arquivos de código e conteúdo; trocar de branch Git não seleciona automaticamente uma branch de banco.

O `.env` da raiz guarda a `DATABASE_URL` local; `.env.ndq-validation` é usado pelo validador da branch descartável, e `.env.ndq-development` guarda apenas identificadores de proteção para o inicializador de desenvolvimento. Esses arquivos são ignorados pelo Git; [.env.example](.env.example) contém o modelo sem segredos. Nunca coloque URLs de conexão, senhas ou tokens em commits, relatórios, HTML gerado ou JavaScript do navegador.

## Validação conhecida e limites

No último checkpoint informado, **91 testes offline passaram** e **20 testes de integração PostgreSQL restritos a `localhost/ndq_test` ficaram em *skip***. Em uma branch descartável do Neon, a migração `001_foundation.sql` foi aplicada, os três dossiês piloto foram importados e a repetição não criou revisões. O relatório apontou **36 chaves estrangeiras validadas** e **3 reconstruções exatas dos dados**, além de conferências de identidades, referências de fontes e estados editoriais. A inicialização posterior da branch persistente de desenvolvimento foi informada com `success=true`.

Esses resultados foram informados nas etapas de validação da v0.4.1; não equivalem a testes de concorrência, de todas as restrições SQL, de futuras correções/revisões, de cobertura de fontes ou de coleta automática. Os três pilotos são recortes históricos, não constatações atualizadas em tempo real. O piloto da Câmara trata o PL 1777/2025 como proposta; o tema plant-based da Anvisa é uma observação da agenda; e o pregão da Fundepar registra edital e retificação, sem comprovar contratação ou entrega.

Na fase 1 da v0.4.2, uma coleta real recuperou sete tramitações, um autor e uma lista vazia de relacionadas para o piloto da Câmara. A identidade e o hash da baseline Neon de desenvolvimento coincidiram com o JSON local. A revisão editorial posterior confirmou que diferenças entre resumos curatoriais e despachos literais **não são correções de fonte**; uma lista vazia no endpoint é observação válida, sem provar ausência global de relações. A URI oficial do autor é preservada sem fusão com ator legado ou atribuição de relatoria. `dataHora`, inclusive `00:00`, é preservado como valor procedimental da fonte, sem afirmar ocorrência naquele instante. Nenhuma proposta foi ingerida. Na preparação do executor v0.4.2c, **153 testes offline passaram e 27 testes PostgreSQL locais ficaram em skip** (180 casos); não houve teste SQL, migração remota ou nova coleta ao vivo. Os testes com o relatório privado local são ignorados quando ele não está disponível. Paginação com várias páginas, relações não vazias e correções de fonte foram exercitadas por testes sintéticos, sem comprovação ao vivo desses cenários.

## Próximos marcos

1. **v0.4.2:** coletor e normalização implementados na fase 1; contrato de ingestão ainda pendente.
2. **v0.4.3:** detecção de mudanças e relações documentadas, com proveniência e revisão.
3. **v0.4.4:** descoberta semanal e verificações D+1 de datas oficialmente agendadas, com tratamento de resultados inconclusivos.
4. **v0.4.5:** fluxo operacional de revisão editorial e publicação supervisionada.

GitHub Actions é uma opção planejada para executar verificações agendadas; horários exatos de execução não devem ser tratados como garantia. Os marcos v0.4.3–v0.4.5 continuam planejados; não há publicação automática.

## Documentação

- [AGENTS.md](AGENTS.md): princípios editoriais, segurança e método de trabalho para agentes e contribuidores.
- [IMPLEMENTACAO_MVP_v0.2.md](IMPLEMENTACAO_MVP_v0.2.md): introdução dos dados estruturados e compatibilidade.
- [IMPLEMENTACAO_MVP_v0.3.md](IMPLEMENTACAO_MVP_v0.3.md): limites da geração factual e da prosa editorial.
- [IMPLEMENTACAO_MVP_v0.4.md](IMPLEMENTACAO_MVP_v0.4.md): schema, migração, importador, verificadores e procedimentos de banco.

As notas v0.4 preservam checkpoints anteriores à validação no Neon; a seção **Validação conhecida e limites** acima registra o estado v0.4.1 informado posteriormente.

**Princípio de engenharia:** automatizar descoberta e organização, preservar evidências rastreáveis e exigir revisão apropriada antes da publicação.

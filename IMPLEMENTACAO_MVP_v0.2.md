---
title: "Observatório NDQ — Implementação MVP v0.2: camada de dados"
version: "0.2"
date: 2026-10-27
status: implementado_em_branch_privada_pendente_aplicacao
project: "Observatório NDQ"
tags:
  - NDQ
  - observatorio
  - implementacao
  - dados
---

# Observatório NDQ — Implementação MVP v0.2

> [!important] Objetivo e limite
> Consolidar o **contrato de intercâmbio de dados**, mantendo a apresentação v0.1 aprovada. O esquema `ndq-dossier-0.2` é **provisório**: não representa banco de dados, coleta contínua homologada, nova conferência jurídica, revisão independente ou autorização para publicar.

## 1. Marco anterior e mudança de fase

- **MVP visual v0.1:** sete critérios funcionais/visuais aprovados pelo responsável do projeto.
- **MVP v0.2:** estrutura de dados dos três dossiês, referências e checagens automatizadas. É uma evolução **privada** da mesma branch.
- **Sem alteração da home original, navegabilidade, identidade visual ou três textos editoriais.** Os seis arquivos HTML gerados permanecem idênticos aos da v0.1 no teste local (hash SHA-256, mesmo conteúdo).

## 2. Organização do código

```text
observatorio/
  conteudo/
    dossies/                     # legado v0.1: fragmentos HTML editoriais aprovados
      pl-1777-2025.json
      anvisa-plant-based.json
      fundepar-pao-de-queijo-vegano.json
    registros/                    # NOVO: contrato estruturado de intercâmbio
      pl-1777-2025.json
      anvisa-plant-based.json
      fundepar-pao-de-queijo-vegano.json
  dossies/                        # páginas estáticas geradas
  index.html                      # listagem estática gerada
tools/
  build_site.py                   # MODIFICADO: lê metadados da v0.2, corpo editorial da v0.1
  check_site.py                   # MODIFICADO: inclui validação cruzada dos registros
  validate_dossiers.py            # NOVO: integridade, fonte, tempo, estados e evidência
  test_dossiers.py                # NOVO: testes negativos e regressão do contrato
```

> [!warning] Fase de transição declarada
> Os **cards** passam a usar os campos `presentation` dos registros normalizados; o conteúdo detalhado permanece temporariamente em `card_html`/`detail_html` da v0.1. Não há duplicidade ocultada: o validador exige igualdade dos metadados nos dois formatos. **Os blocos HTML ainda não são gerados integralmente a partir de fatos normalizados.** Eles não devem receber texto externo de coletores sem desenho de componentes/escape e revisão.

## 3. Contrato `ndq-dossier-0.2`

Campos do envelope: `schema_version`, `dossier_id`, `slug`, `kind`, `institution`, `scope`, `created_at`, `last_observed_at`, `publication_status`, `presentation`, `theme`, `official_identities`, `source_references`, `facts`, `events`, `official_status_observations`, `actor_roles`, `dossier_relations`, `deadlines`, `coverage_evidence`, `change_history`, `quality_states`, `editorial_assessment`, `provenance_note` e `type_data`.

### Invariantes aplicadas

1. **Identidade:** `dossier_id` estável e independente de `slug`; IDs oficiais têm instituição + namespace + valor. Sem fusão entre proposições/temas/compras por semelhança de título.
2. **Fonte:** fatos, eventos, papéis e mudanças referenciam IDs de fonte existentes; só URLs HTTPS. `observed_at` não substitui `occurred_at`, `published_at` ou vigência.
3. **Data:** horário da API da Câmara sem fuso explícito conserva `occurrence_timezone_status: not_provided_by_source`; não convertemos arbitrariamente para Brasília/UTC.
4. **Estado:** situação do órgão não se confunde com `transport_status`, `coverage_status`, `editorial_status` ou `publication_status`.
5. **Aprovação:** todos os registros continuam `private_preview_only`; revisão de protótipo **não** é revisão jurídica independente nem licença para publicação.
6. **Incerteza:** listas vazias significam **não carregadas neste piloto**, não ausência de eventos/documentos. O caso da Anvisa não equivale a RDC vigente; o edital Fundepar não comprova compra executada ou entrega.
7. **Dinheiro:** valores de edital guardados como **inteiros em centavos de real**, evitando arredondamento em operações financeiras (`885839000` e `885775000` centavos; delta `-64000`).
8. **Retificação:** `change_history` da Fundepar liga **documento original + retificação**, e não afirma mudança de item nem entrega.
9. **Sem espelho:** registros armazenam referências, localizadores e metadados, nunca cópias permanentes de PDFs oficiais.

## 4. Pilotos estruturados

| Tipo | Identidade estável | Evidência incorporada | Não homologado |
|---|---|---|---|
| Câmara | `ndq:camara:proposicao:2498778` | PL 1777/2025, situação consultada, fonte da ementa, autoria/relatoria, **7 movimentações** com data e referência | Vigência de lei, cobertura exaustiva, atualização automática |
| Anvisa | `ndq:anvisa:agenda:AR2627-3.7` | Tema plant-based, observação “Em andamento” datada de 27/10/2026, fonte da planilha | Norma vigente, consulta aberta, cronologia completa |
| PNCP/Fundepar | `ndq:pncp:22112109000153:2026:82` | Identidades PNCP/pregão, avisos de 20 e 22/05/2026, previsão de produto vegano, valores e **retificação de R$ 640** | Contrato assinado, entrega, fiscalização de execução |

**Procedência:** informações transpostas dos protótipos e arquivos de validação anteriores. Esta versão **não executa coletas novas**, não abre APIs e não confirma o estado presente de nenhum processo.

## 5. Como instalar na branch existente

O pacote `NDQ_MVP_Camada_de_Dados_v0.2_patch.zip` contém **apenas arquivos novos ou modificados**, sem pasta externa e sem `.git`.

1. Confirme que estás na branch de desenvolvimento: `git branch --show-current`.
2. Extraia o conteúdo do ZIP **na raiz do repositório `ndq`**, substituindo `tools/build_site.py`, `tools/check_site.py` e `README.md` quando solicitado. Os três arquivos antigos de `observatorio/conteudo/dossies/` permanecem inalterados.
3. Execute no PowerShell, na raiz:

```powershell
py -3 tools/validate_dossiers.py
py -3 tools/test_dossiers.py
py -3 tools/build_site.py
py -3 tools/check_site.py
py -3 -m http.server 8000
```

4. Confira novamente `/`, `/nao-de-queijo/`, `/observatorio/` e seus três dossiês.
5. Para gravar na branch: `git status`, `git add .`, `git commit -m "feat: estrutura dados dos dossies NDQ v0.2"`, `git push`.
6. **Não fazer merge para a branch publicada** enquanto o portal estiver em teste privado.

## 6. Testes já executados neste pacote

- Validação cruzada dos três registros: **PASS**.
- **12 testes automatizados**: aprovação dos casos, identidade, integridade das fontes, deriva editorial, publicação bloqueada, não confundir agenda com norma, não confundir edital com entrega, retificação monetária, precisão temporal, unicidade de eventos, impactos não comprovados e observação temporal. **PASS**.
- `build_site.py`: três dossiês e a home gerados. **PASS**.
- `check_site.py`: **seis HTMLs**, links locais, IDs, imagens, HTTPS, estados privados e registros estruturados. **PASS**.
- Comparação dos seis HTMLs com a v0.1: **sem diferenças de bytes**.

> [!note] Cobertura do teste
> Os testes confirmam integridade do pacote local; **não** validam cobertura de fontes externas, atualidade de links oficiais, qualidade jurídica ou acurácia de novos dados captados automaticamente.

## 7. Gate para a v0.3

Depois que esta camada for aceita, migrar progressivamente **seções do corpo dos dossiês** para componentes parametrizados por fatos/eventos/atores e suas fontes, criando casos de teste de atualização de dados. A conexão automática deve usar uma camada de ingestão de fontes **separada** do pipeline editorial e exigir reconciliação, revisão e autorização antes de alterar conteúdos públicos.

**Próxima entrega sugerida:** substituir primeiro o trecho de histórico do PL por eventos estruturados, usando os sete movimentos como casos de regressão, sem modificar o texto aprovado; depois Anvisa e Fundepar. Só mais tarde decidir persistência/banco e automação.

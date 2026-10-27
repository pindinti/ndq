---
title: "Observatório NDQ — Implementação do MVP v0.3"
project: Observatório NDQ
version: "0.3"
date: 2026-10-27
status: "implementado_em_pacote_privado_pendente_aceite_local"
tags:
  - observatorio-ndq
  - mvp
  - implementacao
  - dossies
---

# Observatório NDQ — MVP v0.3: dossiês orientados a dados

> [!important] Escopo e estado
> Este pacote é **incremental sobre a v0.2** e trabalha **somente com registros já documentados** na fase de validação. Nenhuma fonte oficial foi recolhida automaticamente nesta etapa. **Não há publicação ao vivo, detecção contínua de mudanças, avaliação de vigência ou homologação do conteúdo para lançamento público.** A revisão visual/funcional da v0.3 no computador do responsável continua necessária.

## 1. Decisão de implementação

A v0.1 reuniu os três protótipos em um site estático. A v0.2 criou `observatorio/conteudo/registros/*.json`, mas deixou cronologias, fontes, pessoas e valores repetidos no HTML herdado. A **v0.3 troca essas cópias por componentes gerados** a partir dos registros estruturados, mantendo os **textos explicativos e avaliações editoriais em templates aprovados**.

```text
observatorio/conteudo/
├── registros/                 Dados estruturados com fontes, datas e estados
│   ├── pl-1777-2025.json
│   ├── anvisa-plant-based.json
│   └── fundepar-pao-de-queijo-vegano.json
├── editorial/                 Narrativas curadas + marcadores para dados
│   ├── pl-1777-2025.json
│   ├── anvisa-plant-based.json
│   └── fundepar-pao-de-queijo-vegano.json
└── dossies/                   Fixtures legadas v0.1, preservadas para validação

tools/
├── build_site.py              Gera páginas e cards a partir de registros + templates
├── render_dossiers.py         Gera blocos factuais e mantém tipos de evento separados
├── validate_dossiers.py       Impede inconsistências, referências ausentes e publicação
├── test_dossiers.py           12 testes existentes da v0.2
├── test_render_dossiers.py    16 novos testes de renderização da v0.3
└── check_site.py              Confere links e estrutura HTML gerada
```

Não há dependências adicionais: o **gerador e os validadores usam só a biblioteca padrão do Python 3.10+**. Não mudamos o site base, as rotas, os estilos de interface, o serviço de hospedagem, a home institucional ou a seção Não de Queijo.

## 2. O que agora é gerado dos registros

| Componente do dossiê | Origem na v0.3 | Observação de fidelidade |
|---|---|---|
| Linha do tempo — Câmara | `events[]` | **7 movimentações** observadas; horário da API sem fuso confirmado |
| Marcos de contexto — Anvisa | `contextual_milestones[]` | **5 marcos** institucionais/NDQ, **não** movimentos processuais completos |
| Evolução documental — Fundepar | `events[]` + `contextual_milestones[]` | **2 documentos oficiais** + 1 comunicação + 1 **data apenas prevista** |
| Fontes oficiais | `source_references[]` | URLs, rótulos, estado da análise e referências; HTML escapa texto dinâmico |
| Autoria e relatoria — Câmara | `actor_roles[]` | Com data e referência; não infere partido histórico |
| Dados dos cards e faixa informativa | `presentation`, `facts`, `type_data`, observações | O timestamp da observação **não** é tratado como data do ato |
| Estado na barra lateral | `official_status_observations[]`, quando disponível | Fundepar usa **estado documental**, não status atual de licitação inferido |
| Valor retificado da Fundepar | `notice_versions[]` + `change_history[]` | Diferença calculada como **R$ 640 a menos** e verificada em centavos |
| Categorias alimentares da Fundepar | `type_data.item_requirements[]` | Seis categorias derivadas do aviso previamente examinado |

> [!note] Campos que continuam editoriais
> Headline explicativa no topo do dossiê, parágrafos de contexto, interpretação, perguntas em aberto, qualificações jurídicas e avisos de cautela permanecem em `observatorio/conteudo/editorial/*.json`. Eles **não são gerados livremente de APIs nem pela IA**. A migração para componentes totalmente estruturados **não está concluída**; não afirmar que o dossiê é inteiramente automático.

## 3. Semântica: três cronologias que não são iguais

**PL 1777/2025:** as sete entradas possuem identidade de evento local, sequência quando obtida, data/hora observada, origem e link de conferência. Não existe confirmação de fuso horário para os horários da API. A sequência de registros não prova completude histórica.

**Anvisa AR2627-3.7:** em `events[]`, **zero** eventos de tramitação formal normalizada. Os cinco `contextual_milestones[]` são referências institucionais selecionadas ou observação do NDQ. Marcos com precisão mensal (`2021-10`, `2025-12`) **não recebem dia inventado**. O registro de 27/10/2026 é uma **observação do Observatório**, não um novo ato da agência. “Em andamento na agenda” não equivale a consulta pública aberta nem a norma final.

**Fundepar, Pregão SRP 669/2026:** os avisos de 20/05 e 22/05 constituem entradas documentais; a notícia governamental de 27/05 é contexto institucional; e a data de 11/06 é **data prevista de disputa**, sem prova nesta revisão de que a sessão foi realizada. O valor máximo foi corrigido de R$ 8.858.390,00 para R$ 8.857.750,00, sem demonstrar adjudicação, compra ou entrega.

## 4. Proteções e critérios de geração

O validador recusa:

- Publicação fora de `private_preview_only`, uso de template sem estado aprovado ou descompasso entre registros e fixtures anteriores.
- Referência documental ausente/sem HTTPS, identificadores e eventos duplicados e alterações monetárias sem consistência com os avisos.
- Marco previsto que passa a declarar `occurred_at`; marco com apenas mês que recebe um dia arbitrário; evento contextual tratado como efeito normativo.
- Substituição automática do estado de agenda por regra vigente; declaração de fornecimento efetivamente entregue sem revisão documental.
- Interpolação direta de textos dinâmicos no HTML sem escape, marcador desconhecido ou duplicado.

Esses controles têm escopo **dos três pilotos**. Não representam controle universal de atos oficiais, infalibilidade do conteúdo, auditoria jurídica nem proteção de acesso ao repositório.

## 5. Como aplicar na mesma branch

1. Confirme que a **v0.2** está instalada, com seus testes funcionando.
2. Mantenha a branch de desenvolvimento ativa; não faça merge na `main`.
3. Extraia o arquivo `NDQ_MVP_Dossies_Orientados_a_Dados_v0.3_patch.zip` **na raiz do repositório `ndq/`**, substituindo os arquivos indicados no pacote. **Não apague `.git`, `assets/`, `CNAME` ou `index.html`**.
4. Execute no terminal PowerShell, na raiz do repositório:

```powershell
py -3 tools/validate_dossiers.py
py -3 tools/test_dossiers.py
py -3 tools/test_render_dossiers.py
py -3 tools/build_site.py
py -3 tools/check_site.py
py -3 -m http.server 8000
```

5. Confira `http://localhost:8000/observatorio/`, abra cada dossiê completo e verifique especialmente linhas do tempo, valores, links oficiais, versão mobile e presença do aviso **“PRÉVIA PRIVADA”**.
6. Para parar o servidor: `Ctrl + C`. Somente após conferência local e aprovação editorial, faça commit/push **na branch de desenvolvimento**:

```powershell
git status
git add .
git commit -m "feat: renderiza dossies a partir de dados estruturados v0.3"
git push
```

O comando `build_site.py` deve sempre ser executado **depois** de alterar um registro ou template: o GitHub Pages serve os **HTMLs gerados**, não executa Python no servidor.

## 6. Testes executados na preparação do pacote

- **12 testes anteriores** da v0.2: aprovados.
- **16 testes novos** da v0.3: aprovados.
- **Validação de referências e metadados:** três registros, nove eventos formais, quinze referências estruturadas e sete marcos de contexto/NDQ: aprovados no recorte do piloto.
- **Teste de geração e integridade do site:** seis páginas HTML, links locais, IDs, fontes e estados privados: aprovado.
- **Renderização em Chromium:** desktop 1440 px e mobile 430 px para os três dossiês, alternância card/dossiê e sem rolagem horizontal: aprovado no ambiente de preparação.

A **revisão no Windows/VS Code do usuário** e a reconferência das fontes antes do lançamento público continuam pendentes.

## 7. Limites e próximo marco

- Não existem coletores em execução nem atualização automática do conteúdo no portal.
- As páginas permanecem snapshots editoriais de 27/10/2026 e algumas explicações mencionam explicitamente esse recorte.
- O conteúdo nas fixtures legadas permanece intencionalmente como baseline de regressão; o `build_site.py` **não copia seus blocos dinâmicos** para a página.
- `noindex` é somente um sinal a mecanismos de busca; **não torna uma publicação em GitHub Pages privada**.
- O modelo `ndq-dossier-0.2` é provisório. Não foi escolhido banco de dados nem infraestrutura de processamento contínuo.

**Próximo marco sugerido — v0.4:** definir um contrato estável de importação de observações dos validadores (sem atualizações externas em produção), exercer um caso real de diff com proveniência, e implantar a fila de revisão editorial. Só então considerar atualização supervisionada dos dossiês.

> **Critério de aceite da v0.3:** após copiar o patch e gerar o site na branch, os três dossiês mantêm o visual aprovado e suas seções factuais passam a refletir os registros estruturados, preservando as fronteiras entre fatos, contextos, datas previstas e inferências não comprovadas.

# Não de Queijo · MVP integrado do site v0.1

> **Ambiente de revisão. Não publicar automaticamente.** A home institucional existente foi preservada. As novas páginas e dossiês são prévias editoriais com dados observados em **27/10/2026**, sem atualização contínua.

## Estrutura

```text
/                          Home institucional laranja existente
/nao-de-queijo/             Página em construção: auditoria da marca e possível reabertura
/observatorio/              Home do Observatório NDQ, busca e filtros
/observatorio/dossies/
  pl-1777-2025/             Dossiê legislativo (Câmara)
  anvisa-plant-based/       Tema da Agenda Regulatória (Anvisa)
  fundepar-pao-de-queijo-vegano/  Contratação pública (Fundepar/PNCP)
/observatorio/conteudo/dossies/*.json  Fixtures editoriais privadas
/observatorio/assets/      Estilos e scripts compartilhados
/tools/build_site.py        Geração dos HTMLs estáticos a partir das fixtures
/tools/check_site.py        Checagens de integridade local
```

### Preservação

- `CNAME` continua `www.naodequeijo.com.br`.
- `assets/ndq-logo.png` está intacto.
- A home original mantém seu conteúdo, slogan, fundo laranja, Google Analytics e Cloudflare Insights; só recebeu os dois links de navegação e seus estilos complementares.
- Nenhuma dependência npm, framework, serviço pago, autenticação ou banco de dados foi introduzido.

## Testar sem publicar

1. Crie uma **branch de desenvolvimento** no seu repositório antes de aplicar os arquivos. **Não faça merge nem push na branch publicada** antes de revisar.
2. Extraia este pacote **sobre a pasta do repositório**, sem apagar sua pasta `.git` existente.
3. Abra o terminal na raiz e execute:

```powershell
py -3 tools/build_site.py
py -3 tools/check_site.py
py -3 -m http.server 8000
```

4. Acesse `http://localhost:8000/`, clique nos dois links e entre em `http://localhost:8000/observatorio/` para verificar filtros, busca e navegação.
5. Cada card abre diretamente a visualização completa de seu dossiê (`#dossie`); na página é possível alternar para o card e controlar as **Notas internas**.

> A marcação `noindex` nos novos HTMLs desencoraja indexação, mas **não torna páginas públicas privadas**. Para manter esta etapa privada, trabalhe na branch local e não a publique no GitHub Pages. A home já existente no domínio não foi alterada por este pacote até você aplicar e publicar.

## Onde editar o conteúdo

Cada dossiê tem um arquivo-fonte em `observatorio/conteudo/dossies/<slug>.json`:

- Os campos `title`, `description`, `eyebrow`, `status`, `relevance`, `theme`, `verified_at` alimentam a home e a listagem.
- `card_html` e `detail_html` preservam os blocos editoriais dos **protótipos aprovados**, agora mantidos como conteúdo separado do shell de navegação e gerados em HTML estático.
- `evidence_model` inclui metadados e exemplos de eventos/alterações onde já temos material estruturado. Esses campos ainda **não são usados para garantir atualização da página** e não representam o modelo definitivo de banco.
- `publication_status: private_preview_only` e `editorial_status: prototype_approved_for_private_integration` permanecem internos.

Após editar um fixture, execute `py -3 tools/build_site.py` e depois `py -3 tools/check_site.py` para atualizar as páginas estáticas.

**Importante:** o gerador trata os blocos `card_html` e `detail_html` como **HTML editorial confiável** dos três protótipos. Eles não devem receber texto externo sem sanitização. A próxima fase deverá normalizar os blocos de conteúdo, evidências, eventos e versões antes de conectar coleta automática.

## Situação editorial

O MVP visual está integrado, mas **não está homologado para publicação** nem para atualizações automáticas. Permanecem pendentes reconferência das fontes na data da publicação, revisão editorial, cronologia do Senado, descoberta FNDE, avaliação de cobertura, controle de versões e conexão com validadores.

O dossiê Fundepar distingue edital e retificação documentados de execução/entrega. O PL não é lei vigente e o tema plant-based na agenda não é regulamentação aprovada. As datas exibidas pertencem à última observação de outubro de 2026.

## Critério de aceite desta entrega

- [x] Home original e domínio preservados
- [x] Dois destinos navegáveis
- [x] Home do Observatório com filtros e busca
- [x] Três dossiês completos navegáveis
- [x] Navegação em desktop e mobile; sem rolagem horizontal nos testes
- [x] Fixtures e código de interface mantidos separadamente
- [x] Sem publicação/integração de produção
- [ ] Revisão de conteúdo antes de publicação pública
- [ ] Modelo plenamente normalizado e conexão com fontes oficiais

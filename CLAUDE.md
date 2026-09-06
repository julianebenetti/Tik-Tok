# Espião TikTok Shop — Moda Feminina

Ferramenta pra decidir que vídeo gravar como afiliada do TikTok Shop: o que está
vendendo, em qual loja, com que comissão, e qual formato de vídeo funcionou.

Projeto próprio, **sem relação com a mentoria MGD-Benetti**.

## Como está montado
- `espiao-tiktok-moda.html` — a página. Roda sozinha, lendo o Supabase direto.
- `kalodata-api.py` — cliente dos dez endpoints da API do Kalodata.
- `tiktok-sync.py` — importa os exports em CSV, pra quando não houver crédito de API.
- `dados-kalodata/` — onde os CSV são largados.
- `.github/workflows/tiktok-sync.yml` — roda o import a cada commit nessa pasta.

## Permissões do Supabase (importante)
A chave anônima viaja dentro do HTML, que é servido em endereço público no VPS.
**Não dá pra escondê-la** — deixar o repositório privado não resolve nada, porque
qualquer pessoa que abrir a página vê a chave no código-fonte. O que dá é limitar o
que essa chave permite:

- Leitura liberada em todas as tabelas `tiktok_*`.
- Escrita só onde a página tem botão que salva: status de produto, roteiro e
  tendência, marcar radar, anotar e apagar vídeo, adicionar e apagar perfil.
- `tiktok_lojas`, `tiktok_categorias` e `tiktok_importacoes` são somente-leitura pro
  anônimo: quem escreve nelas é o sync, com a chave de serviço.
- Apagar produto foi trocado por **descartar** (muda o status). O histórico fica e a
  chave da página não precisa de permissão pra destruir linha.

Se um dia isso precisar ficar realmente fechado, o caminho é login na página com
Supabase Auth, não esconder a chave.

## Onde roda
Duas opções, a mesma base de código:

- **VPS da Hostinger** (preferida): `vps/README.md` tem o passo a passo. Chaves num
  `.env` com `chmod 600`, varredura semanal por cron, logs em `logs/`. Os scripts só
  usam a biblioteca padrão do Python 3, então não há `pip install` — exceto `openpyxl`,
  e só se for importar export em `.xlsx`.
- **GitHub Actions**: workflow com botão em Actions, sem agendamento (cada chamada
  gasta crédito do Kalodata). Chaves como secrets do repositório.

## Secrets do GitHub Actions
`KALODATA_KEY`, `SUPABASE_URL` e `SUPABASE_KEY`. A chave do Kalodata é mostrada uma
única vez e gerar outra invalida a anterior na hora — ela nunca vai pro repositório
nem pra print.

- Página `espiao-tiktok-moda.html`, publicada no VPS da Hostinger. Lê o Supabase
  `tkxkrbdvcctoajuigvvv`, o mesmo que a AfiliDash usa, mas em tabelas próprias
  com prefixo `tiktok_` — nada aqui toca as tabelas `garimpo_*` ou `shopee_*`.
- A Juliane é afiliada do TikTok Shop e usa esse espião pra decidir que vídeo gravar.
- **Projeto separado da mentoria MGD-Benetti.** Nada deste repositório deve voltar
  pra lá; são operações diferentes.
- Tabelas: `tiktok_tendencias`, `tiktok_produtos`, `tiktok_videos`, `tiktok_roteiros`,
  `tiktok_criadores`. Namespace `tk*` no JS da página, nunca misturar com `garimpo*`.
- `tiktok_produtos.fonte`: `pesquisa` = veio da varredura automática (sem comissão/link,
  o nome é a categoria campeã); `affiliate_center` = a Juliane colou o produto real do
  Centro de Afiliados do TikTok Shop; `manual` = digitado à mão.
- Piso de comissão: R$9,00 por venda. Abaixo disso não entra.
- Nunca buscar nem inserir suplemento, vitamina, colágeno ou whey. A Juliane não
  anuncia suplementação, mesmo que o produto passe em todos os outros critérios.
- **Nunca inventar** @ de criador, link de vídeo, nome de seller, número de vendas ou
  comissão. Sem dado verificado, o campo fica NULL.
- Rotina diária `Espião TikTok Shop — Moda Feminina (diário)`, 7h30 (Brasília). Se mudar
  critério de busca no futuro, atualizar o prompt dela também, não só fazer busca manual.

### Dados reais de venda (Kalodata)
- O TikTok não abre faturamento de outro vendedor, e a API oficial de afiliado só cobre a
  própria vitrine. Quem tem GMV por loja/produto é o **Kalodata** (estimativa a partir de
  sinais públicos, **não é dado oficial** — sempre deixar isso claro na tela).
- Fluxo alternativo, sem API: a Juliane exporta Lojas e Produtos do Kalodata → larga em
  `dados-kalodata/` → o workflow `.github/workflows/tiktok-sync.yml` roda o
  `tiktok-sync.py` → grava em `tiktok_lojas` e `tiktok_ranking_produtos`.
- Cada import é um snapshot por `data_ref` + `periodo`. Nunca sobrescrever histórico:
  a página mostra só o snapshot mais recente, mas o passado fica pra comparar evolução.
- `tiktok-sync.py` casa as colunas por apelido (pt-BR e inglês) e aceita `R$ 1.234,56`,
  `987,4 mil`, `1.2M`. Coluna não reconhecida vai pra `colunas_ignoradas` em
  `tiktok_importacoes` — é lá que se descobre o que falta mapear. Use `--dry-run` antes.
- Secrets do GitHub Actions: `SUPABASE_URL` e `SUPABASE_KEY`.
- **O Kalodata tem API oficial** (Centro Aberto): `POST https://www.kalodata.com/openapi/v1/…`,
  JSON, chave secreta no cabeçalho, limite de 100 chamadas a cada 10 segundos, e é **paga por
  chamada** (a conta tem saldo de créditos). Aceita `region: BR`, `language: pt-BR`,
  `currency: BRL` e `date_range: last7Day`. O cliente é o `kalodata-api.py`.
  Padrão dos caminhos: `/tiktok/<familia>/detail` e `/tiktok/<familia>/rank`, para as
  famílias video, product, shop, creator, category e live. Confirmados na doc:
  `video/detail`, `video/rank`, `shop/detail`, `shop/rank` e `product/detail`; os demais
  seguem o padrão mas ainda não foram vistos, e estão marcados como "provável" no
  dicionário ENDPOINTS.
- `product/detail` já entrega `video_revenue` e `live_revenue` separados, então o split
  que decide se vale gravar vem pronto da fonte, sem precisar calcular.
- `commission_rate` vem **em pontos percentuais** (1.0 = 1%), não em fração. A comissão em
  reais sai de `unit_price` vezes essa taxa.
- **O `sort_field` do `/product/rank` resolve os dois filtros manuais**: ordenar por
  `video_revenue` traz quem vende por vídeo em vez de live, e por `commission_rate` traz
  quem paga melhor. Ordenar por `launch_date` acha produto recém-lançado, antes de saturar.
  A lista completa está em ORDENACOES_PRODUTO no `kalodata-api.py`.
- Cuidado: `product_review_count` é **quantidade** de avaliações, não nota. A API de produto
  não devolve nota.
- **A regra de curadoria virou filtro de API.** O `/product/rank` aceita `is_affiliate: 1`
  (só o que dá pra afiliar), `commission_rate: ">=15"` e `unit_price_range: "60-1000"`.
  Combinados, garantem o piso de R$9: 15% de R$60 dá exatamente R$9. Está na constante
  CURADORIA do `kalodata-api.py`, ligada pela opção `--curadoria`.
- Outros filtros úteis do `/product/rank`: `delivery_type: "local"` (envio nacional, chega
  mais rápido), `launch_date: "<7"` (lançado nos últimos 7 dias, onda subindo — note que
  como **filtro** ele usa esse formato de dias, e como campo de **ordenação** é data),
  `is_tts_product`, `video_id` (produtos de um vídeo), `livestream_id` e `need_all`.
- `need_extra: true` traz `seller_name` e `sku_count`, então o nome da loja só aparece
  no ranking com essa flag ligada.
- **Os endpoints de categoria recusam `lastDay`** e intervalos de data naturais. Aceitam só
  last7Day, last30Day, last90Day, last180Day e last365Day. O `periodo_api` trata isso
  separado, senão a chamada falha.
- **Criador**: o `creator/detail` devolve `creator_contact_email`, `whatsapp`, `line`,
  `facebook`, `zalo` e `ins`. O `montar_criador` **não copia nada disso de propósito** —
  é dado pessoal de terceiro que não consentiu, e não faz falta pra decidir o que gravar.
  Fica só o @, que é identidade pública. Se alguém for mexer nesse mapeamento, manter assim.
- No `creator/rank`, `creator_type` filtra INDEPENDENT no **servidor**, então não se gasta
  chamada trazendo criador de loja pra descartar depois. `followers_range` e
  `engagement_rate` (LOW <8%, MEDIUM 8-20%, HIGH >20%) permitem comparar com criador do
  mesmo tamanho — comparar com quem tem milhões de seguidores não diz nada.
- Nesse endpoint `category_ids` está **obsoleto**; use `category_id_list`, que é string
  com os ids separados por vírgula.
- O criador traz `video_gpm` e `live_gpm` separados, que é a régua pra Juliane comparar o
  próprio desempenho com o dos pares. E `creator_status` separa INDEPENDENT (afiliado
  independente, o par dela) de BELONGED_TO_SELLER (criador da própria loja).
- A categoria é a chave de tudo: o `category_id` de moda feminina vira filtro em produto,
  loja e vídeo. `--categorias` lista todas e destaca as de moda. A resposta ainda traz
  `top3_shop_revenue_ratio`, que é concentração — categoria pouco concentrada é mais fácil
  de entrar — e o split vídeo x live no nível da categoria inteira.
- Para filtrar por categoria (`category_ids`) é preciso descobrir o id de "Roupas
  femininas e roupas íntimas femininas". Esse id vem dos endpoints de `category`, que
  ainda não foram documentados aqui.
- **Limite de taxa é por endpoint**: os `/detail` aceitam 100 chamadas a cada 10 segundos,
  os `/rank` só 10. O cliente controla isso por caminho, com 20% de folga.
- **A API separa a receita da loja por canal**: `affiliate_revenue`, `self_account_revenue`
  e `shoppingmall_revenue`. A fatia de afiliado é o filtro que importa — loja que fatura
  com afiliado é loja acostumada a trabalhar com criador, e é onde a Juliane consegue
  entrar. Dá pra ordenar o ranking direto por `affiliate_revenue`. A página mostra isso
  na coluna "Afiliado" (verde ≥40%, amarelo ≥15%, vermelho abaixo).
- `shop/detail` ainda traz `top3_product_ids`, `creator_number` (quantos criadores já
  disputam a loja), `video_number`, `live_number` e `seller_type` (BRAND ou RETAILER).
- O `/tiktok/video/rank` limita a janela a 30 dias e traz até 100 linhas por chamada.
  Aceita filtrar por `product_id` (é o equivalente à aba Vídeo e Ads da interface),
  `shop_id`, `creator_id`, `category_ids`, `keyword`, faixa de receita, faixa de
  seguidores, faixa de ROAS e `is_ai_video`. Ordena por qualquer campo numérico da
  resposta via `sort_field`.
- Nunca fazer uma chamada por produto — sempre usar os endpoints de lista, senão o crédito
  evapora. `--testar` gasta exatamente 1 chamada.
- **A chave de API do Kalodata é mostrada uma única vez** e gerar outra invalida a anterior
  na hora. Ela vive só como secret `KALODATA_KEY` no GitHub Actions — nunca no repositório,
  nunca em print, nunca colada em conversa. Chave que apareceu em print está queimada e
  precisa ser regerada.
- A doc não diz o nome do cabeçalho da chave. O `--descobrir-cabecalho` testa os nomes
  prováveis e identifica o certo: com saldo zerado, um erro de crédito já prova que a
  autenticação passou, porque a checagem de saldo vem depois da de chave.
- O campo `video_gpm` da API é a receita por mil visualizações já calculada. É o número que
  substitui a conta manual de quanto vale o alcance.
- O `tiktok-sync.py` importa três tipos de export e descobre qual é pelas colunas:
  **Ranking de Produto** (`Informações do produto`, `Receita`, `Itens Vendidos`,
  `Taxa de comissão`…), **Marcas e Lojas**, e **Vídeo e Ads** (`Conteúdo de vídeo`,
  `Visualizações`, `Data de publicação`) — esse último vai pra `tiktok_videos`, e o
  script marca `anuncio = true` quando a legenda tem "AD".
- **A métrica que decide se vale gravar** é o split de receita do produto:
  `receita_live` / `receita_video` / `receita_cartao`. Produto com quase tudo em live
  não vende por vídeo gravado, por mais que o GMV total seja alto. A página mostra isso
  na coluna "Vídeo" (verde ≥30%, amarelo ≥10%, vermelho abaixo disso).
- Comissão do TikTok Shop costuma ser 10% nos campeões de moda feminina, o que em ticket
  de R$20 a R$65 fica **abaixo do piso de R$9**. Filtrar por `Taxa de comissão` e por
  ticket mais alto no Kalodata, senão o ranking vem cheio de produto que não paga.
- **Cauda longa**: nos 8 vídeos que mais faturaram a Calça Pantalona entre 30/08 e 05/09,
  nenhum foi postado dentro dessa janela — o mais novo tinha 18 dias, o mais velho 66,
  média de 38. Vídeo de moda continua vendendo por semanas, então a estratégia é acumular
  volume, não caçar viral. Isso está registrado como tendência no banco.
- **Referência de conversão** medida no top 8 de vídeos da Calça Pantalona (30/08~05/09):
  261.830 visualizações geraram 252 vendas, ou seja **0,096%, ~1 venda a cada 1.000 views**,
  e isso variou pouquíssimo entre vídeos (0,088% a 0,104%). Serve de régua: alcance é o que
  muda o resultado, não a conversão. Quem decide o quanto sobra é a comissão — a R$5,65 por
  venda dá ~R$5,44 por 1.000 views; com 20% de comissão o mesmo alcance dobra pra ~R$10,88.

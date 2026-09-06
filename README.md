# Espião TikTok Shop — Moda Feminina

Descobre o que está vendendo em moda feminina no TikTok Shop e transforma isso em
roteiro de vídeo. Feito para quem trabalha como afiliada e precisa decidir, toda
semana, o que gravar.

## O que a página mostra

- **Tendências** com fase (subindo, no pico, saturando) e a evidência de cada uma.
- **Maiores vendedores**, com a fatia do faturamento que vem de afiliado. Loja que
  vive de criador é onde há espaço para entrar.
- **Ranking de produtos**, com a fatia da receita que vem de vídeo e não de live, e a
  comissão em reais marcada quando fica abaixo do piso de R$9.
- **Vídeos que viralizaram**, com faturamento, vendas e vendas por mil visualizações.
- **Roteiros** com gancho, script marcado segundo a segundo e CTA.
- **Perfis monitorados**.

## De onde vem o dado

Da API do Kalodata, que estima os números a partir de sinais públicos do TikTok.
**Não é dado oficial da plataforma** — serve para comparar e ver tendência, não como
faturamento auditado. A página deixa isso explícito em cada tabela.

## Rodando

```bash
export KALODATA_KEY="..."        # nunca commitar
export SUPABASE_KEY="..."

python kalodata-api.py --descobrir-cabecalho   # acha o nome do cabeçalho da chave
python kalodata-api.py --testar                # gasta 1 chamada, confere a chave
python kalodata-api.py --categorias --nivel 1  # acha o id de moda feminina
python kalodata-api.py --ranking-produtos --curadoria
```

`--curadoria` já aplica a regra: só produto afiliável, comissão a partir de 15% e
ticket de R$60 para cima, que é o que garante os R$9 por venda.

Sem crédito de API, dá para exportar CSV do Kalodata e largar em `dados-kalodata/`.
O `tiktok-sync.py` reconhece as colunas em português e em inglês.

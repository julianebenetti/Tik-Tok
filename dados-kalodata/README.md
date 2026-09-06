# Exports do Kalodata

Largue aqui o CSV (ou XLSX) exportado do Kalodata e faça commit. O GitHub Actions
importa sozinho pro Supabase e a página `espiao-tiktok-moda.html` passa a mostrar
os números.

## Como exportar

1. No Kalodata, filtre a categoria **Roupas femininas e roupas íntimas femininas** e o
   período de **Últimos 7 dias**.
2. Em **Fonte de receita (canal)**, filtre por **Vídeo**. Sem esse filtro o ranking
   enche de produto que só vende em live, e vídeo gravado não pega esse dinheiro.
3. Em **Taxa de comissão**, peça no mínimo 15%. Os campeões de moda feminina costumam
   pagar 10%, o que em ticket de R$20 a R$65 fica abaixo do piso de R$9 por venda.
4. Exporte a tela de **Produto** e a de **Marcas e Lojas** (dois arquivos separados).
   Dentro de um produto, a aba **Vídeo e Ads** também tem Exportar — esse arquivo vira
   a lista de vídeos que já venderam aquele produto.
5. Jogue os arquivos nesta pasta e faça commit.

Não precisa renomear. O script descobre sozinho se o arquivo é de loja ou de produto
pelas colunas, e aceita cabeçalho em português ou em inglês.

## Rodar na mão

```bash
export SUPABASE_KEY="a chave do projeto"
python tiktok-sync.py                          # importa tudo desta pasta
python tiktok-sync.py arquivo.csv --dry-run    # só mostra o mapeamento, não grava
python tiktok-sync.py arquivo.csv --periodo 30d --data 2026-09-06
python tiktok-sync.py videos.csv --produto "Calça Pantalona"   # export de Vídeo e Ads
```

O `--dry-run` imprime qual coluna do arquivo virou qual campo do banco, e lista as
colunas que ele não reconheceu. Se aparecer coluna importante em "não mapeadas",
é só acrescentar o rótulo em `ALIASES_LOJA` ou `ALIASES_PRODUTO`, no topo do
`tiktok-sync.py`.

## Sobre os números

O Kalodata **estima** GMV e unidades a partir de sinais públicos do TikTok. Não é
dado oficial da plataforma. Serve pra comparar loja com loja e ver tendência, não
pra tratar como faturamento auditado.

# Rodando no VPS da Hostinger

O script não usa nenhuma biblioteca externa, só a biblioteca padrão do Python 3.
Não tem `pip install` nesta parte.

## 1. Trazer o código

```bash
cd /opt          # ou /home/SEU_USUARIO, onde preferir
git clone https://github.com/julianebenetti/Tik-Tok.git espiao-tiktok
cd espiao-tiktok
python3 --version    # precisa ser 3.8 ou mais novo
```

## 2. Guardar as chaves

```bash
cp vps/.env.exemplo .env
nano .env            # preencha as quatro linhas
chmod 600 .env       # só o seu usuário lê
```

O `chmod 600` importa: sem ele, qualquer usuário do servidor lê sua chave.
O `.env` está no `.gitignore`, então nunca sobe pro GitHub.

## 3. Descobrir o cabeçalho e testar

```bash
set -a; source .env; set +a
python3 kalodata-api.py --descobrir-cabecalho   # não gasta crédito se a conta estiver zerada
python3 kalodata-api.py --testar                # gasta 1 chamada
```

Se o cabeçalho encontrado for diferente de `secret-key`, corrija no `.env`.

## 4. Pegar o ID de moda feminina

```bash
python3 kalodata-api.py --categorias --nivel 1
```

Anote o ID da categoria de roupas femininas e coloque em `CATEGORIA_MODA` no `.env`.

## 5. Rodar a varredura

```bash
./vps/varredura.sh 7d
```

Grava em `logs/varredura-AAAA-MM-DD.log` e mantém os últimos 30 dias.

## 6. Deixar automático

```bash
timedatectl                     # confira o fuso do servidor
crontab -e
```

Acrescente, ajustando o caminho e a hora ao fuso do servidor:

```
# toda segunda às 7h, varredura do Espião TikTok
0 7 * * 1 /opt/espiao-tiktok/vps/varredura.sh 7d >> /opt/espiao-tiktok/logs/cron.log 2>&1
```

Se o servidor estiver em UTC, 7h de Brasília é `0 10 * * 1`.

## 7. Publicar a página

Copie o `espiao-tiktok-moda.html` pra pasta que o servidor web serve, a mesma
onde já estão a AfiliDash e o `garimpo-shopee.html`.

```bash
cp espiao-tiktok-moda.html /var/www/html/     # ajuste o caminho ao seu servidor
```

Pra atualizar tudo depois de uma mudança no repositório:

```bash
cd /opt/espiao-tiktok && git pull && cp espiao-tiktok-moda.html /var/www/html/
```

## Onde NÃO colocar a chave

- Não coloque no `espiao-tiktok-moda.html`. Essa página é servida em endereço
  público e qualquer pessoa lê o código-fonte dela.
- Não coloque em nenhum arquivo versionado. Só no `.env`, com `chmod 600`.

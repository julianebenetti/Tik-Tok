"""
Cliente da API do Kalodata -> Supabase (Espião TikTok Shop)

Substitui o export manual de CSV: puxa os dados direto da API oficial do Kalodata
e grava nas mesmas tabelas que o tiktok-sync.py alimenta.

Uso:
    python kalodata-api.py --testar              # 1 chamada só, confere se a chave funciona
    python kalodata-api.py --video 7404191282148511007
    python kalodata-api.py --ranking-videos --paginas 2
    python kalodata-api.py --videos-do-produto 1729587769570529799

Variáveis de ambiente:
    KALODATA_KEY     obrigatória — a chave criada em Conta no Centro Aberto do Kalodata
    KALODATA_HEADER  nome do cabeçalho que leva a chave (padrão: secret-key)
    SUPABASE_URL     padrão: projeto da AfiliDash
    SUPABASE_KEY     obrigatória pra gravar

CUIDADO COM CRÉDITO: a API do Kalodata é paga por chamada. Este script nunca faz
uma chamada por produto — sempre prefira os endpoints de lista, que trazem tudo
de uma vez. O --testar gasta exatamente 1 chamada.

O QUE AINDA PRECISA SER CONFIRMADO NA DOCUMENTAÇÃO:
  1. O nome exato do cabeçalho da chave (a doc diz "secret-key nos cabeçalhos",
     mas não mostra o nome). Ajuste em KALODATA_HEADER ou na variável de ambiente.
  2. Os endpoints marcados como "provável" em ENDPOINTS. Confirmados pela doc:
     video/detail, video/rank e shop/detail. Os outros seguem o mesmo padrão
     (/tiktok/<familia>/detail e /tiktok/<familia>/rank), mas não foram vistos.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date

BASE = "https://www.kalodata.com/openapi/v1"

# Caminho, situação e limite de taxa (chamadas por 10 segundos), conforme a doc.
# Os endpoints de /rank são bem mais restritos que os de /detail.
ENDPOINTS = {
    "video_detalhe":     ("/tiktok/video/detail",    "confirmado", 100),
    "video_ranking":     ("/tiktok/video/rank",      "confirmado",  10),
    "loja_detalhe":      ("/tiktok/shop/detail",     "confirmado", 100),
    "loja_ranking":      ("/tiktok/shop/rank",       "confirmado",  10),
    "produto_detalhe":   ("/tiktok/product/detail",  "confirmado", 100),
    "produto_ranking":   ("/tiktok/product/rank",    "confirmado",  10),
    "criador_detalhe":   ("/tiktok/creator/detail",  "confirmado", 100),
    "criador_ranking":   ("/tiktok/creator/rank",    "confirmado",  10),
    "categoria_detalhe": ("/tiktok/category/detail", "confirmado", 100),
    "categoria_ranking": ("/tiktok/category/rank",   "confirmado",  10),
}

# Padrões do Brasil, conforme a lista de valores aceitos na documentação
PADRAO = {"region": "BR", "language": "pt-BR", "currency": "BRL", "date_range": "last7Day"}

KALODATA_KEY    = os.environ.get("KALODATA_KEY", "")
KALODATA_HEADER = os.environ.get("KALODATA_HEADER", "secret-key")
SUPABASE_URL    = os.environ.get("SUPABASE_URL", "https://tkxkrbdvcctoajuigvvv.supabase.co")
SUPABASE_KEY    = os.environ.get("SUPABASE_KEY", "")

# Cada endpoint tem seu limite. Guardo o instante da última chamada por caminho
# e espero o suficiente pra ficar com folga abaixo do teto.
_ultima_chamada = {}


def chamar(nome_endpoint, corpo):
    """Uma chamada POST na API. Devolve o dict de resposta ou levanta erro claro."""
    global _ultima_chamada
    if not KALODATA_KEY:
        raise SystemExit("Falta a variável de ambiente KALODATA_KEY.")

    caminho, situacao, teto = ENDPOINTS[nome_endpoint]
    intervalo = 10.0 / teto * 1.2  # 20% de folga
    espera = intervalo - (time.time() - _ultima_chamada.get(caminho, 0.0))
    if espera > 0:
        time.sleep(espera)
    _ultima_chamada[caminho] = time.time()

    return _post(caminho, situacao, corpo, KALODATA_HEADER)


def _post(caminho, situacao, corpo, cabecalho, cru=False):
    payload = json.dumps({**PADRAO, **corpo}).encode()
    req = urllib.request.Request(
        BASE + caminho, data=payload, method="POST",
        headers={"Content-Type": "application/json", cabecalho: KALODATA_KEY},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            resposta = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode()[:500]
        if cru:
            return e.code, detalhe
        if e.code in (401, 403):
            raise SystemExit(
                f"A API recusou a chave (HTTP {e.code}).\n"
                f"Cabeçalho usado: {cabecalho!r}. Rode --descobrir-cabecalho pra achar o certo.\n"
                f"Resposta: {detalhe}")
        if e.code == 404 and situacao == "provável":
            raise SystemExit(
                f"O caminho {caminho!r} não existe. Ele estava marcado como provável.\n"
                f"Abra '{nome_endpoint}' no Centro Aberto do Kalodata e corrija ENDPOINTS "
                f"no topo deste arquivo.")
        raise SystemExit(f"A API respondeu {e.code}: {detalhe}")

    if cru:
        return 200, resposta
    if not resposta.get("success"):
        raise SystemExit(
            f"A API respondeu success=false.\n"
            f"code: {resposta.get('code')}\nmessage: {resposta.get('message')}\n"
            f"Se falar em crédito ou saldo, recarregue no Centro Aberto antes de rodar de novo.")
    return resposta


# ── Supabase ─────────────────────────────────────────────────────────────
def supabase(metodo, caminho, corpo=None, headers=None):
    if not SUPABASE_KEY:
        raise SystemExit("Falta a variável de ambiente SUPABASE_KEY.")
    cabecalhos = {"apikey": SUPABASE_KEY, "Authorization": f"Bearer {SUPABASE_KEY}",
                  "Content-Type": "application/json"}
    cabecalhos.update(headers or {})
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(f"{SUPABASE_URL}/rest/v1/{caminho}",
                                 data=dados, headers=cabecalhos, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            texto = r.read().decode()
            return json.loads(texto) if texto.strip() else []
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Supabase respondeu {e.code}: {e.read().decode()[:500]}")


def gravar(tabela, registros, conflito):
    if not registros:
        return 0
    for i in range(0, len(registros), 200):
        supabase("POST", f"{tabela}?on_conflict={conflito}", registros[i:i + 200],
                 {"Prefer": "resolution=merge-duplicates,return=minimal"})
    return len(registros)


# ── Vídeo ────────────────────────────────────────────────────────────────
def montar_video(d, periodo, data_ref):
    """Traduz a resposta de /tiktok/video/detail pra uma linha de tiktok_videos."""
    arroba = d.get("belonged_creator_handle")
    return {
        "video_id":          d.get("video_id"),
        "arroba":            f"@{arroba}" if arroba and not str(arroba).startswith("@") else arroba,
        "criador_id":        d.get("belonged_creator_id"),
        "gancho":            d.get("video_title"),
        "produtos_no_video": d.get("product_number"),
        "receita":           d.get("revenue"),
        # a API escreve "sales_volumn" mesmo, com o erro de digitação
        "itens_vendidos":    d.get("sales_volumn", d.get("sales_volume")),
        "views":             d.get("views"),
        "gpm":               d.get("video_gpm"),
        "ads_views":         d.get("ads_views"),
        "ads_roas":          d.get("ads_roas"),
        "ad_cpa":            d.get("ad_cpa"),
        "proporcao_ads":     d.get("ad_view_ratio"),
        "likes":             d.get("digg_count"),
        "compartilhamentos": d.get("share_count"),
        "comentarios":       d.get("comment_count"),
        "duracao_seg":       int(d["duration"]) if d.get("duration") is not None else None,
        "anuncio":           d.get("ad") == 1,
        "video_ia":          d.get("ai_video") == 1,
        "link_video":        f"https://www.tiktok.com/@{arroba}/video/{d.get('video_id')}"
                             if arroba and d.get("video_id") else None,
        "periodo":           periodo,
        "data_ref":          data_ref,
        "data_deteccao":     data_ref,
        "fonte":             "kalodata",
    }


def buscar_video(video_id, periodo, data_ref, gravar_no_banco=True):
    resposta = chamar("video_detalhe", {"video_id": video_id, "date_range": periodo_api(periodo),
                                        "need_extra": False})
    linha = montar_video(resposta.get("data") or {}, periodo, data_ref)
    print(json.dumps(linha, ensure_ascii=False, indent=1))
    if gravar_no_banco:
        gravar("tiktok_videos", [linha], "video_id")
        print("Gravado em tiktok_videos.")
    return linha


def montar_video_rank(d, periodo, data_ref):
    """Traduz uma linha de /tiktok/video/rank. Tem menos campos que o detail,
    mas traz crescimento da receita e a fatia de receita vinda de anúncio."""
    arroba = d.get("belonged_creator_handle")
    return {
        "video_id":              d.get("video_id"),
        "arroba":                f"@{arroba}" if arroba and not str(arroba).startswith("@") else arroba,
        "criador_id":            d.get("belonged_creator_id"),
        "gancho":                d.get("video_title"),
        "receita":               d.get("revenue"),
        "views":                 d.get("views"),
        "crescimento_pct":       d.get("revenue_growth_rate"),
        "ads_roas":              d.get("ads_roas"),
        "likes":                 d.get("digg_count"),
        "compartilhamentos":     d.get("share_count"),
        "comentarios":           d.get("comment_count"),
        "proporcao_receita_ads": d.get("ad_revenue_ratio"),
        "proporcao_ads":         d.get("ad_view_ratio"),
        "data_postagem":         (d.get("creator_debut") or "")[:10] or None,
        "anuncio":               d.get("ad") == 1,
        "video_ia":              d.get("ai_video") == 1,
        "link_video":            f"https://www.tiktok.com/@{arroba}/video/{d.get('video_id')}"
                                 if arroba and d.get("video_id") else None,
        "periodo":               periodo,
        "data_ref":              data_ref,
        "data_deteccao":         data_ref,
        "fonte":                 "kalodata",
    }


def buscar_ranking_videos(periodo, data_ref, paginas=1, por_pagina=100,
                          produto_id=None, loja_id=None, categorias=None,
                          palavra=None, so_organico=False, dry_run=False):
    """Puxa o ranking de vídeos. Uma chamada traz até 100 linhas — sempre prefira
    aumentar por_pagina a fazer várias chamadas."""
    todos = []
    for pagina in range(1, paginas + 1):
        corpo = {
            "date_range": periodo_api(periodo),
            "sort_field": {"field": "revenue", "type": "DESC"},
            "page_size": min(por_pagina, 100),
            "page_number": pagina,
        }
        if produto_id:  corpo["product_id"] = produto_id
        if loja_id:     corpo["shop_id"] = loja_id
        if categorias:  corpo["category_ids"] = categorias
        if palavra:     corpo["keyword"] = palavra
        resposta = chamar("video_ranking", corpo)
        linhas = resposta.get("data") or []
        print(f"  página {pagina}: {len(linhas)} vídeo(s)")
        todos += [montar_video_rank(d, periodo, data_ref) for d in linhas]
        if len(linhas) < corpo["page_size"]:
            break

    if so_organico:
        antes = len(todos)
        todos = [v for v in todos if not v["anuncio"]]
        print(f"  filtrei os impulsionados: {antes} → {len(todos)}")

    com_id = [v for v in todos if v.get("video_id")]
    print(f"  {len(com_id)} vídeo(s) com id, prontos pra gravar")
    if com_id:
        print(f"  exemplo: {json.dumps(com_id[0], ensure_ascii=False)[:260]}")
    if dry_run:
        print("  (dry-run: nada foi gravado)")
        return com_id
    gravar("tiktok_videos", com_id, "video_id")
    print(f"  {len(com_id)} linha(s) gravada(s) em tiktok_videos.")
    return com_id


def montar_loja(d, periodo, data_ref):
    """Traduz uma loja, tanto de /shop/detail quanto de /shop/rank.
    A doc escreve alguns campos de dois jeitos, então leio os dois."""
    def pega(*nomes):
        for n in nomes:
            if d.get(n) is not None:
                return d[n]
        return None
    return {
        "loja_id":           d.get("shop_id"),
        "loja":              d.get("shop_name"),
        "gmv":               d.get("revenue"),
        "receita_afiliados": d.get("affiliate_revenue"),
        "receita_propria":   pega("self_account_revenue", "self_promotion_revenue"),
        "receita_shopping":  pega("shoppingmall_revenue", "shopping_mall_revenue"),
        "unidades":          pega("sales_volumn", "sales_volume"),
        "preco_medio":       d.get("unit_price"),
        "criadores":         d.get("creator_number"),
        "produtos_ativos":   pega("product_number", "on_sell_product_count"),
        "videos":            d.get("video_number"),
        "lives":             d.get("live_number"),
        "crescimento_pct":   d.get("revenue_growth_rate"),
        "tipo_loja":         pega("seller_type", "shop_type"),
        "top3_produtos":     d.get("top3_product_ids"),
        "imagem_url":        d.get("image_url"),
        "categorias":        d.get("category_list"),
        "ranking":           d.get("rank"),
        "periodo":           periodo,
        "data_ref":          data_ref,
        "fonte":             "kalodata",
    }


def buscar_ranking_lojas(periodo, data_ref, paginas=1, por_pagina=100,
                         ordenar_por="revenue", categorias=None, palavra=None,
                         tipo_loja=None, faixa_receita=None, dry_run=False):
    """Ranking de lojas. Ordene por affiliate_revenue pra achar quem já vende
    com criador, não só quem vende muito."""
    todas = []
    for pagina in range(1, paginas + 1):
        corpo = {
            "date_range": periodo_api(periodo),
            "sort_field": {"field": ordenar_por, "type": "DESC"},
            "page_size": min(por_pagina, 100),
            "page_number": pagina,
            "need_category": 1,
        }
        if categorias:     corpo["category_ids"] = categorias
        if palavra:        corpo["keyword"] = palavra
        if tipo_loja:      corpo["shop_type"] = tipo_loja
        if faixa_receita:  corpo["revenue_range"] = faixa_receita
        resposta = chamar("loja_ranking", corpo)
        linhas = resposta.get("data") or []
        print(f"  página {pagina}: {len(linhas)} loja(s)")
        todas += [montar_loja(d, periodo, data_ref) for d in linhas]
        if len(linhas) < corpo["page_size"]:
            break

    for i, loja in enumerate(todas, start=1):
        if loja.get("ranking") is None:
            loja["ranking"] = i
    com_id = [l for l in todas if l.get("loja_id") and l.get("loja")]
    print(f"  {len(com_id)} loja(s) pronta(s) pra gravar")
    if com_id:
        print(f"  exemplo: {json.dumps(com_id[0], ensure_ascii=False)[:280]}")
    if dry_run:
        print("  (dry-run: nada foi gravado)")
        return com_id
    gravar("tiktok_lojas", com_id, "loja_id,periodo,data_ref")
    print(f"  {len(com_id)} linha(s) gravada(s) em tiktok_lojas.")
    return com_id


def buscar_loja(loja_id, periodo, data_ref, dry_run=False):
    resposta = chamar("loja_detalhe", {"shop_id": loja_id,
                                       "date_range": periodo_api(periodo),
                                       "need_extra": False})
    linha = montar_loja(resposta.get("data") or {}, periodo, data_ref)
    print(json.dumps(linha, ensure_ascii=False, indent=1))
    if not dry_run and linha.get("loja_id"):
        gravar("tiktok_lojas", [linha], "loja_id,periodo,data_ref")
        print("Gravado em tiktok_lojas.")
    return linha


def montar_produto(d, periodo, data_ref):
    """Traduz /tiktok/product/detail. O ouro aqui é video_revenue x live_revenue:
    diz na hora se o produto vende por vídeo gravado ou só ao vivo."""
    def pega(*nomes):
        for n in nomes:
            if d.get(n) is not None:
                return d[n]
        return None
    receita = d.get("revenue")
    preco_min, preco_max = d.get("min_price"), d.get("max_price")
    pct = d.get("commission_rate")
    base = d.get("unit_price") if d.get("unit_price") is not None else preco_min
    com_reais = round(float(base) * float(pct) / 100, 2) if (base is not None and pct) else None
    return {
        "produto_id":        d.get("product_id"),
        "produto":           d.get("product_name"),
        "loja_id":           pega("product_shop_id", "seller_id"),
        "loja":              pega("seller_name", "shop_name"),
        "sku_count":         d.get("sku_count"),
        "cat_principal_id":  d.get("pri_cate_id"),
        "cat_secundaria_id": d.get("sec_cate_id"),
        "cat_terciaria_id":  d.get("ter_cate_id"),
        "preco_min":         preco_min,
        "preco_max":         preco_max,
        "preco":             preco_min if preco_min == preco_max else None,
        "preco_medio":       d.get("unit_price"),
        "comissao_reais":    com_reais,
        "gmv":               receita,
        "receita_video":     d.get("video_revenue"),
        "receita_live":      d.get("live_revenue"),
        "unidades":          pega("sales_volumn", "sales_volume"),
        "criadores":         d.get("creator_number"),
        "videos":            d.get("video_number"),
        "lives":             d.get("live_number"),
        "crescimento_pct":   d.get("revenue_growth_rate"),
        # a doc é explícita: commission_rate já vem em pontos percentuais (1.0 = 1%)
        "comissao_percentual": d.get("commission_rate"),
        "avaliacoes_qtd":    d.get("product_review_count"),
        "data_lancamento":   (d.get("launch_date") or "")[:10] or None,
        "tipo_envio":        d.get("delivery_type"),
        "receita_cartao":    d.get("showcase_revenue"),
        "receita_shopping":  d.get("shopping_mall_revenue"),
        "imagem_url":        pega("master_image_url", "image_url"),
        "periodo":           periodo,
        "data_ref":          data_ref,
        "fonte":             "kalodata",
    }


# Campos que o /product/rank aceita ordenar. Os dois que interessam pra ela:
# video_revenue acha quem vende por vídeo, commission_rate acha quem paga bem.
ORDENACOES_PRODUTO = {
    "revenue", "commission_rate", "revenue_growth_rate", "sales_volumn",
    "unit_price", "launch_date", "live_revenue", "video_revenue",
    "showcase_revenue", "product_id", "product_name",
}


# A regra de curadoria da Juliane vira filtro da própria API:
# só produto afiliável, comissão a partir de 15% e ticket que garante os R$9.
# 15% de R$60 = R$9 cravado, então esse é o piso de preço que fecha a conta.
CURADORIA = {"is_affiliate": 1, "commission_rate": ">=15", "unit_price_range": "60-1000"}


def buscar_ranking_produtos(periodo, data_ref, paginas=1, por_pagina=100,
                            ordenar_por="revenue", categorias=None, palavra=None,
                            loja_id=None, dry_run=False, curadoria=False,
                            comissao=None, faixa_preco=None, envio=None,
                            lancados_ha=None, so_afiliado=False):
    if ordenar_por not in ORDENACOES_PRODUTO:
        raise SystemExit("--ordenar-por aceita: " + ", ".join(sorted(ORDENACOES_PRODUTO)))
    todos = []
    for pagina in range(1, paginas + 1):
        corpo = {
            "date_range": periodo_api(periodo),
            "sort_field": {"field": ordenar_por, "type": "DESC"},
            "page_size": min(por_pagina, 100),
            "page_number": pagina,
            "need_image": 1,
            "need_extra": True,   # traz nome do vendedor e quantidade de SKU
        }
        if curadoria:     corpo.update(CURADORIA)
        if so_afiliado:   corpo["is_affiliate"] = 1
        if comissao:      corpo["commission_rate"] = comissao
        if faixa_preco:   corpo["unit_price_range"] = faixa_preco
        if envio:         corpo["delivery_type"] = envio
        if lancados_ha:   corpo["launch_date"] = lancados_ha
        if categorias:    corpo["category_ids"] = categorias
        if palavra:       corpo["keyword"] = palavra
        if loja_id:       corpo["shop_id"] = loja_id
        if pagina == 1:
            filtros = {k: v for k, v in corpo.items()
                       if k not in ("date_range", "sort_field", "page_size",
                                    "page_number", "need_image", "need_extra")}
            print(f"  filtros: {json.dumps(filtros, ensure_ascii=False) if filtros else 'nenhum'}")
        resposta = chamar("produto_ranking", corpo)
        linhas = resposta.get("data") or []
        print(f"  página {pagina}: {len(linhas)} produto(s)")
        todos += [montar_produto(d, periodo, data_ref) for d in linhas]
        if len(linhas) < corpo["page_size"]:
            break

    for i, p in enumerate(todos, start=1):
        if p.get("ranking") is None:
            p["ranking"] = i
    validos = [p for p in todos if p.get("produto_id") and p.get("produto")]

    acima = [p for p in validos if (p.get("comissao_reais") or 0) >= 9]
    print(f"  {len(validos)} produto(s), {len(acima)} com comissão a partir de R$9")
    if validos:
        print(f"  exemplo: {json.dumps(validos[0], ensure_ascii=False)[:280]}")
    if dry_run:
        print("  (dry-run: nada foi gravado)")
        return validos
    gravar("tiktok_ranking_produtos", validos, "produto_id,periodo,data_ref")
    print(f"  {len(validos)} linha(s) gravada(s) em tiktok_ranking_produtos.")
    return validos


def buscar_produto(produto_id, periodo, data_ref, dry_run=False):
    resposta = chamar("produto_detalhe", {"product_id": produto_id,
                                          "date_range": periodo_api(periodo),
                                          "need_image": 1, "need_extra": False})
    linha = montar_produto(resposta.get("data") or {}, periodo, data_ref)
    print(json.dumps(linha, ensure_ascii=False, indent=1))
    if linha.get("gmv") and linha.get("receita_video") is not None:
        fatia = float(linha["receita_video"]) / float(linha["gmv"]) * 100
        print(f"\nFatia de vídeo: {fatia:.1f}% da receita "
              f"({'vale gravar' if fatia >= 30 else 'vende mais ao vivo que por vídeo'})")
    if not dry_run and linha.get("produto_id") and linha.get("produto"):
        gravar("tiktok_ranking_produtos", [linha], "produto_id,periodo,data_ref")
        print("Gravado em tiktok_ranking_produtos.")
    return linha


PERIODOS = {"1d": "lastDay", "7d": "last7Day", "30d": "last30Day"}


# Categoria é o único que recusa lastDay e intervalos naturais.
PERIODOS_CATEGORIA = {"7d": "last7Day", "30d": "last30Day", "90d": "last90Day",
                      "180d": "last180Day", "365d": "last365Day"}


def periodo_api(periodo, familia=None):
    # O ranking de vídeos limita a janela a 30 dias, então fico nesse conjunto.
    tabela = PERIODOS_CATEGORIA if familia == "categoria" else PERIODOS
    if periodo not in tabela:
        raise SystemExit(
            f"--periodo aceita {', '.join(tabela)}"
            + (" nos endpoints de categoria." if familia == "categoria" else "."))
    return tabela[periodo]


def _inteiro(valor):
    """A API manda seguidores e visualizações como texto em alguns endpoints."""
    try:
        return int(float(valor)) if valor is not None else None
    except (TypeError, ValueError):
        return None


def montar_criador(d, periodo, data_ref):
    """Traduz /tiktok/creator/detail e /rank.

    De propósito, NÃO copio os campos creator_contact_* (email, whatsapp, line,
    facebook, zalo). São dados pessoais de terceiros, não fazem falta pra decidir
    o que gravar, e guardar isso num banco é responsabilidade que não vale a pena.
    Fica só o @, que é a identidade pública na plataforma."""
    handle = d.get("creator_handle")
    seguidores = _inteiro(d.get("creator_followers"))
    return {
        "criador_id":       d.get("creator_id"),
        "arroba":           f"@{handle}" if handle and not str(handle).startswith("@") else handle,
        "apelido":          d.get("creator_nickname"),
        "nome":             d.get("creator_nickname"),
        "bio":              d.get("creator_bio"),
        "status":           d.get("creator_status"),
        "regiao":           d.get("creator_region"),
        "loja_vinculada":   d.get("creator_belonged_shop_id") or None,
        "seguidores":       seguidores,
        "novos_seguidores": d.get("new_followers"),
        "gmv":              d.get("revenue"),
        "unidades":         d.get("sales_volumn", d.get("sales_volume")),
        "preco_medio":      d.get("unit_price"),
        "receita_video":    d.get("video_revenue"),
        "receita_live":     d.get("live_revenue"),
        "video_views":      d.get("video_views"),
        "live_views":       d.get("live_views"),
        "video_gpm":        d.get("video_gpm"),
        "live_gpm":         d.get("live_gpm"),
        "videos":           d.get("video_number"),
        "lives":            d.get("live_number"),
        "lojas":            d.get("shop_number"),
        "produtos":         d.get("product_number"),
        "top3_produtos":    [str(x) for x in (d.get("top3_product_ids") or [])] or None,
        "content_views":    _inteiro(d.get("content_views")),
        "categorias":       d.get("category_list"),
        "categorias_nivel3": d.get("ter_category_list"),
        "crescimento_pct":  d.get("revenue_growth_rate"),
        "ranking":          d.get("rank"),
        "link":             f"https://www.tiktok.com/@{handle}" if handle else None,
        "tipo":             "concorrente",
        "periodo":          periodo,
        "data_ref":         data_ref,
        "fonte":            "kalodata",
        "ultima_revisao":   data_ref,
    }


def buscar_criador(criador_id, periodo, data_ref, dry_run=False):
    resposta = chamar("criador_detalhe", {"creator_id": criador_id,
                                          "date_range": periodo_api(periodo),
                                          "need_extra": False})
    linha = montar_criador(resposta.get("data") or {}, periodo, data_ref)
    print(json.dumps(linha, ensure_ascii=False, indent=1))
    if linha.get("video_gpm") is not None:
        print(f"\nGPM de vídeo: R${float(linha['video_gpm']):.2f} por mil visualizações."
              + (f"  Em live: R${float(linha['live_gpm']):.2f}."
                 if linha.get("live_gpm") is not None else ""))
        print("Compare com o seu: é a mesma régua.")
    if not dry_run and linha.get("criador_id"):
        gravar("tiktok_criadores", [linha], "criador_id,periodo,data_ref")
        print("Gravado em tiktok_criadores.")
    return linha


def buscar_ranking_criadores(periodo, data_ref, paginas=1, por_pagina=100,
                             ordenar_por="revenue", categorias=None,
                             so_independentes=False, dry_run=False,
                             faixa_seguidores=None, engajamento=None, palavra=None,
                             loja_id=None, produto_id=None):
    """O filtro de tipo de criador é do servidor, então não gasta chamada trazendo
    criador de loja pra descartar depois. faixa_seguidores é o que permite comparar
    com gente do seu tamanho, em vez de com quem tem milhões."""
    todos = []
    for pagina in range(1, paginas + 1):
        corpo = {"date_range": periodo_api(periodo),
                 "sort_field": {"field": ordenar_por, "type": "DESC"},
                 "page_size": min(por_pagina, 100), "page_number": pagina,
                 "need_category": 1, "need_ter_category": 1}
        # a doc marca category_ids como obsoleto e prefere a lista separada por vírgula
        if categorias:        corpo["category_id_list"] = ",".join(str(c) for c in categorias)
        if so_independentes:  corpo["creator_type"] = "INDEPENDENT"
        if faixa_seguidores:  corpo["followers_range"] = faixa_seguidores
        if engajamento:       corpo["engagement_rate"] = engajamento
        if palavra:           corpo["keyword"] = palavra
        if loja_id:           corpo["shop_id"] = loja_id
        if produto_id:        corpo["product_id"] = produto_id
        if pagina == 1:
            filtros = {k: v for k, v in corpo.items()
                       if k not in ("date_range", "sort_field", "page_size", "page_number",
                                    "need_category", "need_ter_category")}
            print(f"  filtros: {json.dumps(filtros, ensure_ascii=False) if filtros else 'nenhum'}")
        resposta = chamar("criador_ranking", corpo)
        linhas = resposta.get("data") or []
        print(f"  página {pagina}: {len(linhas)} criador(es)")
        todos += [montar_criador(d, periodo, data_ref) for d in linhas]
        if len(linhas) < min(por_pagina, 100):
            break
    for i, c in enumerate(todos, start=1):
        if c.get("ranking") is None:
            c["ranking"] = i
    validos = [c for c in todos if c.get("criador_id") and c.get("arroba")]

    com_gpm = [c for c in validos if c.get("video_gpm") is not None]
    if com_gpm:
        media = sum(float(c["video_gpm"]) for c in com_gpm) / len(com_gpm)
        print(f"  GPM de vídeo médio do grupo: R${media:.2f} por mil visualizações")
    if dry_run:
        print("  (dry-run: nada foi gravado)")
        return validos
    gravar("tiktok_criadores", validos, "criador_id,periodo,data_ref")
    print(f"  {len(validos)} linha(s) gravada(s) em tiktok_criadores.")
    return validos


def montar_categoria(d, periodo, data_ref):
    return {
        "categoria_id":       d.get("category_id"),
        "categoria":          d.get("category_name"),
        "gmv":                d.get("revenue"),
        "receita_video":      d.get("video_revenue"),
        "receita_live":       d.get("live_revenue"),
        "receita_afiliados":  d.get("affiliate_revenue"),
        "receita_propria":    d.get("self_operate_revenue"),
        "receita_shopping":   d.get("shopping_mall_revenue"),
        "crescimento_pct":    d.get("revenue_growth_rate"),
        "lojas":              d.get("shop_number"),
        "receita_media_loja": (d.get("average_shop_revenue")
                               if d.get("average_shop_revenue") is not None
                               else d.get("average_revenue")),
        "concentracao_top3":  d.get("top3_shop_revenue_ratio"),
        "vendas":             d.get("sale"),
        "produtos_ativos":    d.get("active_product_number"),
        "ranking":            d.get("rank"),
        "periodo":            periodo,
        "data_ref":           data_ref,
        "fonte":              "kalodata",
    }


def buscar_categorias(periodo, data_ref, paginas=1, por_pagina=100,
                      ordenar_por="revenue", dry_run=False,
                      nivel=None, dentro_de=None):
    """Lista as categorias. É daqui que sai o category_id de moda feminina,
    que vira filtro em todos os outros endpoints.

    nivel: 1 = categorias de topo, 2 = médio, 3 = folha.
    dentro_de: id de uma categoria-mãe, pra abrir as filhas dela."""
    todas = []
    for pagina in range(1, paginas + 1):
        corpo = {
            "date_range": periodo_api(periodo, "categoria"),
            "sort_field": {"field": ordenar_por, "type": "DESC"},
            "page_size": min(por_pagina, 100), "page_number": pagina}
        if nivel:      corpo["category_level"] = int(nivel)
        if dentro_de:  corpo["category_ids"] = [dentro_de]
        resposta = chamar("categoria_ranking", corpo)
        linhas = resposta.get("data") or []
        print(f"  página {pagina}: {len(linhas)} categoria(s)")
        todas += [montar_categoria(d, periodo, data_ref) for d in linhas]
        if len(linhas) < min(por_pagina, 100):
            break

    for i, c in enumerate(todas, start=1):
        if c.get("ranking") is None:
            c["ranking"] = i
    validas = [c for c in todas if c.get("categoria_id")]

    print(f"\n  {len(validas)} categoria(s)"
          + (f", nível {nivel}" if nivel else "")
          + (f", dentro de {dentro_de}" if dentro_de else "")
          + ". As de moda feminina:")
    alvos = ("moda", "roupa", "femin", "vestu", "women", "apparel", "fashion",
             "intima", "lingerie", "cal\u00e7ado", "bolsa", "acess")
    achou = False
    for c in validas:
        nome = (c.get("categoria") or "").lower()
        if any(a in nome for a in alvos):
            achou = True
            fatia = (float(c["receita_video"]) / float(c["gmv"]) * 100
                     if c.get("gmv") and c.get("receita_video") is not None else None)
            print(f"    id {c['categoria_id']:>10}  {c['categoria']}"
                  + (f"  ({fatia:.0f}% por vídeo)" if fatia is not None else ""))
    if not achou:
        print("    nenhuma bateu com os termos de moda. Lista completa:")
        for c in validas[:40]:
            print(f"    id {c['categoria_id']:>10}  {c.get('categoria')}")

    if dry_run:
        print("  (dry-run: nada foi gravado)")
        return validas
    gravar("tiktok_categorias", validas, "categoria_id,periodo,data_ref")
    print(f"  {len(validas)} linha(s) gravada(s) em tiktok_categorias.")
    return validas


def buscar_categoria(categoria_id, periodo, data_ref, dry_run=False):
    resposta = chamar("categoria_detalhe", {
        "category_id": categoria_id, "date_range": periodo_api(periodo, "categoria")})
    linha = montar_categoria(resposta.get("data") or {}, periodo, data_ref)
    print(json.dumps(linha, ensure_ascii=False, indent=1))
    if not dry_run and linha.get("categoria_id"):
        gravar("tiktok_categorias", [linha], "categoria_id,periodo,data_ref")
        print("Gravado em tiktok_categorias.")
    return linha


# A doc diz "autenticação de secret-key nos cabeçalhos" mas nunca mostra o nome.
# Em vez de pedir mais um print, testo os nomes prováveis e vejo qual passa.
CABECALHOS_CANDIDATOS = [
    "secret-key", "Secret-Key", "X-Secret-Key", "secretkey",
    "api-key", "X-API-Key", "apikey", "key", "Key", "Authorization",
]

# Palavras que indicam que a autenticação PASSOU e o que barrou foi saldo.
# Nesse caso o cabeçalho está certo, só falta recarregar.
SINAIS_DE_CREDITO = ("credit", "crédito", "quota", "saldo", "balance",
                     "insufficient", "recarregar", "recharge", "points")


def descobrir_cabecalho():
    """Testa os nomes prováveis de cabeçalho. Erro de saldo também conta como
    acerto: significa que a chave foi aceita e só falta crédito."""
    if not KALODATA_KEY:
        raise SystemExit("Falta a variável de ambiente KALODATA_KEY.")
    caminho, situacao, _ = ENDPOINTS["video_detalhe"]
    corpo = {"region": "US", "language": "en-US", "currency": "USD",
             "date_range": "last7Day", "video_id": "7404191282148511007",
             "need_extra": False}
    print("Testando os nomes prováveis de cabeçalho, 1 chamada cada.\n")
    for nome in CABECALHOS_CANDIDATOS:
        time.sleep(0.2)
        try:
            status, corpo_resp = _post(caminho, situacao, corpo, nome, cru=True)
        except Exception as erro:
            print(f"  {nome:16} erro de rede: {erro}")
            continue
        texto = json.dumps(corpo_resp, ensure_ascii=False).lower() if isinstance(
            corpo_resp, dict) else str(corpo_resp).lower()
        if status in (401, 403):
            print(f"  {nome:16} recusado (HTTP {status})")
        elif status == 200 and isinstance(corpo_resp, dict) and corpo_resp.get("success"):
            print(f"\n  ✓ É esse: {nome}\n")
            print(f"Defina KALODATA_HEADER={nome} e pode rodar tudo.")
            return nome
        elif any(p in texto for p in SINAIS_DE_CREDITO):
            print(f"\n  ✓ É esse: {nome}  (a chave passou; o que falta é crédito)\n")
            print(f"Defina KALODATA_HEADER={nome} e recarregue o saldo no Centro Aberto.")
            return nome
        else:
            print(f"  {nome:16} HTTP {status}: {str(corpo_resp)[:90]}")
    print("\nNenhum funcionou. Mande um print da página Conta mostrando o exemplo "
          "de requisição, que ali costuma aparecer o nome do cabeçalho.")
    return None


def testar():
    """Gasta 1 chamada só, com os parâmetros de exemplo da própria documentação."""
    print(f"Base: {BASE}")
    print(f"Cabeçalho da chave: {KALODATA_HEADER}")
    print(f"Chave: {'definida' if KALODATA_KEY else 'AUSENTE'}")
    print("Chamando /tiktok/video/detail com o exemplo da doc (1 chamada)…\n")
    resposta = chamar("video_detalhe", {
        "region": "US", "language": "en-US", "currency": "USD",
        "date_range": "last7Day", "video_id": "7404191282148511007", "need_extra": False})
    print(json.dumps(resposta, ensure_ascii=False, indent=1)[:1500])
    print("\nA chave funciona. Pode rodar as buscas de verdade.")


def main():
    args = sys.argv[1:]
    if not args or "--ajuda" in args or "-h" in args:
        print(__doc__)
        print("Situação dos endpoints:")
        for nome, (caminho, situacao, teto) in ENDPOINTS.items():
            print(f"  {nome:18} {caminho:26} {situacao:11} {teto:>3} chamadas/10s")
        return

    periodo, data_ref = "7d", date.today().isoformat()
    video_id = produto_id = loja_id = palavra = produto_detalhe_id = None
    ordenar_por, tipo_loja = "revenue", None
    comissao = faixa_preco = envio = lancados_ha = categoria_id = None
    nivel = dentro_de = criador_id = None
    faixa_seguidores = engajamento = None
    paginas, por_pagina = 1, 100
    dry_run = "--dry-run" in args
    so_organico = "--so-organico" in args
    i = 0
    while i < len(args):
        if args[i] == "--periodo" and i + 1 < len(args):
            periodo = args[i + 1]; i += 2
        elif args[i] == "--data" and i + 1 < len(args):
            data_ref = args[i + 1]; i += 2
        elif args[i] == "--video" and i + 1 < len(args):
            video_id = args[i + 1]; i += 2
        elif args[i] == "--videos-do-produto" and i + 1 < len(args):
            produto_id = args[i + 1]; i += 2
        elif args[i] == "--videos-da-loja" and i + 1 < len(args):
            loja_id = args[i + 1]; i += 2
        elif args[i] == "--seguidores" and i + 1 < len(args):
            faixa_seguidores = args[i + 1]; i += 2
        elif args[i] == "--engajamento" and i + 1 < len(args):
            engajamento = args[i + 1].upper(); i += 2
        elif args[i] == "--criador" and i + 1 < len(args):
            criador_id = args[i + 1]; i += 2
        elif args[i] == "--nivel" and i + 1 < len(args):
            nivel = args[i + 1]; i += 2
        elif args[i] == "--dentro-de" and i + 1 < len(args):
            dentro_de = args[i + 1]; i += 2
        elif args[i] == "--categoria" and i + 1 < len(args):
            categoria_id = args[i + 1]; i += 2
        elif args[i] == "--comissao" and i + 1 < len(args):
            comissao = args[i + 1]; i += 2
        elif args[i] == "--faixa-preco" and i + 1 < len(args):
            faixa_preco = args[i + 1]; i += 2
        elif args[i] == "--envio" and i + 1 < len(args):
            envio = args[i + 1]; i += 2
        elif args[i] == "--lancados-ha" and i + 1 < len(args):
            lancados_ha = args[i + 1]; i += 2
        elif args[i] == "--produto" and i + 1 < len(args):
            produto_detalhe_id = args[i + 1]; i += 2
        elif args[i] == "--loja" and i + 1 < len(args):
            loja_id = args[i + 1]; i += 2
        elif args[i] == "--ordenar-por" and i + 1 < len(args):
            ordenar_por = args[i + 1]; i += 2
        elif args[i] == "--tipo-loja" and i + 1 < len(args):
            tipo_loja = args[i + 1]; i += 2
        elif args[i] == "--palavra" and i + 1 < len(args):
            palavra = args[i + 1]; i += 2
        elif args[i] == "--paginas" and i + 1 < len(args):
            paginas = int(args[i + 1]); i += 2
        elif args[i] == "--por-pagina" and i + 1 < len(args):
            por_pagina = int(args[i + 1]); i += 2
        else:
            i += 1

    if "--descobrir-cabecalho" in args:
        descobrir_cabecalho()
    elif "--testar" in args:
        testar()
    elif "--ranking-criadores" in args:
        print(f"Ranking de criadores, período {periodo}, ordenado por {ordenar_por}:")
        buscar_ranking_criadores(periodo, data_ref, paginas, por_pagina, ordenar_por,
                                 None, "--so-independentes" in args, dry_run,
                                 faixa_seguidores, engajamento, palavra, loja_id, produto_id)
    elif criador_id:
        buscar_criador(criador_id, periodo, data_ref, dry_run)
    elif "--categorias" in args:
        print(f"Categorias, período {periodo}, {paginas} chamada(s):")
        buscar_categorias(periodo, data_ref, paginas, por_pagina, ordenar_por, dry_run,
                          nivel=nivel, dentro_de=dentro_de)
    elif categoria_id:
        buscar_categoria(categoria_id, periodo, data_ref, dry_run)
    elif "--ranking-produtos" in args:
        print(f"Ranking de produtos, período {periodo}, ordenado por {ordenar_por}, "
              f"{paginas} chamada(s):")
        buscar_ranking_produtos(periodo, data_ref, paginas, por_pagina, ordenar_por,
                                None, palavra, loja_id, dry_run,
                                curadoria="--curadoria" in args,
                                comissao=comissao, faixa_preco=faixa_preco,
                                envio=envio, lancados_ha=lancados_ha,
                                so_afiliado="--so-afiliado" in args)
    elif "--ranking-lojas" in args:
        print(f"Ranking de lojas, período {periodo}, ordenado por {ordenar_por}, "
              f"{paginas} chamada(s):")
        buscar_ranking_lojas(periodo, data_ref, paginas, por_pagina, ordenar_por,
                             None, palavra, tipo_loja, None, dry_run)
    elif produto_detalhe_id:
        buscar_produto(produto_detalhe_id, periodo, data_ref, dry_run)
    elif loja_id and "--videos-da-loja" not in args:
        buscar_loja(loja_id, periodo, data_ref, dry_run)
    elif video_id:
        buscar_video(video_id, periodo, data_ref)
    elif produto_id or loja_id or palavra or "--ranking-videos" in args:
        alvo = (f"produto {produto_id}" if produto_id else
                f"loja {loja_id}" if loja_id else
                f"palavra {palavra!r}" if palavra else "geral")
        print(f"Ranking de vídeos ({alvo}), período {periodo}, "
              f"até {paginas * min(por_pagina, 100)} linhas, "
              f"{paginas} chamada(s):")
        buscar_ranking_videos(periodo, data_ref, paginas, por_pagina,
                              produto_id, loja_id, None, palavra, so_organico, dry_run)
    else:
        raise SystemExit(
            "Nada pra fazer. Opções:\n"
            "  --descobrir-cabecalho         acha o nome do cabeçalho da chave sozinho\n"
            "  --testar                      confere a chave, gasta 1 chamada\n"
            "  --ranking-videos              top de vídeos do período\n"
            "  --ranking-criadores           top de criadores; some --so-independentes\n"
            "  --seguidores 1000-10000       compara com gente do seu tamanho\n"
            "  --engajamento MEDIUM          LOW (<8%), MEDIUM (8-20%) ou HIGH (>20%)\n"
            "  --criador <id>                detalhe de um criador, com o GPM dele\n"
            "  --categorias --nivel 1        categorias de topo (é onde está moda feminina)\n"
            "  --categorias --dentro-de <id> abre as subcategorias de uma categoria\n"
            "  --categoria <id>              detalhe de uma categoria\n"
            "  --ranking-produtos            top de produtos do período\n"
            "  --ordenar-por video_revenue       produtos que vendem por vídeo, não por live\n"
            "  --ordenar-por commission_rate     produtos que pagam a maior comissão\n"
            "  --curadoria                   sua regra pronta: afiliável, 15%+ e ticket R$60+\n"
            "  --so-afiliado                 só produtos do programa de afiliados\n"
            "  --comissao \">=20\"             filtro de comissão: 15-35, >=20, <10\n"
            "  --faixa-preco 60-200          filtro de ticket\n"
            "  --envio local                 só envio nacional (local) ou global\n"
            "  --lancados-ha \"<7\"            produto novo: <3, <7, >30 dias\n"
            "  --ranking-lojas               top de lojas do período\n"
            "  --ordenar-por affiliate_revenue   ordena as lojas por receita de afiliado\n"
            "  --tipo-loja BRAND             só marca própria, ou RETAILER pra varejista\n"
            "  --loja <id>                   detalhe de uma loja\n"
            "  --produto <id>                detalhe de um produto, com o split vídeo x live\n"
            "  --videos-do-produto <id>      os vídeos que vendem um produto\n"
            "  --videos-da-loja <id>         os vídeos que vendem uma loja\n"
            "  --palavra fashion             busca por palavra-chave\n"
            "  --video <id>                  detalhe de um vídeo\n"
            "Junte --so-organico pra descartar os impulsionados, e --dry-run pra não gravar.")


if __name__ == "__main__":
    main()

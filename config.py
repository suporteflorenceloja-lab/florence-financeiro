CATEGORIES = [
    "RECEITA BRUTA",
    "SIMPLES NACIONAL",
    "MATERIAL",
    "AVIAMENTO",
    "ETIQUETA",
    "ALÇA",
    "FACÇÃO",
    "TALHAÇÃO",
    "EMBALAGEM",
    "FRETE PRODUÇÃO",
    "FRETE PEDIDO",
    "CONTABILIDADE",
    "ECOMMERCE",
    "MATERIAIS EXPEDIENTE",
    "TRÁFEGO",
    "SERVIÇOS MKT/EVENTOS",
    "ENERGIA",
    "CONDOMÍNIO",
    "ALUGUEL",
    "TRANSPORTE",
    "INTERNET",
    "SALÁRIO",
    "OUTROS",
]

# DRE structure — each entry is one visible row in the report
# type: "income" | "cost" | "subtotal" | "result" | "section"
DRE_ROWS = [
    {"id": "receita_bruta",   "label": "RECEITA BRUTA",            "categories": ["RECEITA BRUTA"],          "type": "income",   "level": 0},
    {"id": "simples",         "label": "(−) Simples Nacional",     "categories": ["SIMPLES NACIONAL"],       "type": "cost",     "level": 1},
    {"id": "receita_liquida", "label": "= RECEITA LÍQUIDA",        "type": "subtotal", "level": 0,
     "formula": lambda v: v["receita_bruta"] - v["simples"]},

    {"id": "_cpv",            "label": "CUSTO DO PRODUTO VENDIDO", "type": "section",  "level": 0},
    {"id": "material",        "label": "Material",                 "categories": ["MATERIAL"],                "type": "cost",     "level": 1},
    {"id": "aviamento",       "label": "Aviamento",                "categories": ["AVIAMENTO"],               "type": "cost",     "level": 1},
    {"id": "etiqueta",        "label": "Etiqueta",                 "categories": ["ETIQUETA"],                "type": "cost",     "level": 1},
    {"id": "alca",            "label": "Alça",                     "categories": ["ALÇA"],                    "type": "cost",     "level": 1},
    {"id": "faccao",          "label": "Facção",                   "categories": ["FACÇÃO"],                  "type": "cost",     "level": 1},
    {"id": "talhacao",        "label": "Talhação",                 "categories": ["TALHAÇÃO"],                "type": "cost",     "level": 1},
    {"id": "embalagem",       "label": "Embalagem",                "categories": ["EMBALAGEM"],               "type": "cost",     "level": 1},
    {"id": "frete_prod",      "label": "Frete Produção",           "categories": ["FRETE PRODUÇÃO"],          "type": "cost",     "level": 1},
    {"id": "total_cpv",       "label": "= TOTAL CPV",              "type": "subtotal", "level": 0,
     "formula": lambda v: v["material"] + v["aviamento"] + v["etiqueta"] + v["alca"] + v["faccao"] + v["talhacao"] + v["embalagem"] + v["frete_prod"]},

    {"id": "lucro_bruto",     "label": "= LUCRO BRUTO",            "type": "subtotal", "level": 0,
     "formula": lambda v: v["receita_liquida"] - v["total_cpv"]},

    {"id": "_desp",           "label": "DESPESAS OPERACIONAIS",    "type": "section",  "level": 0},
    {"id": "frete_pedido",    "label": "Frete Pedido",             "categories": ["FRETE PEDIDO"],            "type": "cost",     "level": 1},
    {"id": "contabilidade",   "label": "Contabilidade",            "categories": ["CONTABILIDADE"],           "type": "cost",     "level": 1},
    {"id": "ecommerce",       "label": "E-commerce",               "categories": ["ECOMMERCE"],               "type": "cost",     "level": 1},
    {"id": "mat_exp",         "label": "Materiais de Expediente",  "categories": ["MATERIAIS EXPEDIENTE"],    "type": "cost",     "level": 1},
    {"id": "trafego",         "label": "Tráfego",                  "categories": ["TRÁFEGO"],                 "type": "cost",     "level": 1},
    {"id": "mkt",             "label": "Serviços MKT/Eventos",     "categories": ["SERVIÇOS MKT/EVENTOS"],    "type": "cost",     "level": 1},
    {"id": "energia",         "label": "Energia",                  "categories": ["ENERGIA"],                 "type": "cost",     "level": 1},
    {"id": "condominio",      "label": "Condomínio",               "categories": ["CONDOMÍNIO"],              "type": "cost",     "level": 1},
    {"id": "aluguel",         "label": "Aluguel",                  "categories": ["ALUGUEL"],                 "type": "cost",     "level": 1},
    {"id": "transporte",      "label": "Transporte",               "categories": ["TRANSPORTE"],              "type": "cost",     "level": 1},
    {"id": "internet",        "label": "Internet",                 "categories": ["INTERNET"],                "type": "cost",     "level": 1},
    {"id": "salario",         "label": "Salário",                  "categories": ["SALÁRIO"],                 "type": "cost",     "level": 1},
    {"id": "outros",          "label": "Outros",                   "categories": ["OUTROS"],                  "type": "cost",     "level": 1},
    {"id": "total_desp",      "label": "= TOTAL DESPESAS",         "type": "subtotal", "level": 0,
     "formula": lambda v: v["frete_pedido"] + v["contabilidade"] + v["ecommerce"] + v["mat_exp"] + v["trafego"] + v["mkt"] + v["energia"] + v["condominio"] + v["aluguel"] + v["transporte"] + v["internet"] + v["salario"] + v["outros"]},

    {"id": "resultado",       "label": "= RESULTADO OPERACIONAL",  "type": "result",   "level": 0,
     "formula": lambda v: v["lucro_bruto"] - v["total_desp"]},
]

SKIP_DESCRIPTIONS = [
    "RESGATE CONTAMAX",
    "APLICACAO CONTAMAX",
    "TED RECEBIDA 47917930000178",
]

MONTHS_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}

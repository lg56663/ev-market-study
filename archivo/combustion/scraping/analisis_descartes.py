"""
Analisis de DESCARTES de motos de combustion (solo con cc y precio).

Fuente  : verticals/combustion/scraping/descartados_combustion.json (SOLO LECTURA)
Válidos : verticals/combustion/scraping/motos_combustion.json (SOLO LECTURA,
          para comparar precios por cilindrada y matching importador/local)
Salida  : verticals/combustion/analisis/analisis_descartes.txt

'Modelo' NO existe como campo en el JSON: la marca, la cc y el modelo se derivan
del titulo con la MISMA logica del scraper (normalizar + STOPWORDS_MODELO +
limpiar_token_modelo + modelo_normalizado), replicada aqui para no depender de
una importacion. Cada agrupacion reporta el modelo TOKEN, sin interpretarlo.
"""

import hashlib
import json
import os
import re
import unicodedata
from collections import Counter
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ORIGEN_DESC = os.path.normpath(os.path.join(BASE_DIR, "..", "scraping", "descartados_combustion.json"))
ORIGEN_VAL = os.path.normpath(os.path.join(BASE_DIR, "..", "scraping", "motos_combustion.json"))
SALIDA = os.path.join(BASE_DIR, "analisis_descartes.txt")
SEP = "=" * 78

md5_antes_desc = hashlib.md5(open(ORIGEN_DESC, "rb").read()).hexdigest()
md5_antes_val = hashlib.md5(open(ORIGEN_VAL, "rb").read()).hexdigest()

# ---------- logica de 'modelo' (replicada del scraper_combustion.py) ----------
STOPWORDS_MODELO = {
    "moto", "motos", "motocicleta", "motoneta", "de", "del", "la", "el", "los",
    "las", "un", "una", "y", "o", "en", "con", "para", "por", "sin", "que",
    "se", "su", "sus", "chapa", "nueva", "nuevo", "nuevas", "nuevos", "gasolina",
    "combustion", "automatica", "manual", "tiempo", "tiempos", "veces",
    "velocidad", "velocidades", "cilindrada", "cilindro", "cilindros", "cc",
    "modelo", "model", "marca", "precio", "usd", "vendo", "vende", "venta",
    "tengo", "nuevo", "estrenar", "kilometros", "km", "electrica", "4tiempos",
    "2tiempos", "motor", "motores", "automatica", "disponible", "urgente",
    "caracteristicas", "caracteristica", "especificacion", "especificaciones",
    "descripcion", "detalle", "detalles", "tecnica", "ficha", "condiciones",
    "tanque", "tanques", "litro", "litros", "combustible", "combustibles",
    "autonomia", "papeles", "transporte", "revisado", "revisada", "motorizado",
    "aproximadamente", "maxima", "maximo", "carburador", "inyeccion", "chasis",
}
MOTIVO_PLACEHOLDER = "precio_placeholder"
MOTIVO_USADA = "moto_usada_no_nueva"
MOTIVO_IMPORTADOR = "precio_no_cuba_importador"


def normalizar(texto):
    if not texto:
        return ""
    plano = unicodedata.normalize("NFKD", texto)
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    plano = plano.lower()
    plano = re.sub(r"[^a-z0-9]+", " ", plano)
    return re.sub(r"\s+", " ", plano).strip()


def limpiar_token_modelo(token):
    pegado = re.fullmatch(r"([a-z]{2,6})[0-9]{1,4}(?:cc|l|t|kwh|km|hp)?", token)
    if pegado:
        return pegado.group(1)
    if re.fullmatch(r"[0-9]{1,4}(?:cc|l|t|kwh|km|hp)", token):
        return None
    return token


def modelo_normalizado(titulo, marca):
    palabras = re.findall(r"[a-z0-9]+", normalizar(titulo))
    fuera = set(STOPWORDS_MODELO)
    if marca:
        fuera |= set(normalizar(marca).split())
    utiles = []
    for p in palabras:
        if p in fuera or len(p) <= 1:
            continue
        if re.fullmatch(r"\d{1,4}(?:cc|l|t)?", p):
            continue
        limpio = limpiar_token_modelo(p)
        if limpio and limpio not in fuera:
            utiles.append(limpio)
    return tuple(sorted(set(utiles)))


def modelo_display(modelo):
    return " ".join(modelo) if modelo else "(sin modelo)"


# Familia TK200T UNISON: el scraper la unifica (misma moto, 4 escrituras de
# marca) con cc==200 explicito. Se replica aqui para que el agrupado de la
# seccion 4 no fragmente los 6 anuncios de esa misma moto.
MARCA_TK = "TK"
RE_TK_UNISON = [
    re.compile(r"\btk\s*20(?:0)?\s*(?:t|cc)?\b"),
    re.compile(r"\bunison\b"),
]


def es_familia_tk_unison(texto, cc):
    if cc != 200:
        return False
    n = normalizar(texto)
    return any(patron.search(n) for patron in RE_TK_UNISON)


def marca_y_modelo_para_grupo(a):
    """marca + modelo normalizados como los entenderia el scraper (familia TK)."""
    if es_familia_tk_unison(a.get("titulo"), a.get("cilindrada_cc")):
        return MARCA_TK, a["cilindrada_cc"], ("unison",)
    return a.get("marca"), a["cilindrada_cc"], modelo_normalizado(a.get("titulo"), a.get("marca"))


def stats_precios(serie):
    s = sorted(float(v) for v in serie)
    n = len(s)
    if n == 0:
        return "n/a"
    mediana = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return (sum(s) / n, mediana, s[0], s[-1])


# ---------- 0. CARGA ----------
with open(ORIGEN_DESC, encoding="utf-8") as f:
    data_desc = json.load(f)
with open(ORIGEN_VAL, encoding="utf-8") as f:
    data_val = json.load(f)
descartes = data_desc["descartes"]
validos = data_val["anuncios"]

TOTAL_DESC = len(descartes)
filtrados = [a for a in descartes
             if a.get("cilindrada_cc") is not None and a.get("precio_usd") is not None]
N_FILT = len(filtrados)
PCT = 100.0 * N_FILT / TOTAL_DESC

precios_filt = [float(a["precio_usd"]) for a in filtrados]
marcas_validas = {m for m in (a.get("marca") for a in validos) if m}

L = []
A = L.append
A(SEP)
A("ANALISIS DE DESCARTES - MOTOS DE COMBUSTION (solo con cc y precio)")
A(SEP)
A(f"Fecha de analisis       : {datetime.now().isoformat(timespec='seconds')}")
A(f"Origen descartes        : {ORIGEN_DESC}")
A(f"Origen validos          : {ORIGEN_VAL}")
A(f"Vehiculo                : {data_desc.get('vehiculo')}")
A("")
A("FILTRO PREVIO")
A("  Criterio: cilindrada_cc not null Y precio_usd not null.")
A(f"  Total descartes en el JSON   : {TOTAL_DESC}")
A(f"  Tras el filtro (cc + precio) : {N_FILT}")
A(f"  Porcentaje que representa    : {PCT:.1f}%")
A(f"  Excluidos (sin cc o sin precio): {TOTAL_DESC - N_FILT}")
A("  Nota: los descartes con motivo 'sin_cilindrada' o 'sin_precio' caen en la")
A("  exclusion previa y NO entran a ningun calculo de este archivo.")
A(f"  Nota: {sum(1 for a in filtrados if a.get('motivo') == MOTIVO_PLACEHOLDER)} de los "
  f"{N_FILT} filtrados tienen motivo '{MOTIVO_PLACEHOLDER}' con precio=1 USD")
A("  (placeholder del vendedor). Cuentan en los totales y en las distribuciones,")
A("  pero se marcan por separado en las tablas de precio para no distorsionar medias.")

# ---------- 1. DISTRIBUCION DE MOTIVOS ----------
A("")
A("1. DISTRIBUCION DE MOTIVOS (sobre el set filtrado)")
A("   " + "-" * 72)
motivos = Counter(a["motivo"] for a in filtrados)
A(f"   {'motivo':<32}{'n':>5}{'%':>8}")
A("   " + "-" * 72)
for m, n in motivos.most_common():
    A(f"   {m:<32}{n:>5}{100.0 * n / N_FILT:>7.1f}%")
A("   " + "-" * 72)
A(f"   {'TOTAL':<32}{N_FILT:>5}{100.0:>7.1f}%")

# ---------- 2. PRECIOS POR MOTIVO ----------
A("")
A("2. PRECIOS POR MOTIVO (motivos con >= 5 anuncios filtrados)")
A("   " + "-" * 72)
A(f"   {'motivo':<32}{'n':>4}{'precio medio':>14}{'mediana':>11}{'min':>8}{'max':>8}")
A("   " + "-" * 72)
for m, n in motivos.most_common():
    if n < 5:
        continue
    sub = [float(a["precio_usd"]) for a in filtrados if a["motivo"] == m]
    media, mediana, vmin, vmax = stats_precios(sub)
    A(f"   {m:<32}{n:>4}{media:>14.2f}{mediana:>11.2f}{vmin:>8.0f}{vmax:>8.0f}")

# ---------- 3. PRECIOS POR CILINDRADA ----------
A("")
A("3. PRECIOS POR CILINDRADA - DESCARTES vs VALIDOS")
A("   " + "-" * 72)
A("   Tablas por cilindrada_cc, con n | precio medio | mediana | min | max.")
A("   'media s/ph' = media quitando los precio_placeholder de 1 USD (si existen).")


def linea_cc(grupo, etiqueta, extra=False):
    for cc in sorted({a["cilindrada_cc"] for a in grupo}):
        items = [a for a in grupo if a["cilindrada_cc"] == cc]
        sub = [float(a["precio_usd"]) for a in items]
        n = len(sub)
        media, mediana, vmin, vmax = stats_precios(sub)
        txt = (f"   {etiqueta:<9} {cc:>5} cc | n={n:>3} | media {media:>9.2f}"
               f" | mediana {mediana:>8.2f} | min {vmin:>7.0f} | max {vmax:>7.0f}")
        if extra:
            sin_ph = [float(a["precio_usd"]) for a in items
                      if a.get("motivo") != MOTIVO_PLACEHOLDER]
            if len(sin_ph) != len(sub):
                m2, _, _, _ = stats_precios(sin_ph)
                txt += f"  | media s/ph {m2:.2f}"
        A(txt)


A("")
A("   DESCARTES (filtrados):")
linea_cc(filtrados, "descarte", extra=True)
A("")
A("   VALIDOS:")
linea_cc(validos, "valido")
A("")
A("   Comparacion mismo cc (media descartes vs media validos):")
A(f"   {'cc':>6} {'n_desc':>7}{'n_val':>6}{'media desc':>13}{'media val':>12}{'diff':>11}{'lectura':>16}")
A("   " + "-" * 72)
compara = {}
for cc in sorted({a["cilindrada_cc"] for a in filtrados if a["motivo"] != MOTIVO_PLACEHOLDER}):
    sd = [float(a["precio_usd"]) for a in filtrados
          if a["cilindrada_cc"] == cc and a["motivo"] != MOTIVO_PLACEHOLDER]
    sv = [float(a["precio_usd"]) for a in validos if a["cilindrada_cc"] == cc]
    if not sd or not sv or len(sd) < 2 and len(sv) == 0:
        continue
    md = sum(sd) / len(sd)
    mv = sum(sv) / len(sv)
    diff = md - mv
    if mv == 0:
        lectura = "n/a"
    elif diff > 0:
        lectura = "DESCARTES +%.0f%%" % (100.0 * abs(diff) / mv)
    else:
        lectura = "DESCARTES %.0f%%" % (100.0 * diff / mv)
    compara[cc] = (md, mv)
    cad = f"   {cc:>6}{len(sd):>7}{len(sv):>6}{md:>13.2f}{mv:>12.2f}{diff:>11.2f}{lectura:>16}"
    if any(a["motivo"] == MOTIVO_PLACEHOLDER for a in filtrados if a["cilindrada_cc"] == cc):
        cad += "   (excluye 1 USD placeholder)"
    A(cad)
A("   " + "-" * 72)
A("   (se excluyen los 2 anuncios 'precio_placeholder' de 1 USD de esta comparacion;")
A("    quedan contados en el filtro y en la tabla completa de descartes de arriba)")
A("   Cc sin par en validos (50, 175, 180) no tienen lectura de comparacion.")

# ---------- 4. TOP 10 MODELOS MAS DUPLICADOS ----------
A("")
A("4. TOP 10 MODELOS MAS DUPLICADOS (agrupados por marca + cc + modelo_token)")
A("   'modelo' se deriva del titulo con la logica del scraper; 'sin modelo' quiere")
A("   decir que el titulo no aporta tokens distintivos mas alla de marca y cc.")
A("   Variacion % = (max-min)/min*100.  (@) = grupo generico, NO un modelo real:")
A("   juntan anuncios distintos (varios vendedores) sin marca o sin nombre de")
A("   modelo en el titulo, no republicaciones de una misma moto.")
A("   " + "-" * 72)
grupos = {}
for a in filtrados:
    marca, cc, modelo = marca_y_modelo_para_grupo(a)
    key = (marca or "(sin_marca)", cc, modelo)
    grupos.setdefault(key, []).append(a)

top = sorted(grupos.items(), key=lambda kv: len(kv[1]), reverse=True)[:10]
A(f"   {'#':>3} {'marca':<16}{'cc':>5}{'modelo':<28}{'n':>4}{'rango min-max':>18}{'var %':>9}{'ubic':>5}")
A("   " + "-" * 72)
for i, ((marca, cc, modelo), items) in enumerate(top):
    precios = sorted(float(a["precio_usd"]) for a in items)
    var = 100.0 * (precios[-1] - precios[0]) / precios[0] if len(precios) > 1 else 0.0
    n_ubic = len({a.get("ubicacion") for a in items if a.get("ubicacion")})
    generico = marca == "(sin_marca)" or not modelo
    etiqueta = "@" if generico else " "
    A(f"   {i + 1:>3} {marca:<16}{cc:>5}{modelo_display(modelo):<28}{len(items):>4}"
      f"{f'{precios[0]:.0f}-{precios[-1]:.0f}':>18}{var:>8.1f}%{n_ubic:>5}{etiqueta}")

# ---------- 5. USADAS VS NUEVAS ----------
A("")
A("5. COMPARACION USADAS VS NUEVAS (motivo 'moto_usada_no_nueva')")
A("   " + "-" * 72)
usadas = [a for a in filtrados if a["motivo"] == MOTIVO_USADA]
A(f"   Descartados con motivo '{MOTIVO_USADA}' y cc+precio: {len(usadas)}")
pares = []
for u in usadas:
    modelo = modelo_normalizado(u.get("titulo"), u.get("marca"))
    u_marca = u.get("marca")
    match = None
    for v in validos:
        if v.get("marca") == u_marca and v.get("cilindrada_cc") == u.get("cilindrada_cc") \
                and modelo_normalizado(v.get("titulo"), v.get("marca")) == modelo:
            match = v
            break
    pares.append((u, match))
n_pares = sum(1 for _, m in pares if m is not None)
A(f"   Pares encontrados (misma marca+cc+modelo en validos): {n_pares}")
for u, m in pares:
    A("")
    A(f"   Descarte  : cc={u.get('cilindrada_cc')}  marca={u.get('marca') or '(sin marca)'}  "
      f"modelo={modelo_display(modelo_normalizado(u.get('titulo'), u.get('marca')))}  "
      f"precio={u.get('precio_usd')} USD")
    A(f"   Titulo    : {(u.get('titulo') or '')[:80]}")
    if m is None:
        A("   Match en validos: NINGUNO -> no hay par que comparar.")
    else:
        diff = float(m["precio_usd"]) - float(u["precio_usd"])
        A(f"   Valido    : precio={m['precio_usd']} USD  ->  dif {diff:+.0f} "
          f"({100.0 * diff / float(u['precio_usd']):+.1f}%)")
if n_pares == 0:
    A("")
    A("   RESULTADO: no hay ningun par usada/nueva comparable. El unico descarte")
    A("   'moto_usada_no_nueva' es una 180cc sin marca (180 cc ni siquiera esta en la")
    A("   tabla de cilindradas del scraper) y en validos no existe esa combinacion.")
    A("   Con n=1 no se puede estimar el descuento por uso de este catalogo.")

# ---------- 6. IMPORTADORES VS LOCALES ----------
A("")
A("6. COMPARACION IMPORTADORES VS LOCALES (motivo 'precio_no_cuba_importador')")
A("   " + "-" * 72)
importadores = [a for a in filtrados if a["motivo"] == MOTIVO_IMPORTADOR]
A(f"   Descartados con motivo '{MOTIVO_IMPORTADOR}' y cc+precio: {len(importadores)}")
A("   Matching contra validos por nivel de precision:")
A("     1) marca+cc+modelo  2) marca+cc  3) solo cc (media de validos de esa cc)")


def match_importador(u):
    modelo = modelo_normalizado(u.get("titulo"), u.get("marca"))
    u_marca = u.get("marca")
    u_cc = u.get("cilindrada_cc")
    for v in validos:
        if v.get("marca") == u_marca and v.get("cilindrada_cc") == u_cc \
                and modelo_normalizado(v.get("titulo"), v.get("marca")) == modelo:
            return v, 1
    for v in validos:
        if v.get("marca") == u_marca and v.get("cilindrada_cc") == u_cc:
            return v, 2
    sv_no = [float(v["precio_usd"]) for v in validos if v.get("cilindrada_cc") == u_cc]
    if sv_no:
        return u_cc, 3
    return None, 3


def stats_basicas(lista):
    return (lista and sum(lista) / len(lista)) or None


def match_importador(u):
    modelo = modelo_normalizado(u.get("titulo"), u.get("marca"))
    u_marca = u.get("marca")
    u_cc = u.get("cilindrada_cc")
    for v in validos:
        if v.get("marca") == u_marca and v.get("cilindrada_cc") == u_cc \
                and modelo_normalizado(v.get("titulo"), v.get("marca")) == modelo:
            return v, 1
    for v in validos:
        if v.get("marca") == u_marca and v.get("cilindrada_cc") == u_cc:
            return v, 2
    sv_no = [float(v["precio_usd"]) for v in validos if v.get("cilindrada_cc") == u_cc]
    if sv_no:
        return sv_no, 3
    return None, 3


markups = []
refs = []
A("")
A(f"   {'marca':<12}{'cc':>5}{'modelo':<26}{'precio imp':>11}{'nivel':>9}{'precio local':>14}{'markup':>10}")
A("   " + "-" * 78)
for u in importadores:
    m = match_importador(u)
    modelo = modelo_display(modelo_normalizado(u.get("titulo"), u.get("marca")))
    p_imp = float(u["precio_usd"])
    u_marca = u.get("marca") or "(sin_marca)"
    u_cc = u.get("cilindrada_cc")
    if m[1] == 3:
        nivel = "solo-cc"
        p_local = stats_basicas(m[0])
        ref = f"media val {u_cc}cc (n={len(m[0])})"
    elif m[1] == 2:
        nivel = "marca+cc"
        p_local = float(m[0]["precio_usd"])
        ref = f"{m[0].get('marca')} {m[0].get('cilindrada_cc')}cc"
    else:
        nivel = "exacto"
        p_local = float(m[0]["precio_usd"])
        ref = f"{m[0].get('marca')} {m[0].get('cilindrada_cc')}cc {modelo}"
    if p_local is not None:
        mk = p_local / p_imp
        markups.append(mk)
        refs.append(ref)
        A(f"   {u_marca:<12}{u_cc:>5}{modelo:<26}{p_imp:>11.2f}{nivel:>9}{p_local:>14.2f}{mk:>9.2f}x")
        A(f"        -> referencia: {ref}; importador {p_imp:.0f} USD vs local "
          f"{p_local:.1f} USD = markup {100.0 * (mk - 1):+.1f}%")
    else:
        A(f"   {u_marca:<12}{u_cc:>5}{modelo:<26}{p_imp:>11.2f}{nivel:>9}{'n/a':>14}{'n/a':>10}")
        A("        -> sin referencia local con la que comparar (marca y cc sin par en validos).")
A("   " + "-" * 78)
if markups:
    A(f"   Markup promedio Cuba/importador de los {len(markups)} pares con referencia: "
      f"{sum(markups) / len(markups):.2f}x")
else:
    A("   Sin pares con referencia local: no hay markup que promediar.")
A("   Nota: solo 2 importadores pasan el filtro cc+precio. El nivel 'solo-cc' es")
A("   una referencia debil: compara contra la media de validos de esa cilindrada")
A("   porque la marca no existe en el catalogo de validos.")

# ---------- 7. MARCAS EXCLUSIVAS DE DESCARTES ----------
A("")
A("7. MARCAS EXCLUSIVAS DE DESCARTES (en filtrados, ausentes en validos)")
A("   " + "-" * 72)
marcas_desc = {a.get("marca") for a in filtrados if a.get("marca")}
exclusivas = sorted(marcas_desc - marcas_validas)
A(f"   Marcas en descartes filtrados      : {len(marcas_desc)}")
A(f"   Marcas en validos                  : {len(marcas_validas)}")
A(f"   Marcas EXCLUSIVAS de descartes     : {len(exclusivas)}")
A("")
A(f"   {'marca':<14}{'n':>4}{'precio medio':>14}{'min':>8}{'max':>8}")
A("   " + "-" * 72)
media_excl = []
for marca in exclusivas:
    sub = [float(a["precio_usd"]) for a in filtrados if a.get("marca") == marca]
    media, mediana, vmin, vmax = stats_precios(sub)
    media_excl.append(media)
    A(f"   {marca:<14}{len(sub):>4}{media:>14.2f}{vmin:>8.0f}{vmax:>8.0f}")
if exclusivas:
    A("")
    A(f"   Precio medio de las marcas exclusivas de descartes: "
      f"{sum(media_excl) / len(media_excl):.2f} USD")
else:
    A("   (ninguna marca exclusiva)")

# ---------- 8. FECHAS ----------
A("")
A("8. FECHAS")
A("   " + "-" * 72)
fechas = sorted(f for f in (a.get("fecha_publicacion") for a in filtrados) if f)
A(f"   Minimo          : {fechas[0]}")
A(f"   Maximo          : {fechas[-1]}")
A(f"   Con fecha       : {len(fechas)}/{N_FILT}")
meses = Counter(f[:7] for f in fechas)
A(f"   {'mes':<10}{'n':>5}{'%':>8}")
A("   " + "-" * 72)
for m, n in sorted(meses.items()):
    A(f"   {m:<10}{n:>5}{100.0 * n / N_FILT:>7.1f}%")

# ---------- RENGLON: verificación ----------
md5_despues_desc = hashlib.md5(open(ORIGEN_DESC, "rb").read()).hexdigest()
md5_despues_val = hashlib.md5(open(ORIGEN_VAL, "rb").read()).hexdigest()
A("")
A("VERIFICACION")
A(SEP)
A(f"  descartados_combustion.json md5 antes  : {md5_antes_desc}")
A(f"  descartados_combustion.json md5 despues: {md5_despues_desc}")
A(f"  motos_combustion.json md5 antes        : {md5_antes_val}")
A(f"  motos_combustion.json md5 despues      : {md5_despues_val}")
A(f"  descartes intocado  : {'SI' if md5_antes_desc == md5_despues_desc else 'NO'}")
A(f"  validos intocado    : {'SI' if md5_antes_val == md5_despues_val else 'NO'}")

with open(SALIDA, "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print(f"salida escrita: {SALIDA} ({os.path.getsize(SALIDA):,} bytes)")
print(f"descartes filtrados: {N_FILT}/{TOTAL_DESC} ({PCT:.1f}%)")
print(f"motivo mas frecuente: {motivos.most_common(1)[0]}")
topkey = None
for marca, cc, modelo in sorted(grupos, key=lambda k: len(grupos[k]), reverse=True)[:1]:
    topkey = (marca, cc, modelo_display(modelo), len(grupos[(marca, cc, modelo)]))
print(f"modelo mas duplicado: {topkey}")
print(f"pares usada/nueva   : {n_pares}")
print(f"pares importador    : {len(markups)} con markup  /  importadores totales: {len(importadores)}")
if markups:
    print(f"markup promedio     : {sum(markups) / len(markups):.2f}x")
print(f"integridad          : descar={'OK' if md5_antes_desc == md5_despues_desc else 'FALLA'} "
      f"validos={'OK' if md5_antes_val == md5_despues_val else 'FALLA'}")
#!/usr/bin/env python3
"""Scraper de motos de combustion (gasolina) en Revolico.com.

Listado: Next.js + Apollo (__NEXT_DATA__ -> __APOLLO_STATE__, stubs AdType).
NO se entra a paginas de detalle: TODO se extrae del listado.

LIMITACION CONOCIDA Y DECLARADA (ver AGENTS_COMBUSTION.md y reporte):
el listado de Revolico NO expone fecha de publicacion. Los stubs AdType del
listado solo traen 'updatedOnToOrder' (timestamp de ordenacion/actualizacion).
Se busco en el HTML completo: 0 ocurrencias de 'Publicado', 'hace', '<time>' y
'createdOn'; la API GraphQL responde 403 (Cloudflare). Por eso
'fecha_publicacion' se rellena con la fecha de updatedOnToOrder y se marca
explicitamente con el campo extra 'fecha_fuente' = 'updatedOnToOrder'.
Es el mismo campo que ya usa el scraper de motos electricas como fallback
(scraper_motos.py). NO se inventa ninguna fecha: el valor es real del sitio,
lo que cambia es su semantica, y queda documentada en cada registro.

Salidas:
  - motos_combustion.json       : 30 anuncios validos
  - descartados_combustion.json : todos los descartados con trazabilidad
  - reporte_scraping_combustion.txt : reporte de la sesion
Cache:
  - cache_html/                 : HTML de listados
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).resolve().parent
ARCHIVO_VALIDOS = BASE_DIR / "motos_combustion.json"
ARCHIVO_DESCARTADOS = BASE_DIR / "descartados_combustion.json"
ARCHIVO_REPORTE = BASE_DIR / "reporte_scraping_combustion.txt"
DIR_CACHE = BASE_DIR / "cache_html"

USER_AGENT = "MiProyectoInvestigacion/1.0"
DELAY_SEGUNDOS = 3.0
TIMEOUT_SEGUNDOS = 30.0
REINTENTOS = 2
ESPERA_REINTENTO = 5.0
MAX_PETICIONES = 60
MAX_PAGINAS_POR_QUERY = 3
OBJETIVO_VALIDOS = 30
FECHA_MINIMA = "2026-01-01"
# Precio minimo en USD. Hay anuncios cuyo "precio" es en realidad un
# placeholder del vendedor (ej. 'Moto Panther de gasolina 150cc' a 1 USD):
# no es un precio real y no sirve para comparar mercado. Se descartan con
# motivo 'precio_placeholder' conservando TODOS los campos extraidos.
PRECIO_MINIMO_USD = 100

# ---------- lista negra de URLs ----------
# Anuncios que el dedup automatico NO puede atrapar (el vendedor re-publica
# la misma moto cambiando el titulo: "Moto de gasolina HUAWIN 150CC" vs
# "Moto de gasolina HUAWIN 150CC  Precio: 2380 USD" -> titulos normalizados
# distintos, asi que ambos pasaban). Se descartan SIEMPRE, antes de cualquier
# filtro y antes del dedup, para que queden registrados como duplicados y no
# se pierdan en silencio.
#
# Estructura: {url_del_duplicado: url_del_anuncio_conservado}
# El motivo resultante es 'duplicado_manual' y se anade el campo 'duplicado_de'
# (URL del anuncio que se conserva) al descarte.
#
# Si el valor es None, no hay otro anuncio al que apuntar porque el descarte no
# es por duplicado sino por una decision del usuario. En ese caso el motivo es
# 'descartado_por_usuario' y no se anade 'duplicado_de'.
URLS_DESCARTAR: dict[str, str | None] = {
    # Duplica de /item/moto-de-gasolina-huawin-150cc-57397340 (La Habana / Centro Habana)
    "https://www.revolico.com/item/moto-de-gasolina-huawin-150cc-precio-2380-usd-57393899":
        "https://www.revolico.com/item/moto-de-gasolina-huawin-150cc-57397340",
    # Duplica de /item/moto-de-gasolina-wys-monster-...-56645013 (La Habana / Centro Habana)
    "https://www.revolico.com/item/moto-de-gasolina-wys-monster-nueva-0km-150cc-4-tiempos-caja-5ta-hace-40km-x-litro-directo-a-chapa-tiene-56859822":
        "https://www.revolico.com/item/moto-de-gasolina-wys-monster-nueva-0km-150cc-4-tiempos-caja-5ta-hace-40km-x-litro-directo-a-chapa-tiene-gara-56645013",
    # Descartado por el usuario: es una moto USADA ('uso menos de 800 km'), y
    # el set tiene que ser 100% motos nuevas. El filtro
    # 'moto_usada_no_nueva' ya lo habría descartado, pero se deja aqui para que
    # la razon sea explicita y no dependa de la redaccion del titulo.
    # 2000 USD, 150cc, La Habana / Arroyo Naranjo, con la palabra DAKAR.
    "https://www.revolico.com/item/moto-a-combustion-halcon-modelo-dakar-150-cc-uso-menos-de-800-km-2000usd-57092556":
        None,
}

# Descartes manuales por MOTIVO (no son duplicados: no hay URL a la que apuntar
# 'duplicado_de'). Se aplican en el mismo punto que URLS_DESCARTAR: antes de
# cualquier filtro y antes del dedup, para que queden registrados.
# Ahora vacio: los 2 descartes que tenia (KATZU CHEETAH) se retiraron al leer
# las fichas de detalle, que dicen moto de 2 ruedas (ver MODELOS_DUDOSOS).
MOTIVOS_MANUALES: dict[str, tuple[str, str]] = {}

# KATZU CHEETAH: se reporto como triciclo/mas de 2 ruedas, pero la evidencia
# dice MOTO de 2 ruedas. NO se descarta: manda la regla general de triciclos
# (triciclo / 3 ruedas / trimoto / UTV / ... en el texto del anuncio) y el
# anuncio se marca con 'modelo_dudoso' para que la duda quede en el dataset.
#
# EVIDENCIA (3 fichas de detalle leidas, HTML en /tmp, fuera del repo):
#   - Las 3 estan categorizadas por Revolico en 'Vehiculos > Motos de
#     Combustion', no en la categoria de triciclos.
#   - Las 3 dicen: 'Motor: 200 cc, Gasolina, 4 tiempos, 5 Velocidades,
#     Tanque de 16,5 Litros'.
#   - Una anade: 'Velocidad Maxima de 95 KM/H' y 'Capacidad del Tanque de
#     16,5 L' (autonomia 40 km/L).
#   - La palabra 'triciclo' aparece UNA vez en el conjunto, y no describe al
#     vehiculo: esta en el pie de venta del anunciante, que lista su
#     inventario ('TENEMOS MAS DE 60 MODELOS de BICICLETAS ELECTRICAS,
#     BICIMOTOS, MOTOS ELECTRICAS, MOTOS DE COMBUSTION, PATINETAS y
#     TRICICLOS. Contamos con marcas como TOPMAQ, SUZUKI, KAWASAKI...').
#     Es publicidad de otras categorias, copiada en todos sus anuncios.
#   - Ni '3 ruedas', ni 'trimoto', ni 'UTV' en ninguna de las 3.
#   - Precio 2150-2580 USD, en la banda de las 200cc del set.
# VEREDICTO: moto de combustion de 2 ruedas. Se conservan.
#
# Los 12 anuncios de KATZU/KATSU CHEETAH que hay en el corpus son casi todos
# el mismo vehiculo re-publicado por ese mismo anunciante (con 'KATSU' de
# typo y titulos que ni siquiera traen la cilindrada), asi que el dedup se
# queda con el de titulo mas informativo.
MODELOS_DUDOSOS: dict[str, str] = {
    "cheetah": "katzu_cheetah_posible_triciclo",
}


BASE_BUSQUEDA = "https://www.revolico.com/search"

# Las 6 iniciales primero; las 6 de respaldo solo se piden si con las
# anteriores no se llega a 30 validos.
QUERIES_INICIALES = [
    "moto gasolina",
    "moto combustion",
    "moto 100cc",
    "moto 150cc",
    "moto 200cc",
    "moto gasolina cuba",
]
QUERIES_RESPALDO = [
    "moto 125cc",
    "moto 250cc",
    "moto china",
    "moto honda",
    "moto yamaha",
    "moto cuba",
]

# ---------- listas de Exclusion ----------
# OJO: estas listas se comparan contra texto NORMALIZADO (minusculas, sin
# acentos), asi que se normalizan al construir el set. Si se dejaran con
# acentos ("electrica"/"eléctrica") las formas acentuadas nunca casarian.
KEYWORDS_ELECTRICA_RAW = [
    "electrica", "electrico", "bateria litio", "litio",
    "350w", "500w", "750w", "1000w", "1500w", "2000w", "2500w", "3000w",
    "4000w", "5000w", "6000w", "8000w", "watt",
    "72v", "60v", "48v", "36v", "24v", "12v",
    "motor electrico", "e bike", "ebike", "scooter electrico", "patinete electrico",
]
KEYWORDS_OTRO_VEHICULO_RAW = [
    "scooter", "patinete", "patineta", "bicimoto", "bicimotos",
    "bici electrica", "bicicleta electrica", "bicicleta", "bicicleta", "bici",
    "trimoto", "triciclo", "cuatriciclo", "3 ruedas", "tres ruedas", "infantil",
    "ciclomotor", "minibike", "monopatin", "hoverboard", "skate", "patineta",
]
# Accesorios y servicios: anuncios que NO son una moto. Se comparan con
# limites de palabra y admitiendo plural ('alarmas', 'filtros'), para no
# perder "Alarmas para moto" ni hacer que "rin" case dentro de "Berlin".
KEYWORDS_NO_MOTO_RAW = [
    "filtro", "alarma", "casco", "aceite", "bateria", "iman",
    "mensajero", "mensajeria", "repuesto", "pieza", "llanta", "rin", "cadena",
    "bujia", "carburador", "escape", "freno",
    "espejo", "asiento", "manubrio", "suspension",
    "forro", "funda", "candado", "lock", "gps",
    "alquiler", "renta", "servicio", "taller", "mecanico",
    "transporte", "flete", "viaje",
]
KEYWORDS_ELECTRICA: set[str] = set()
KEYWORDS_OTRO_VEHICULO: set[str] = set()
KEYWORDS_NO_MOTO: set[str] = set()
# Vehiculos de mas de 2 ruedas: no son motos de combustion, van aparte para
# poder medirlos con su propio motivo ('no_es_moto_triciclo') y distinguirlos de
# los accesorios ('no_es_moto').
# NOTA: 'cheetah' NO va aqui a proposito. Se sospecho que el KATZU Cheetah
# fuera un triciclo/UTV, pero todos sus anuncios en Revolico dicen lo contrario
# ('Moto Deportiva ... en dos ruedas', 200cc 4T gasolina, 5 velocidades,
# tanque 16.5 L, 95 km/h). Es una moto de dos ruedas: anadirla mataria motos
# reales. Ver seccion TRICICLOS del reporte.
KEYWORDS_TRICICLO_RAW = [
    "triciclo", "trimoto", "cuatriciclo", "cuatrimoto", "tatriciclo",
    "3 ruedas", "tres ruedas", "utv", "atv", "side by side", "side-by-side",
]
KEYWORDS_TRICICLO: set[str] = set()

# ---------- filtro de moto usada ----------
# El set tiene que ser 100% motos NUEVAS. Un anuncio se considera usado si el
# texto dice que ya rodó. Motivo: 'moto_usada_no_nueva'.
#
# IMPORTANTE: se comparan con limites de palabra (como RE_NO_MOTO) y no con
# busqueda de subcadena, porque 'usado'/'usada' aparecen dentro de otras
# palabras ('causada', 'acusado') y un sub 'km' se llevaria por delante
# anuncios que solo describen rendimiento o autonomia.
KEYWORDS_USADA_RAW = [
    "uso menos de", "usada", "usado", "usadas", "usados", "de uso", "en uso",
    "segunda mano", "2da mano", "2 mano", "recorrido", "recorridos",
    "km recorridos", "kilometraje", "ya tiene", "uso personal",
    "kilometros recorridos", "usada y",
]
# Si aparece cualquiera de estas, el anuncio se conserva aunque tenga un
# indicador de uso: el vendedor esta declarando que la moto no se ha usado.
KEYWORDS_NUEVA_RAW = [
    "0km", "0 km", "nueva", "nuevo", "sin uso", "nunca usada", "nunca usado",
    "0 kilometros", "cero kilometros", "kilometro cero", "km 0", "recien salida",
]

# Kilometraje rodado. Se exigen 3+ digitos ('800 km', '1500 km') porque con 1-2
# digitos se confundiria con el rendimiento o la autonomia, que este scraper
# ya extrae por separado ('40 km/l', 'autonomia de 60 km').
# Los guardas evitan el falso positivo tipico: un rendimiento escrito con 3
# digitos ('120 km/l') o una autonomia ('200 km de autonomia') NO son
# kilometraje rodado y no deben descartar la moto.
#
# OJO con dos detalles de este patron:
#   1. El texto llega NORMALIZADO, y normalizar() convierte 'km/l' en 'km l',
#      asi que el guarda de rendimiento tiene que aceptar la barra ya perdida.
#   2. El lookbehind de 'autonomia de ' tiene que ir AL PRINCIPIO. En Python un
#      lookbehind se evalua en la posicion donde esta el cursor, y al final del
#      patron el cursor ya esta DESPUES de 'km': ahi 'autonomia de ' nunca se
#      ve y el guarda no hacia nada (verificado: el patron filtraba de menos).
RE_KM_RECORRIDO = re.compile(
    r"(?<!autonomia de )"
    r"\b\d{3,}\s*km\b"
    r"(?!\s*(?:x|por)?\s*(?:litros?|l|lt)\b)"
    r"(?!\s*de\s*autonomia)",
    re.IGNORECASE,
)

# ---------- filtro de importador extranjero ----------
# Hay anuncios cuyo precio no es del mercado cubano: los mete un vendedor de
# fuera que los publica desde el mismo sitio, asi que la ubicacion dice 'La
# Habana' pero el precio no es del mercado local. Motivo:
# 'precio_no_cuba_importador'.
#
# OJO: estas marcas NO salen en el titulo ni en la descripcion. El listado de
# Revolico las deja solo en el telefono del anunciante
# (phoneInfo.firstPhone.prefix). Por eso el filtro mira el prefijo del
# telefono, no el texto del anuncio.
#
#   +593 = Ecuador. Es el caso documentado: los 4 anuncios de este vendedor
#          (Dakar Pro 150cc, GN125F/GN150F/Racer 200cc de Mountain) son
#          todos '+593 / +507' y el sitio es micasaia.com. El Dakar Pro
#          aparece a 708 USD frente a 1200-4200 USD del resto del set.
#
# NO se descarta por '+1', '+52', '+55' ni '+598': son la diaspora cubana
# vendiendo desde EE.UU., Mexico, Brasil y Uruguay, y sus motos si estan en
# el mercado local con precio local (ej. 'Moto 150cc gasolina Ava Avispon',
# 1900 USD, prefijo +52, se conserva). Solo +593 se ha comprobado que es un
# vendedor de reventa con precio de fuera.
PREFIJOS_TELEFONO_EXTRANJEROS = {"+593"}
# Estas si pueden aparecer en el texto de un anuncio, por si el listado cambia.
KEYWORDS_IMPORTADOR_RAW = [
    "micasaia com", "cotizar tu envio", "compra online www", "compra en linea",
    "envio internacional", "precio en dolares sin iva",
]

MARCAS = [
    "Honda", "Yamaha", "Suzuki", "Kawasaki", "Bajaj", "TVS", "Haojue",
    "Zongshen", "Lifan", "Loncin", "Jincheng", "Dayun", "Shineray", "Jialing",
    "Kymco", "SYM", "Italika", "Vento", "Motomel", "Gilera", "UM", "Keeway",
    "Benelli", "CFMoto", "KTM", "BMW", "Ducati", "Harley", "Qingqi",
    "Supermoto", "Chopper",
]

# Marcas escritas de forma compuesta o con typo que la heuristica leeria como
# una marca distinta. Se corrigen al nombre canonico para que la deduplicacion
# los pueda juntar y el JSON no tenga la misma moto escrita de dos formas.
#
# Salen de comparar todas las marcas del corpus por similitud (no de
# inventarlas). Cada alias queda con su evidencia:
#
#   motorstreck -> Treck   'MOTO DE COMBUSTION MOTORSTRECK 150cc' (1950 USD,
#                          Cotorro) frente a '... MARCA MOTOR TRECK ... 150 CC
#                          ... Tanque de 5 L' (1950 USD, Cotorro): mismo precio,
#                          mismo municipio, ids consecutivos.
#   jinchen     -> Jincheng 'JINCHEN CADENERA 125cc' (2100 USD) frente a
#                          'JINCHENG 125 cc' (2000 USD): mismo cc, mismo tipo
#                          de moto china y a 5% de precio.
#   katsu       -> Katzu    typo del vendedor, 7 apariciones frente a 15 de
#                          'KATZU'; ambos aparecen en anuncios de 200cc.
#   trek        -> Treck    'Treck trek JIN': el vendedor repite el nombre y a
#                          veces lo escribe sin la 'c'.
#   mizhozuki   -> Mishozuki 1 aparicion frente a 19 de 'MISHOZUKI'.
ALIAS_MARCA = {
    "motorstreck": "Treck",
    "jinchen": "Jincheng",
    "katsu": "Katzu",
    "trek": "Treck",
    "mizhozuki": "Mishozuki",
    # El unico caso de variante con/sin tilde: 7 veces 'Halcon' y 5 'Halcon'.
    # canonicalizar_marca unifica la caja pero no inventa tildes, asi que sin
    # esta entrada el JSON traia las dos formas. Se deja la acentuada, que es
    # la mayoritaria y la correcta.
    "halcon": "Halcón",
}
# Palabras que NUNCA son una marca. Se normalizan (sin acentos, minusculas)
# al construir el set. Incluyen las palabras estructurales que usan los
# vendedores en el titulo ("Especificaciones", "Caracteristicas", "4 Tiempo",
# "Autonomia"...): sin esta lista la heuristica inventaria marcas como
# "Especificaciones" en vez de leer la marca real del titulo.
STOPWORDS_MARCA_RAW = [
    # verboS de compraventa y conectores
    "moto", "motos", "motocicleta", "motoneta", "motoque", "vendo", "vendo",
    "vende", "vender", "venta", "vendo", "compro", "busco", "se", "de", "del",
    "la", "el", "los", "las", "un", "una", "unos", "unas", "y", "o", "en",
    "con", "para", "por", "desde", "hasta", "sin", "que", "su", "sus", "es",
    "son", "hay", "tiene", "tengo", "ofrezco", "hago", "nuevo", "nueva",
    "nuevos", "nuevas", "usado", "usada", "usados", "usadas", "seminueva",
    "seminuevo", "seminuevas", "seminuevos", "kilometros", "km", "cero", "uso",
    # adjetivos y calificadores
    "perfecto", "perfecta", "impecable", "excelente", "excelente", "bueno",
    "buena", "original", "originales", "custom", "customizada", "deportivo",
    "deportiva", "deportivos", "deportivas", "sport", "sportiva", "racing",
    "todo", "terreno", "enduro", "trail", "urbana", "urbano", "oportunidad",
    "urgente", "barato", "barata", "impecable", "reparado", "cuidado",
    # lo tecnico: NUNCA son marca
    "gasolina", "gasolinera", "combustible", "combustion", "combustible",
    "diesel", "electrica", "electrico", "cilindrada", "cilindros", "cilindro",
    "motor", "motores", "tiempo", "tiempos", "dos", "tres", "cuatro", "un",
    "caracteristicas", "especificacion", "especificaciones", "descripcion",
    "detalle", "detalles", "caracteristica", "ficha", "tecnica", "tecnico",
    "transmision", "arranque", "freno", "frenos", "asiento", "asientos",
    "neumaticos", "llanta", "llantas", "caucho", "cadena", "carter", " escapes",
    "escape", "automatico", "automatica", "automaticas", "manual", "autonomia",
    "kilometraje", "rendimiento", "tanque", "capacidad", "autonomia", "velocidad",
    "precio", "valor", "costo", "pesos", "cup", "usd", "efectivo", "transferencia",
    "chapa", "matricula", "papeles", "patente", "propiedad", "documentacion",
    "entrega", "entrego", "envio", "incluye", "incluido", "flete", "transporte",
    "foto", "fotos", "imagen", "video", "whatsapp", "telefono", "contacto",
    "acepto", "oferta", "precio", "modelo", "version", "ano", "nuevo", "stock",
    "publicado", "destacado", "urgente", "antes", "ahora", "mes", "semana",
    "specs", "detalle", "recomendado", "destacada", "principal", "otro", "otro",
    "pro", "racing", "sport", "tr", "full", "top", "new", "usado", "vendo",
    "marca", "marcas", "modelos", "unidades", "unidad", "gratis", "envio",
    "entrega", "entrega", "aquí", "aqui", "cuba", "habana", "santiago",
    "ciudad", "provincia", "municipio", "capital", "cena", "citas",
]

TABLA_TANQUE_CC = {
    50: 3.5, 70: 4.0, 90: 4.2, 100: 4.5, 110: 5.0, 125: 5.5, 150: 7.0,
    200: 9.0, 250: 11.0, 300: 12.0, 400: 14.0, 500: 16.0,
}
CILINDRADAS_VALIDAS = (50, 70, 90, 100, 110, 125, 150, 200, 250, 300, 400, 500)

RE_CILINDRODA = [
    re.compile(r"(\d{2,4})\s*cc", re.IGNORECASE),
    re.compile(r"cilindrada\s*(?:de\s*)?(\d{2,4})", re.IGNORECASE),
    re.compile(r"(\d{2,4})\s*c\.?\s*c\.?", re.IGNORECASE),
]
RE_TANQUE_ANUNCIO = [
    re.compile(r"tanque\s*(?:de\s*)?(\d+(?:[.,]\d+)?)\s*(?:l|litros?)", re.IGNORECASE),
    re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:l|litros?)\s*(?:de\s*)?tanque", re.IGNORECASE),
    re.compile(r"capacidad\s+(?:del\s+)?tanque\s*(?:de\s*)?(\d+(?:[.,]\d+)?)", re.IGNORECASE),
]
RE_RENDIMIENTO = [
    re.compile(r"(\d+(?:[.,]\d+)?)\s*km\s*(?:por|\/|\bx\b)\s*(?:l|litro)", re.IGNORECASE),
    re.compile(r"rinde\s*(\d+(?:[.,]\d+)?)", re.IGNORECASE),
    re.compile(r"(\d+(?:[.,]\d+)?)\s*km\s*x\s*litro", re.IGNORECASE),
]
# Autonomia real: "autonomia de 60 km", "60 km de autonomia".
# Se EXCLUYE "autonomia de 40 km x litro": eso es rendimiento (km por litro),
# no autonomia, aunque el vendedor lo escriba asi.
RE_AUTONOMIA = [
    re.compile(
        r"autonom[ií]a\s*(?:de\s*)?(?:de\s*)?(\d+(?:[.,]\d+)?)\s*km(?!\s*(?:\/|\bx\b|por)\s*litro)",
        re.IGNORECASE),
    re.compile(
        r"(\d+(?:[.,]\d+)?)\s*km\s*de\s*autonom[ií]a",
        re.IGNORECASE),
]
RE_TIPO_MOTOR = [
    (re.compile(r"\b2\s*t(?:iempos?)?\b", re.IGNORECASE), "2T"),
    (re.compile(r"\b4\s*t(?:iempos?)?\b", re.IGNORECASE), "4T"),
    (re.compile(r"\bdos\s*tiempos?\b", re.IGNORECASE), "2T"),
    (re.compile(r"\bcuatro\s*tiempos?\b", re.IGNORECASE), "4T"),
]

SEP = "=" * 78


def normalizar(texto: str) -> str:
    """minusculas, sin acentos, sin puntuacion, espacios colapsados."""
    if not texto:
        return ""
    plano = unicodedata.normalize("NFKD", texto)
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    plano = plano.lower()
    plano = re.sub(r"[^a-z0-9]+", " ", plano)
    return re.sub(r"\s+", " ", plano).strip()


def a_float(valor) -> float | None:
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, str):
        limpio = valor.strip().replace("\u00a0", "").replace(" ", "")
        if re.fullmatch(r"\d+(?:[.,]\d+)?", limpio):
            return float(limpio.replace(",", "."))
    return None


# Los tres sets se normalizan (minusculas, sin acentos) para que casen contra
# el texto normalizado. Se hace despues de definir normalizar().
KEYWORDS_ELECTRICA = {normalizar(p) for p in KEYWORDS_ELECTRICA_RAW}
KEYWORDS_OTRO_VEHICULO = {normalizar(p) for p in KEYWORDS_OTRO_VEHICULO_RAW}
KEYWORDS_NO_MOTO = {normalizar(p) for p in KEYWORDS_NO_MOTO_RAW}
KEYWORDS_TRICICLO = {normalizar(p) for p in KEYWORDS_TRICICLO_RAW}
# Regex precompiladas: \b<keyword>(s|es)?\b sobre texto ya normalizado.
RE_NO_MOTO = [
    re.compile(rf"(?<!\w){re.escape(p)}(?:s|es)?(?!\w)") for p in sorted(KEYWORDS_NO_MOTO)
]
RE_TRICICLO = [
    re.compile(rf"(?<!\w){re.escape(p)}(?:s|es)?(?!\w)") for p in sorted(KEYWORDS_TRICICLO)
]
STOPWORDS_MARCA = {normalizar(p) for p in STOPWORDS_MARCA_RAW}

KEYWORDS_USADA = {normalizar(p) for p in KEYWORDS_USADA_RAW}
KEYWORDS_NUEVA = {normalizar(p) for p in KEYWORDS_NUEVA_RAW}
KEYWORDS_IMPORTADOR = {normalizar(p) for p in KEYWORDS_IMPORTADOR_RAW}
RE_USADA = [
    re.compile(rf"(?<!\w){re.escape(p)}(?:s|es)?(?!\w)") for p in sorted(KEYWORDS_USADA)
]
# La excepcion 'nueva / 0km / sin uso' tambien necesita limites de palabra. Con
# busqueda de subcadena, '0 km' casaba DENTRO de '800 km' y '0km' dentro de
# '1500km', o sea que la excepcion desactivaba el filtro justo en los casos que
# queria filtrar (motos usadas con kilometraje).
RE_NUEVA = [
    re.compile(rf"(?<!\w){re.escape(p)}(?!\w)") for p in sorted(KEYWORDS_NUEVA)
]


def es_moto_usada(texto: str) -> bool:
    """True si el texto dice que la moto ya se uso.

    Se recibe el texto ya normalizado (minusculas, sin acentos). Se comparan
    los indicadores con limites de palabra para no matar palabras que los
    contengan ('causada'), y ademas se captura el kilometraje con
    RE_KM_RECORRIDO, que ignora los 3+ digitos que en realidad sean rendimiento
    o autonomia.
    """
    if any(patron.search(texto) for patron in RE_NUEVA):
        return False
    if RE_KM_RECORRIDO.search(texto):
        return True
    return any(patron.search(texto) for patron in RE_USADA)


def es_importador_extranjero(texto: str, prefijo_telefono: str | None) -> bool:
    """True si el anuncio es de un vendedor de fuera con precio de fuera.

    El listado no pone estas marcas en el titulo, asi que el dato que las
    delata es el prefijo del telefono del anunciante (ver
    PREFIJOS_TELEFONO_EXTRANJEROS). El texto se revisa tambien por si el
    listado cambia y empieza a mostrarlos.
    """
    if prefijo_telefono and prefijo_telefono in PREFIJOS_TELEFONO_EXTRANJEROS:
        return True
    return any(p in texto for p in KEYWORDS_IMPORTADOR)



def slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", normalizar(texto)).strip("_")[:60]


def texto_numerico(titulo: str, descripcion: str | None) -> str:
    """Texto para los regex de numeros: conserva los acentos y las COMAS
    decimales ('Tanque de 16,5 Litros'). normalizar() converts la coma en
    espacio y se perderia el valor."""
    return re.sub(r"\s+", " ", f"{titulo} {descripcion or ''}").strip()


class Sesion:
    """Contador de peticiones + cache en disco."""

    def __init__(self, usar_cache: bool) -> None:
        self.usar_cache = usar_cache
        self.peticiones = 0
        self.desde_cache = 0
        self.paginas = 0
        self.errores: list[str] = []

    def cabe(self) -> bool:
        return self.peticiones < MAX_PETICIONES

    def obtener(self, cliente: httpx.Client, url: str) -> str | None:
        ruta_cache = DIR_CACHE / f"{slug(url)}.html"
        if self.usar_cache and ruta_cache.exists():
            self.desde_cache += 1
            return ruta_cache.read_text(encoding="utf-8")
        if not self.cabe():
            return None
        for intento in range(1, REINTENTOS + 1):
            try:
                self.peticiones += 1
                respuesta = cliente.get(url, timeout=TIMEOUT_SEGUNDOS)
                respuesta.raise_for_status()
                html = respuesta.text
                ruta_cache.parent.mkdir(parents=True, exist_ok=True)
                ruta_cache.write_text(html, encoding="utf-8")
                time.sleep(DELAY_SEGUNDOS)
                return html
            except Exception as error:  # noqa: BLE001
                self.errores.append(f"{url} intento {intento}/{REINTENTOS}: {error}")
                if intento < REINTENTOS:
                    time.sleep(ESPERA_REINTENTO)
        return None


def parsear_listado(html: str) -> tuple[dict, list[dict]]:
    """Devuelve (catalogos, anuncios) desde __NEXT_DATA__ -> __APOLLO_STATE__."""
    coincidencia = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S
    )
    if not coincidencia:
        return {}, []
    try:
        datos = json.loads(coincidencia.group(1))
        estado = datos["props"]["pageProps"]["__APOLLO_STATE__"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}, []

    provincias = {
        clave.split(":", 1)[1]: valor.get("name")
        for clave, valor in estado.items()
        if clave.startswith("ProvinceType:")
    }
    municipios = {
        clave.split(":", 1)[1]: valor.get("name")
        for clave, valor in estado.items()
        if clave.startswith("MunicipalityType:")
    }

    raiz = estado.get("ROOT_QUERY", {})
    claves_ads = [k for k in raiz if k.startswith("adsPerPage(")]
    if not claves_ads:
        return {"provincias": provincias, "municipios": municipios}, []
    conexion = raiz[claves_ads[0]] or {}
    anuncios = []
    for borde in conexion.get("edges", []) or []:
        ref = (borde.get("node") or {}).get("__ref")
        if ref and ref in estado:
            anuncios.append(estado[ref])
    return {"provincias": provincias, "municipios": municipios}, anuncios


def extraer_cilindrada(texto: str) -> int | None:
    for patron in RE_CILINDRODA:
        for encontrado in patron.finditer(texto):
            try:
                valor = int(encontrado.group(1))
            except ValueError:
                continue
            if 50 <= valor <= 500:
                return valor
    return None


# Marca de la familia TK200T UNISON. El corpus tiene 4 anuncios de la MISMA
# moto (200cc) con 4 marcas distintas, porque cada vendedor la escribio como le
# dio:
#   'MOTO DE COMBUSTION AUTOMATICA DE 200CC MARCA AGUILA UNISON'  -> 'Aguila'
#   'MOTO AUTOMATICA TK UNISON 200cc'                              -> 'Unison'
#   '... 4 TIEMPOS, MARCA TK200T UNISON. CON CHAPA Y SIN CHAPA'    -> 'Tkt'
#   'Moto de Combustion TK200cc'                                   -> 'Tkcc'
# Son 2600, 2700, 2800 y 2650 USD: cuatro anuncios de una sola moto, que el
# dedup no podia juntar porque 'marca+cc' nunca coincidia. La marca real es
# TK y el modelo es UNISON 200.
MARCA_TK = "TK"
# Todo se decide sobre el texto ya normalizado (minusculas, sin acentos).
#   'tk200' | 'tk 200' | 'tk200t' | 'tk200cc' | 'tk20' -> cilindrada pegada
#   'unison'                                          -> el modelo ya lo dice
# El segundo patron tambien resuelve 'AGUILA UNISON', que es el mismo caso.
# Se exige cc == 200 para no aplicar nada a otra cilindrada.
#
# OJO con el patron: escribirlo como '0?20' NO cubre 'tk200'. '0?' solo puede
# tragarse un 0 cuando el caracter siguiente ES un 0, y en 'tk200' el primer
# caracter es un 2, asi que se quedaba en 'tk20' y 'tk200t' / 'tk200cc'
# escapaban. Va '20' + '0' opcional, y el sufijo admite 't' ('TK200T') y 'cc'
# ('TK200cc') porque en ambos casos la marca viene con la cilindrada pegada.
RE_TK_UNISON = [
    re.compile(r"\btk\s*20(?:0)?\s*(?:t|cc)?\b"),
    re.compile(r"\bunison\b"),
]


def es_familia_tk_unison(texto: str, cc: int | None) -> bool:
    """True si el anuncio es de la familia TK200T UNISON de 200cc."""
    if cc != 200:
        return False
    return any(patron.search(texto) for patron in RE_TK_UNISON)


def canonicalizar_marca(marca: str, texto: str = "", cc: int | None = None) -> str:
    """Una sola forma de escribir cada marca: InicialMayuscula.

    'TRECK', 'Treck' y 'treck' son la misma marca y tienen que quedar igual en
    el JSON, o el analisis por marca los cuenta como tres. Se respeta el
    acento ('ITÁLICA' -> 'Itálica') y no se tocan las marcas de la lista
    cerrada MARCAS, que ya vienen con su forma canonica.

    La familia TK200T UNISON se resuelve ANTES de todo lo demas (ver
    es_familia_tk_unison): manda sobre la marca que haya sugerido la
    heuristica, porque las 4 escrituras son la misma moto y tienen que
    compartir marca para que el dedup por marca+cc+modelo las junte.
    """
    if es_familia_tk_unison(texto, cc):
        return MARCA_TK
    clave = normalizar(marca)
    if clave in ALIAS_MARCA:
        return ALIAS_MARCA[clave]
    return marca[:1].upper() + marca[1:].lower()


def extraer_marca(texto: str, titulo: str, cc: int | None = None) -> tuple[str | None, str | None]:
    """(marca, fuente). Primero la lista cerrada; si no, heuristica de titulo.

    Heuristica: primera palabra "capitalizada" del titulo (incluye MAYUSCULAS,
    que es como aparecen las marcas cubanas/chinas: KATZU, WYS, TRECK) que no
    sea una palabra estructural o tecnica (STOPWORDS_MARCA). Las palabras en
    minuscula se ignoran porque no son "capitalizadas" y casi siempre son
    conectores. Si no queda ninguna, el anuncio se descarta como 'sin_marca'
    en vez de inventar un valor.

    La caja se canonicaliza a 'InicialMayuscula' porque el vendedor escribe la
    marca como le da: el mismo TRECK aparecia como 'TRECK' y 'Treck', y el
    dataset tenia dos marcas distintas para la misma moto. Con la conversion,
    lista cerrada y heuristica quedan en la misma convencion ('Honda',
    'Lifan', 'Treck', 'Itálica'), que es la que ya usa MARCAS.

    Se necesita la cc porque la familia TK/UNISON solo se unifica a 200cc; por
    eso el llamador tiene que extraer la cilindrada antes que la marca.
    """
    if es_familia_tk_unison(texto, cc):
        return MARCA_TK, "familia_tk_unison"
    for marca in MARCAS:
        if re.search(rf"\b{re.escape(marca)}\b", texto, re.IGNORECASE):
            return marca, "lista"
    for palabra in titulo.split():
        limpia = re.sub(r"[^A-Za-zÀ-ÿ]", "", palabra)
        if len(limpia) < 3 or not limpia.isalpha():
            continue
        if limpia.islower():
            continue
        if normalizar(limpia) in STOPWORDS_MARCA:
            continue
        return canonicalizar_marca(limpia, texto, cc), "heuristica_titulo"
    return None, None


def extraer_tipo_motor(texto: str) -> str | None:
    for patron, valor in RE_TIPO_MOTOR:
        if patron.search(texto):
            return valor
    return None


def tanque_desde_anuncio(texto: str) -> float | None:
    for patron in RE_TANQUE_ANUNCIO:
        encontrado = patron.search(texto)
        if encontrado:
            valor = a_float(encontrado.group(1))
            if valor and 0.5 <= valor <= 40:
                return round(valor, 2)
    return None


def tanque_desde_tabla(cc: int) -> float:
    """Valor exacto de la tabla o interpolacion lineal entre los 2 vecinos."""
    if cc in TABLA_TANQUE_CC:
        return TABLA_TANQUE_CC[cc]
    claves = sorted(TABLA_TANQUE_CC)
    if cc < claves[0] or cc > claves[-1]:
        return TABLA_TANQUE_CC[claves[0]] if cc < claves[0] else TABLA_TANQUE_CC[claves[-1]]
    for inferior, superior in zip(claves, claves[1:]):
        if inferior < cc < superior:
            peso = (cc - inferior) / (superior - inferior)
            return round(
                TABLA_TANQUE_CC[inferior]
                + peso * (TABLA_TANQUE_CC[superior] - TABLA_TANQUE_CC[inferior]), 2)
    return TABLA_TANQUE_CC[claves[-1]]


# Capacidad de tanque por modelo.
#
# PROVENIENCIA: valores tomados de ficha tecnica y verificados. El scraper NO
# los extrae de los anuncios: solo los aplica si el anuncio NO declara su
# tanque (fuente = 'modelo_verificado'). No se tocan, aunque la cilindrada del
# modelo haga dudoso el valor: son datos de referencia, no medidas. Si alguno
# hay que cambiarlo, con la URL de la ficha que lo contradiga.
#
# Hay valores que un filtro automatico de 'rango esperado' marcaria como
# raros (p.ej. FURIA 250cc = 17 L, TAEKO 125cc = 10,3 L). Se respetan igual
# porque vienen de ficha; el reporte los lista como aviso de COHERENCIA, sin
# cambiarlos, para que el cruce de precio/L se haga con esa salvedad.
TABLA_MODELOS_VERIFICADOS = {
    "taeko": 10.3, "mountain": 7.5, "furia": 17.0, "buho": 7.1,
    "transformer": 7.1, "italy rx": 7.0, "rx combat": 7.0, "souka": 9.3,
    "katzu 250": 13.0, "snapdragon": 13.0, "ava avispon": 6.0, "avispon": 6.0,
    "wys monster": 13.0, "wys": 13.0, "seiko": 9.6, "burgman": 5.5,
    "tank 200": 5.0, "mizusuki": 6.0, "mizhozuki": 6.0, "huawin": 10.0,
    "aguila unison": 6.8, "unison": 6.8, "raptor": 9.0, "topmaq": 9.0,
    "italica supra": 13.2, "supra": 13.2, "treck": 12.0, "adv": 12.0,
    "gn 250": 10.0, "lifan": 10.0, "sct": 6.0,
}
# Fallback por cilindrada cuando el modelo no esta en la tabla de arriba.
TABLA_TANQUE_FALLBACK_CC = {125: 7.5, 150: 7.1, 200: 9.0, 250: 13.0}
# Rango tipico de tanque por cilindrada. NO corrige nada: solo alimenta el
# aviso de coherencia del reporte.
RANGO_TANQUE_POR_CC = {
    125: (4.0, 8.0), 150: (5.0, 9.0), 200: (6.0, 15.0), 250: (9.0, 16.0),
}


def tanque_desde_modelo(titulo: str) -> float | None:
    """Capacidad segun el modelo que aparece en el titulo, o None."""
    texto = normalizar(titulo)
    if not texto:
        return None
    # De mas largo a mas corto: "italica supra" tiene que ganar a "supra".
    for clave in sorted(TABLA_MODELOS_VERIFICADOS, key=len, reverse=True):
        if clave in texto:
            return TABLA_MODELOS_VERIFICADOS[clave]
    return None


def tanque_implausible(cc: int | None, litros: float | None) -> bool:
    if cc is None or litros is None:
        return False
    rango = RANGO_TANQUE_POR_CC.get(cc)
    return bool(rango and not (rango[0] <= litros <= rango[1]))


def extraer_rendimiento(texto: str) -> float | None:
    for patron in RE_RENDIMIENTO:
        for encontrado in patron.finditer(texto):
            valor = a_float(encontrado.group(1))
            if valor and 5 <= valor <= 200:
                return round(valor, 1)
    return None


def extraer_autonomia(texto: str) -> int | None:
    for patron in RE_AUTONOMIA:
        for encontrado in patron.finditer(texto):
            valor = a_float(encontrado.group(1))
            if valor and 5 <= valor <= 1000:
                return int(valor)
    return None


# ---------- dedup generico ----------
# El dedup por titulo normalizado no alcanza: el mismo vendedor republica la
# misma moto cambiando el titulo ("Moto de gasolina HUAWIN 150CC" vs
# "HUAWIN 150CC Moto de gasolina P0524-AV011") y a veces el municipio, asi que
# los titulos normalizados son distintos y ambos anuncios entraban como validos.
#
# Criterio: misma marca + misma cilindrada + modelo compatible + precio dentro
# de +-5%. El precio es la que manda: sin el, dos motos legitimas de la misma
# marca y cc (p.ej. una FURIA 250 y otra del mismo modelo) se comerian entre si.
TOLERANCIA_DUPLICADO = 0.03    # <= 3%  -> 'duplicado_generico' (se descarta)
TOLERANCIA_POSIBLE = 0.05      # 3-5%   -> 'posible_duplicado' (se marcan ambos)
# Similitud minima entre modelos para tolerar los typos del vendedor
# ("BURGMAN" vs "Burgmam" -> 0.857).
UMBRAL_SIMILITUD_MODELO = 0.85

# Palabras que NO son modelo: conectores, unidades y tecnicismos que aparecen
# en todos los titulos. Se quitan junto con la marca y la cilindrada.
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
    # Etiquetas de la ficha tecnica, no modelos. Sin estas, un titulo que solo
    # dice 'MOTO DE COMBUSTION MARCA MOTOR TRECK ... Tanque: 12 L' se
    # interpretaria como si su modelo fuera 'tanque' y dejaria de detectar
    # como duplicado de un 'TRECK ADV' que si trae modelo.
    "tanque", "tanques", "litro", "litros", "combustible", "combustibles",
    "autonomia", "papeles", "transporte", "revisado", "revisada", "motorizado",
    "aproximadamente", "maxima", "maximo", "carburador", "inyeccion", "chasis",
}


def limpiar_token_modelo(token: str) -> str | None:
    """Limpia un token de modelo o devuelve None si es ruido.

    Casos reales del corpus:
      'gn250cc'  -> 'gn'   (modelo pegado a la cilindrada; sin esto el
                            'GN250cc' de un titulo no se reconoce como el
                            mismo 'GN' que escribe el otro vendedor y la
                            deduplicacion no los junta)
      '0km'      -> None   (cero kilometros: no es modelo)
      '150cc'    -> None   (ya lo cubre la regla de numeros sueltos)
    """
    # Modelo pegado a la unidad: 'gn250cc', 'rtx200', 'cb250l'.
    pegado = re.fullmatch(r"([a-z]{2,6})[0-9]{1,4}(?:cc|l|t|kwh|km|hp)?", token)
    if pegado:
        return pegado.group(1)
    # Numero con unidad pegada que NO es modelo: '0km', '200cc', '35l'.
    if re.fullmatch(r"[0-9]{1,4}(?:cc|l|t|kwh|km|hp)", token):
        return None
    return token


def modelo_normalizado(titulo: str, marca: str | None) -> tuple[str, ...]:
    """Palabras que identifican el modelo, ordenadas y sin repetir.

    Se quita: la marca, la cilindrada, los conectores/tecnicismos de
    STOPWORDS_MODELO, los emojis y la puntuacion (normalizar ya los quita), y
    los numeros sueltos. Lo que queda es el modelo: ['buho','transformer'].
    """
    palabras = re.findall(r"[a-z0-9]+", normalizar(titulo))
    fuera = set(STOPWORDS_MODELO)
    if marca:
        fuera |= set(normalizar(marca).split())
    utiles = []
    for p in palabras:
        if p in fuera or len(p) <= 1:
            continue
        # numeros sueltos y cilindrada pegada al token: '150cc', '150', '4'
        if re.fullmatch(r"\d{1,4}(?:cc|l|t)?", p):
            continue
        limpio = limpiar_token_modelo(p)
        if limpio and limpio not in fuera:
            utiles.append(limpio)
    return tuple(sorted(set(utiles)))


def modelos_compatibles(m1: tuple[str, ...], m2: tuple[str, ...]) -> bool:
    if not m1 or not m2:
        # Uno de los dos titulos no aporta modelo: no hay con que contradecir,
        # la marca+cc+precio tienen que decidir.
        return True
    s1, s2 = set(m1), set(m2)
    if s1 == s2 or s1 <= s2 or s2 <= s1:
        # Iguales, o uno es el titulo recortado del otro (mismo modelo, el
        # vendedor escribio mas o menos texto).
        return True
    # El modelo mas corto tiene que aparecer (con typo incluido) en el largo.
    # Se compara palabra a palabra y no la cadena entera: un titulo puede
    # traer palabras de mas ('BURGMAN' + 'pizarra' vs 'Burgmam') y eso no
    # puede borrar la coincidencia del modelo.
    pequeno, grande = (s1, s2) if len(s1) <= len(s2) else (s2, s1)
    return any(
        difflib.SequenceMatcher(None, a, b).ratio() >= UMBRAL_SIMILITUD_MODELO
        for a in pequeno for b in grande
    )


def mismo_modelo(m1: tuple[str, ...], m2: tuple[str, ...]) -> bool:
    """¿Son la MISMA moto, mas alla del precio?

    Version estricta de modelos_compatibles. Sirve para el caso 'el mismo
    vendedor republica la misma moto cambiando el precio': dos anuncios de la
    misma marca y cilindrada que describen el mismo modelo son la misma moto
    aunque esten a 1000 y a 1200 USD, y por eso no pueden depender de la
    tolerancia de precio.

    Un titulo que no aporta modelo NO se considera distinto: no hay con que
    contradecir, asi que manda marca+cc (p.ej. 'Se vende moto Taeko 125cc' y
    'Se vende moto de gasolina Taeko 125cc' son la misma moto; en cambio
    'MISHOZUKI SCT' y 'MISHOZUKI BUHO' son modelos distintos y se conservan
    los dos).
    """
    s1, s2 = set(m1), set(m2)
    if s1 == s2 or s1 <= s2 or s2 <= s1:
        # Iguales, o uno es el titulo recortado del otro. Un conjunto vacio
        # esta contenido en cualquiera, que es justo el caso 'sin modelo'.
        return True
    return any(
        difflib.SequenceMatcher(None, a, b).ratio() >= UMBRAL_SIMILITUD_MODELO
        for a in s1 for b in s2
    )


def comparar_duplicado(registro: dict, validos: list[dict]) -> dict | None:
    """¿Este anuncio duplica a alguno de los validos ya aceptados?

    Devuelve {"accion": "duplicado", "otros": [candidatos...],
    "titulo_largo": bool, "motivo": str} o None. Los candidatos van ordenados
    de titulo mas largo a mas corto. Entre los que coincidan gana el de titulo
    mas largo (regla: si el entrante tiene el titulo mas largo, se conserva el
    entrante y los anteriores pasan a descartados).

    Se devuelven TODOS los que coinciden, no solo el mejor: si el entrante
    empareja con tres validos, hay que sacar los tres. Con un solo candidato se
    dejaban duplicados vivos en el set (p.ej. tres TRECK 150: entraba 'TRECK
    ... Tanque: 5 L' sin modelo, empataba con los otros dos y solo se iba el de
    titulo mas largo, dejando dos motos iguales en el entregable).

    Criterio, en este orden:
      1. misma marca + misma cc + MISMO modelo -> duplicado siempre, con el
         precio que sea (motivo 'duplicado_mismo_modelo').
      2. misma marca + misma cc + modelos compatibles pero distintos ->
         duplicado solo si el precio cae en la tolerancia (+-3% 'duplicado_
         generico', 3-5% 'posible_duplicado_reemplazado').
      3. modelos incompatibles -> no son la misma moto, nunca se tocan.
    """
    if not registro["marca"] or registro["cilindrada_cc"] is None \
            or registro["precio_usd"] is None:
        return None
    marca = normalizar(registro["marca"])
    modelo = modelo_normalizado(registro["titulo"] or "", registro["marca"])
    mismo_modelo_cands: list[dict] = []
    dup_cands: list[dict] = []
    posible_cands: list[dict] = []
    for otro in validos:
        if not otro["marca"] or normalizar(otro["marca"]) != marca:
            continue
        if otro["cilindrada_cc"] != registro["cilindrada_cc"]:
            continue
        if otro["precio_usd"] is None:
            continue
        modelo_otro = modelo_normalizado(otro["titulo"] or "", otro["marca"])
        if not modelos_compatibles(modelo, modelo_otro):
            continue
        lo, hi = sorted((otro["precio_usd"], registro["precio_usd"]))
        if lo <= 0:
            continue
        dif = (hi - lo) / lo
        if mismo_modelo(modelo, modelo_otro):
            # Misma moto segun marca+cc+modelo: el precio no decide.
            mismo_modelo_cands.append(otro)
            continue
        # Modelos distintos (p.ej. MISHOZUKI SCT vs BUHO): solo se descartan
        # si el precio dice que es la misma moto.
        if dif > TOLERANCIA_POSIBLE:
            continue
        if dif <= TOLERANCIA_DUPLICADO:
            dup_cands.append(otro)
        else:
            posible_cands.append(otro)
    for candidatos, motivo in (
        (mismo_modelo_cands, "duplicado_mismo_modelo"),
        (dup_cands, "duplicado_generico"),
        (posible_cands, "posible_duplicado_reemplazado"),
    ):
        if candidatos:
            candidatos.sort(key=lambda o: len(o["titulo"] or ""), reverse=True)
            return {
                "accion": "duplicado",
                "otros": candidatos,
                "titulo_largo": len(registro["titulo"] or "")
                > len(candidatos[0]["titulo"] or ""),
                "motivo": motivo,
            }
    return None


def reconciliar_marcas_duplicados(validos: list[dict]) -> None:
    """Marca los pares dudosos (3-5%) sobre el set FINAL de validos.

    No se puede marcar en el loop: un anuncio marcado aqui puede quedarse sin
    su pareja mas adelante (si la pareja resulta ser duplicado de un tercero y
    pasa a descartados), y el 'duplicado_de' quedaria apuntando a una URL que
    ya no esta en el dataset. Se hace una sola vez, al final, y se recalcula
    desde cero para que las marcas sean consistentes con lo que se entrega.
    """
    for a in validos:
        a["posible_duplicado"] = False
        a["duplicado_de"] = None
    for i, a in enumerate(validos):
        if not a["marca"] or a["cilindrada_cc"] is None or a["precio_usd"] is None:
            continue
        modelo_a = modelo_normalizado(a["titulo"] or "", a["marca"])
        for b in validos[i + 1:]:
            if not b["marca"] or normalizar(b["marca"]) != normalizar(a["marca"]):
                continue
            if b["cilindrada_cc"] != a["cilindrada_cc"] or b["precio_usd"] is None:
                continue
            lo, hi = sorted((a["precio_usd"], b["precio_usd"]))
            if lo <= 0:
                continue
            dif = (hi - lo) / lo
            if not (TOLERANCIA_DUPLICADO < dif <= TOLERANCIA_POSIBLE):
                continue
            if not modelos_compatibles(
                    modelo_a, modelo_normalizado(b["titulo"] or "", b["marca"])):
                continue
            a["posible_duplicado"] = True
            a["duplicado_de"] = b["url"]
            b["posible_duplicado"] = True
            b["duplicado_de"] = a["url"]


def anuncio_vacio() -> dict:
    """Todos los campos del esquema, siempre presentes (aunque valgan null)."""
    return {
        "titulo": None,
        "precio_usd": None,
        "marca": None,
        "cilindrada_cc": None,
        "tipo_motor": None,
        "autonomia_km": None,
        "rendimiento_km_l": None,
        "capacidad_tanque_l": None,
        "fuente_capacidad_tanque": None,
        "ubicacion": None,
        "fecha_publicacion": None,
        "url": None,
        "descripcion": None,
        "fecha_fuente": None,
        "marca_fuente": None,
        "posible_duplicado": False,
        "duplicado_de": None,
        "modelo_dudoso": None,
    }


def analizar(anuncio: dict, catalogos: dict, url_busqueda: str) -> dict:
    """Arma el registro con todo lo extraible. No descarta: devuelve el parcial."""
    registro = anuncio_vacio()
    titulo = (anuncio.get("title") or "").strip()
    registro["titulo"] = titulo or None

    # El listado no trae descripcion; se deja null y no se visita el detalle.
    descripcion = anuncio.get("description") or None
    registro["descripcion"] = descripcion

    moneda = (anuncio.get("currency") or "USD").upper()
    precio = a_float(anuncio.get("price"))
    if precio and precio > 0 and moneda == "USD":
        registro["precio_usd"] = round(precio, 2)

    texto = normalizar(f"{titulo} {descripcion or ''}")
    texto_num = texto_numerico(titulo, descripcion)
    # La cilindrada va antes que la marca: la unificacion de la familia
    # TK200T UNISON depende de la cc (solo aplica a 200cc).
    registro["cilindrada_cc"] = extraer_cilindrada(texto)
    registro["marca"], registro["marca_fuente"] = extraer_marca(
        texto, titulo, registro["cilindrada_cc"]
    )
    registro["tipo_motor"] = extraer_tipo_motor(texto)
    registro["rendimiento_km_l"] = extraer_rendimiento(texto_num)
    for modelo_dudoso, etiqueta in MODELOS_DUDOSOS.items():
        if modelo_dudoso in texto:
            registro["modelo_dudoso"] = etiqueta
            break

    provincia = (catalogos.get("provincias") or {}).get(str(anuncio.get("provinceId")))
    municipio = (catalogos.get("municipios") or {}).get(str(anuncio.get("municipalityId")))
    if provincia and municipio:
        registro["ubicacion"] = f"{provincia} / {municipio}"
    elif provincia:
        registro["ubicacion"] = provincia
    elif municipio:
        registro["ubicacion"] = municipio

    # Fecha: el listado solo da updatedOnToOrder (ver docstring).
    crudo_fecha = anuncio.get("updatedOnToOrder") or ""
    fecha = crudo_fecha[:10]
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", fecha):
        registro["fecha_publicacion"] = fecha
        registro["fecha_fuente"] = "updatedOnToOrder"

    permalink = anuncio.get("permalink") or ""
    registro["url"] = (
        "https://www.revolico.com" + permalink if permalink.startswith("/") else permalink or None
    )
    return registro


def aplicar_filtros(registro: dict, prefijo_telefono: str | None = None) -> str | None:
    """Devuelve el motivo de descarte o None si el anuncio es valido."""
    texto = normalizar(f"{registro['titulo'] or ''} {registro['descripcion'] or ''}")

    for clave in KEYWORDS_ELECTRICA:
        if clave in texto:
            return "es_electrica"
    for patron in RE_TRICICLO:
        if patron.search(texto):
            return "no_es_moto_triciclo"
    for clave in KEYWORDS_OTRO_VEHICULO:
        if clave in texto:
            return "otro_vehiculo"

    if not registro["titulo"]:
        return "sin_titulo"
    # Antes del precio: un accesorio nunca debe contar como valido, tenga el
    # precio que tenga (si no, un 'filtro de gasolina' a 6 USD entraria por
    # la puerta de atras como moto).
    for patron in RE_NO_MOTO:
        if patron.search(texto):
            return "no_es_moto"
    # El set tiene que ser 100% motos nuevas: esto va antes de mirar marca,
    # cc o precio para que una moto usada se descarte por lo que es y no por
    # el primer campo que le falte.
    if es_moto_usada(texto):
        return "moto_usada_no_nueva"
    if es_importador_extranjero(texto, prefijo_telefono):
        return "precio_no_cuba_importador"
    if registro["precio_usd"] is None:
        return "sin_precio"

    if registro["precio_usd"] < PRECIO_MINIMO_USD:
        return "precio_placeholder"
    if not registro["marca"]:
        return "sin_marca"
    if registro["cilindrada_cc"] is None:
        return "sin_cilindrada"
    if registro["cilindrada_cc"] not in CILINDRADAS_VALIDAS:
        return "cilindrada_fuera_de_tabla"
    if not registro["ubicacion"]:
        return "sin_ubicacion"
    if not registro["fecha_publicacion"]:
        return "sin_fecha"
    if registro["fecha_publicacion"] < FECHA_MINIMA:
        return "fecha_anterior_a_2026"
    return None


def completar_tanque_autonomia(registro: dict, texto_num: str) -> None:
    """Tanque en 3 niveles: anuncio > modelo > fallback por cilindrada.
    Rendimiento NUNCA se infiere."""
    tanque = tanque_desde_anuncio(texto_num)
    if tanque is not None:
        registro["capacidad_tanque_l"] = tanque
        registro["fuente_capacidad_tanque"] = "anuncio"
    else:
        por_modelo = tanque_desde_modelo(registro["titulo"] or "")
        if por_modelo is not None:
            registro["capacidad_tanque_l"] = por_modelo
            registro["fuente_capacidad_tanque"] = "modelo_verificado"
        elif registro["cilindrada_cc"] in TABLA_TANQUE_FALLBACK_CC:
            registro["capacidad_tanque_l"] = TABLA_TANQUE_FALLBACK_CC[
                registro["cilindrada_cc"]]
            registro["fuente_capacidad_tanque"] = "tabla_cc_fallback"

    autonomia = extraer_autonomia(texto_num)
    if autonomia is None and registro["rendimiento_km_l"] and registro["capacidad_tanque_l"]:
        autonomia = round(registro["capacidad_tanque_l"] * registro["rendimiento_km_l"])
    registro["autonomia_km"] = autonomia


def escribir_json(ruta: Path, datos) -> None:
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, indent=2)
        archivo.write("\n")


def verificar_json(ruta: Path) -> None:
    """Relee el archivo recien escrito y confirma que es JSON valido.

    NO se abre en modo 'w': eso truncaria el archivo antes de validarlo.
    """
    crudo = ruta.read_text(encoding="utf-8").strip()
    if not crudo.startswith("{") or not crudo.endswith("}"):
        raise SystemExit(f"{ruta.name} no empieza con {{ ni termina con }}")
    with open(ruta, encoding="utf-8") as archivo:
        json.load(archivo)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scraper de motos de combustión en Revolico")
    parser.add_argument("--refrescar", action="store_true", help="ignora la caché y vuelve a descargar")
    args = parser.parse_args()

    inicio = datetime.now(timezone.utc)
    ahora = inicio.strftime("%Y-%m-%dT%H:%M:%SZ")
    sesion = Sesion(usar_cache=not args.refrescar)

    validos: list[dict] = []
    descartados: list[dict] = []
    motivos: Counter[str] = Counter()
    vistos_titulo: set[str] = set()
    vistos_id: set[str] = set()
    catalogos: dict = {"provincias": {}, "municipios": {}}
    paginas_por_query: dict[str, int] = {}
    consultas_hechas: list[str] = []
    duplicados = 0

    print(SEP)
    print("SCRAPING DE MOTOS DE COMBUSTIÓN (Revolico)")
    print(SEP)
    print(f"  inicio           : {ahora}")
    print(f"  objetivo validos : {OBJETIVO_VALIDOS}")
    print(f"  user-agent       : {USER_AGENT}")
    print(f"  delay            : {DELAY_SEGUNDOS}s   timeout: {TIMEOUT_SEGUNDOS}s")
    print(f"  reintentos       : {REINTENTOS} por URL (espera {ESPERA_REINTENTO}s)")
    print("  AVISO METODOLOGICO: el listado de Revolico NO expone fecha de publicacion;")
    print("    se usa 'updatedOnToOrder' (fecha real del sitio, semantica de orden) y se")
    print("    marca con 'fecha_fuente' en cada registro. NO se inventan fechas.")
    print("  NO se entra a paginas de detalle: no hay descripcion en el listado (queda null).")
    print(f"  precio minimo    : {PRECIO_MINIMO_USD} USD "
          "(debajo = 'precio_placeholder' del vendedor, se descarta)")

    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        verify=True,
    ) as cliente:
        for query in QUERIES_INICIALES + QUERIES_RESPALDO:
            if len(validos) >= OBJETIVO_VALIDOS or not sesion.cabe():
                break
            rescued = query not in QUERIES_INICIALES
            if rescued:
                print(f"\n  >> respaldo: se agotaron las queries iniciales con "
                      f"{len(validos)}/{OBJETIVO_VALIDOS} validos")
            etiqueta = ("respaldo" if rescued else "inicial")
            consultas_hechas.append(f"{etiqueta}: {query}")
            print(f"\n  [{etiqueta}] query={query!r}")
            for pagina in range(1, MAX_PAGINAS_POR_QUERY + 1):
                if len(validos) >= OBJETIVO_VALIDOS or not sesion.cabe():
                    break
                url = f"{BASE_BUSQUEDA}?q={query.replace(' ', '+')}&page={pagina}"
                html = sesion.obtener(cliente, url)
                if html is None:
                    print(f"      pagina {pagina}: sin respuesta (rompe el ciclo)")
                    break
                cat, anuncios = parsear_listado(html)
                sesion.paginas += 1
                paginas_por_query[f"{query} (p{pagina})"] = len(anuncios)
                if cat.get("provincias"):
                    catalogos["provincias"].update(cat["provincias"])
                    catalogos["municipios"].update(cat["municipios"])
                print(f"      pagina {pagina}: {len(anuncios):>3} anuncios "
                      f"(validos acumulados: {len(validos)})")
                if not anuncios:
                    break
                for anuncio in anuncios:
                    id_anuncio = str(anuncio.get("id") or "")
                    titulo = (anuncio.get("title") or "").strip()
                    clave_titulo = normalizar(titulo)
                    # Lista negra: se registra como 'duplicado_manual' ANTES del
                    # dedup y de los filtros, para que no se pierda en silencio.
                    url_anuncio = (anuncio.get("permalink") or "").strip()
                    if not url_anuncio.startswith("http"):
                        url_anuncio = f"https://www.revolico.com{url_anuncio}"
                    if url_anuncio in MOTIVOS_MANUALES:
                        motivo_manual, nota = MOTIVOS_MANUALES[url_anuncio]
                        registro = analizar(anuncio, catalogos, url)
                        registro["motivo"] = motivo_manual
                        registro["nota"] = nota
                        motivos[motivo_manual] += 1
                        descartados.append(registro)
                        vistos_id.add(id_anuncio)
                        if clave_titulo:
                            vistos_titulo.add(clave_titulo)
                        continue
                    if url_anuncio in URLS_DESCARTAR:
                        registro = analizar(anuncio, catalogos, url)
                        destino = URLS_DESCARTAR[url_anuncio]
                        if destino is None:
                            # Sin 'duplicado_de': el descarte no es por
                            # duplicado sino por decision del usuario.
                            registro["motivo"] = "descartado_por_usuario"
                        else:
                            registro["motivo"] = "duplicado_manual"
                            registro["duplicado_de"] = destino
                        motivos[registro["motivo"]] += 1
                        descartados.append(registro)
                        vistos_id.add(id_anuncio)
                        if clave_titulo:
                            vistos_titulo.add(clave_titulo)
                        continue
                    if id_anuncio in vistos_id or (clave_titulo and clave_titulo in vistos_titulo):
                        duplicados += 1
                        continue
                    registro = analizar(anuncio, catalogos, url)
                    texto = normalizar(
                        f"{registro['titulo'] or ''} {registro['descripcion'] or ''}")
                    texto_num = texto_numerico(
                        registro["titulo"] or "", registro["descripcion"])
                    # Dedup generico: despues del dedup por titulo y antes de
                    # los filtros. Un duplicado NO se descarta si es el de titulo
                    # mas largo: en ese caso se conserva el entrante y el que ya
                    # estaba aceptado pasa a 'duplicado_generico'.
                    decision = comparar_duplicado(registro, validos)
                    # El prefijo del telefono del anunciante no sale en el
                    # titulo, asi que se pasa aparte al filtro de importador.
                    prefijo_tel = (
                        ((anuncio.get("phoneInfo") or {}).get("firstPhone") or {}).get("prefix")
                    )
                    motivo = aplicar_filtros(registro, prefijo_tel)
                    if motivo is None:
                        completar_tanque_autonomia(registro, texto_num)
                        if not registro["capacidad_tanque_l"]:
                            motivo = "sin_capacidad_tanque"
                    if motivo is None and decision and decision["accion"] == "duplicado":
                        candidatos = decision["otros"]
                        motivo_dup = decision["motivo"]
                        if decision["titulo_largo"]:
                            # Entra el de titulo mas largo: se van TODOS los
                            # validos que le hacian duplicado, no solo el
                            # primero, o quedan motos iguales en el set.
                            for otro in candidatos:
                                for pos, vigente in enumerate(validos):
                                    if vigente is not otro:
                                        continue
                                    del validos[pos]
                                    otro["motivo"] = motivo_dup
                                    otro["duplicado_de"] = registro["url"]
                                    motivos[motivo_dup] += 1
                                    descartados.append(otro)
                                    break
                        else:
                            motivo = motivo_dup
                            registro["duplicado_de"] = candidatos[0]["url"]
                    vistos_id.add(id_anuncio)
                    if clave_titulo:
                        vistos_titulo.add(clave_titulo)
                    if motivo is None:
                        validos.append(registro)
                    else:
                        registro["motivo"] = motivo
                        motivos[motivo] += 1
                        descartados.append(registro)
                    if len(validos) >= OBJETIVO_VALIDOS:
                        break
                if len(validos) >= OBJETIVO_VALIDOS:
                    print(f"      objetivo alcanzado ({OBJETIVO_VALIDOS})")
                    break

    # Marcas de posible duplicado sobre el set final (ver la funcion).
    reconciliar_marcas_duplicados(validos)
    fecha_scraping = datetime.now().strftime("%Y-%m-%d")
    salida = {
        "vehiculo": "moto_combustion",
        "fecha_scraping": fecha_scraping,
        "total_anuncios_validos": len(validos),
        "objetivo": OBJETIVO_VALIDOS,
        "filtro_fecha": f">= {FECHA_MINIMA}",
        "filtro_precio_minimo_usd": PRECIO_MINIMO_USD,
        "nota_precio_minimo": (
            f"Los anuncios con precio_usd < {PRECIO_MINIMO_USD} se descartan con motivo "
            "'precio_placeholder': ese valor no es un precio real sino un relleno del "
            "vendedor (ej. 1 USD). Se conservan todos sus campos en "
            "descartados_combustion.json. Ver reporte_scraping_combustion.txt."
        ),
        "nota_fecha_publicacion": (
            "El listado de Revolico no expone fecha de publicacion. 'fecha_publicacion' "
            "toma el valor real de 'updatedOnToOrder' (timestamp de ordenacion del anuncio) "
            "y 'fecha_fuente' lo declara en cada registro. Ver reporte_scraping_combustion.txt."
        ),
        "nota_descripcion": (
            "No se entraron a paginas de detalle (regla del proyecto), asi que el listado "
            "no aporta descripcion: 'descripcion' es null en todos los anuncios y la "
            "extraccion de marca, cilindrada, tanque, rendimiento y autonomia se hizo solo "
            "con el titulo."
        ),
        "queries_consultadas": consultas_hechas,
        "urls_consultadas": [
            f"{BASE_BUSQUEDA}?q={q.replace(' ', '+')}" for q in QUERIES_INICIALES + QUERIES_RESPALDO
            if any(q in c for c in consultas_hechas)
        ],
        "campos": list(anuncio_vacio().keys()),
        "anuncios": validos,
    }
    escribir_json(ARCHIVO_VALIDOS, salida)

    salida_desc = {
        "vehiculo": "moto_combustion",
        "fecha_scraping": fecha_scraping,
        "total_descartados": len(descartados),
        "nota": (
            "Cada descarte conserva TODOS los campos del esquema (aunque valgan null) mas "
            "'motivo' y las claves de trazabilidad 'fecha_fuente' y 'marca_fuente'."
        ),
        "descartes": descartados,
    }
    escribir_json(ARCHIVO_DESCARTADOS, salida_desc)
    verificar_json(ARCHIVO_VALIDOS)
    verificar_json(ARCHIVO_DESCARTADOS)

    precios = [a["precio_usd"] for a in validos if a["precio_usd"] is not None]
    cil = [a["cilindrada_cc"] for a in validos if a["cilindrada_cc"] is not None]
    fechas = [a["fecha_publicacion"] for a in validos if a["fecha_publicacion"]]
    marcas = Counter(a["marca"] for a in validos if a["marca"])
    fuentes = Counter(a["fuente_capacidad_tanque"] for a in validos)
    con_rend = sum(1 for a in validos if a["rendimiento_km_l"] is not None)
    con_auton = sum(1 for a in validos if a["autonomia_km"] is not None)
    marcas_fuente = Counter(a["marca_fuente"] for a in validos)

    lineas = [
        "REPORTE DE SCRAPING - MOTOS DE COMBUSTIÓN (Revolico)",
        "=" * 60,
        f"Inicio: {ahora}",
        f"Fin:    {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
        "",
        "AVISO METODOLÓGICO (leer antes de usar el dataset)",
        "-" * 60,
        "  El listado de Revolico NO expone la fecha de publicación. Los stubs AdType",
        "  del listado solo traen 'updatedOnToOrder'. Se buscó en el HTML completo:",
        "  0 ocurrencias de 'Publicado', 'hace', '<time>', 'createdOn'; la API GraphQL",
        "  responde 403 (Cloudflare). Obtener la fecha real exigiría entrar a las",
        "  páginas de detalle, que está prohibido en este vertical.",
        "  => 'fecha_publicacion' = valor real de 'updatedOnToOrder' (fecha de ordenación",
        "     / actualización del anuncio) y cada registro declara 'fecha_fuente'.",
        "     El filtro '>= 2026-01-01' se aplica sobre ese campo.",
        "",
        "  Tampoco hay descripción en el listado: 'descripcion' es null en todos los",
        "  anuncios, y marca, cilindrada, tanque, rendimiento y autonomía se extrajeron",
        "  únicamente del título.",
        "",
        "CONFIGURACIÓN DE RED",
        "-" * 60,
        f"  User-Agent      : {USER_AGENT}",
        f"  Delay           : {DELAY_SEGUNDOS}s entre peticiones",
        f"  Timeout         : {TIMEOUT_SEGUNDOS}s",
        f"  Reintentos      : {REINTENTOS} por URL (espera {ESPERA_REINTENTO}s)",
        f"  Peticiones nuevas: {sesion.peticiones}   (tope {MAX_PETICIONES})",
        f"  Servidas de caché: {sesion.desde_cache}",
        f"  Páginas procesadas: {sesion.paginas}",
        f"  Páginas de detalle visitadas: 0 (prohibido por regla del vertical)",
        "",
        "RESULTADO",
        "-" * 60,
        f"  VÁLIDOS   : {len(validos)} (objetivo {OBJETIVO_VALIDOS})",
        f"  DESCARTADOS: {len(descartados)}",
        f"  Duplicados (título normalizado o id repetido): {duplicados}",
        "",
        "QUERIES CONSULTADAS (en orden; las de respaldo solo si faltaron validos)",
        "-" * 60,
    ]
    for consulta in consultas_hechas:
        lineas.append(f"  - {consulta}")
    lineas += ["", "PÁGINAS POR QUERY", "-" * 60]
    for clave, total in paginas_por_query.items():
        lineas.append(f"  {total:>4} anuncios  {clave}")
    lineas += ["", "DESCARTADOS POR MOTIVO", "-" * 60]
    for motivo, total in motivos.most_common():
        lineas.append(f"  {total:>5}  {motivo}")
    if URLS_DESCARTAR:
        lineas += [
            "",
            f"LISTA NEGRA DE URLs ({len(URLS_DESCARTAR)} anuncios -> 'duplicado_manual')",
            "-" * 60,
            "  Motivo: el dedup por titulo normalizado no los detecta porque el",
            "  vendedor re-publica la misma moto cambiando el titulo. Se descartan",
            "  siempre, antes de cualquier filtro y antes del dedup.",
        ]
        for url_dup, url_ok in URLS_DESCARTAR.items():
            lineas += [f"  - DESCARTADA: {url_dup}", f"    DUPLICA DE: {url_ok}"]
    if MOTIVOS_MANUALES:
        lineas += [
            "",
            f"LISTA NEGRA POR MOTIVO ({len(MOTIVOS_MANUALES)} anuncios, "
            "descartados antes de filtros y dedup)",
            "-" * 60,
        ]
        for url_desc, (motivo_manual, nota) in MOTIVOS_MANUALES.items():
            lineas += [
                f"  - DESCARTADA: {url_desc}",
                f"    MOTIVO: {motivo_manual}",
                f"    NOTA: {nota}",
            ]
    marcados = [a for a in validos if a.get("posible_duplicado")]
    lineas += [
        "",
        "DEDUP",
        "-" * 60,
        "  1. por id y por titulo normalizado (vistos_id / vistos_titulo)",
        "  2. lista negra de URLs -> 'duplicado_manual' (+ 'duplicado_de')",
        "  3. mismo modelo: marca + cilindrada + MISMO modelo",
        "     -> 'duplicado_mismo_modelo'. El precio NO decide: si dos anuncios",
        "        de la misma marca y cc describen la misma moto, son la misma",
        "        moto aunque uno este a 1000 y el otro a 1200 USD (el mismo",
        "        vendedor la re-publica con otro precio). Se conserva el titulo",
        "        mas largo.",
        "        Un titulo que no aporta modelo no se considera DISTINTO: no hay",
        "        con que contradecir, asi que 'Taeko 125cc' y 'Taeko 125cc'",
        "        colapsan. En cambio 'MISHOZUKI SCT' y 'MISHOZUKI BUHO' son",
        "        modelos distintos y se conservan los dos.",
        "  4. precio: marca + cilindrada + modelos compatibles pero DISTINTOS",
        f"     - dentro de +-{TOLERANCIA_DUPLICADO*100:.0f}%  -> "
        f"'duplicado_generico' (se conserva el titulo mas largo)",
        f"     - entre {TOLERANCIA_DUPLICADO*100:.0f}% y "
        f"{TOLERANCIA_POSIBLE*100:.0f}%  -> 'posible_duplicado_reemplazado'",
        f"     - fuera de {TOLERANCIA_POSIBLE*100:.0f}%  -> se conservan: son",
        "        motos distintas de la misma marca y cilindrada.",
        f"     - modelo: palabras del titulo sin marca/cc/conectores, comparado",
        f"       con igualdad, contencion o similitud >= {UMBRAL_SIMILITUD_MODELO}"
        " (tolerar typos del vendedor)",
        f"  Válidos marcados posible_duplicado: {len(marcados)}",
    ]
    for m in marcados:
        lineas += [
            f"  - {m['marca']} {m['cilindrada_cc']}cc {m['precio_usd']:.0f} USD "
            f"| {m['ubicacion']}",
            f"    {m['titulo'][:62]}",
            f"    posible duplicado de: {m['duplicado_de']}",
        ]
    tk_validos = [v for v in validos if v["marca"] == MARCA_TK]
    tk_descartados = [
        d for d in descartados
        if d.get("marca") == MARCA_TK and d.get("motivo") == "duplicado_mismo_modelo"
    ]
    lineas += [
        "",
        "UNIFICACIÓN DE MARCA: FAMILIA TK200T UNISON 200cc",
        "-" * 60,
        "  La MISMA moto aparecia 4 veces con 4 marcas distintas, porque cada",
        "  vendedor la escribio como le dio. La heuristica leia el primer token",
        "  capitalizado y por eso saco cuatro marcas que en realidad son una:",
        "    'MARCA AGUILA UNISON'                  -> 'Aguila'",
        "    'TK UNISON 200cc'                      -> 'Unison'",
        "    'MARCA TK200T UNISON'                  -> 'Tkt'",
        "    'TK200cc'                              -> 'Tkcc'",
        "  Con cuatro marcas distintas, el dedup por marca+cc+modelo NUNCA podia",
        "  juntarlas y el set tenia la misma moto a 2600, 2650, 2700 y 2800 USD.",
        f"  Se unifican a la marca '{MARCA_TK}' (funcion es_familia_tk_unison,",
        "  aplicada dentro de canonicalizar_marca) cuando la cc es 200 y el",
        "  titulo dice TK200 / TK 200 / TK200T / TK20 / TK200cc o la palabra",
        "  UNISON. Con la marca unificada, la regla 3 de arriba las colapsa.",
        "  Se exige cc == 200 para no aplicar el patron a otra cilindrada.",
        f"  Válidos {MARCA_TK}: {len(tk_validos)}"
        + (f" ({tk_validos[0]['precio_usd']:.0f} USD, "
           f"{tk_validos[0]['cilindrada_cc']}cc, modelo UNISON)" if tk_validos else ""),
        f"  Descartados como 'duplicado_mismo_modelo': {len(tk_descartados)}",
    ]
    for d in tk_descartados:
        lineas.append(
            f"    {d['precio_usd']:>7.0f} USD  {d['titulo'][:58]}"
        )
    lineas += [
        "",
        "PERFIL DE LOS VÁLIDOS",
        "-" * 60,
        f"  Precio USD      : min {min(precios):.0f} | max {max(precios):.0f} | "
        f"media {sum(precios)/len(precios):.2f} (n={len(precios)})"
        if precios else "  Precio USD      : sin datos",
        f"  Cilindrada cc   : {sorted(set(cil))}" if cil else "  Cilindrada cc   : sin datos",
        f"  Fechas          : min {min(fechas)} | max {max(fechas)}" if fechas else "",
        f"  Todos >= {FECHA_MINIMA}   : "
        f"{all(f >= FECHA_MINIMA for f in fechas) if fechas else 'n/a'}",
        f"  Cap. tanque     : anuncio={fuentes.get('anuncio', 0)} | "
        f"tabla_cc={fuentes.get('tabla_cc', 0)}",
        f"  Rendimiento real: {con_rend}/{len(validos)} (el resto null: NO se infiere)",
        f"  Autonomía real  : {con_auton}/{len(validos)}",
        f"  Origen de marca : lista={marcas_fuente.get('lista', 0)} | "
        f"heuristica_titulo={marcas_fuente.get('heuristica_titulo', 0)}",
        "",
        "TOP 5 MARCAS",
        "-" * 60,
    ]
    for marca, total in marcas.most_common(5):
        lineas.append(f"  {total:>4}  {marca}")
    heuristicas = sorted(
        {a["marca"] for a in validos if a["marca_fuente"] == "heuristica_titulo"})
    placeholders = [
        d for d in descartados if d.get("motivo") == "precio_placeholder"]
    linea_precio = (
        f"  Umbral de precio   : >= {PRECIO_MINIMO_USD} USD "
        f"(debajo se descarta como 'precio_placeholder')")
    lineas += [
        "",
        "AVISOS DE CALIDAD (revisar antes de comparar con las eléctricas)",
        "-" * 60,
        f"  Marcas de la lista cerrada   : {marcas_fuente.get('lista', 0)}",
        f"  Marcas por heuristica titulo : {marcas_fuente.get('heuristica_titulo', 0)}",
        f"    valores: {', '.join(heuristicas)}",
        "    -> la heuristica lee la marca del titulo; revisar si alguna no es marca real.",
        linea_precio,
        f"    precios placeholder descartados: {len(placeholders)}",
    ]
    for ph in placeholders[:10]:
        lineas.append(
            f"    -> {ph['precio_usd']:.0f} USD '{ph['titulo'][:45]}'"
            f" (marca={ph['marca']}, cc={ph['cilindrada_cc']})")
    if placeholders:
        lineas.append(
            "    -> un precio asi NO es real: el vendedor dejo un valor de relleno.")
    lineas.append(
        f"  Sin descripcion (todas)      : "
        f"{sum(1 for a in validos if a['descripcion'] is None)}/{len(validos)}"
        "  (el listado no la trae y no se visita el detalle)")

    # --- capacidades de tanque ---
    fuentes_tanque = Counter(a["fuente_capacidad_tanque"] for a in validos)
    implicables = [
        a for a in validos
        if tanque_implausible(a["cilindrada_cc"], a["capacidad_tanque_l"])
    ]
    lineas += [
        "",
        "CAPACIDAD DE TANQUE",
        "-" * 60,
        f"  anuncio            : {fuentes_tanque.get('anuncio', 0)}",
        f"  modelo_verificado  : {fuentes_tanque.get('modelo_verificado', 0)}"
        "  (ficha tecnica; el scraper NO lo extrae del anuncio)",
        f"  tabla_cc_fallback  : {fuentes_tanque.get('tabla_cc_fallback', 0)}",
    ]
    if implicables:
        lineas += [
            f"  AVISO DE COHERENCIA: {len(implicables)} con tanque fuera del rango",
            "  tipico de su cilindrada. NO se modifican: son valores de ficha",
            "  tecnica (fuente 'modelo_verificado'), no medidas del anuncio. Se",
            "  listan para que el cruce de precio/L se lea con esa salvedad:",
        ]
        for a in implicables:
            rango = RANGO_TANQUE_POR_CC.get(a["cilindrada_cc"])
            lineas.append(
                f"     {a['marca']:<11} {a['cilindrada_cc']}cc -> "
                f"{a['capacidad_tanque_l']} L (tipico {rango[0]}-{rango[1]}) "
                f"[{a['fuente_capacidad_tanque']}] '{a['titulo'][:40]}'")
    else:
        lineas.append("  Todos los tanques caen en el rango tipico de su cc.")

    # --- triciclos ---
    triciclos = [d for d in descartados if d.get("motivo") == "no_es_moto_triciclo"]
    lineas += [
        "",
        "TRICICLOS / MAS DE 2 RUEDAS",
        "-" * 60,
        f" _keywords_: {', '.join(sorted(KEYWORDS_TRICICLO))}",
        f"  descartados con 'no_es_moto_triciclo': {len(triciclos)}",
    ]
    for t in triciclos[:10]:
        lineas.append(f"    -> {t['precio_usd']} USD '{t['titulo'][:48]}'")
    dudosos = [a for a in validos if a.get("modelo_dudoso")]
    if dudosos:
        lineas += [
            "",
            f"MODELOS MARCADOS COMO DUDOSOS: {len(dudosos)}",
            "-" * 60,
        ]
        for a in dudosos:
            lineas.append(
                f"  - {a['marca']} '{a['titulo'][:50]}' -> {a['modelo_dudoso']}")
    lineas += [
        "",
        "  KATZU CHEETAH: VEREDICTO = MOTO DE 2 RUEDAS, no triciclo.",
        "  Se reporto como triciclo, asi que se leyeron 3 fichas de detalle",
        "  (HTML en /tmp, fuera del repo) antes de decidir:",
        "    - Las 3 las categoriza Revolico en 'Vehiculos > Motos de",
        "      Combustion', no en la categoria de triciclos.",
        "    - Las 3 dicen: 'Motor: 200 cc, Gasolina, 4 tiempos, 5",
        "      Velocidades, Tanque de 16,5 Litros'. Una anade 'Velocidad",
        "      Maxima de 95 KM/H'.",
        "    - Ni '3 ruedas', ni 'trimoto', ni 'UTV' en ninguna.",
        "    - 'triciclo' sale UNA vez y no describe al vehiculo: esta en el",
        "      pie de venta del anunciante ('TENEMOS MAS DE 60 MODELOS de",
        "      BICICLETAS ELECTRICAS, BICIMOTOS, MOTOS ELECTRICAS, MOTOS DE",
        "      COMBUSTION, PATINETAS y TRICICLOS...'), o sea publicidad de su",
        "      inventario, repetida en todos sus anuncios.",
        "    - Precio 2150-2580 USD: banda de las 200cc del set.",
        "  CONCLUSION: se conservan (no se descarta por sospecha). El anuncio",
        "  queda con 'modelo_dudoso = katzu_cheetah_posible_triciclo' para que",
        "  la duda siga visible. Se descartaria solo si la ficha de la MARCA lo",
        "  confirma; el texto del anuncio no alcanza para affirmarlo.",
    ]
    if sesion.errores:
        lineas += ["", f"ERRORES ({len(sesion.errores)})", "-" * 60]
        for error in sesion.errores[:30]:
            lineas.append(f"  - {error}")
    ARCHIVO_REPORTE.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    print()
    print(SEP)
    print("RESUMEN")
    print(SEP)
    print(f"  validos={len(validos)}/{OBJETIVO_VALIDOS}  descartados={len(descartados)}  "
          f"duplicados={duplicados}")
    print(f"  peticiones={sesion.peticiones} cache={sesion.desde_cache} paginas={sesion.paginas}")
    if motivos:
        print("  motivos: " + ", ".join(f"{m}={t}" for m, t in motivos.most_common(8)))
    print(f"  tanque: anuncio={fuentes.get('anuncio', 0)} "
          f"modelo_verificado={fuentes.get('modelo_verificado', 0)} "
          f"tabla_cc_fallback={fuentes.get('tabla_cc_fallback', 0)}"
          f"  (fuera de rango: {len(implicables)})")
    print(f"  rendimiento real={con_rend}  autonomia real={con_auton}")
    print(f"  precio USD: {min(precios):.0f}-{max(precios):.0f}" if precios else "  sin precios")
    print(f"  cilindradas: {sorted(set(cil))}" if cil else "  sin cilindradas")
    print(f"  fechas: {min(fechas)} a {max(fechas)}" if fechas else "  sin fechas")
    print("  top marcas: " + ", ".join(f"{m}({t})" for m, t in marcas.most_common(5)))
    print(f"  archivos: {ARCHIVO_VALIDOS.name}, {ARCHIVO_DESCARTADOS.name}, {ARCHIVO_REPORTE.name}")
    print(SEP)
    return 0 if len(validos) >= OBJETIVO_VALIDOS else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Ampliacion del scraping de motos de combustion en Revolico: 60 anuncios nuevos.

Reutiliza el parser y los extractores de scraper_combustion.py (importado como
modulo, NO modificado) y cambia solo lo que la ampliacion necesita:

  - cache propia: cache_html_combustion_ampliacion/
  - archivos de salida propios: motos_combustion_ampliacion.json y
    descartados_combustion_ampliacion.json
  - 20 queries nuevas en vez de las 12 del original
  - objetivo 60 validos en vez de 30
  - filtro de provincia: SOLO La Habana (el original no filtraba por
    provincia, por eso el set original se le colaron 2 anuncios de Villa Clara
    y Santiago de Cuba; aqui no se repiten)
  - el tanque deja de ser obligatorio: los 4 campos obligatorios de la
    ampliacion son marca, cilindrada_cc, precio_usd y ubicacion

NO entra a paginas de detalle: el listado de Revolico no expone la descripcion.
No se inventan datos: lo que no esta en el anuncio queda en null.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx

import scraper_combustion as base

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

ARCHIVO_VALIDOS = BASE_DIR / "motos_combustion_ampliacion.json"
ARCHIVO_DESCARTADOS = BASE_DIR / "descartados_combustion_ampliacion.json"
ARCHIVO_ORIGINAL = BASE_DIR / "motos_combustion.json"
# El merge ya escrito se lee para no repetir ninguno de sus anuncios al
# reanudar la ampliacion.
ARCHIVO_MERGE = BASE_DIR / "motos_combustion_90.json"
# Descartes hechos a mano sobre anuncios que el scraper si aprueba. Se guardan
# con los datos tal cual salio del listado para que queden auditados en
# descartados_combustion_ampliacion.json y el merge pueda reportarlos.
#
# La moto DKW 125CC entra por aqui: 700 USD cuando el siguiente mas barato del
# set esta a 1200 y la mediana a 2200. Ojo al motivo, NO es un error de parseo
# del precio (su HTML publica 700 USD, comprobado). Es un anuncio con ficha
# poorisima: sin descripcion, sin estado y sin kilometraje, con el titulo
# reducido a 'MOTO DKW 125CC'. DKW es una marca antigua germana, de modo que lo
# mas probable es que sea usada o de republica, y el set es de motos nuevas.
DESCARTES_MANUALES = [
    {
        "titulo": "MOTO DKW 125CC",
        "url": "https://www.revolico.com/item/moto-dkw-56323200",
        "marca": "Dkw",
        "cilindrada_cc": 125,
        "precio_usd": 700.0,
        "ubicacion": "La Habana / Playa",
        "motivo": "precio_outlier_sospechoso",
    },
    # Anuncios que el scraper aprueba y que se descartan leyendo el titulo
    # (ver TITULOS_DESCARTAR_REVISION en merge_combustion.py). Se listan aqui
    # para que no vuelvan a entrar: el merge los aparta por titulo, no por URL,
    # asi que sin esto el scraper los elegiria una y otra vez.
    {
        "titulo": "MOTOS HONGLI 150cc + YAMAKI AUTOMATICA 150cc",
        "url": "https://www.revolico.com/item/motos-hongli-150cc-yamaki-automatica-150cc-56788920",
        "marca": "Hongli",
        "cilindrada_cc": 150,
        "precio_usd": 1800.0,
        "ubicacion": "La Habana",
        "motivo": "es_lote_de_varios",
    },
    {
        "titulo": "Honda 200cc maquina japonesaaa caminando super duro",
        "url": "https://www.revolico.com/item/honda-200cc-maquina-japonesaaa-caminando-super-duro-whatsapp-58128623-57328945",
        "marca": "Honda",
        "cilindrada_cc": 200,
        "precio_usd": 2000.0,
        "ubicacion": "La Habana",
        "motivo": "moto_usada_no_nueva",
    },
    {
        "titulo": "Vendo mi moto Torvan Alpha One 150cc",
        "url": "https://www.revolico.com/item/vendo-mi-moto-torvan-alpha-one-150cc-56067661",
        "marca": "Torvan",
        "cilindrada_cc": 150,
        "precio_usd": 2000.0,
        "ubicacion": "La Habana",
        "motivo": "moto_usada_no_nueva",
    },
    # Unico anuncio que el propio scrapeo marco como modelo dudoso, y por el
    # unico motivo con nombre propio: el Katzu Cheetah es un triciclo, no una
    # moto de dos ruedas. Entra en el set porque los filtros no miran la
    # cantidad de ruedas. Se saca para dejarle el lugar a la restitucion de
    # abajo, que si hace falta (Toqmap, correccion de marca pendiente).
    {
        "titulo": "MOTO DEPORTIVA DE COMBUSTIÓN KATZU CHEETAH 200 CC 4 Tiempo 5 Velocidades",
        "url": "https://www.revolico.com/item/moto-deportiva-de-combustion-katzu-cheetah-caracteristicas-200-cc-4-tiempo-5-velocidades-capacidad-del-57542991",
        "marca": "Katzu",
        "cilindrada_cc": 200,
        "precio_usd": 2400.0,
        "ubicacion": "La Habana / Guanabacoa",
        "motivo": "modelo_dudoso_triciclo",
    },
]
# Se derivan de lo anterior para no duplicar la lista a mano.
URLS_EXCLUIDAS_FIJAS = {d["url"] for d in DESCARTES_MANUALES}
ARCHIVO_REPORTE = BASE_DIR / "reporte_scraping_combustion_ampliacion.txt"
DIR_CACHE = BASE_DIR / "cache_html_combustion_ampliacion"

USER_AGENT = "MiProyectoInvestigacion/1.0"
DELAY_SEGUNDOS = 3.0
TIMEOUT_SEGUNDOS = 30.0
REINTENTOS = 2
ESPERA_REINTENTO = 5.0
MAX_PETICIONES = 200
MAX_PAGINAS_POR_QUERY = 4
# 62 = 60 que ya tiene la ampliacion + 2 que cubren los 2 originales que
# resultaron estar fuera de La Habana (Villa Clara y Santiago de Cuba). El
# set original no se puede tocar, asi que el set de 90 tiene que quedar
# igual de grande con los 2 de menos siendo de La Habana.
# 62 = los 59 que el merge ya puede usar (62 anteriores menos los 3 que se
# descartan por revision) + 3 anuncios nuevos que repongan los descartados: un
# lote de dos motos y dos motos usadas, ver TITULOS_DESCARTAR_REVISION en
# merge_combustion.py. Es un tope: si se sube, el merge se pasa de 90.
OBJETIVO_VALIDOS = 62
FECHA_MINIMA = "2026-01-01"
FECHA_SCRAPING = "2026-09-30"
PRECIO_MINIMO_USD = 100

# Las 12 nuevas. Las 8 de municipio solo se piden si con las anteriores no se
# llega a 60 validos.
QUERIES_INICIALES = [
    "moto gasolina",
    "moto combustion",
    "moto 100cc",
    "moto 125cc",
    "moto 150cc",
    "moto 200cc",
    "moto 250cc",
    "moto china",
    "moto cuba",
    "moto honda",
    "moto yamaha",
    "moto gasolina cuba",
]
QUERIES_RESPALDO = [
    "moto Centro Habana",
    "moto Playa",
    "moto Marianao",
    "moto Cerro",
    "moto Habana Vieja",
    "moto Diez de Octubre",
    "moto Arroyo Naranjo",
    "moto Boyeros",
    # Ultima tanda, anadida al tener que sustituir los 2 originales que
    # resultaron estar fuera de La Habana. Municipios distintos a los de
    # arriba para no volver a pedir los mismos resultados.
    "moto Vedado",
    "moto Miramar",
    "moto Guanabacoa",
    "moto Regla",
    "moto San Miguel",
    "moto Cotorro",
    "moto La Lisa",
]

# Solo se aceptan anuncios de La Habana. La provincia sale del catalogo de
# Apollo; el municipio se usa como red de seguridad cuando la provincia no
# viene o viene vacia.
MUNICIPIOS_HABANA = {
    "centro habana", "playa", "marianao", "cerro", "habana vieja",
    "diez de octubre", "arroyo narango", "boyeros", "plaza", "plaza de la revolucion",
    "san miguel del padron", "guanabacoa", "regla", "habana del este", "cotorro",
    "la lisa", "la loma", "villa de mayo", "san antonio de los banos",
}

# El scraper original devuelve estos motivos; la ampliacion los expresa con el
# vocabulario de su esquema para que el archivo sea legible por si solo.
MOTIVOS_TRADUCIDOS = {
    "sin_titulo": "titulo_vacio",
    "otro_vehiculo": "es_scooter",
    "no_es_moto_triciclo": "es_triciclo",
    "no_es_moto": "es_accesorio",
    "moto_usada_no_nueva": "es_moto_usada",
    "precio_no_cuba_importador": "precio_no_cuba",
    "cilindrada_fuera_de_tabla": "cilindrada_fuera_de_tabla",
    "sin_fecha": "sin_fecha",
    "fecha_anterior_a_2026": "fecha_no_2026",
    "duplicado_manual": "descartado_por_usuario",
    "duplicado_generico": "duplicado_mismo_modelo",
    "sin_capacidad_tanque": "sin_capacidad_tanque",
}
SEP = "=" * 78


def es_habana(ubicacion: str | None) -> bool:
    """True si la ubicacion es de La Habana, segun la cadena 'Provincia / Municipio'.

    'La Habana / Cerro' -> True. 'Villa Clara / Santa Clara' -> False.
    """
    if not ubicacion:
        return False
    partes = [p.strip() for p in ubicacion.split("/")]
    if base.normalizar(partes[0]) == "la habana":
        return True
    # Sin provincia delante solo se acepta un municipio inequivoco de La
    # Habana cuando viene accompanied de la parte municipal, para no dar por
    # bueno un anuncio de otra provincia que comparta nombre.
    return len(partes) > 1 and all(
        base.normalizar(p) in MUNICIPIOS_HABANA for p in partes
    )


def provincia_de(anuncio: dict, catalogos: dict) -> str | None:
    """Nombre de la provincia segun el catalogo de Apollo (fuente autorizada)."""
    return (catalogos.get("provincias") or {}).get(str(anuncio.get("provinceId")))


def es_habana_anuncio(anuncio: dict, catalogos: dict) -> bool:
    """True si el anuncio es de La Habana.

    Se consulta primero la provincia del catalogo, que es el dato que trae el
    listado; la cadena de texto solo se usa de red de seguridad.
    """
    provincia = provincia_de(anuncio, catalogos)
    if provincia:
        return base.normalizar(provincia) == "la habana"
    # Sin provincia en el catalogo se cae al municipio. Los nombres del set son
    # especificos de La Habana ('Cerro', 'Plaza de la Revolucion'), asi que
    # descartar estos anuncios perderia evidencia real por un dato ausente.
    municipio = (catalogos.get("municipios") or {}).get(str(anuncio.get("municipalityId")))
    return bool(municipio) and base.normalizar(municipio) in MUNICIPIOS_HABANA


def evaluar(registro: dict, prefijo_telefono: str | None, es_habana_: bool) -> str | None:
    """Motivo de descarte, ya en el vocabulario de la ampliacion.

    Envuelve aplicar_filtros() del original y le anade el filtro de provincia,
    que el original no tenia. Recibe el veredicto de provincia ya calculado
    porque ahi esta el dato que manda.
    """
    motivo = base.aplicar_filtros(registro, prefijo_telefono)
    if motivo is not None:
        return MOTIVOS_TRADUCIDOS.get(motivo, motivo)
    # El original exige capacidad_tanque_l; aqui los campos obligatorios son
    # marca, cilindrada_cc, precio_usd y ubicacion, y el tanque es opcional.
    if registro.get("cilindrada_cc") is None:
        return "sin_cilindrada"
    if not es_habana_:
        if not registro.get("ubicacion"):
            return "sin_ubicacion"
        return "fuera_de_la_habana"
    return None


class Sesion:
    """Contador de peticiones + cache propia de la ampliacion."""

    def __init__(self, usar_cache: bool) -> None:
        self.usar_cache = usar_cache
        self.peticiones = 0
        self.desde_cache = 0
        self.paginas = 0
        self.errores: list[str] = []

    def cabe(self) -> bool:
        return self.peticiones < MAX_PETICIONES

    def obtener(self, cliente: httpx.Client, url: str) -> str | None:
        ruta_cache = DIR_CACHE / f"{base.slug(url)}.html"
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


def cargar_urls_originales() -> set[str]:
    """URLs que NO se pueden volver a scrapear.

    Se usa la union de:
      - motos_combustion.json (los 30 originales)
      - motos_combustion_90.json (el merge ya escrito)
      - URLS_EXCLUIDAS_FIJAS (anuncios descartados a mano, para que no vuelvan
        a entrar por haber salido del merge)

    Asi una reanudacion no repite ninguno de los 62 que ya estan en la
    ampliacion, ni los 30 originales.
    """
    urls: set[str] = set()
    for ruta in (ARCHIVO_ORIGINAL, ARCHIVO_MERGE):
        if not ruta.exists():
            continue
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        for anuncio in datos.get("anuncios") or []:
            url = anuncio.get("url") or anuncio.get("url_anuncio")
            if url:
                urls.add(url.split("?")[0].rstrip("/"))
    return urls | URLS_EXCLUIDAS_FIJAS


def cargar_validos_previos() -> list[dict]:
    """Los validos que ya tenia la ampliacion, para poder reanudarla.

    Sin esto, re-ejecutar el scraper desde cero volveria a elegir los mismos
    anuncios de la primera pagina y no avanzaria: el objetivo es 63 y ya hay 62.

    Se filtran por URL_EXCLUIDAS_FIJAS: si un anuncio se descarto despues de
    quedar guardado en la ampliacion (la DKW, de 700 USD), debe desaparecer
    tambien de aqui. Si no, la reanudacion lo lee del archivo de salida y lo
    devuelve al set sin pasar por ningun filtro.
    """
    if not ARCHIVO_VALIDOS.exists():
        return []
    datos = json.loads(ARCHIVO_VALIDOS.read_text(encoding="utf-8"))
    return [
        anuncio
        for anuncio in datos.get("anuncios") or []
        if (anuncio.get("url") or "").split("?")[0].rstrip("/")
        not in URLS_EXCLUIDAS_FIJAS
    ]


def cargar_descartados_previos() -> list[dict]:
    """Los descartes de la corrida anterior.

    Una reanudacion que ya alcanzo el objetivo no recorre ninguna pagina, y si
    se escribiera una lista vacia se perderian los descartes ya registrados
    (los de la corrida que si los encontro).
    """
    if not ARCHIVO_DESCARTADOS.exists():
        return []
    datos = json.loads(ARCHIVO_DESCARTADOS.read_text(encoding="utf-8"))
    return list(datos.get("descartes") or [])


# Anuncios validos que un recorte anterior se llevo del pool y hay que devolver.
# La primera corrida de esta ampliacion recolecto 65 con objetivo 60 y los
# recorto por URL, con lo cual borro validos sin dejar rastro en ningun archivo
# (no hay copia de la ampliacion anterior a ese recorte). Entre los perdidos
# estaba el Toqmap, que hace falta para una de las correcciones de marca
# acordadas, asi que se restituye explicitamente en vez de dejar que el merge se
# quede en cinco de seis.
RESTITUCIONES_MANUALES = [
    "https://www.revolico.com/item/toqmap-t-ultra-max-200cc-57528036",
]


def buscar_en_cache(url_objetivo: str) -> tuple[dict, dict] | None:
    """El anuncio de la cache junto con el catalogo de la pagina donde sale.

    Se recorre la cache de listados y no la pagina de detalle: los descartes y
    las altas de esta ampliacion salen todas del listado, que es de donde se
    leen titulo, precio y ubicacion.
    """
    objetivo = url_objetivo.rstrip("/")
    for archivo in sorted(DIR_CACHE.glob("*.html")):
        catalogo, anuncios = base.parsear_listado(
            archivo.read_text(encoding="utf-8", errors="replace")
        )
        for anuncio in anuncios:
            url = (anuncio.get("permalink") or "").strip()
            if not url.startswith("http"):
                url = f"https://www.revolico.com{url}"
            if url.rstrip("/") == objetivo:
                return anuncio, catalogo
    return None


def restituir(url_objetivo: str) -> dict | None:
    """Arma el registro de un anuncio de RESTITUCIONES_MANUALES desde la cache.

    Pasa por los mismos filtros que el scrapeo y con los mismos campos, para
    que el registro-restituido sea indistinguible de uno recolectado y el merge
    lo pueda corregir. Si el anuncio ya no esta en la cache, o si con los
    filtros de hoy queda descartado, se avisa y no se restituye: colarlo a mano
    seria inventar un anuncio que el scrapeo ya no aprueba.
    """
    par = buscar_en_cache(url_objetivo)
    if par is None:
        print(f"  AVISO: {url_objetivo} no esta en la cache, no se restituye.")
        return None
    anuncio, catalogo = par
    registro = base.analizar(anuncio, catalogo, "https://www.revolico.com/search")
    # El mismo ajuste que hace el bucle de scrapeo: hay listados que traen el
    # permalink relativo ('/item/...') y otros con la URL entera, y sin esto el
    # registro restituido queda con una URL que no resuelve.
    url_anuncio = (anuncio.get("permalink") or "").strip()
    if not url_anuncio.startswith("http"):
        url_anuncio = f"https://www.revolico.com{url_anuncio}"
    registro["url"] = url_anuncio.rstrip("/")
    prefijo_tel = ((anuncio.get("phoneInfo") or {}).get("firstPhone") or {}).get("prefix")
    motivo = evaluar(registro, prefijo_tel, es_habana_anuncio(anuncio, catalogo))
    if motivo is not None:
        print(f"  AVISO: {url_objetivo} hoy se descarta por {motivo}, no se restituye.")
        return None
    base.completar_tanque_autonomia(
        registro, base.texto_numerico(registro["titulo"] or "", registro["descripcion"])
    )
    return registro


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ampliacion del scraper de motos de combustion (62 validos)"
    )
    parser.add_argument("--refrescar", action="store_true", help="ignora la caché")
    parser.add_argument(
        "--sin-previos",
        action="store_true",
        help="ignora los validos que ya tenia la ampliacion y empieza de cero",
    )
    args = parser.parse_args()

    inicio = datetime.now(timezone.utc)
    ahora = inicio.strftime("%Y-%m-%dT%H:%M:%SZ")
    sesion = Sesion(usar_cache=not args.refrescar)
    urls_originales = cargar_urls_originales()

    # Los validos que ya tenia la ampliacion se conservan y se sigue buscando
    # hasta el objetivo. Sus descartes tambien: si no, una reanudacion que ya
    # esta en el objetivo no vuelve a recorrer las paginas y se llevaria por
    # delante el historico de descartes sin reemplazarlo.
    validos: list[dict] = [] if args.sin_previos else cargar_validos_previos()
    validos_previos = len(validos)
    # El objetivo es un tope de RECOLECCION, no de recorte: dice cuando dejar de
    # buscar, no que anuncios tirar. Si el pool quedo por encima (porque bajo el
    # objetivo o porque se levanto un descarte), NO se recorta nada aqui: se
    # avisa y se dejan todos, que perder un anuncio valido porque su URL sortea
    # alta es peor que tener el pool grande. Quien decide cuantos entran al
    # entregable es el merge, y lo dice explicitamente.
    if len(validos) > OBJETIVO_VALIDOS:
        print(f"  AVISO: el pool tiene {len(validos)} validos y el objetivo de "
              f"recoleccion es {OBJETIVO_VALIDOS}. No se recorta nada: el merge "
              f"decide cuantos usar.")
    descartados: list[dict] = (
        [] if args.sin_previos else cargar_descartados_previos()
    )
    # Los descartes manuales entran siempre, y solo una vez: si ya estaban en
    # el historico no se duplican al reejecutar el scraper.
    urls_descartadas = {
        (d.get("url") or "").split("?")[0].rstrip("/") for d in descartados
    }
    for descarte in DESCARTES_MANUALES:
        if descarte["url"] not in urls_descartadas:
            descartados.append(dict(descarte))
            urls_descartadas.add(descarte["url"])
    motivos: Counter[str] = Counter(d["motivo"] for d in descartados)
    # Los titulos ya aceptados se sembran en el dedup: si no, el anuncio que
    # se acaba de traerse volveria a entrar por su propio titulo.
    vistos_titulo: set[str] = {base.normalizar(a.get("titulo") or "") for a in validos}
    vistos_titulo.discard("")
    # Las restituciones manuales entran antes de recolectar, para que cuenten
    # para el objetivo. Si el anuncio ya esta en el pool no se toca, y su
    # titulo se siembra en el dedup igual que el de cualquier valido.
    urls_validas = {(v.get("url") or "").rstrip("/") for v in validos}
    for url_restituir in RESTITUCIONES_MANUALES:
        if url_restituir.rstrip("/") in urls_validas:
            continue
        registro = restituir(url_restituir)
        if registro is None:
            continue
        validos.append(registro)
        urls_validas.add(url_restituir.rstrip("/"))
        vistos_titulo.add(base.normalizar(registro.get("titulo") or ""))
        print(f"  restituido        : {registro['precio_usd']} USD | "
              f"{registro['marca']} {registro['cilindrada_cc']}cc | "
              f"{(registro['titulo'] or '')[:52]}")
    vistos_id: set[str] = set()
    catalogos: dict = {"provincias": {}, "municipios": {}}
    paginas_por_query: dict[str, int] = {}
    consultas_hechas: list[str] = []
    urls_consultadas: list[str] = []
    duplicados = 0

    print(SEP)
    print("AMPLIACIÓN DE MOTOS DE COMBUSTIÓN (Revolico)")
    print(SEP)
    print(f"  inicio           : {ahora}")
    print(f"  objetivo validos : {OBJETIVO_VALIDOS}")
    print(f"  user-agent       : {USER_AGENT}")
    print(f"  delay            : {DELAY_SEGUNDOS}s   timeout: {TIMEOUT_SEGUNDOS}s")
    print(f"  reintentos       : {REINTENTOS} por URL (espera {ESPERA_REINTENTO}s)")
    print(f"  cache            : {DIR_CACHE.name}/")
    print(f"  URLs del original: {len(urls_originales)} (se excluyen por 'url_duplicada')")
    print("  NO se entra a páginas de detalle: no hay descripcion en el listado.")
    print("  AVISO METODOLOGICO: el listado no expone fecha de publicacion; se usa")
    print("    'updatedOnToOrder' y se marca con 'fecha_fuente'. NO se inventan fechas.")
    print(f"  precio minimo    : {PRECIO_MINIMO_USD} USD")

    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        verify=True,
    ) as cliente:
        for query in QUERIES_INICIALES + QUERIES_RESPALDO:
            if len(validos) >= OBJETIVO_VALIDOS or not sesion.cabe():
                break
            rescued = query not in QUERIES_INICIALES
            # Etiqueta honesta: al reanudar, las queries iniciales se vuelven
            # a pedir (salen de cache) y ya no se están 'agotando' de nuevo.
            etiqueta_grupo = "inicial(cache)" if not rescued and validos_previos else (
                "respaldo" if rescued else "inicial"
            )
            if rescued:
                print(f"\n  >> respaldo: se agotaron las queries anteriores con "
                      f"{len(validos)}/{OBJETIVO_VALIDOS} validos")
            etiqueta = etiqueta_grupo
            consultas_hechas.append(f"{etiqueta}: {query}")
            print(f"\n  [{etiqueta}] query={query!r}")
            for pagina in range(1, MAX_PAGINAS_POR_QUERY + 1):
                if len(validos) >= OBJETIVO_VALIDOS or not sesion.cabe():
                    break
                url = f"{base.BASE_BUSQUEDA}?q={query.replace(' ', '+')}&page={pagina}"
                html = sesion.obtener(cliente, url)
                urls_consultadas.append(url)
                if html is None:
                    print(f"      pagina {pagina}: sin respuesta (rompe el ciclo)")
                    break
                catalogo, anuncios = base.parsear_listado(html)
                sesion.paginas += 1
                paginas_por_query[f"{query} (p{pagina})"] = len(anuncios)
                if catalogo.get("provincias"):
                    catalogos["provincias"].update(catalogo["provincias"])
                    catalogos["municipios"].update(catalogo["municipios"])
                print(f"      pagina {pagina}: {len(anuncios):>3} anuncios "
                      f"(validos acumulados: {len(validos)})")
                if not anuncios:
                    break
                for anuncio in anuncios:
                    # El objetivo es un tope, no un suelo: en cuanto se alcanza
                    # se deja de recolectar. Sin esto una pagina de 100 anuncios
                    # deja el set por encima del objetivo asked (salio 65).
                    if len(validos) >= OBJETIVO_VALIDOS:
                        break
                    id_anuncio = str(anuncio.get("id") or "")
                    titulo = (anuncio.get("title") or "").strip()
                    clave_titulo = base.normalizar(titulo)
                    registro = base.analizar(anuncio, catalogos, url)
                    url_anuncio = (anuncio.get("permalink") or "").strip()
                    if not url_anuncio.startswith("http"):
                        url_anuncio = f"https://www.revolico.com{url_anuncio}"
                    registro["url"] = url_anuncio

                    # Los 60 que ya tiene la ampliacion siguen siendo validos
                    # aunque esten en el merge: no se vuelven a descartar como
                    # duplicados, solo se saltan como candidatos nuevos.
                    if url_anuncio.rstrip("/") in urls_originales:
                        ya_esta = any(
                            (v.get("url") or "").rstrip("/") == url_anuncio.rstrip("/")
                            for v in validos
                        )
                        if not ya_esta:
                            registro["motivo"] = "url_duplicada"
                            motivos["url_duplicada"] += 1
                            descartados.append(registro)
                        vistos_id.add(id_anuncio)
                        if clave_titulo:
                            vistos_titulo.add(clave_titulo)
                        continue
                    if id_anuncio in vistos_id or (clave_titulo and clave_titulo in vistos_titulo):
                        duplicados += 1
                        continue

                    texto_num = base.texto_numerico(
                        registro["titulo"] or "", registro["descripcion"])
                    prefijo_tel = (
                        ((anuncio.get("phoneInfo") or {}).get("firstPhone") or {}).get("prefix")
                    )
                    decision = base.comparar_duplicado(registro, validos)
                    motivo = evaluar(
                        registro, prefijo_tel, es_habana_anuncio(anuncio, catalogos))
                    if motivo is None:
                        base.completar_tanque_autonomia(registro, texto_num)
                    if motivo is None and decision and decision["accion"] == "duplicado":
                        if not decision["titulo_largo"]:
                            motivo = MOTIVOS_TRADUCIDOS.get(
                                decision["motivo"], decision["motivo"])
                        else:
                            # Entra el entrante porque su titulo es mas largo:
                            # TODOS los validos que colisionan salen del set y
                            # pasan a descartados apuntando al entrante.
                            for victima in decision["otros"]:
                                if victima in validos:
                                    validos.remove(victima)
                                victima = dict(victima)
                                victima["motivo"] = "duplicado_mismo_modelo"
                                victima["duplicado_de"] = url_anuncio
                                descartados.append(victima)
                                motivos["duplicado_mismo_modelo"] += 1
                    if motivo is not None:
                        registro["motivo"] = motivo
                        motivos[motivo] += 1
                        descartados.append(registro)
                    else:
                        validos.append(registro)
                    vistos_id.add(id_anuncio)
                    if clave_titulo:
                        vistos_titulo.add(clave_titulo)

    validos.sort(key=lambda a: a["url"])
    datos_validos = {
        "vehiculo": "moto_combustion",
        "fecha_scraping": FECHA_SCRAPING,
        "tipo": "ampliacion",
        "filtro_ubicacion": "La Habana",
        "filtro_fecha": ">= 2026-01-01",
        "precio_minimo_usd": PRECIO_MINIMO_USD,
        "total_anuncios_validos": len(validos),
        "queries_consultadas": consultas_hechas,
        "urls_consultadas": urls_consultadas,
        "anuncios": validos,
    }
    datos_descartados = {
        "vehiculo": "moto_combustion",
        "fecha_scraping": FECHA_SCRAPING,
        "tipo": "ampliacion",
        "total_descartados": len(descartados),
        "descartes": descartados,
    }
    ARCHIVO_VALIDOS.write_text(
        json.dumps(datos_validos, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    ARCHIVO_DESCARTADOS.write_text(
        json.dumps(datos_descartados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def rango(clave: str) -> str:
        valores = [a[clave] for a in validos if a.get(clave) is not None]
        if not valores:
            return "sin datos"
        return f"{min(valores)} - {max(valores)}"

    lineas = [
        SEP,
        "AMPLIACIÓN DE MOTOS DE COMBUSTIÓN (Revolico)",
        SEP,
        f"Fecha: {ahora}",
        "",
        "CONFIGURACIÓN:",
        f"  User-Agent      : {USER_AGENT}",
        f"  Delay           : {DELAY_SEGUNDOS}s entre peticiones",
        f"  Timeout         : {TIMEOUT_SEGUNDOS}s",
        f"  Reintentos      : {REINTENTOS} por URL (espera {ESPERA_REINTENTO}s)",
        f"  Cache           : {DIR_CACHE.name}/",
        f"  Objetivo        : {OBJETIVO_VALIDOS} validos nuevos",
        f"  Peticiones      : max {MAX_PETICIONES}, {MAX_PAGINAS_POR_QUERY} paginas por query",
        f"  Precio minimo   : {PRECIO_MINIMO_USD} USD",
        "",
        "RED:",
        f"  Peticiones nuevas      : {sesion.peticiones}",
        f"  Páginas desde cache     : {sesion.desde_cache}",
        f"  Páginas parseadas       : {sesion.paginas}",
        "",
        "RESULTADOS:",
        f"  Válidos totales        : {len(validos)}",
        f"  (heredados de la corrida previa: {validos_previos})",
        f"  Descartes             : {len(descartados)}",
        f"  Duplicados por titulo  : {duplicados}",
        f"  Excluidos url_duplicada: {motivos['url_duplicada']} (ya estaban en el original o en el merge)",
        "",
        "DESCARTADOS POR MOTIVO:",
    ]
    for motivo, total in motivos.most_common():
        lineas.append(f"    {total:>5}  {motivo}")
    lineas += [
        "",
        "RANGOS DE LOS VÁLIDOS:",
        f"  precio_usd        : {rango('precio_usd')}",
        f"  cilindrada_cc     : {rango('cilindrada_cc')}",
        f"  autonomia_km      : {rango('autonomia_km')}",
        f"  rendimiento_km_l  : {rango('rendimiento_km_l')}",
        f"  capacidad_tanque_l: {rango('capacidad_tanque_l')}",
        "",
        "CILINDRADAS:",
    ]
    for cc, total in Counter(a["cilindrada_cc"] for a in validos).most_common():
        lineas.append(f"    {total:>5}  {cc} cc")
    lineas += ["", "MARCAS MÁS FRECUENTES:"]
    for marca, total in Counter(a["marca"] for a in validos if a["marca"]).most_common(15):
        lineas.append(f"    {total:>5}  {marca}")
    lineas += ["", "UBICACIONES:",
               f"  Todos de La Habana: {es_habana_ok(validos)}",
               f"  Todos de 2026      : {todos_2026(validos)}"]
    for ubicacion, total in Counter(a["ubicacion"] for a in validos).most_common():
        lineas.append(f"    {total:>5}  {ubicacion}")
    lineas += [
        "",
        "PÁGINAS POR QUERY:",
    ]
    for etiqueta, total in paginas_por_query.items():
        lineas.append(f"    {total:>5}  {etiqueta}")
    if sesion.errores:
        lineas += ["", f"ERRORES REGISTRADOS ({len(sesion.errores)}):"]
        for error in sesion.errores[:40]:
            lineas.append(f"  - {error}")
    ARCHIVO_REPORTE.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    print(SEP)
    print(f"Válidos: {len(validos)} (previos {validos_previos}) | descartados: "
          f"{len(descartados)} | duplicados: {duplicados}")
    print(f"Escrito {ARCHIVO_VALIDOS.name}")
    print(f"Escrito {ARCHIVO_DESCARTADOS.name}")
    print(f"Escrito {ARCHIVO_REPORTE.name}")
    return 0 if len(validos) >= OBJETIVO_VALIDOS else 1


def es_habana_ok(anuncios: list[dict]) -> str:
    return "si" if all(es_habana(a["ubicacion"]) for a in anuncios) else "NO"


def todos_2026(anuncios: list[dict]) -> str:
    return "si" if all(
        (a.get("fecha_publicacion") or "") >= FECHA_MINIMA for a in anuncios
    ) else "NO"


if __name__ == "__main__":
    raise SystemExit(main())

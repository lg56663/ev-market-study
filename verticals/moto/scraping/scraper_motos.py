#!/usr/bin/env python3
"""Scraper de motos electricas en Revolico.com.

Listado: Next.js + Apollo (__NEXT_DATA__ -> __APOLLO_STATE__, stubs AdType).
El listado solo trae titulo, precio, currency, provinceId y municipalityId, asi
que para completar los campos obligatorios se entra a la pagina de detalle de los
anuncios que pasan los filtros de inclusion y exclusion, y se extrae la
descripcion completa.

Salidas:
  - motos_electricas.json       : 30 anuncios validos
  - descartados_motos.json      : todos los descartados con trazabilidad
  - reporte_scraping_motos.txt  : reporte de la sesion
Cache:
  - cache_html/                 : HTML de listados
  - cache_detalle/{id}.html     : HTML de paginas de detalle
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).resolve().parent
ARCHIVO_VALIDOS = BASE_DIR / "motos_electricas.json"
ARCHIVO_DESCARTADOS = BASE_DIR / "descartados_motos.json"
ARCHIVO_REPORTE = BASE_DIR / "reporte_scraping_motos.txt"
DIR_CACHE_LISTADO = BASE_DIR / "cache_html"
DIR_CACHE_DETALLE = BASE_DIR / "cache_detalle"

USER_AGENT = "MiProyectoInvestigacion/1.0 (estudiante; contacto@ejemplo.com)"
DELAY_SEGUNDOS = 3.0
MAX_PETICIONES = 150
MAX_PAGINAS_POR_URL = 10
RESERVA_PETICIONES_DETALLE = 70
TIMEOUT_SEGUNDOS = 30.0
OBJETIVO_VALIDOS = 30
TIPO_CAMBIO_CUP = 250.0

SEARCH_URLS = [
    "https://www.revolico.com/search?q=moto+electrica",
    "https://www.revolico.com/search?q=moto+electrica+cuba",
    "https://www.revolico.com/search?q=motico+electrica",
    "https://www.revolico.com/search?q=moto+electrica+3000w",
    "https://www.revolico.com/search?q=moto+electrica+2000w",
    "https://www.revolico.com/search?q=moto+electrica+1000w",
    "https://www.revolico.com/search?q=moto+electrica+72v",
    "https://www.revolico.com/search?q=moto+electrica+60v",
    "https://www.revolico.com/search?q=moto+electrica+5000w",
    "https://www.revolico.com/search?q=moto+electrica+4000w",
    "https://www.revolico.com/search?q=motico+3000w",
]

TIPOS_BATERIA_VALIDOS = ("litio", "gel", "plomo-acido", "lifepo4")

ESQUEMA_ANUNCIO = [
    "id_anuncio",
    "titulo",
    "precio_usd",
    "precio_cup",
    "moneda_original",
    "marca",
    "modelo",
    "tipo_vehiculo",
    "motor_w",
    "voltaje_v",
    "amperaje_ah",
    "tipo_bateria",
    "autonomia_km",
    "velocidad_max_kmh",
    "tiempo_carga_horas",
    "peso_kg",
    "tipo_moto",
    "licencia_requerida",
    "numero_ruedas",
    "tipo_frenos",
    "suspension",
    "accesorios",
    "estado",
    "kilometraje",
    "papeles",
    "color",
    "ubicacion",
    "municipio",
    "vendedor",
    "fecha_publicacion",
    "fecha_recoleccion",
    "descripcion",
    "url_anuncio",
]

CAMPOS_OBLIGATORIOS = [
    "marca",
    "motor_w",
    "tipo_bateria",
    "autonomia_km",
    "precio_usd",
    "ubicacion",
]

PALABRAS_EXCLUSION_TIPO = [
    "scooter",
    "scooters",
    "bicimoto",
    "bicimotos",
    "bici electrica",
    "bicicleta electrica",
    "patinete",
    "patineta",
    "triciclo",
    "triciclos",
    "trimoto",
    "infantil",
    "nino",
    "nina",
    "ciclomotor",
]

PATRONES_EXCLUSION_TIPO = [
    r"\b3\s+ruedas?\b",
    r"\btres\s+ruedas?\b",
    r"\bcuatriciclo\w*\b",
    r"\b4\s+ruedas?\b",
    r"\bcuatro\s+ruedas?\b",
]

PALABRAS_EXCLUSION_COMBUSTION = [
    "gasolina",
    "gasolinera",
    "combustion",
    "combustible",
    "nafta",
    "bencina",
    "diesel",
    "cilindrada",
    "cilindro",
    "carburador",
    "carburada",
    "tanque de gasolina",
    "tanque de combustible",
]

PATRONES_EXCLUSION_COMBUSTION = [
    r"\b2t\b",
    r"\b4t\b",
    r"\b(?:50|70|90|100|110|125|150|200|250|300|400|500|600|750|1000)\s*cc\b",
    r"\binyeccion\b",
    r"\bescape\b",
]

PALABRAS_NO_MARCA = {
    "moto", "motos", "motico", "moticos", "electrica", "electricas", "electrico",
    "electricos", "scooter", "scooters", "bicicleta", "bici", "patinete",
    "triciclo", "vehiculo", "de", "del", "la", "el", "los", "las", "un", "una",
    "y", "con", "para", "por", "sin", "nueva", "nuevo", "nuevas", "nuevos",
    "usada", "usado", "usadas", "seminueva", "venta", "vendo", "vender",
    "compro", "precio", "oferta", "promo", "promocion", "descuento", "rebaja",
    "rebajas", "oportunidad", "unico", "unica", "barato", "barata", "rapida",
    "rapido", "datos", "votol", "vota", "llave", "llaves", "asiento", "asientos",
    "rueda", "ruedas", "sobre", "dos", "tres", "cuatro", "hasta", "autonomia",
    "autonomo", "marca", "modelo", "motor", "bateria", "voltaje", "amperaje",
    "potencia", "cod", "codigo", "especificaciones", "especificacion", "version",
    "edicion", "solo", "queda", "quedan", "ningun", "ninguna", "incluye",
    "ganga", "ultima", "listed", "listo", "super", "sport", "pro", "plus",
    "max", "lite", "full", "2026", "2025", "battery", "baterias", "litio",
    "litium", "lithium", "acido", "cargador", "sensor", "alarma", "gps",
    "bluetooth", "usb", "pantalla", "velocidad", "caja", "asiento",
    "retrovisor", "espejo", "parabrisas", "frenos", "disco", "amortiguador",
    "0km", "0k", "km", "k", "watt", "watts", "vmax", "precio", "usd", "cup",
}

MARCAS_CONOCIDAS = [
    "topmaq", "izuki", "bucatti", "shikra", "halcon", "yoazaki", "yoazaky",
    "vortax", "vortex", "fenix", "flamingo", "aguila", "x-ryder", "nox", "bxc",
    "oxm", "segway", "volta", "vedca", "leiliang", "muratec", "murazaki",
    "galaxy", "x-mox", "xmox", "maf", "fly", "racing", "challenger", "laituning",
    "electric", "joycity", "sunra", "niu", "tank", "raptor", "e4", "ava",
    "vmax", "kugoo", "anker", "predator", "mountain", "shock", "city", "evo",
    "denza", "q7", "q5", "xpro", "a1", "b2", "t6", "s3", "r1", "m6", "p3",
    "k2", "h6", "w5", "v6", "n5", "f3", "s90", "n3", "fs-shark", "em-50",
]

RX_MOTOR_W = re.compile(r"(\d{3,5})\s*w")
RX_VOLTAJE = re.compile(r"(\d{2,3})\s*v\b")
RX_AMPERAJE = re.compile(r"(\d{1,3})\s*(?:ah|amp)m?\b")
RX_AUTONOMIA = re.compile(r"([^0-9]{0,16}?)(\d{1,4})\s*km(?!\s*/?\s*h\b)")
RX_AUTONOMIA_RANGO = re.compile(
    r"(?:autonomia|recorrido|alcance|rango|hasta)\D{0,40}?(\d{1,4})\s*(?:a|to)\s*(\d{1,4})"
)
CONTEXTO_VELOCIDAD = ("velocidad", "vmax", "maxima", "max", "rapidez")
RX_VELOCIDAD = re.compile(r"(\d{2,3})\s*km\s*/?\s*h\b")
RX_TIEMPO_CARGA = re.compile(r"carg\w*\D{0,25}?(\d{1,2}(?:[.,]\d)?)\s*(?:h|hs|horas?)\b")
RX_PESO = re.compile(r"peso\w*\D{0,15}?(\d{2,3}(?:[.,]\d)?)\s*kg\b")
RX_COLOR = re.compile(r"colores?\s*:?\s*([a-z]{3,20}(?:\s+[a-z]{3,20})?)")
RX_PAPELES_OK = re.compile(r"papeles\s+(?:en\s+)?regla")
RX_PAPELES_NO = re.compile(r"sin\s+papeles")
RX_FRENOS = re.compile(r"frenos?\s+(?:de\s+)?(disco|hidraulico|electronico|magnetico)")
RX_SUSPENSION = re.compile(r"(doble\s+suspension|suspension\s+(?:trasera|delantera|trasera\s+y\s+delantera))")
RX_PRECIO_TITULO = re.compile(r"\$\s*(\d{3,6})")
RX_INCLUSION_MOTO_ELECTRICA = re.compile(r"\bmoto\w*\s+electr\w+")
RX_INCLUSION_MOTICO = re.compile(r"\bmotico\w*")
RX_ESTADO_NUEVO = re.compile(r"\b(nueva|nuevo|nuevas|nuevos|recientes?|0\s*km)\b")
RX_ESTADO_USADO = re.compile(r"\b(usada|usado|usadas|usados|seminueva|seminuevo)\b")
RX_MARCADOR = re.compile(r"\b(?:moto\w*\s+electr\w*|motico\w*)\b")
RX_ETIQUETA_MODELO = re.compile(r"\bmodelo\s*:?\s*([a-z0-9\-]{1,20})\b")
RX_CORTE_SEGMENTO = re.compile(
    r"\$|//|•|·|—|–|\d{2,}\s*(?:w|v|ah|amp|km|kw|usd|cup|cc)\b|\b\d{3,}\b"
)

TIPOS_BATERIA_PATRONES = [
    ("lifepo4", re.compile(r"\b(?:lifepo\s?4|life\s?po\s?4|lifepo)\b")),
    ("litio", re.compile(r"\blit(?:io|ium|ihum|iun|hium|e|o)\b|\blit[a-z]{2,7}\b")),
    ("gel", re.compile(r"\bgel\b")),
    ("plomo-acido", re.compile(r"\b(?:plomo|ploma|acido)\b")),
]


def normalizar(texto: str) -> str:
    if not texto:
        return ""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    sin_acentos = "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", sin_acentos).strip()


def clave_titulo(titulo: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalizar(titulo))


def _contiene(texto: str, frases: list[str]) -> str | None:
    for frase in frases:
        if re.search(r"\b" + re.escape(frase).replace(r"\ ", r"\s+") + r"\b", texto):
            return frase
    return None


def verificar_inclusion(texto: str) -> bool:
    if RX_INCLUSION_MOTO_ELECTRICA.search(texto):
        return True
    if RX_INCLUSION_MOTICO.search(texto):
        return True
    return "moto" in texto and "electr" in texto


def verificar_exclusion_tipo(texto: str) -> str | None:
    encontrada = _contiene(texto, PALABRAS_EXCLUSION_TIPO)
    if encontrada:
        return encontrada
    for patron in PATRONES_EXCLUSION_TIPO:
        coincidencia = re.search(patron, texto)
        if coincidencia:
            return coincidencia.group(0)
    return None


def verificar_exclusion_combustion(texto: str) -> str | None:
    encontrada = _contiene(texto, PALABRAS_EXCLUSION_COMBUSTION)
    if encontrada:
        return encontrada
    for patron in PATRONES_EXCLUSION_COMBUSTION:
        coincidencia = re.search(patron, texto)
        if not coincidencia:
            continue
        if patron in (r"\binyeccion\b", r"\bescape\b") and "electr" in texto:
            continue
        return coincidencia.group(0)
    return None


def extraer_motor_w(texto: str) -> int | None:
    valores = [int(m) for m in RX_MOTOR_W.findall(texto)]
    valores = [v for v in valores if 100 <= v <= 60000]
    return max(valores) if valores else None


def extraer_voltaje(texto: str) -> int | None:
    valores = [int(v) for v in RX_VOLTAJE.findall(texto)]
    valores = [v for v in valores if 12 <= v <= 400]
    return max(valores) if valores else None


def extraer_amperaje(texto: str) -> int | None:
    valores = [int(a) for a in RX_AMPERAJE.findall(texto)]
    valores = [a for a in valores if 1 <= a <= 400]
    return max(valores) if valores else None


def extraer_autonomia(texto: str) -> int | None:
    valores = []
    for contexto, numero in RX_AUTONOMIA.findall(texto):
        if any(marca in contexto for marca in CONTEXTO_VELOCIDAD):
            continue
        valores.append(int(numero))
    for bajo, alto in RX_AUTONOMIA_RANGO.findall(texto):
        valores.extend([int(bajo), int(alto)])
    valores = [v for v in valores if 1 <= v <= 1000]
    return max(valores) if valores else None


def extraer_velocidad(texto: str) -> int | None:
    valores = [int(v) for v in RX_VELOCIDAD.findall(texto)]
    valores = [v for v in valores if 5 <= v <= 200]
    return max(valores) if valores else None


def extraer_tipo_bateria(texto: str) -> str | None:
    for etiqueta, patron in TIPOS_BATERIA_PATRONES:
        if patron.search(texto):
            return etiqueta
    return None


def extraer_tiempo_carga(texto: str) -> float | None:
    coincidencia = RX_TIEMPO_CARGA.search(texto)
    if not coincidencia:
        return None
    try:
        return float(coincidencia.group(1).replace(",", "."))
    except ValueError:
        return None


def extraer_peso(texto: str) -> float | None:
    coincidencia = RX_PESO.search(texto)
    if not coincidencia:
        return None
    try:
        return float(coincidencia.group(1).replace(",", "."))
    except ValueError:
        return None


def extraer_color(texto: str) -> str | None:
    coincidencia = RX_COLOR.search(texto)
    return coincidencia.group(1).strip() if coincidencia else None


def extraer_papeles(texto: str):
    if RX_PAPELES_OK.search(texto):
        return True
    if RX_PAPELES_NO.search(texto):
        return False
    return None


def extraer_frenos(texto: str) -> str | None:
    coincidencia = RX_FRENOS.search(texto)
    return coincidencia.group(1) if coincidencia else None


def extraer_suspension(texto: str) -> str | None:
    coincidencia = RX_SUSPENSION.search(texto)
    if not coincidencia:
        return None
    return re.sub(r"\s+", " ", coincidencia.group(1)).replace("suspension ", "").strip()


def extraer_estado(texto: str) -> str | None:
    if RX_ESTADO_USADO.search(texto):
        return "usado"
    if RX_ESTADO_NUEVO.search(texto):
        return "nuevo"
    return None


def limpiar_marca(palabra: str) -> str:
    return re.sub(r"[^a-z0-9]", "", palabra)


def _base_alineado(texto: str) -> str:
    salida = []
    for caracter in texto:
        if unicodedata.category(caracter) == "Mn":
            salida.append(caracter)
        else:
            salida.append(unicodedata.normalize("NFD", caracter)[0])
    return "".join(salida)


def _recuperar_casing(titulo_raw: str, palabra: str) -> str:
    patron = re.escape(palabra).replace(r"\-", r"[\s\-_]")
    coincidencia = re.search(patron, _base_alineado(titulo_raw), re.IGNORECASE)
    if coincidencia:
        return titulo_raw[coincidencia.start() : coincidencia.end()].strip(" -_")
    return palabra.title()


def extraer_marca_modelo(titulo_raw: str, texto: str):
    marca_palabra = None
    resto: list[str] = []

    marcador = RX_MARCADOR.search(texto)
    if marcador:
        segmento = texto[marcador.end() :]
        corte = RX_CORTE_SEGMENTO.split(segmento, maxsplit=1)[0]
        utiles = []
        for token in re.split(r"[^a-z0-9áéíóúñ\-]+", corte):
            limpio = limpiar_marca(token)
            if len(limpio) < 2 or limpio in PALABRAS_NO_MARCA:
                continue
            if not re.match(r"[a-z]", limpio):
                continue
            utiles.append(token)
        if utiles:
            marca_palabra = utiles[0]
            resto = utiles[1:]

    if not marca_palabra:
        for conocida in MARCAS_CONOCIDAS:
            if re.search(r"\b" + re.escape(conocida) + r"\b", texto):
                marca_palabra = conocida
                break

    if not marca_palabra or not (2 <= len(limpiar_marca(marca_palabra)) <= 30):
        return None, None

    marca = _recuperar_casing(titulo_raw, marca_palabra)
    modelo = " ".join(_recuperar_casing(titulo_raw, p) for p in resto).strip() or None
    etiqueta = RX_ETIQUETA_MODELO.search(texto)
    if etiqueta:
        modelo = _recuperar_casing(titulo_raw, etiqueta.group(1))
    return marca, modelo


def anuncio_vacio() -> dict:
    anuncio = {campo: None for campo in ESQUEMA_ANUNCIO}
    anuncio.update(
        {
            "precio_usd": None,
            "tipo_vehiculo": "moto_electrica",
            "tipo_moto": "moto_electrica",
            "accesorios": [],
            "descripcion": "",
            "url_busqueda": None,
            "motivo": None,
            "campos_faltantes": [],
            "visito_detalle": False,
        }
    )
    return anuncio


def parsear_listado(html: str):
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None or not script.string:
        return {}, []
    try:
        estado = json.loads(script.string)["props"]["pageProps"]["__APOLLO_STATE__"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}, []

    provincias, municipios = {}, {}
    for clave, valor in estado.items():
        if not isinstance(valor, dict):
            continue
        if clave.startswith("ProvinceType:"):
            provincias[str(valor.get("id"))] = valor.get("name")
        elif clave.startswith("MunicipalityType:"):
            municipios[str(valor.get("id"))] = valor.get("name")

    anuncios = [v for k, v in estado.items() if k.startswith("AdType:") and isinstance(v, dict)]
    return {"provincias": provincias, "municipios": municipios}, anuncios


def parsear_detalle(html: str) -> dict | None:
    soup = BeautifulSoup(html, "lxml")
    script = soup.find("script", id="__NEXT_DATA__")
    if script is None or not script.string:
        return None
    try:
        estado = json.loads(script.string)["props"]["pageProps"]["__APOLLO_STATE__"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    for clave, valor in estado.items():
        if clave.startswith("AdType:") and isinstance(valor, dict) and valor.get("description"):
            return valor
    return None


class Sesion:
    def __init__(self, usar_cache: bool = True) -> None:
        self.peticiones = 0
        self.errores: list[str] = []
        self.paginas_listado = 0
        self.paginas_detalle = 0
        self.desde_cache = 0
        self.usar_cache = usar_cache
        if self.usar_cache:
            DIR_CACHE_LISTADO.mkdir(exist_ok=True)
            DIR_CACHE_DETALLE.mkdir(exist_ok=True)

    def cabeceras(self) -> dict:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-CU,es;q=0.9,en;q=0.8",
        }

    def cabe(self, tope: int = MAX_PETICIONES) -> bool:
        return self.peticiones < tope

    def _descargar(self, cliente: httpx.Client, url: str, destino: Path | None) -> str | None:
        if not self.cabe():
            self.errores.append(f"limite de {MAX_PETICIONES} peticiones alcanzado: {url}")
            return None
        for intento in range(2):
            self.peticiones += 1
            try:
                respuesta = cliente.get(url, headers=self.cabeceras(), timeout=TIMEOUT_SEGUNDOS)
                if respuesta.status_code == 429:
                    self.errores.append(f"HTTP 429 en {url}; esperando 60s")
                    time.sleep(60)
                    continue
                if respuesta.status_code == 404:
                    self.errores.append(f"HTTP 404 en {url}")
                    return None
                if respuesta.status_code >= 400:
                    self.errores.append(f"HTTP {respuesta.status_code} en {url}")
                    return None
                if self.usar_cache and destino is not None:
                    destino.write_text(respuesta.text, encoding="utf-8")
                return respuesta.text
            except httpx.HTTPError as exc:
                self.errores.append(f"error de red en {url}: {type(exc).__name__}")
                return None
            finally:
                if intento == 0:
                    time.sleep(DELAY_SEGUNDOS)
        return None

    def obtener_listado(self, cliente: httpx.Client, url: str) -> str | None:
        destino = DIR_CACHE_LISTADO / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".html")
        if self.usar_cache and destino.exists():
            self.desde_cache += 1
            return destino.read_text(encoding="utf-8", errors="replace")
        return self._descargar(cliente, url, destino)

    def obtener_detalle(self, cliente: httpx.Client, url: str, id_anuncio: str) -> str | None:
        destino = DIR_CACHE_DETALLE / f"{id_anuncio}.html"
        if self.usar_cache and destino.exists():
            self.desde_cache += 1
            return destino.read_text(encoding="utf-8", errors="replace")
        return self._descargar(cliente, url, destino)


def construir_registro(
    anuncio: dict,
    catalogos: dict,
    url_busqueda: str,
    ahora: str,
    detalle: dict | None,
    visito_detalle: bool = False,
) -> dict:
    titulo_raw = (anuncio.get("title") or "").strip()
    texto_titulo = normalizar(titulo_raw)
    descripcion = (detalle.get("description") if detalle else "") or ""
    texto_desc = normalizar(descripcion)
    texto_completo = f"{texto_titulo} {texto_desc}".strip()

    registro = anuncio_vacio()
    registro["titulo"] = titulo_raw
    registro["fecha_recoleccion"] = ahora
    registro["url_busqueda"] = url_busqueda
    registro["descripcion"] = descripcion
    registro["visito_detalle"] = bool(visito_detalle)

    permalink = anuncio.get("permalink") or ""
    registro["url_anuncio"] = (
        "https://www.revolico.com" + permalink if permalink.startswith("/") else permalink
    )
    fecha = (detalle or {}).get("updatedOnByUser") or anuncio.get("updatedOnToOrder")
    if fecha:
        registro["fecha_publicacion"] = fecha
    if detalle and detalle.get("name"):
        registro["vendedor"] = detalle.get("name")

    provincia = catalogos["provincias"].get(str(anuncio.get("provinceId")))
    municipio = catalogos["municipios"].get(str(anuncio.get("municipalityId")))
    registro["ubicacion"] = provincia
    registro["municipio"] = municipio

    moneda = (anuncio.get("currency") or "USD").upper()
    registro["moneda_original"] = moneda
    precio = anuncio.get("price")
    if isinstance(precio, (int, float)) and precio > 0:
        if moneda == "CUP":
            registro["precio_cup"] = int(precio)
            registro["precio_usd"] = round(float(precio) / TIPO_CAMBIO_CUP, 2)
        else:
            registro["precio_usd"] = round(float(precio), 2)
    else:
        coincidencia = RX_PRECIO_TITULO.search(texto_completo)
        if coincidencia:
            registro["precio_usd"] = int(coincidencia.group(1))

    registro["marca"], registro["modelo"] = extraer_marca_modelo(titulo_raw, texto_titulo)

    def primero(fn):
        return fn(texto_titulo) or fn(texto_desc)

    registro["motor_w"] = primero(extraer_motor_w)
    registro["voltaje_v"] = primero(extraer_voltaje)
    registro["amperaje_ah"] = primero(extraer_amperaje)
    registro["tipo_bateria"] = primero(extraer_tipo_bateria)
    registro["autonomia_km"] = primero(extraer_autonomia)
    registro["velocidad_max_kmh"] = primero(extraer_velocidad)
    registro["tiempo_carga_horas"] = primero(extraer_tiempo_carga)
    registro["peso_kg"] = primero(extraer_peso)
    registro["tipo_frenos"] = primero(extraer_frenos)
    registro["suspension"] = primero(extraer_suspension)
    registro["color"] = primero(extraer_color)
    registro["papeles"] = primero(extraer_papeles)
    registro["estado"] = primero(extraer_estado)
    registro["numero_ruedas"] = 2
    registro["texto_completo"] = texto_completo
    return registro


def aplicar_filtros(registro: dict):
    """Devuelve (ok, motivo, campos_faltantes) aplicando el orden de AGENTS."""
    texto = registro.get("texto_completo") or ""
    if not verificar_inclusion(texto):
        return False, "exclusión: no coincide con criterio de inclusión (no es moto eléctrica)", []

    excluido_tipo = verificar_exclusion_tipo(texto)
    if excluido_tipo:
        return False, f"exclusión por tipo de vehículo: '{excluido_tipo}'", []

    excluido_combustible = verificar_exclusion_combustion(texto)
    if excluido_combustible:
        return False, f"exclusión por motor de combustión: '{excluido_combustible}'", []

    motor = registro["motor_w"]
    if motor is not None and motor < 1000:
        return False, "exclusión: motor menor a 1000W (probable bicimoto)", []

    faltantes = []
    if not registro["marca"]:
        faltantes.append("marca")
    if registro["motor_w"] is None:
        faltantes.append("motor_w")
    if registro["tipo_bateria"] not in TIPOS_BATERIA_VALIDOS:
        faltantes.append("tipo_bateria")
    if registro["autonomia_km"] is None:
        faltantes.append("autonomia_km")
    if registro["precio_usd"] is None:
        faltantes.append("precio_usd")
    if not registro["ubicacion"]:
        faltantes.append("ubicacion")

    if faltantes:
        return False, "campos obligatorios incompletos: " + ", ".join(faltantes), faltantes
    return True, None, []


def clave_de_motivo(motivo: str, faltantes: list[str]) -> str:
    if motivo.startswith("exclusión por tipo de vehículo"):
        return "exclusión por tipo de vehículo (scooter/bicimoto/patinete/ruedas)"
    if motivo.startswith("exclusión por motor de combustión"):
        return "exclusión por motor de combustión"
    if "menor a 1000W" in motivo:
        return "exclusión: motor menor a 1000W (probable bicimoto)"
    if motivo.startswith("exclusión: no coincide"):
        return "exclusión: no cumple criterio de inclusión (no es moto eléctrica)"
    if faltantes:
        return "campos obligatorios incompletos: " + ", ".join(faltantes)
    return motivo


def escribir_json(ruta: Path, datos) -> None:
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, indent=2)
        archivo.write("\n")


def verificar_json(ruta: Path) -> None:
    crudo = ruta.read_text(encoding="utf-8").strip()
    if not crudo.startswith("{") or not crudo.endswith("}"):
        raise SystemExit(f"{ruta.name} no empieza con {{ ni termina con }}")
    with open(ruta, encoding="utf-8") as archivo:
        json.load(archivo)


def main() -> int:
    parser = argparse.ArgumentParser(description="Scraper de motos eléctricas en Revolico")
    parser.add_argument("--refrescar", action="store_true", help="ignora la caché y vuelve a descargar")
    args = parser.parse_args()

    inicio = datetime.now(timezone.utc)
    ahora = inicio.strftime("%Y-%m-%dT%H:%M:%SZ")
    sesion = Sesion(usar_cache=not args.refrescar)
    tope_listado = MAX_PETICIONES - RESERVA_PETICIONES_DETALLE

    vistos: dict[str, tuple[dict, str]] = {}
    catalogo: dict = {"provincias": {}, "municipios": {}}
    paginas_por_url: dict[str, int] = {}

    with httpx.Client(follow_redirects=True, verify=True) as cliente:
        for url_base in SEARCH_URLS:
            for pagina in range(1, MAX_PAGINAS_POR_URL + 1):
                if not sesion.cabe(tope_listado):
                    break
                url = f"{url_base}&page={pagina}"
                html = sesion.obtener_listado(cliente, url)
                if html is None:
                    break
                cat, anuncios = parsear_listado(html)
                if not anuncios:
                    break
                sesion.paginas_listado += 1
                paginas_por_url[url_base] = pagina
                for clave in ("provincias", "municipios"):
                    catalogo[clave].update(cat.get(clave, {}))
                nuevos = 0
                for anuncio in anuncios:
                    clave = clave_titulo(anuncio.get("title") or "")
                    if not clave or clave in vistos:
                        continue
                    vistos[clave] = (anuncio, url)
                    nuevos += 1
                print(
                    f"[list p{pagina:>2}] {url_base.split('q=')[1]:<24} "
                    f"n={len(anuncios):>3} nuevos={nuevos:>3} "
                    f"(únicos {len(vistos)}, pets {sesion.peticiones})"
                )
                if nuevos == 0:
                    break
            time.sleep(DELAY_SEGUNDOS)

        print(f"\nListado terminado: {len(vistos)} anuncios únicos, "
              f"{sesion.peticiones} peticiones, {sesion.desde_cache} desde caché\n")

        validos: list[dict] = []
        descartados: list[dict] = []
        motivos = Counter()
        faltantes_totales = Counter()

        def registrar_descarte(registro: dict, motivo: str, faltantes: list[str]) -> None:
            registro.pop("texto_completo", None)
            registro["id_anuncio"] = f"descartado_{len(descartados) + 1:03d}"
            registro["motivo"] = motivo
            registro["campos_faltantes"] = faltantes
            descartados.append(registro)
            motivos[clave_de_motivo(motivo, faltantes)] += 1
            for campo in faltantes:
                faltantes_totales[campo] += 1

        def registrar_valido(registro: dict) -> None:
            registro.pop("texto_completo", None)
            registro["id_anuncio"] = f"moto_elec_{len(validos) + 1:03d}"
            validos.append(registro)

        candidatos: list[tuple[int, int, str, dict, str]] = []
        for clave, (anuncio, url_busqueda) in vistos.items():
            registro = construir_registro(anuncio, catalogo, url_busqueda, ahora, None)
            texto = registro["texto_completo"]
            if not verificar_inclusion(texto):
                registrar_descarte(
                    registro,
                    "exclusión: no coincide con criterio de inclusión (no es moto eléctrica)",
                    [],
                )
                continue
            excluido_tipo = verificar_exclusion_tipo(texto)
            if excluido_tipo:
                registrar_descarte(registro, f"exclusión por tipo de vehículo: '{excluido_tipo}'", [])
                continue
            excluido_combustible = verificar_exclusion_combustion(texto)
            if excluido_combustible:
                registrar_descarte(
                    registro, f"exclusión por motor de combustión: '{excluido_combustible}'", []
                )
                continue
            _, _, faltantes_titulo = aplicar_filtros(registro)
            candidatos.append(
                (len(faltantes_titulo), -len(registro["titulo"]), clave, anuncio, url_busqueda)
            )

        candidatos.sort(key=lambda item: (item[0], item[1]))
        print(f"Candidatos a visita de detalle: {len(candidatos)} "
              f"(ordenados por campos obligatorios que faltan)\n")

        visitados: set[str] = set()
        for indice, (faltantes, _largo, clave, anuncio, url_busqueda) in enumerate(candidatos, 1):
            if len(validos) >= OBJETIVO_VALIDOS or not sesion.cabe():
                break
            id_anuncio = str(anuncio.get("id") or clave)
            url_detalle = "https://www.revolico.com" + (anuncio.get("permalink") or "")
            html = sesion.obtener_detalle(cliente, url_detalle, id_anuncio)
            detalle = parsear_detalle(html) if html is not None else None
            if detalle is not None:
                sesion.paginas_detalle += 1
            visitados.add(clave)
            registro = construir_registro(
                anuncio, catalogo, url_busqueda, ahora, detalle, visito_detalle=html is not None
            )
            ok, motivo, faltantes_final = aplicar_filtros(registro)
            if ok:
                registrar_valido(registro)
            else:
                registrar_descarte(registro, motivo, faltantes_final)
            if indice % 10 == 0 or len(validos) >= OBJETIVO_VALIDOS:
                print(
                    f"[detalle {indice:>3}/{len(candidatos)}] "
                    f"válidos={len(validos)}/{OBJETIVO_VALIDOS} "
                    f"pets={sesion.peticiones} cache={sesion.desde_cache}"
                )

        sin_visitar = 0
        for _faltantes, _largo, clave, anuncio, url_busqueda in candidatos:
            if clave in visitados:
                continue
            sin_visitar += 1
            registro = construir_registro(anuncio, catalogo, url_busqueda, ahora, None)
            ok, motivo, faltantes_final = aplicar_filtros(registro)
            if ok:
                registrar_valido(registro)
            else:
                registrar_descarte(registro, motivo, faltantes_final)
        if sin_visitar:
            print(f"[detalle] {sin_visitar} candidatos sin visitar (presupuesto agotado); "
                  f"evaluados solo con el título")

    seleccionados = validos[:OBJETIVO_VALIDOS]
    for indice, anuncio in enumerate(seleccionados, 1):
        anuncio["id_anuncio"] = f"moto_elec_{indice:03d}"

    escribir_json(
        ARCHIVO_VALIDOS,
        {
            "vehiculo": "moto_electrica",
            "total_anuncios_validos": len(seleccionados),
            "anuncios": seleccionados,
        },
    )
    escribir_json(
        ARCHIVO_DESCARTADOS,
        {
            "total_revisados": len(vistos),
            "total_descartados": len(descartados),
            "total_validos": len(seleccionados),
            "descartados": descartados,
        },
    )
    verificar_json(ARCHIVO_VALIDOS)
    verificar_json(ARCHIVO_DESCARTADOS)

    lineas = [
        "REPORTE DE SCRAPING - MOTOS ELÉCTRICAS (Revolico)",
        "=" * 52,
        f"Fecha: {ahora}",
        f"URLs consultadas: {len(paginas_por_url)} de {len(SEARCH_URLS)}",
        "",
        "ESTA EJECUCIÓN:",
        f"  Peticiones nuevas a revolico.com: {sesion.peticiones}",
        f"  HTML servidos desde caché: {sesion.desde_cache}",
        f"  Páginas de listado procesadas: {sesion.paginas_listado}",
        f"  Páginas de detalle obtenidas: {sesion.paginas_detalle}",
        f"  Límite por sesión: {MAX_PETICIONES} peticiones",
        "",
        "ACUMULADO (todas las ejecuciones, según el contenido de la caché):",
        f"  Páginas de listado en cache_html/: {len(list(DIR_CACHE_LISTADO.glob('*.html')))}",
        f"  Páginas de detalle en cache_detalle/: {len(list(DIR_CACHE_DETALLE.glob('*.html')))}",
        f"  Peticiones acumuladas: "
        f"{len(list(DIR_CACHE_LISTADO.glob('*.html'))) + len(list(DIR_CACHE_DETALLE.glob('*.html')))}",
        f"  Delay entre peticiones: {DELAY_SEGUNDOS}s",
        "",
        f"Anuncios únicos revisados: {len(vistos)}",
        f"VÁLIDOS obtenidos: {len(seleccionados)} (objetivo {OBJETIVO_VALIDOS})",
        f"DESCARTADOS: {len(descartados)}",
        "",
        "Páginas por URL de búsqueda:",
    ]
    for url_base, total in paginas_por_url.items():
        lineas.append(f"  {total:>2}  {url_base}")
    lineas += ["", "DESCARTADOS POR MOTIVO:"]
    for motivo, total in motivos.most_common():
        lineas.append(f"  {total:>4}  {motivo}")
    lineas += ["", "CAMPOS OBLIGATORIOS QUE MÁS FALLAN:"]
    for campo, total in faltantes_totales.most_common():
        lineas.append(f"  {total:>4}  {campo}")
    if sesion.errores:
        lineas += ["", f"ERRORES REGISTRADOS ({len(sesion.errores)}):"]
        for error in sesion.errores[:40]:
            lineas.append(f"  - {error}")
        if len(sesion.errores) > 40:
            lineas.append(f"  ... y {len(sesion.errores) - 40} más")
    ARCHIVO_REPORTE.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    total_cache = len(list(DIR_CACHE_LISTADO.glob("*.html"))) + len(
        list(DIR_CACHE_DETALLE.glob("*.html"))
    )
    print(f"\nRevisados: {len(vistos)} | VÁLIDOS: {len(seleccionados)} | Descartados: {len(descartados)}")
    print(f"Peticiones nuevas (esta ejecución): {sesion.peticiones} "
          f"(listado {sesion.paginas_listado} páginas, detalle {sesion.paginas_detalle} páginas)")
    print(f"HTML desde caché (esta ejecución): {sesion.desde_cache}")
    print(f"Peticiones acumuladas (todas las ejecuciones): {total_cache} (límite {MAX_PETICIONES}/sesión)")
    print("Motivos:")
    for motivo, total in motivos.most_common():
        print(f"  {total:>4}  {motivo}")
    print("Campos que más fallan:")
    for campo, total in faltantes_totales.most_common():
        print(f"  {total:>4}  {campo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

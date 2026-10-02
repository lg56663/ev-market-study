#!/usr/bin/env python3
"""Ampliacion del scraping de motos electricas en Revolico.com (30 -> 90 anuncios).

Solo se consultan paginas de listado (search). NO se entra a paginas de detalle:
los campos obligatorios (marca, motor_w, autonomia_km, precio_usd, ubicacion) se
extraen del titulo del anuncio mas el precio/moneda/ubicacion del listado
(Next.js + Apollo, __NEXT_DATA__ -> __APOLLO_STATE__).

tipo_bateria es OPCIONAL: si no aparece en el titulo se guarda como null y el
anuncio sigue siendo valido.

Salidas:
  - motos_electricas_ampliacion.json      : hasta 60 anuncios validos nuevos
  - descartados_motos_ampliacion.json     : todos los descartados con trazabilidad
  - reporte_scraping_motos_ampliacion.txt : reporte de la sesion
Cache (separada):
  - cache_html_ampliacion/

No se modifica motos_electricas.json ni scraper_motos.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import httpx
import sys as _sys

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in _sys.path:
    _sys.path.insert(0, str(BASE_DIR))

import scraper_motos as base  # noqa: E402  (reutiliza los extractores ya validados)

ARCHIVO_VALIDOS = BASE_DIR / "motos_electricas_ampliacion.json"
ARCHIVO_DESCARTADOS = BASE_DIR / "descartados_motos_ampliacion.json"
ARCHIVO_REPORTE = BASE_DIR / "reporte_scraping_motos_ampliacion.txt"
ARCHIVO_BASE = BASE_DIR / "motos_electricas.json"
DIR_CACHE = BASE_DIR / "cache_html_ampliacion"

USER_AGENT = "MiProyectoInvestigacion/1.0"
DELAY_SEGUNDOS = 3.0
TIMEOUT_SEGUNDOS = 30.0
REINTENTOS = 2
ESPERA_REINTENTO = 5.0
MAX_PETICIONES = 260
MAX_PAGINAS_POR_URL = 12
OBJETIVO_VALIDOS = 60
FECHA_SCRAPING = "2026-09-30"
ANIO_MINIMO = 2026
TIPO_CAMBIO_CUP = 250.0

SEARCH_QUERIES = [
    "moto electrica litio",
    "moto electrica barata",
    "moto electrica nueva",
    "moto electrica 5000w",
    "moto electrica 4000w",
    "moto electrica 1500w",
    "moto electrica 800w",
    "moto electrica importada",
    "moto electrica china",
    "moto electrica sin chapa",
    "moto electrica con chapa",
    "moto electrica La Habana",
]

SEARCH_QUERIES_RESPALDO = [
    "moto electrica Centro Habana",
    "moto electrica Playa",
    "moto electrica Marianao",
    "moto electrica Cerro",
    "moto electrica Habana Vieja",
    "moto electrica Diez de Octubre",
    "moto electrica Arroyo Naranjo",
    "moto electrica Boyeros",
]

SEARCH_QUERIES_EXTRA = [
    "moto electrica 72v",
    "moto electrica 60v",
    "moto electrica 2000w",
    "moto electrica 1000w",
    "moto electrica 3000w",
    "moto electrica 6000w",
    "moto electrica autonomia",
    "moto electrica bateria",
    "motico electrica",
    "moto electrica yoazaky",
    "moto electrica bucatti",
    "moto electrica topmaq",
    "moto electrica izuki",
    "moto electrica shikra",
    "moto electrica halcon",
    "moto electrica votol",
]

MUNICIPIOS_HABANA = {
    "centro habana", "playa", "marianao", "cerro", "habana vieja",
    "diez de octubre", "arroyo naranjo", "boyeros", "plaza",
    "plaza de la revolucion", "san miguel del padron", "guanabacoa",
    "regla", "habana del este", "cotorro", "la lisa",
}

CAMPOS_ANUNCIO = [
    "titulo",
    "marca",
    "motor_w",
    "tipo_bateria",
    "autonomia_km",
    "precio_usd",
    "ubicacion",
    "fecha_publicacion",
    "url",
    "descripcion",
]

TIPOS_BATERIA_VALIDOS = ("litio", "gel", "plomo-acido", "lifepo4")

# Los anuncios que ni siquiera son motos electricas no entran en descartes:
# el vocabulario de motivos es fijo y no tiene codigo para "no es moto electrica".
NO_CANDIDATO = "__no_candidato__"

RX_VELOCIDAD_KM = re.compile(r"(\d{1,4})\s*km(?!\s*/?\s*h\b)")
RX_TENS_KM = re.compile(r"\d{1,3}\s*v\s*$")
CONTEXTO_VELOCIDAD_KM = ("velocidad", "vmax", "maxima", "max ", "rapidez", "km/h", "kmh")


def search_url(query: str) -> str:
    return "https://www.revolico.com/search?q=" + query.replace(" ", "+")


def normalizar(texto: str) -> str:
    return base.normalizar(texto)


def clave_titulo(titulo: str) -> str:
    return base.clave_titulo(titulo)


def es_habana(provincia: str | None, municipio: str | None) -> bool:
    if provincia and normalizar(provincia) == "la habana":
        return True
    if municipio and normalizar(municipio) in MUNICIPIOS_HABANA:
        return True
    return False


def extraer_autonomia(texto: str) -> int | None:
    """Autonomia en km a partir de un texto normalizado.

    Rechaza valores precedidos por voltaje (p.ej. '72v 80km') y menciones de
    velocidad maxima, para no inventar autonomias.
    """
    valores: list[int] = []
    for coincidencia in RX_VELOCIDAD_KM.finditer(texto):
        previo = texto[max(0, coincidencia.start() - 18) : coincidencia.start()]
        if any(marca in previo for marca in CONTEXTO_VELOCIDAD_KM):
            continue
        if RX_TENS_KM.search(previo):
            continue
        valor = int(coincidencia.group(1))
        if 1 <= valor <= 1000:
            valores.append(valor)
    for bajo, alto in base.RX_AUTONOMIA_RANGO.findall(texto):
        valores.extend([int(bajo), int(alto)])
    return max(valores) if valores else None


def extraer_precio(anuncio: dict, texto: str) -> float | None:
    moneda = (anuncio.get("currency") or "USD").upper()
    precio = anuncio.get("price")
    if isinstance(precio, (int, float)) and precio > 0:
        if moneda == "CUP":
            return round(float(precio) / TIPO_CAMBIO_CUP, 2)
        return round(float(precio), 2)
    coincidencia = base.RX_PRECIO_TITULO.search(texto)
    if coincidencia:
        return float(coincidencia.group(1))
    return None


def parsear_fecha(anuncio: dict) -> str | None:
    crudo = anuncio.get("updatedOnToOrder") or anuncio.get("updatedOnByUser")
    if not crudo:
        return None
    coincidencia = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(crudo))
    if not coincidencia:
        return None
    return "-".join(coincidencia.groups())


def construir_campos(anuncio: dict, provincia: str | None, municipio: str | None) -> dict:
    titulo_raw = (anuncio.get("title") or "").strip()
    texto = normalizar(titulo_raw)
    marca, _modelo = base.extraer_marca_modelo(titulo_raw, texto)
    motor_w = base.extraer_motor_w(texto)
    tipo_bateria = base.extraer_tipo_bateria(texto)
    if tipo_bateria not in TIPOS_BATERIA_VALIDOS:
        tipo_bateria = None
    return {
        "titulo": titulo_raw or None,
        "marca": marca or None,
        "motor_w": motor_w,
        "tipo_bateria": tipo_bateria,
        "autonomia_km": extraer_autonomia(texto),
        "precio_usd": extraer_precio(anuncio, texto),
        "ubicacion": provincia or municipio or None,
        "fecha_publicacion": parsear_fecha(anuncio),
        "url": base_registro_url(anuncio),
        "descripcion": None,
        "texto": texto,
        "provincia": provincia,
        "municipio": municipio,
    }


def base_registro_url(anuncio: dict) -> str:
    permalink = anuncio.get("permalink") or ""
    if permalink.startswith("/"):
        return "https://www.revolico.com" + permalink
    return permalink or None


def registro_descarte(campos: dict, motivo: str) -> dict:
    """Todos los campos del esquema, aunque sean null, mas el motivo."""
    registro = {campo: None for campo in CAMPOS_ANUNCIO}
    for campo in CAMPOS_ANUNCIO:
        if campos.get(campo) is not None:
            registro[campo] = campos[campo]
    registro["titulo"] = campos.get("titulo")
    registro["url"] = campos.get("url")
    registro["descripcion"] = campos.get("descripcion") or None
    registro["motivo"] = motivo
    return registro


def evaluar(campos: dict, url_base: set[str], titulos_base: set[str]) -> str | None:
    """Devuelve None si el anuncio es valido, o el codigo de motivo del descarte."""
    texto = campos["texto"]
    if not campos["titulo"]:
        return "titulo_vacio"
    if campos["url"] in url_base:
        return "url_duplicada"
    if clave_titulo(campos["titulo"]) in titulos_base:
        return "url_duplicada"
    if not base.verificar_inclusion(texto):
        return NO_CANDIDATO
    excluido_tipo = base.verificar_exclusion_tipo(texto)
    if excluido_tipo:
        if "triciclo" in excluido_tipo or "trimoto" in excluido_tipo:
            return "es_triciclo"
        if "bicimoto" in excluido_tipo or "bici" in excluido_tipo:
            return "es_bicimoto"
        if "ciclomotor" in excluido_tipo:
            return "es_scooter"
        return "es_scooter"
    if base.verificar_exclusion_combustion(texto):
        return "es_combustion"
    if campos["motor_w"] is None:
        return "sin_motor_w"
    if campos["motor_w"] < 1000:
        return "motor_w_bajo"
    if not campos["marca"]:
        return "sin_marca"
    if campos["autonomia_km"] is None:
        return "sin_autonomia"
    if campos["precio_usd"] is None:
        return "sin_precio"
    if not campos["ubicacion"]:
        return "sin_ubicacion"
    if not es_habana(campos["provincia"], campos["municipio"]):
        return "fuera_de_la_habana"
    fecha = campos["fecha_publicacion"]
    if not fecha or int(fecha[:4]) < ANIO_MINIMO:
        return "fecha_no_2026"
    return None


class Sesion:
    def __init__(self, usar_cache: bool = True) -> None:
        self.peticiones = 0
        self.desde_cache = 0
        self.paginas = 0
        self.errores: list[str] = []
        self.usar_cache = usar_cache
        if usar_cache:
            DIR_CACHE.mkdir(exist_ok=True)

    def cabeceras(self) -> dict:
        return {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-CU,es;q=0.9,en;q=0.8",
        }

    def cabe(self) -> bool:
        return self.peticiones < MAX_PETICIONES

    def obtener(self, cliente: httpx.Client, url: str) -> str | None:
        destino = DIR_CACHE / (hashlib.sha1(url.encode("utf-8")).hexdigest() + ".html")
        if self.usar_cache and destino.exists():
            html = destino.read_text(encoding="utf-8", errors="replace")
            if "__NEXT_DATA__" in html:
                self.desde_cache += 1
                return html
        if not self.cabe():
            self.errores.append(f"limite de {MAX_PETICIONES} peticiones alcanzado: {url}")
            return None
        for intento in range(REINTENTOS):
            self.peticiones += 1
            try:
                respuesta = cliente.get(
                    url, headers=self.cabeceras(), timeout=TIMEOUT_SEGUNDOS
                )
                if respuesta.status_code == 429:
                    self.errores.append(f"HTTP 429 en {url}; esperando 60s")
                    time.sleep(60)
                    continue
                if respuesta.status_code >= 400:
                    self.errores.append(f"HTTP {respuesta.status_code} en {url}")
                    return None
                if self.usar_cache:
                    destino.write_text(respuesta.text, encoding="utf-8")
                return respuesta.text
            except httpx.HTTPError as exc:
                self.errores.append(f"error de red en {url}: {type(exc).__name__}")
                if intento < REINTENTOS - 1:
                    time.sleep(ESPERA_REINTENTO)
        return None


def cargar_urls_base() -> tuple[set[str], set[str]]:
    if not ARCHIVO_BASE.exists():
        return set()
    datos = json.loads(ARCHIVO_BASE.read_text(encoding="utf-8"))
    urls, titulos = set(), set()
    for anuncio in datos.get("anuncios", []):
        url = anuncio.get("url_anuncio")
        if url:
            urls.add(url)
        titulos.add(clave_titulo(anuncio.get("titulo") or ""))
    return urls, titulos


def escribir_json(ruta: Path, datos) -> None:
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(datos, archivo, ensure_ascii=False, indent=2)
        archivo.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ampliacion scraping motos electricas")
    parser.add_argument("--refrescar", action="store_true", help="ignora la caché")
    args = parser.parse_args()

    inicio = datetime.now(timezone.utc)
    ahora = inicio.strftime("%Y-%m-%dT%H:%M:%SZ")
    sesion = Sesion(usar_cache=not args.refrescar)
    urls_base, titulos_base = cargar_urls_base()
    print(f"Anuncios ya existentes en motos_electricas.json: {len(urls_base)} urls\n")

    catalogo: dict = {"provincias": {}, "municipios": {}}
    vistos: dict[str, tuple[dict, str]] = {}
    urls_consultadas: list[str] = []
    paginas_por_query: dict[str, int] = {}

    def rastrear(queries: list[str], etiqueta: str) -> None:
        for query in queries:
            base_url = search_url(query)
            for pagina in range(1, MAX_PAGINAS_POR_URL + 1):
                if not sesion.cabe():
                    print("  (limite de peticiones alcanzado)")
                    return
                url = f"{base_url}&page={pagina}"
                html = sesion.obtener(cliente, url)
                if html is None:
                    break
                urls_consultadas.append(url)
                cat, anuncios = base.parsear_listado(html)
                if not anuncios:
                    break
                sesion.paginas += 1
                paginas_por_query[query] = pagina
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
                    f"[{etiqueta} p{pagina:>2}] {query:<30} n={len(anuncios):>3} "
                    f"nuevos={nuevos:>3} (unicos {len(vistos)}, pets {sesion.peticiones})"
                )
                if nuevos == 0:
                    break
                time.sleep(DELAY_SEGUNDOS)

    def evaluar_vistos() -> tuple[list, list, Counter]:
        validos: list[dict] = []
        descartados: list[dict] = []
        motivos: Counter = Counter()
        vistos_urls: set[str] = set()
        for clave, (anuncio, url_busqueda) in vistos.items():
            provincia = catalogo["provincias"].get(str(anuncio.get("provinceId")))
            municipio = catalogo["municipios"].get(str(anuncio.get("municipalityId")))
            campos = construir_campos(anuncio, provincia, municipio)
            if not es_habana(provincia, municipio):
                motivo = "fuera_de_la_habana"
            else:
                motivo = evaluar(campos, urls_base, titulos_base)
            if campos["url"] in vistos_urls:
                motivo = "url_duplicada"
            if motivo == NO_CANDIDATO:
                continue
            if motivo:
                descartados.append(registro_descarte(campos, motivo))
                motivos[motivo] += 1
                continue
            vistos_urls.add(campos["url"])
            validos.append({campo: campos[campo] for campo in CAMPOS_ANUNCIO})
        return validos, descartados, motivos

    with httpx.Client(follow_redirects=True, verify=True) as cliente:
        rastrear(SEARCH_QUERIES, "q")
        validos, descartados, motivos = evaluar_vistos()
        print(f"\nTras queries base: {len(validos)} validos (objetivo {OBJETIVO_VALIDOS})")

        if len(validos) < OBJETIVO_VALIDOS:
            rastrear(SEARCH_QUERIES_RESPALDO, "res")
            validos, descartados, motivos = evaluar_vistos()
            print(f"Tras queries de respaldo: {len(validos)} validos")

        if len(validos) < OBJETIVO_VALIDOS:
            rastrear(SEARCH_QUERIES_EXTRA, "extra")
            validos, descartados, motivos = evaluar_vistos()
            print(f"Tras queries extra: {len(validos)} validos")

    validos = validos[:OBJETIVO_VALIDOS]

    escribir_json(
        ARCHIVO_VALIDOS,
        {
            "vehiculo": "moto_electrica",
            "fecha_scraping": FECHA_SCRAPING,
            "tipo": "ampliacion",
            "filtro_ubicacion": "La Habana",
            "total_anuncios_validos": len(validos),
            "urls_consultadas": urls_consultadas,
            "anuncios": validos,
        },
    )
    escribir_json(
        ARCHIVO_DESCARTADOS,
        {
            "vehiculo": "moto_electrica",
            "fecha_scraping": FECHA_SCRAPING,
            "tipo": "ampliacion",
            "total_descartados": len(descartados),
            "descartes": descartados,
        },
    )

    precios = [a["precio_usd"] for a in validos if a["precio_usd"] is not None]
    motores = [a["motor_w"] for a in validos if a["motor_w"] is not None]
    marcas = Counter(a["marca"] for a in validos)
    sin_bateria = sum(1 for a in validos if a["tipo_bateria"] is None)

    lineas = [
        "REPORTE DE SCRAPING - MOTOS ELECTRICAS, AMPLIACION 30 -> 90 (Revolico)",
        "=" * 68,
        f"Fecha de scraping: {FECHA_SCRAPING}",
        f"Ejecucion: {ahora}",
        "",
        "CONFIGURACION:",
        f"  User-Agent: {USER_AGENT}",
        f"  Delay entre peticiones: {DELAY_SEGUNDOS}s",
        f"  Timeout: {TIMEOUT_SEGUNDOS}s",
        f"  Reintentos por URL: {REINTENTOS} (espera {ESPERA_REINTENTO}s)",
        "  Cache: cache_html_ampliacion/ (separada de cache_html/)",
        "  Paginas de detalle visitadas: 0",
        f"  Limite por sesion: {MAX_PETICIONES} peticiones",
        "",
        "RED:",
        f"  Peticiones nuevas a revolico.com: {sesion.peticiones}",
        f"  HTML servidos desde cache: {sesion.desde_cache}",
        f"  Paginas de listado procesadas: {sesion.paginas}",
        f"  URLs de listado registradas: {len(urls_consultadas)}",
        "",
        "QUERIES:",
    ]
    for query, total in paginas_por_query.items():
        lineas.append(f"  {total:>2} pag.  {query}")
    lineas += [
        "",
        "RESULTADOS:",
        f"  Anuncios unicos revisados: {len(vistos)}",
        f"  VÁLIDOS nuevos (objetivo {OBJETIVO_VALIDOS}): {len(validos)}",
        f"  DESCARTADOS: {len(descartados)}",
        "",
        "DESCARTADOS POR MOTIVO:",
    ]
    for motivo, total in motivos.most_common():
        lineas.append(f"  {total:>5}  {motivo}")
    lineas += [
        "",
        "CAMPOS OBLIGATORIOS (5): marca, motor_w, autonomia_km, precio_usd, ubicacion",
        "tipo_bateria es OPCIONAL: se guarda null cuando el titulo no lo indica",
        f"  Válidos con tipo_bateria null: {sin_bateria}",
        "",
        "RANGOS DE LOS VÁLIDOS:",
        f"  precio_usd: min={min(precios):.2f}  max={max(precios):.2f}  n={len(precios)}"
        if precios
        else "  precio_usd: sin datos",
        f"  motor_w: min={min(motores)}  max={max(motores)}  n={len(motores)}"
        if motores
        else "  motor_w: sin datos",
        "",
        "MARCAS MÁS FRECUENTES:",
    ]
    for marca, total in marcas.most_common():
        lineas.append(f"  {total:>5}  {marca}")
    if sesion.errores:
        lineas += ["", f"ERRORES REGISTRADOS ({len(sesion.errores)}):"]
        for error in sesion.errores[:40]:
            lineas.append(f"  - {error}")
        if len(sesion.errores) > 40:
            lineas.append(f"  ... y {len(sesion.errores) - 40} más")
    ARCHIVO_REPORTE.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    print(f"\nRevisados: {len(vistos)} | VÁLIDOS: {len(validos)} | Descartados: {len(descartados)}")
    print(f"Peticiones nuevas: {sesion.peticiones} | Desde caché: {sesion.desde_cache}")
    print("Motivos:")
    for motivo, total in motivos.most_common():
        print(f"  {total:>5}  {motivo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

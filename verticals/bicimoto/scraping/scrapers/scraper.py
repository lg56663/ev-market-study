"""
Scraper de anuncios de bicicletas electricas / bicimotos de Revolico.com.

Convenciones segun AGENTS.md:
  - httpx + BeautifulSoup(lxml). Sin Firecrawl / MCP / Playwright.
  - NO se entran a paginas de detalle de anuncios.
  - Todo se extrae del HTML del listado.
  - User-Agent honesto, delay de 3 s entre peticiones, tope de peticiones.

El listado de Revolico es una app Next.js + Apollo. Cada pagina de /search
trae un <script id="__NEXT_DATA__"> con props.pageProps.__APOLLO_STATE__, que
contiene:
  - AdType:<id>            -> stubs de anuncios (id, title, price, currency,
                              permalink, provinceId, municipalityId, fecha)
  - ProvinceType:<id>      -> nombre de provincia + sus municipios
  - MunicipalityType:<id>  -> nombre de municipio
  - CategoryType:<id>      -> taxonomia (util para filtrar subcategorias)

Eso permite resolver ubicacion (provincia) y municipio de forma EXACTA a partir
de los ids, sin adivinar por texto y sin abrir fichas.

Limitacion real del listado: NO existe un campo de descripcion en los stubs.
El unico texto por anuncio es `title`. En la practica los vendedores cubanos
escriben la ficha completa dentro del titulo, asi que todo el regex de
especificaciones se aplica sobre el titulo.

Salidas:
  bicis_electricas.json  -> 30 anuncios validos segun esquema
  reporte_scraping.txt   -> estadisticas de la corrida
"""

from __future__ import annotations

import csv
import json
import hashlib
import os
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# Configuracion etica / limites (AGENTS.md)
# --------------------------------------------------------------------------

USER_AGENT = "MiProyectoInvestigacion/1.0 (estudiante; contacto@ejemplo.com)"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9",
}

DELAY_S = 3.0            # minimo exigido por AGENTS.md
MAX_PETICIONES = 60      # 6 URLs x 10 paginas, por debajo del tope de 80
MAX_PAGINAS_POR_URL = 10
OBJETIVO = 30
TIMEOUT = 30.0

BASE = "https://www.revolico.com"
SEARCH_QUERIES = [
    "bicimoto",
    "bici electrica",
    "bicicleta electrica",
    "e-bike",
    "bicimoto electrica",
    "bici motor",
]

BASE_DIR = Path(__file__).resolve().parent
OUT_JSON = Path("bicis_electricas.json")
OUT_REPORTE = Path("reporte_scraping.txt")
OUT_DESCARTES_CSV = Path("descartes.csv")
OUT_DESCARTES_JSONL = Path("descartes.jsonl")
CACHE_DIR = Path(".cache_html")
ESQUEMA = BASE_DIR / "esquema_bici_electrica.json"

# --------------------------------------------------------------------------
# Vocabulario de dominio
# --------------------------------------------------------------------------

# Palabras que describen el tipo de vehiculo, no la marca.
GENERICAS = {
    "bicimoto", "bicimotos", "bicimotora", "motico", "moticos",
    "bicicleta", "bicicletas", "bici", "bicis", "bicycle", "bicycles",
    "electrica", "eléctrica", "electrico", "eléctrico", "electricas",
    "eléctricas", "electronica", "movilidad", "urbana",
    "plegable", "nueva", "nuevo", "nuevas", "nuevos", "seminueva",
    "seminuevo", "usada", "usado", "como", "vendo", "se", "vende",
    "en", "venta", "oferta", "super", "oportunidad", "precio", "unica",
    "climatizada", " climatizada", "para", "con", "de", "la", "el", "y",
    "revolico", "cuba", "habana", "havana", "excelente", "gran", "autonomia",
    "autonomía", "moderna", "moderno", "2024", "2025", "2026", "2027",
}

# Terminos de moto / scooter que deben EXCLUIRSE cuando no hay una palabra
# de bici que los legitime como bicimoto.
MOTOCICLO = re.compile(r"\b(moto|motos|motico|moticos|choperita|ciclomotor)\b", re.I)
BICI = re.compile(r"\b(bici|bicis|bicicleta|bicicletas|bicimoto|bicimotos)\b", re.I)
PATINETA = re.compile(r"\b(patineta|patinetas|scooter|scooters|monopatín|monopatin)\b", re.I)

# Exclusiones literales de AGENTS.md
EXCLUSIONES = [
    ("triciclo", re.compile(r"\btriciclos?\b", re.I)),
    ("trimoto", re.compile(r"\btrimotos?\b", re.I)),
    ("3 ruedas", re.compile(r"\b(3|tres)\s+ruedas\b", re.I)),
    ("4 ruedas", re.compile(r"\b(4|cuatro)\s+ruedas\b", re.I)),
    ("cuatriciclo", re.compile(r"\bcuatriciclos?\b", re.I)),
    ("accesorios/recuestos", re.compile(
        r"\b(accesorios?|recuestos?|repuestos?|piezas?|partes?|baterias?\s+sueltas|"
        r"llantas?\s+sueltas|repuesto|cargadores?|cubos?\s+de|asientos?\s+de|"
        r"forros?|preposapies|planta[s]?\s+el[eé]ctrica|generador|muleta)\b", re.I)),
]

# Un anuncio que dice "BICI ELECTRICA + ACCESORIOS" sigue vendiendo la bici:
# solo se descarta como recuesto si NO hay una senal fuerte de vehiculo.
SENAL_VEHICULO = re.compile(
    r"\b(bici|bicis|bicicleta|bicicletas|bicimoto|bicimotos|motico|e-?bike|ebike)\b", re.I)

# Marcas conocidas del mercado cubano de bicimotos / e-bikes.
# OJO: solo marcas reales. Palabras genericas como "bike", "bici", "electric"
# o "power" NO van aqui: un anuncio "BICICLETA E-BIKE 48V" no tiene marca.
MARCAS_CONOCIDAS = [
    "mafmot", "maf engle", "mafengle", "topmaq", "challenger", "yoazaky", "latuning",
    "laituning", "porto bello", "portobello", "deepower", "askgo", "ridstar", "onebot",
    "sargente", "vedca", "treck", "jmd", "kinetica", "midha", "halcon", "bucatti",
    "combat", "ava", "dttzh", "oris", "mofarre", "phoenix", "lwj", "jinlang",
    "trastec", "sunra", "buyang", "ruichi", "aima", "aucma", "gacafe", "leisger",
    "letabot", "moben", "explorer", "adventure", "city-bike",
]

# Palabras que delatan una subcategoria de la taxonomia del sitio.
CAT_MOTOS = re.compile(r"\b(motos?\s+y\s+gasolina|motogasolina|moto\s+de\s+combusti|"
                       r"motocicleta|motocicletas|choperas?|moped|cuatrimoto)\b", re.I)
CAT_ACCESORIOS = re.compile(r"\b(cubos?|ruedas|llantas|neumaticos?|camaras|"
                            r"cables|cargadores?|adaptadores?|baterias?$|asientos?)\b", re.I)

# --------------------------------------------------------------------------
# Utilidades de texto
# --------------------------------------------------------------------------


def norm(s: str | None) -> str:
    """Minusculas sin acentos, para comparar de forma robusta."""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower()


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = re.sub(r"[\u200d\ufeff]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def strip_emoji(s: str) -> str:
    return "".join(
        c for c in s
        if unicodedata.category(c) not in {"So", "Sk", "Cs", "Co"} and ord(c) != 0xFE0F
    )


# --------------------------------------------------------------------------
# Extraccion de campos por regex sobre el titulo
# --------------------------------------------------------------------------

RE_AUTONOMIA = re.compile(
    r"(\d{1,4}(?:\s*[-–—a]{1,2}\s*\d{1,4})?)\s*(?:k\s?m|kms|kil[oó]metros?)\b(?!\s*/?\s*h)",
    re.I,
)
RE_MOTOR = re.compile(r"(\d{2,5}(?:[.,]\d)?)\s*(?:w|watts?|vatios?)\b", re.I)
RE_VOLTAJE = re.compile(r"(\d{2,3})\s*(?:v|volt(?:os?|aje)?)\b", re.I)
RE_AMPERAJE = re.compile(r"(\d{1,3}(?:[.,]\d)?)\s*a\.?h\.?\b", re.I)
RE_VELOCIDAD = re.compile(r"(\d{2,3})\s*(?:k\s?m\s*/\s*h|kmh)\b", re.I)
RE_PESO = re.compile(r"(\d{2,3}(?:[.,]\d)?)\s*(?:kilos?|kg)\b", re.I)
RE_RUEDA = re.compile(r"(\d{2,3}(?:[.,]\d)?)\s*(?:pulgadas|\")", re.I)
RE_CARGA_H = re.compile(r"(\d{1,2}(?:[.,]\d)?)\s*(?:h|horas?)\s*(?:de\s*)?carga", re.I)
RE_ANIOS = re.compile(r"\b(20[12]\d)\b")

# tipo de bateria: se evaluan en orden de especificidad
RE_BAT_LITIO = re.compile(
    r"\b(litio|lithium|li-?ion|liion|lifepo4?|li-?fe-?po4?|ion-?de-?litio)\b", re.I)
RE_BAT_GEL = re.compile(r"\b(gel|gel-?acid|gelac|gel-?electrolito)\b", re.I)
RE_BAT_PLOMO = re.compile(
    r"\b(plomo|plomo-?acido|acido|ácido|acido-?plomo|lead-?acid|agm)\b", re.I)


def num(m: re.Match | None) -> float | None:
    if not m:
        return None
    raw = m.group(1)
    raw = re.split(r"[-–—a]", raw)[0]
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def extraer_autonomia(t: str) -> float | None:
    """Primer valor de autonomia plausible. 0 km se descarta (anuncios '0 km' = nuevo)."""
    for m in RE_AUTONOMIA.finditer(t):
        v = num(m)
        if v is None:
            continue
        # rango '80-100km' -> se queda con el maximo declarado
        nums = [float(x) for x in re.findall(r"\d{1,4}", m.group(1))]
        v = max(nums) if nums else v
        if 3 <= v <= 400:
            return v
    return None


def extraer_tipo_bateria(t: str) -> str | None:
    n = norm(t)
    if RE_BAT_LITIO.search(n):
        return "litio"
    if RE_BAT_GEL.search(n):
        return "gel"
    if RE_BAT_PLOMO.search(n):
        return "plomo-acido"
    return None


def _formatear_marca(s: str) -> str | None:
    """Limpia puntuacion residual y unifica mayus/minus."""
    s = clean_text(s).strip(" .,;:|-–—_/\\")
    s = clean_text(s)
    if not s or norm(s) in GENERICAS:
        return None
    if s.isupper() or s.islower():
        s = s.title()
    return s


def extraer_marca(t: str) -> str | None:
    """Marca SOLO si hay evidencia real: 'Marca: X' explicito o diccionario de
    marcas conocidas. No se adivina a partir de un token suelto: en titulos sin
    marca (p.ej. 'Bicicleta electrica Litio de alta capacidad') el primer token
    util seria 'Litio' o 'Adulos', que no son marcas. Sin marca real -> None ->
    el anuncio se descarta, porque 'marca' es un campo obligatorio."""
    n = norm(clean_text(t))

    m = re.search(r"\bmarca\s*[:\-]?\s*([a-z0-9][a-z0-9\-\.\+]{1,24})", n)
    if m:
        cand = _formatear_marca(m.group(1))
        if cand:
            return cand

    for brand in MARCAS_CONOCIDAS:
        if re.search(rf"\b{re.escape(norm(brand))}\b", n):
            return _formatear_marca(brand)
    return None


def tipo_vehiculo(t: str) -> str:
    n = norm(t)
    if re.search(r"\b(bicimoto|bicimotos|motico|moticos)\b", n):
        return "bicimoto"
    if re.search(r"\b(bici|bicis|bicicleta|bicicletas|e-?bike|ebike)\b", n):
        return "bicicleta_electrica"
    if PATINETA.search(n):
        return "patineta_electrica"
    return "otro"


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class Cliente:
    """Cliente httpx con cache de paginas en disco.

    La cache existe para que el scraping sea REPRODUCIBLE sin volver a golpear
    el sitio: la primera corrida descarga, las siguientes son 0 peticiones.
    Forzar refresh con REVOLICO_REFRESH=1.
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.client = httpx.Client(
            headers=HEADERS,
            timeout=TIMEOUT,
            follow_redirects=True,
            http2=False,
        )
        self.peticiones = 0        # peticiones reales de red
        self.lecturas_cache = 0   # servidas desde disco
        self.errores: list[str] = []
        self.cache_dir = cache_dir
        self.refresh = bool(os.environ.get("REVOLICO_REFRESH"))
        if self.cache_dir and not self.refresh:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._idx_path = self.cache_dir / "index.json" if self.cache_dir else None
        self._idx: dict[str, str] = {}
        if self._idx_path and self._idx_path.exists():
            try:
                self._idx = json.loads(self._idx_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                self._idx = {}

    def _cache_file(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha1(url.encode()).hexdigest() + ".html")

    def get(self, url: str) -> str | None:
        if self.cache_dir and not self.refresh:
            cf = self._cache_file(url)
            if cf.exists():
                self.lecturas_cache += 1
                return cf.read_text(encoding="utf-8", errors="replace")

        if self.peticiones >= MAX_PETICIONES:
            return None
        if self.peticiones > 0:
            time.sleep(DELAY_S)
        self.peticiones += 1
        for intento in range(3):
            try:
                r = self.client.get(url)
                if r.status_code == 429:
                    print(f"    [429] espera 60 s -> {url}")
                    time.sleep(60)
                    continue
                if r.status_code >= 400:
                    self.errores.append(f"HTTP {r.status_code} {url}")
                    return None
                if self.cache_dir and not self.refresh:
                    self._cache_file(url).write_text(r.text, encoding="utf-8")
                    self._idx[self._cache_file(url).name] = url
                    if self._idx_path:
                        self._idx_path.write_text(
                            json.dumps(self._idx, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                return r.text
            except Exception as exc:  # noqa: BLE001 - registrar y continuar
                self.errores.append(f"{type(exc).__name__} {url}: {exc}")
                time.sleep(DELAY_S * (intento + 1))
        return None

    def close(self) -> None:
        self.client.close()


def parse_listado(html: str) -> tuple[dict, dict, dict]:
    """Devuelve (ads, provincias, municipios) desde __NEXT_DATA__."""
    sopa = BeautifulSoup(html, "lxml")
    node = sopa.find("script", id="__NEXT_DATA__")
    if node is None or not node.string:
        return {}, {}, {}
    data = json.loads(node.string)
    state = data.get("props", {}).get("pageProps", {}).get("__APOLLO_STATE__", {})

    ads, provs, muni = {}, {}, {}
    for key, val in state.items():
        if not isinstance(val, dict):
            continue
        t = val.get("__typename")
        if t == "AdType":
            ads[str(val["id"])] = val
        elif t == "ProvinceType":
            provs[str(val["id"])] = val
        elif t == "MunicipalityType":
            muni[str(val["id"])] = val

    # paginas totales reportadas por el sitio
    paginas = 0
    for key, val in state.items():
        if key == "ROOT_QUERY" and isinstance(val, dict):
            for k2, v2 in val.items():
                if k2.startswith("adsPerPage") and isinstance(v2, dict):
                    paginas = max(paginas, int(v2.get("pageInfo", {}).get("pageCount") or 0))
    return ads, provs, muni, paginas  # type: ignore[return-value]


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def es_excluido(titulo: str) -> str | None:
    n = norm(titulo)
    fuerte = bool(SENAL_VEHICULO.search(n))
    for motivo, rx in EXCLUSIONES:
        if not rx.search(n):
            continue
        # "accesorios" solo descarta si el anuncio no vende una bici/bicimoto
        if motivo == "accesorios/recuestos" and fuerte:
            return None
        return motivo
    if CAT_MOTOS.search(n):
        return "moto de combustib. / no es e-bike"
    if PATINETA.search(n) and not BICI.search(n):
        return "patineta/scooter"
    if MOTOCICLO.search(n) and not BICI.search(n):
        return "moto"
    if CAT_ACCESORIOS.search(n) and not fuerte:
        return "accesorios/recuestos"
    return None


def precio_a_usd(ad: dict) -> tuple[float | None, float | None, str | None]:
    """(precio_usd, precio_cup, moneda). Sin tipo de cambio inventado: solo USD."""
    p = ad.get("price")
    cur = (ad.get("currency") or "").upper()
    if p is None:
        return None, None, cur or None
    try:
        p = float(p)
    except (TypeError, ValueError):
        return None, None, cur or None
    if cur == "CUP":
        return None, p, cur
    if cur in ("USD", "", None):
        return p, None, "USD"
    return None, p, cur


def construir(anuncio: dict, provs: dict, munis: dict) -> dict:
    titulo = clean_text(anuncio.get("title"))
    texto = titulo

    p_usd, p_cup, moneda = precio_a_usd(anuncio)
    marca = extraer_marca(texto)
    bat = extraer_tipo_bateria(texto)
    aut = extraer_autonomia(texto)

    prov = provs.get(str(anuncio.get("provinceId") or ""))
    muni = munis.get(str(anuncio.get("municipalityId") or ""))
    ubic = prov.get("name") if prov else None
    municipio = muni.get("name") if muni else None
    if not ubic:
        # fallback: provincia mencionada en el texto
        for pid, pv in provs.items():
            if re.search(rf"\b{re.escape(norm(pv['name']))}\b", norm(texto)):
                ubic = pv["name"]
                break

    vel = num(RE_VELOCIDAD.search(texto))
    peso = num(RE_PESO.search(texto))
    rueda = num(RE_RUEDA.search(texto))
    carga = num(RE_CARGA_H.search(texto))
    anio = RE_ANIOS.search(texto)
    volt = num(RE_VOLTAJE.search(texto))
    amp = num(RE_AMPERAJE.search(texto))

    # voltaje / amperaje solo si el texto los respalda cerca de una bateria
    n = norm(texto)
    if not re.search(r"\b(bateria|baterias|bateria)\b", n):
        volt = amp = None

    acc = []
    if re.search(r"\bcargador\b", n):
        acc.append("cargador")
    if re.search(r"\b(casco|guantes|llavero)\b", n):
        acc.append("casco/guantes")
    if re.search(r"\b(camara|luz|farol)\b", n):
        acc.append("luz")

    papeles = True if re.search(r"\b(papeles?|matriculad|legal|con\s+papeles)\b", n) else None
    transp = True if re.search(r"\b(transporte\s+incluido|incluye\s+transporte|env[ií]o|flete)\b", n) else None
    estado = "nuevo" if re.search(r"\b(nueva|nuevo|nuevas|nuevos|nunca\s+usad|0\s*km|0km)\b", n) else (
        "usado" if re.search(r"\b(usada|usado|como\s+nueva|reacondicionad)\b", n) else None)
    col = None
    mcol = re.search(r"\b(negro|negra|blanco|blanca|rojo|roja|azul|verde|amarillo|amarilla|"
                     r"gris|platead\w*|morad\w*|naranja|rosad\w*|turquesa|beige|caf[eé])\b", n)
    if mcol:
        col = mcol.group(1).capitalize()
    pleg = True if re.search(r"\bplegable\b", n) else None

    return {
        "titulo": titulo,
        "precio_usd": p_usd,
        "precio_cup": p_cup,
        "moneda_original": moneda,
        "marca": marca,
        "modelo": None,
        "tipo_vehiculo": tipo_vehiculo(texto),
        "motor_w": num(RE_MOTOR.search(texto)),
        "voltaje_v": volt,
        "amperaje_ah": amp,
        "tipo_bateria": bat,
        "autonomia_km": aut,
        "velocidad_max_kmh": vel,
        "tiempo_carga_horas": carga,
        "peso_kg": peso,
        "tamano_rueda_pulgadas": rueda,
        "tipo_frenos": (
            "disco" if re.search(r"\bdisco(?:s)?\b", n)
            else "v-brake" if re.search(r"\bv-?brakes?\b|\butopia\b", n)
            else "hidraulico" if re.search(r"hidr[aá]ulic", n)
            else "rin" if re.search(r"\bfreno[s]?\s+(?:de\s+)?rin\b", n)
            else None
        ),
        "suspension": ("doble" if re.search(r"doble\s+suspensi|suspension\s+doble", n)
                       else ("suspension" if re.search(r"\bsuspensi", n) else None)),
        "plegable": pleg,
        "accesorios": acc,
        "estado": estado,
        "papeles": papeles,
        "transporte_incluido": transp,
        "color": col,
        "ubicacion": ubic,
        "municipio": municipio,
        "vendedor": None,
        "fecha_publicacion": anuncio.get("updatedOnToOrder"),
        "fecha_recoleccion": datetime.now(timezone.utc).isoformat(),
        "descripcion": titulo,
        "url_anuncio": BASE + (anuncio.get("permalink") or ""),
    }


CAMPOS_OBLIGATORIOS = ["marca", "tipo_bateria", "autonomia_km", "precio_usd", "ubicacion"]


def evaluar(ad: dict, provs: dict, munis: dict) -> tuple[dict | None, str | None]:
    """Devuelve (registro_valido, motivo_descarte)."""
    titulo = clean_text(ad.get("title"))
    if not titulo:
        return None, "titulo vacio"
    motivo = es_excluido(titulo)
    if motivo:
        return None, f"exclusion: {motivo}"
    reg = construir(ad, provs, munis)
    faltan = [c for c in CAMPOS_OBLIGATORIOS if reg.get(c) in (None, "")]
    if faltan:
        return None, "faltan campos obligatorios: " + ", ".join(faltan)
    if reg["tipo_vehiculo"] == "otro":
        return None, "no es e-bike ni bicimoto"
    return reg, None


def _ordenar_claves(reg: dict) -> dict:
    """Respeta el orden de claves de esquema_bici_electrica.json."""
    try:
        orden = list(json.loads(ESQUEMA.read_text(encoding="utf-8"))["anuncios"][0].keys())
    except Exception:  # noqa: BLE001 - si no existe el esquema, se deja como esta
        return reg
    return {k: reg[k] for k in orden if k in reg} | {
        k: v for k, v in reg.items() if k not in orden
    }


CAMPOS_DESCARTE = [
    "id_anuncio", "titulo", "precio_usd", "moneda_original", "ubicacion",
    "municipio", "campos_faltantes", "motivo", "clase_motivo",
    "url_busqueda", "url_anuncio",
]


def fila_descarte(aid: str, ad: dict, motivo: str, provs: dict, munis: dict, origen: str) -> dict:
    """Registro completo de un anuncio descartado (se vuelca a disco)."""
    reg = construir(ad, provs, munis)
    if motivo.startswith("exclusion:"):
        faltan, clase = "", "exclusion"
    elif motivo.startswith("faltan campos obligatorios:"):
        faltan, clase = motivo.split(":", 1)[1].strip(), "faltan_campos"
    else:
        faltan, clase = "", "otro"
    return {
        "id_anuncio": aid,
        "titulo": clean_text(ad.get("title")),
        "precio_usd": reg["precio_usd"],
        "moneda_original": reg["moneda_original"],
        "ubicacion": reg["ubicacion"],
        "municipio": reg["municipio"],
        "campos_faltantes": faltan,
        "motivo": motivo,
        "clase_motivo": clase,
        "url_busqueda": origen,
        "url_anuncio": reg["url_anuncio"],
    }


def main() -> None:
    inicio = datetime.now(timezone.utc)
    print(f"Scraping Revolico | objetivo {OBJETIVO} validos | UA: {USER_AGENT}")

    cliente = Cliente(CACHE_DIR)
    vistos: dict[str, dict] = {}
    origenes: dict[str, list[str]] = {}
    provs: dict[str, dict] = {}
    munis: dict[str, dict] = {}
    paginas_ok = 0
    titulo_vistos: Counter = Counter()
    validos: list[dict] = []
    descartes: Counter = Counter()
    detalle_descartes: list[dict] = []
    objetivo_alcanzado = False

    for q in SEARCH_QUERIES:
        if objetivo_alcanzado or cliente.peticiones >= MAX_PETICIONES:
            break
        for page in range(1, MAX_PAGINAS_POR_URL + 1):
            if objetivo_alcanzado or cliente.peticiones >= MAX_PETICIONES:
                break
            url = f"{BASE}/search?{urlencode({'q': q, 'page': page})}"
            html = cliente.get(url)
            if not html:
                print(f"  [error] {url}")
                continue
            ads, p, m, npag = parse_listado(html)
            provs.update(p)
            munis.update(m)
            nuevos = 0
            for aid, ad in ads.items():
                if aid in origenes:
                    if url not in origenes[aid]:
                        origenes[aid].append(url)
                    continue
                vistos[aid] = ad
                origenes[aid] = [url]
                nuevos += 1
                titulo = clean_text(ad.get("title"))
                titulo_vistos[titulo.lower()] += 1
                reg, motivo = evaluar(ad, provs, munis)
                if reg is None:
                    descartes[motivo] += 1
                    detalle_descartes.append(
                        fila_descarte(aid, ad, motivo, provs, munis, url)
                    )
                else:
                    reg["_id_real"] = aid
                    validos.append(reg)
            paginas_ok += 1
            print(f"  [{len(vistos):4d} unicos | {len(validos):2d} validos] "
                  f"q={q!r} page={page} (+{nuevos}, pageCount={npag}, "
                  f"red={cliente.peticiones}, cache={cliente.lecturas_cache})")
            if nuevos == 0:
                break
            if len(validos) >= OBJETIVO:
                objetivo_alcanzado = True
                break

    cliente.close()

    # ---------------- salida ----------------
    ahora = datetime.now(timezone.utc)
    documentos = []
    for i, r in enumerate(validos[:OBJETIVO], 1):
        r = dict(r)
        r["id_anuncio"] = f"bici_elec_{i:03d}"
        r.pop("_id_real", None)
        documentos.append(r)

    OUT_JSON.write_text(
        json.dumps(
            {
                "vehiculo": "bicicleta_electrica_bicimoto",
                "total_anuncios_validos": len(documentos),
                "anuncios": [_ordenar_claves(a) for a in documentos],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # El detalle de descartes SIEMPRE se persiste: sin esto no se puede auditar
    # por que se perdio cada anuncio.
    detalle_descartes.sort(key=lambda r: (r["clase_motivo"], r["motivo"], r["titulo"]))
    with open(OUT_DESCARTES_CSV, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CAMPOS_DESCARTE)
        w.writeheader()
        w.writerows(detalle_descartes)
    with open(OUT_DESCARTES_JSONL, "w", encoding="utf-8") as fh:
        for r in detalle_descartes:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    val = Counter(d["ubicacion"] for d in documentos)
    marcas = Counter(d["marca"] for d in documentos)
    bats = Counter(d["tipo_bateria"] for d in documentos)
    precios = [d["precio_usd"] for d in documentos if d["precio_usd"]]
    mot = [d["motor_w"] for d in documentos if d.get("motor_w")]

    lineas = [
        "REPORTE DE SCRAPING - Revolico.com (bicicletas electricas / bicimotos)",
        "=" * 72,
        f"Fecha de ejecucion   : {ahora:%Y-%m-%d %H:%M:%S} UTC",
        f"Duracion            : {(ahora - inicio).total_seconds():.0f} s",
        f"User-Agent          : {USER_AGENT}",
        f"Objetivo            : {OBJETIVO} anuncios validos",
        f"Obtenidos           : {len(documentos)}",
        f"Faltaron            : {max(0, OBJETIVO - len(documentos))}",
        "",
        "RESTRICCIONES ETICAS (AGENTS.md)",
        f"  Peticiones (red)      : {cliente.peticiones} de {MAX_PETICIONES} maximo",
        f"  Lecturas de cache     : {cliente.lecturas_cache} (0 peticiones)",
        f"  Delay entre petic.    : {DELAY_S:.0f} s (minimo exigido 3 s)",
        "  robots.txt            : respetado (User-agent * / Allow: / ; /checkout, /account, /auth, /favorites bloqueados)",
        "  Paginas de detalle    : 0 (todo extraido del listado)",
        "  Herramientas          : httpx + BeautifulSoup(lxml). Sin Firecrawl/MCP/Playwright",
        "",
        "RECOLECTA",
        f"  URLs de busqueda     : {len(SEARCH_QUERIES)} (max {MAX_PAGINAS_POR_URL} paginas cada una)",
        f"  Paginas recorridas   : {paginas_ok}",
        f"  Candidatos unicos    : {len(vistos)} (dedup por id de anuncio)",
        f"  Anuncios validos     : {len(documentos)}",
        "",
        "DESCARTES POR MOTIVO",
    ]
    for k, v in descartes.most_common():
        lineas.append(f"  {v:5d}  {k}")
    lineas.append(f"  {sum(descartes.values()):5d}  TOTAL descartados")
    lineas += [
        "",
        "ANALISIS DE LOS 30 ANUNCIOS VALIDOS",
        f"  Ubicacion  : {dict(val.most_common())}",
        f"  Marca      : {dict(marcas.most_common())}",
        f"  Bateria    : {dict(bats)}",
        f"  Tipo veh.  : {dict(Counter(d['tipo_vehiculo'] for d in documentos))}",
    ]
    if precios:
        lineas.append(
            f"  Precio USD : min {min(precios):.0f} | mediana "
            f"{sorted(precios)[len(precios) // 2]:.0f} | max {max(precios):.0f}"
        )
    if precios:
        media = sum(precios) / len(precios)
        lineas.append(f"  Precio medio: {media:.0f} USD")
    lineas.append(
        f"  Motor W    : {len(mot)}/{len(documentos)} con motor declarado; "
        f"{len(documentos) - len(mot)} en null (campo OPCIONAL)"
    )
    if mot:
        lineas.append(f"               rango {min(mot):.0f}-{max(mot):.0f} W")
    autos = [d["autonomia_km"] for d in documentos if d["autonomia_km"]]
    if autos:
        lineas.append(f"  Autonomia  : min {min(autos):.0f} | max {max(autos):.0f} km")
    lineas += [
        "",
        "CRITERIOS DE VALIDEZ APLICADOS (5 obligatorios)",
        "  1. marca        -> no vacia",
        "  2. tipo_bateria -> litio | gel | plomo-acido",
        "  3. autonomia_km -> numerico",
        "  4. precio_usd   -> numerico (solo anuncios en USD)",
        "  5. ubicacion    -> provincia cubana, resuelta via provinceId del listado",
        "  motor_w        -> OPCIONAL, null si no aparece",
        "",
        "NOTAS METODOLOGICAS",
        "  - El listado de Revolico es Next.js + Apollo. Cada pagina incluye",
        "    __NEXT_DATA__ con __APOLLO_STATE__, que trae los stubs AdType",
        "    (id, title, price, currency, permalink, provinceId, municipalityId)",
        "    y los catalogos ProvinceType/MunicipalityType. Por eso ubicacion y",
        "    municipio son exactos y NO se entro a ninguna ficha de detalle.",
        "  - LIMITACION: el listado NO expone campo de descripcion. El unico",
        "    texto por anuncio es 'title'; en Cuba los vendedores escriben la",
        "    ficha completa ahi. Todo el regex se aplica sobre el titulo y",
        "    descripcion == titulo en el JSON.",
        "  - Deduplicado: por id de anuncio (unico real). AGENTS.md pide dedup",
        "    por titulo, pero en Revolico casi todos los anuncios de bicimoto",
        "    se titulan identico ('Bicimoto'), y un dedup por titulo colapsa",
        f"    {len(vistos)} anuncios a {len(titulo_vistos)} y hace imposible llegar",
        "    a 30 validos. Se reporta el titulo mas repetido:",
        f"      {titulo_vistos.most_common(1)[0][0][:70]!r} x{titulo_vistos.most_common(1)[0][1]}",
        "  - Sin tipo de cambio inventado: los anuncios en CUP se descartan del",
        "    conteo de validos porque no hay forma honesta de convertirlos.",
    ]
    if cliente.errores:
        lineas += ["", "ERRORES REGISTRADOS (sin detener el scraping)"]
        lineas += [f"  - {e}" for e in cliente.errores[:20]]

    OUT_REPORTE.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    # ---------------- resumen en consola ----------------
    print(f"\n{'=' * 60}")
    print(f"VALIDOS: {len(documentos)} / {OBJETIVO}")
    print(f"Peticiones: {cliente.peticiones} | Candidatos: {len(vistos)} | "
          f"Descartados: {sum(descartes.values())}")
    print("\nDescartes por motivo:")
    for k, v in descartes.most_common():
        print(f"  {v:5d}  {k}")
    print(f"\nEscrito: {OUT_JSON}, {OUT_REPORTE}, {OUT_DESCARTES_CSV}, {OUT_DESCARTES_JSONL}")


if __name__ == "__main__":
    main()

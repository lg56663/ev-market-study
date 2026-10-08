# EV Market Study

Estudio de mercado de movilidad eléctrica ligera: motos, bicimotos y scooters eléctricos.

## Estructura

- `verticals/{moto,bicimoto,scooter}/scraping/` — scraper, esquema y brief de cada vertical
- `verticals/{moto,bicimoto,scooter}/analisis/` — clustering y métricas
- `core/` — Código compartido
- `config/` — Configuración global
- `sources/` — Catálogo de fuentes
- `data/` — Datos generados (ignorado por Git)
- `analysis/` — Notebooks e informes
- `docs/` — Bitácora de cambios
- `archivo/` — Versiones anteriores de scraping y clustering, por vertical

## Entorno

Un único venv en la raíz, accedido como `venv/` (symlink a `/home/leandro/scraping/venv`):

```bash
venv/bin/python3 verticals/moto/analisis/analisis_clustering.py --k 3
```

Dependencias en `requirements.txt`. Los scrapers resuelven sus rutas con
`BASE_DIR = Path(__file__).resolve().parent`, así que corren desde cualquier directorio.

## Datos

Los datos crudos y procesados NO se versionan. Ver `.gitignore`.

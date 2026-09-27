# EV Market Study — Reglas globales

## Estructura

- `verticals/{moto,bicimoto,scooter}/scraping/` → un scraper por tipo de vehículo,
  junto con su `AGENTS_*.md` y su `esquema_*.json`
- `verticals/{moto,bicimoto,scooter}/analisis/` → análisis y clustering de esa vertical
- `core/` → código compartido entre verticales
- `config/` → settings, markets, brands
- `sources/` → catálogo de fuentes transversales
- `data/` → datos generados (NO versionar)
- `analysis/` → notebooks e informes
- `docs/` → bitácora y documentación transversal
- `venv/` → symlink al intérprete; usar siempre `venv/bin/python3`

## Entorno

- Un solo venv, en la raíz del repo como symlink. Ejecutar con
  `/home/leandro/ev-market-study/venv/bin/python3`
- Los scrapers resuelven sus rutas con `BASE_DIR = Path(__file__).resolve().parent`,
  así que se pueden ejecutar desde cualquier directorio

## Reglas

- Nunca commitear `data/`, cachés, HTML crudo ni `descartados_*.json`
- Nunca commitear `.env` ni secretos
- Cada vertical mantiene su `AGENTS_*.md` específico dentro de `scraping/`
- Los `esquema_*.json` y `opencode.json` de cada vertical sí se versionan
- Lo que se repita entre verticales → mover a `core/`

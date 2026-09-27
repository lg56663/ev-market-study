# EV Market Study — Reglas globales

## Estructura

- `verticals/{moto,bicimoto,scooter}/` → un scraper por tipo de vehículo
- `core/` → código compartido entre verticales
- `config/` → settings, markets, brands
- `sources/` → catálogo de fuentes transversales
- `data/` → datos generados (NO versionar)
- `analysis/` → notebooks e informes

## Reglas

- Nunca commitear `data/`, cachés, HTML crudo ni `descartados_*.json`
- Nunca commitear `.env` ni secretos
- Cada vertical mantiene su `AGENTS.md` específico
- Los `esquema_*.json` y `opencode.json` de cada vertical sí se versionan
- Lo que se repita entre verticales → mover a `core/`

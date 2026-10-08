# Bitácora

## 2026-10-08 — Archivado de versiones anteriores por vertical

<!-- Archivado por archivar_verticales.sh -->
Se movió a `archivo/<vertical>/{clustering,scraping}/` todo lo que no es el clustering
vigente de cada vertical ni su dataset (detalle en `archivo/movimientos.txt`).

| Vertical | Clustering vigente | Dataset vigente |
|---|---|---|
| moto | clustering_120 | motos_electricas_120.json |
| bicimoto | clustering_120 | bicis_electricas_120.json |
| scooter | clustering_121 | scooters_electricos_139.json |
| combustion | clustering_90 | motos_combustion_90.json |

Se conservan en `scraping/` el código (*.py), los AGENTS*.md y los esquema_*.json.
Los AGENTS*.md llevan al final la lista de archivos archivados.
Los 4 `analisis_clustering.py` vigentes usan ahora rutas relativas y `sys.executable`.

## 2026-09-27 — Consolidación del monorepo y corrección de rutas

### Contexto

Se consolidation el proyecto en un único repo (`ev-market-study`) con una vertical
por tipo de vehículo. La estructura ya existía parcialmente; el trabajo de esta
sesión fue cerrar los huecos y reparar referencias rotas.

### Directorios evaluados

| Ruta solicitada | Estado encontrado |
|---|---|
| `/home/leandro/scraping` | Solo contenía `venv/`. Sin código ni análisis. |
| `/home/leandro/ev-market-study` | Repo git con la estructura objetivo ya montada. |
| `/home/leandro/proyecto_motos_electricas` | **No existe.** |
| `/home/leandro/proyecto_scooters_electricos` | **No existe.** |

Al no existir los directorios de origen, no hubo migración de masa: se reparó lo
que estaba roto en lugar de reorganizar. Copia de seguridad previa de los
archivos no versionados en `/tmp/opencode/evms-prep/`.

### Rutas corregidas

1. `verticals/moto/analisis/analisis_clustering.py`
   - `PY`: `/home/leandro/scraping/venv/bin/python3` → `/home/leandro/ev-market-study/venv/bin/python3`
   - `ORIGEN`: `.../moto/SCRAPING/motos_electricas.json` → `.../moto/scraping/motos_electricas.json`
     (la carpeta `SCRAPING/` en mayúsculas llevaba horas vacía; el script no
     podía ni leer su entrada)
2. `verticals/bicimoto/scraping/scraper.py`
   - `ESQUEMA` apuntaba a `/home/leandro/Escritorio/proyecto_bicimotos/`, que no
     existe en ninguna parte del sistema. El `try/except` lo degradaba en
     silencio, así que `_ordenar_claves()` nunca aplicaba el orden del esquema.
     Ahora usa `BASE_DIR / "esquema_bici_electrica.json"`, y se añadió el
     `BASE_DIR` que el archivo nunca tuvo definido.

### Movimientos

- `AGENTS_*.md` y `esquema_*.json` de las tres verticales → `verticals/*/scraping/`
- Cachés sueltos de `verticals/moto/` (`cache_detalle/`, `cache_html/`) → `verticals/moto/scraping/`
- Eliminada `verticals/moto/SCRAPING/` (vacía, residuo del renombrado)
- Referencias a `/home/leandro/scraping/venv` actualizadas en los tres `AGENTS_*.md`

### Git

- `venv` es un **symlink**, y git lo trata como archivo: el patrón `venv/` no lo
  alcanzaba. Añadida la regla `/venv` para excluirlo explícitamente.
- Las excepciones `!verticals/*/esquema_*.json` ya no alcanzaban los esquemas en
  su nueva profundidad → `!verticals/*/scraping/esquema_*.json`
- Ignorados también los artefactos de `analisis/` (`*.json`, `*.txt`, `*.png`)
- Añadido `requirements.txt` (`pip freeze`, 28 paquetes)

### Verificación

```
cd verticals/moto/analisis && venv/bin/python3 analisis_clustering.py --k 3
```

30 anuncios → 3 clusters (Económica urbana n=3, Moto de trabajo n=7, Alta gama n=20).
Genera `k3_motos_con_clusters.json`, `k3_resumen_clustering.txt` y tres PNG.

Integridad del dato crudo, verificada antes y después:

| Archivo | md5 |
|---|---|
| `motos_electricas.json` | `909912cd8377209fcbfa212ea66a7e2d` |
| `scooters_electricos.json` | `187ca3a410b0b5f2b4bfe744b0a60c03` |

### Pendiente
- `verticals/bicimoto/scraping/venv/` fue un segundo venv completo dentro del repo.
  Eliminado el 2026-09-27 (liberó 182 MB). Ya no se referencia en ningún script..
  Ya no se referencia (el `AGENTS.md` apunta al venv de la raíz) pero sigue en disco.
- `verticals/bici/` está vacía y duplica el nombre de `bicimoto`.
- No existen `prompt_scraping_*.txt`; los prompts viven dentro de los `AGENTS_*.md`.
- `verticals/scooter/analisis/` y `verticals/bicimoto/analisis/` vacías.

'# Journal — El Verdadero Costo de Moverse

> Registro cronológico completo del desarrollo del proyecto.
> Reconstruido a partir del historial de git, del sistema de archivos y de los logs de scraping.
> Período: 25 de septiembre – 9 de octubre de 2026.

---

## 2026-09-25 — Primer scraping de bicimotos en Revolico

**Qué hice:** Configuré y ejecuté el primer scraper de bicimotos eléctricas sobre Revolico. Hice 4 búsquedas (bicimoto, bici electrica, bicicleta electrica, e-bike), descargué 20 páginas y obtuve 1.636 URLs únicas. Después filtré por título y quedaron 1.564 precandidatos. Todo esto ocurrió antes de crear el repositorio de git, así que el trabajo quedó solo en el disco local.

**Evidencia:** `verticals/bicimoto/.cache_pool.json` (148 KB, timestamps del 25-sep), `verticals/bicimoto/.scraper_run.log`, `verticals/bicimoto/opencode.json`.

**Qué aprendí:** Que Revolico devuelve muchos duplicados entre búsquedas y hay que deduplicar por ID de anuncio. El filtrado por título descarta ~5% de los resultados (72 de 1.636), sobre todo anuncios que mencionan "bicimoto" pero son de otros vehículos o repuestos.

**Qué me costó:** Definir los términos de búsqueda correctos para no dejar fuera anuncios relevantes ni traer ruido. Terminé usando 4 términos distintos.

**Próximo paso:** Crear el repositorio del proyecto para versionar todo.

---

## 2026-09-26 — Fundación del monorepo EV Market Study

**Qué hice:** Primer commit con la estructura del monorepo: verticales moto, bicimoto y scooter con sus scrapers, código compartido en `core/`, configuración en `config/` y `sources/`, y `.gitignore` para no versionar data. Versioné esquemas JSON y configs de OpenCode. Añadí `.gitkeep` en las carpetas vacías para preservarlas.

**Evidencia:** `README.md`, `AGENTS.md`, `.gitignore`, `.env.example`, `verticals/*/opencode.json`, `verticals/*/scraping/scraper*.py`.

**Qué aprendí:** Organizar el proyecto por verticales desde el día uno, con `AGENTS.md` y esquemas propios por vertical, sienta las reglas de convivencia del monorepo.

**Qué me costó:** Decidir qué se comparte en `core/` y qué queda por vertical sin duplicar reglas ni esquemas.

**Próximo paso:** Poblar los scrapers con datos reales de los tres verticales.

---

## 2026-09-27 — Datos crudos y primer clustering de los tres verticales

**Qué hice:** Versioné los JSONs de scraping (30 anuncios por vertical) junto con los descartados y su motivo de exclusión, la bitácora inicial y `requirements.txt`. Añadí clustering de motos (k=4, silhouette 0.52), bicimotos (k=4, silhouette 0.50) y scooters (k=3, silhouette 0.60), cada uno con segmentos nombrados y boxplots.

**Evidencia:** `verticals/moto/analisis/`, `verticals/bicimoto/analisis/`, `verticals/scooter/analisis/`, `requirements.txt`.

**Qué aprendí:** En clustering conviene priorizar la interpretabilidad sobre la silhouette máxima (k=4 en motos frente a k=7; k=3 en scooters frente a k=4). También que `motor_w` no sirve como feature porque fue opcional en el scraping.

**Qué me costó:** Justificar el número de clusters cuando los precios se solapan, y tratar outliers como el anuncio de moto a 4.200 USD.

**Próximo paso:** Validar los segmentos cuando el dataset crezca más allá de los 30 registros.

---

## 2026-09-28 — Gráficos de clustering renovados

**Qué hice:** Reemplacé los gráficos de clustering de los 3 verticales por scatter con centroides, precio medio por cluster, tamaños y métricas de silhouette.

**Evidencia:** `verticals/*/analisis/clusters_dispersion.png`, `clusters_precio_medio.png`, `clusters_tamanos.png`, `clusters_metricas.png`.

**Qué aprendí:** Incluir la silhouette como gráfico aporta evidencia visual de la separación entre clusters.

**Qué me costó:** Mantener la misma familia de gráficos y métricas de forma consistente entre los tres verticales.

**Próximo paso:** Repetir el proceso con datasets más grandes.

---

## 2026-09-29 — Nueva vertical: motos de combustión

**Qué hice:** Añadí el scraping de motos de combustión (30 válidas nuevas, sin duplicados, marcas unificadas) con su esquema ajustado (enum `marca_fuente` y campo `fecha_fuente`). Completé el vertical con análisis de descartes y clustering k=4. Hallazgo importante: la cilindrada (cc) no correlaciona con el precio.

**Evidencia:** `verticals/combustion/scraping/motos_combustion.json`, `AGENTS_COMBUSTION.md`, `esquema_moto_combustion.json`, `hallazgo_no_correlacion.txt`.

**Qué aprendí:** En el mercado de combustión, la cilindrada no correlaciona con el precio, un contraste interesante con el mercado eléctrico donde el precio sí correlaciona con potencia y autonomía.

**Qué me costó:** Unificar marcas de una fuente sin estructura clara y cerrar el esquema con los campos nuevos.

**Próximo paso:** Comparar el mercado de combustión con los verticales eléctricos.

---

## 2026-09-30 — Día sin actividad registrada

*(No hay commits ni archivos modificados este día.)*

---

## 2026-10-01 — Ampliación del scraping y limpieza de datasets

**Qué hice:** Amplié el scraping de los 4 verticales a motos 90, bicimotos 90, scooters 121 y combustión 90 (9 campos, La Habana 2026). Limpié motos y bicimotos hasta 120 anuncios × 10 campos exactos: marcas a Title Case (22 distintas), `motor_w` unificado a number y fusión de variantes (HALCÓN/HALCON, YOAZAKY/YOAZAKI, Maf Engle → Maf). Reorganicé el scraping en carpetas (`fuentes/`, `descartados/`, `scrapers/`, `historico/`).

**Evidencia:** `verticals/moto/scraping/motos_electricas_120.json`, `verticals/bicimoto/scraping/bicis_electricas_120.json`, `limpiar_120.py`, `limpiar_motos_120.py`, `reporte_correcciones*.json`.

**Qué aprendí:** Escribir limpiezas idempotentes y reproducibles con reportes de correcciones documentadas facilita repetir el proceso sin sorpresas.

**Qué me costó:** La normalización de datos de fuentes ruidosas (telegram, reemplazos).

**Próximo paso:** Correr clustering sobre los datasets ampliados.

---

## 2026-10-02 — Clustering ampliado a los datasets de ~120

**Qué hice:** Reorganicé el análisis moviendo el clustering de 30 a `clustering_30/`. Añadí el clustering ampliado de los 4 verticales: motos 120, bicimotos 120, scooters 121 y combustión 90. Los k finales: bicimoto k=5, moto k=8, scooter k=6 (silhouette 0.5215), combustión k=8.

**Evidencia:** `verticals/*/analisis/clustering_120/` o `clustering_121/` o `clustering_90/`, con JSONs, PNGs y `resumen_clustering_*.txt`.

**Qué aprendí:** Versionar cada ronda de clustering en su propia subcarpeta permite comparar cómo cambian los segmentos al crecer los datos.

**Qué me costó:** Reubicar todos los scripts, JSONs y PNGs de los 4 verticales sin romper rutas de salida.

**Próximo paso:** Pasar del análisis por vertical a un análisis agregado del mercado.

---

## 2026-10-03 — Día sin actividad registrada

*(No hay commits ni archivos modificados este día.)*

---

## 2026-10-04 — Primer análisis de clustering y comparación formal vs informal

**Qué hice:** Analicé los clusters de los 3 verticales eléctricos usando los datasets de 120 anuncios. Comencé el primer intento de comparación formal vs informal con un inventario pequeño de 5 modelos (2 bicimotos + 3 motos). Generé los primeros gráficos de dispersión y el CSV `comparacion_formal_vs_informal.csv`.

**Evidencia:** *(archivos movidos o sobreescritos en la reorg del 6-oct, no quedan con mtime original)*, `output/comparacion_formal_vs_informal.csv`, `output/dispersion_formal_informal.png`.

**Qué aprendí:** Que la comparación con pocos homólogos (Topmaq 2000 con 1 homólogo, Izuki Viola con 4) es estadísticamente débil. Hay que ampliar el inventario formal.

**Qué me costó:** Decidir el criterio de emparejamiento entre vehículos formales e informales. Terminé usando solo potencia (±300 W) y autonomía (±15 km), sin usar la marca.

**Próximo paso:** Ampliar el inventario del mercado formal con más modelos de importadoras privadas.

---

## 2026-10-05 — Ampliación del inventario formal y top de marcas

**Qué hice:** Amplié el inventario del mercado formal no estatal a 24 ofertas (8 bicimotos + 16 motos), con datos extraídos de importadoras como Envios CubAmerica y MZ Products. Ejecuté el comparador de mercados con el inventario ampliado. Comencé el análisis de marcas con los datos válidos más descartados por falta de campos. Limpié carpetas obsoletas (evidencia en el Trash: `bicimoto`, `motos`).

**Evidencia:** `data/mercado_formal_bicimotos.csv`, `data/mercado_formal_motos.csv`, `scripts/comparar_mercados.py`, carpetas `bicimoto` y `motos` movidas al Trash.

**Qué aprendí:** Que el mercado informal no es sistemáticamente más caro que el formal. En bicimotos el informal es ~20% más barato, pero en motos el formal es ~4% más barato. La narrativa depende del vertical.

**Qué me costó:** Los primeros intentos de scraping de importadoras con Playwright fallaron (bloqueo geográfico de `cdn.playwright.dev`). Se resolvió con el espejo de `npmmirror`. Después el scraping no dio resultados limpios, así que cambiamos a extracción manual con capturas.

**Próximo paso:** Analizar los municipios con más oferta y generar el mapa con Datawrapper.

---

## 2026-10-06 — Reorganización del proyecto y análisis de mercados

**Qué hice:** Reorganicé el proyecto y agregué el análisis del mercado eléctrico: script de análisis por marcas, gráficos (frecuencia, precios, plotly interactivo), y comparativas formal vs. informal. Moví datasets históricos a `archivo/` y añadí reglas al `.gitignore`. Este commit consolidó la estructura final del repo.

**Evidencia:** `.gitignore`, `archivo/movimientos.txt`, `scripts/analisis_marcas.py`, `verticals/analisis_mercado_electrico/`, `output/*.csv`.

**Qué aprendí:** Cruzar los datos de scraping con el mercado formal vs. informal (`comparar_mercados.py`) da contexto sobre la oferta real de La Habana.

**Qué me costó:** Consolidar outputs CSV, JSON y gráficos históricos en una estructura versionable sin duplicar artefactos.

**Próximo paso:** Depurar los artefactos de marcas que quedaron en el vertical y archivar versiones antiguas.

---

## 2026-10-07 — Análisis de municipios, mapa y top 10 final

**Qué hice:** Fusioné los 3 verticales eléctricos (bicimoto, moto, scooter) para el conteo de anuncios por municipio. Generé `conteo_municipios.csv` con 1.454 anuncios distribuidos en los 15 municipios de La Habana. Creé el mapa en Datawrapper con el GeoJSON de La Habana obtenido del repositorio `yudivian/cuba-geojsons`. Generé el top 10 de marcas con 1.648 anuncios con marca válida y el desglose por vertical.

**Evidencia:** `output/conteo_municipios.csv`, `output/top10_marcas_desglose.csv`, `output/top10_marcas_por_vertical.csv`, `graficos/geojson la habana.json`, `graficos/mapa_oferta_informal_habana.png`, `graficos/grafico_top10_marcas.png`.

**Qué aprendí:** Que Playa (309 anuncios) y Centro Habana (301) concentran el 40% de la oferta. Las 10 marcas líderes concentran 822 de 1.648 anuncios (el 50% del mercado), con Topmaq (195), Vedca (125) y Yoazaky (100) a la cabeza.

**Qué me costó:** Normalizar los nombres de los municipios entre los 3 JSON (había alias como "Plaza" vs "Plaza de la Revolución"). También decidir si incluir o no los descartados por falta de campos, y normalizar nombres de marcas (Yoazaky/Yoazaki, Vedca/Vedga, Mishozuki/Mishosuki).

**Próximo paso:** Crear el issue de seguimiento en el repo de la clase y subir el trabajo final.

---

## 2026-10-08 — Archivo de versiones y limpieza del análisis de marcas

**Qué hice:** Archivé las versiones anteriores de cada vertical (datos, scrapers y clustering) en `archivo/<vertical>/{clustering,scraping}/`. Adapté los `analisis_clustering.py` a rutas relativas y `sys.executable`. Eliminé `resumen_marcas.txt`, `marcas_top15.csv` y `marcas_top15_plano.json` del vertical de análisis de marcas. Subí todo a GitHub con el commit `9ca35f9`.

**Evidencia:** `archivo/movimientos.txt`, `verticals/*/analisis/clustering_*/analisis_clustering.py`, `README.md`, borrados de `marcas_top15*`.

**Qué aprendí:** Usar rutas relativas en los scripts de clustering permite ejecutarlos desde cualquier directorio sin acoplarse a la ubicación del repo. Que Git trata los symlinks como archivos, así que `venv/` no se excluía con el patrón habitual; hubo que añadir la regla `/venv`.

**Qué me costó:** Decidir qué era "versión vigente" y qué era "archivo histórico", porque había solapamiento entre carpetas (por ejemplo, `clustering_30` con clustering ya superado).

**Próximo paso:** Completar el informe final con la comparación de precios 3000 W vs 125 cc y los datos de proveedores internacionales.

---

## 2026-10-09 — Issue de seguimiento, journal y consolidación

**Qué hice:** Creé el issue en `matcom/ICD-26-27` con el formato pedido por el profesor (Name, Domain, Repositorio). Revisé el estado del repo, verifiqué los commits subidos a GitHub y reconstruí el journal completo del proyecto con OpenCode. Consolidé la información de los top 3 proveedores internacionales (China, España, Vietnam) y el rol de Panamá como hub logístico.

**Evidencia:** Issue en `matcom/ICD-26-27`, `docs/journal_completo.md`, análisis de proveedores internacionales, comparación de precios 3000 W vs 125 cc.

**Qué aprendí:** Que el journal es más útil cuando se completa al final del día, y que la información de proveedores internacionales (OEC, WITS) complementa muy bien el análisis del mercado informal cubano. También que OpenCode puede fallar por errores de certificado SSL al intentar escribir archivos; conviene tener un plan B manual.

**Qué me costó:** Decidir el "Domain" del issue (terminé poniendo "Transporte") y reconstruir el journal completo de los días en que trabajé sin commitear (25-sep, 4-oct, 5-oct, 7-oct, 9-oct).

**Próximo paso:** Completar el informe final y cerrar el proyecto con la entrega al profesor.

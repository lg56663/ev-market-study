# Handoff — El Verdadero Costo de Moverse

> Documento de traspaso. Actualizado al 9 de octubre de 2026.
> Repo: https://github.com/lg56663/ev-market-study

---

## 1. Qué es el proyecto

**Pregunta general:** ¿Cuál es el verdadero costo operativo, económico y regulatorio de adquirir y usar un ciclomotor eléctrico en La Habana en 2026?

Análisis del costo real de la movilidad eléctrica de dos ruedas en Cuba (2026). Cubre 3 verticales eléctricos (moto, bicimoto, scooter) y 1 de control de combustión. Incluye scraping de Revolico y Telegram, clustering, comparación formal vs informal, análisis de marcas, mapa de municipios y comparación con proveedores internacionales.

**Estado de entrega:** código, datos, clustering y outputs completos. Falta el informe final y cerrar las preguntas abiertas (sección 5).

---

## 2. Estado actual

| Componente | Estado | Evidencia |
|---|---|---|
| Scraping de los 4 verticales | Completado | `verticals/*/scraping/` |
| Limpieza de datasets (120 anuncios × 10 campos) | Completado | `limpiar_120.py`, `motos_electricas_120.json` |
| Clustering de los 4 verticales | Completado | `verticals/*/analisis/clustering_*` |
| Análisis de descartes | Completado (combustión) | `archivo/combustion/scraping/analisis_descartes.txt` |
| Comparación formal vs informal | Completado | `output/comparacion_formal_vs_informal.csv` |
| Top 10 marcas (global + por vertical) | Completado | `output/top10_marcas_*.csv` |
| Mapa de municipios | Completado | `graficos/mapa_oferta_informal_habana.png` |
| Journal del proyecto | Completado (199 líneas) | `docs/journal.md` |
| Informe final | Pendiente | — |
| Respuestas a las preguntas del proyecto | Parcial [revisar] | ver sección 5 |

---

## 3. Estructura del repo

```
ev-market-study/
├── data/                 # INPUT de scripts (no versionado)
│   ├── bicis_120_con_clusters.json
│   ├── motos_120_con_clusters.json
│   └── mercado_formal_bicimotos.csv, mercado_formal_motos.csv
├── output/               # OUTPUT de análisis
│   ├── comparacion_formal_vs_informal.csv
│   ├── conteo_municipios.csv, anuncios_por_municipio.csv
│   ├── top10_marcas_desglose.csv, top10_marcas_por_vertical.csv
│   └── dispersion_formal_informal.png
├── graficos/             # OUTPUT visual
│   ├── mapa_oferta_informal_habana.png
│   ├── grafico_top10_marcas.png
│   └── geojson la habana.json
├── scripts/              # Análisis transversales
│   ├── comparar_mercados.py
│   └── analisis_marcas.py
├── verticals/            # INPUT + código por vertical
│   ├── bicimoto/{scraping,analisis}/
│   ├── moto/{scraping,analisis}/
│   ├── scooter/{scraping,analisis}/
│   └── combustion/{scraping,analisis}/
├── archivo/              # HISTÓRICO: versiones anteriores por vertical
├── docs/
│   └── journal.md
├── core/, config/, sources/, analysis/, tests/   # vacías (.gitkeep)
└── README.md, AGENTS.md, .gitignore
```

---

## 4. Datos clave del análisis

### Clustering (k-means, StandardScaler)

| Vertical | n clusterizado | k final | Silhouette | Features |
|---|---|---|---|---|
| Bicimoto | 120 | 5 | 0.5874 | precio_usd, motor_w, autonomia_km |
| Moto | 118 (2 outliers fuera) | 8 | 0.5063 | precio_usd, motor_w, autonomia_km |
| Scooter | 108 (13 sin motor_w fuera) | 6 | 0.5215 | precio_usd, motor_w, autonomia_km |
| Combustión | 90 | 8 (9 tras 1 fusión) | 0.6003 | precio_usd, cilindrada_cc |

Nombres de los clusters: ver cada `resumen_clustering_*.txt` en `verticals/*/analisis/clustering_*/`.

### Comparación formal vs informal (20 modelos con homólogos)

| Ámbito | Diferencia media | Más barato |
|---|---|---|
| Global | informal ~5.5% | informal |
| Bicimoto | informal ~20.0% | informal (7 de 8) |
| Moto | formal ~4.1% | formal (9 de 12) |

4 modelos formales quedaron sin homólogos (premium: Fly Racing T08 4000W, Izuki Challenger, LINCE 70AH, Panther Tank 2500W).

### Top 10 marcas (1.648 anuncios con marca [revisar]; top 10 = 824)

| Rank | Marca | Bicimoto | Moto | Scooter | Total |
|---|---|---|---|---|---|
| 1 | Topmaq | 18 | 177 | 0 | 195 |
| 2 | Vedca | 16 | 107 | 2 | 125 |
| 3 | Yoazaky | 16 | 83 | 1 | 100 |
| 4 | Bucatti | 4 | 95 | 0 | 99 |
| 5 | Izuki | 0 | 74 | 0 | 74 |
| 6 | Jmd | 33 | 11 | 16 | 60 |
| 7 | Halcon | 10 | 34 | 8 | 52 |
| 8 | Fly | 1 | 46 | 2 | 49 |
| 9 | Challenger | 7 | 19 | 9 | 35 |
| 10 | Maf | 7 | 14 | 14 | 35 |

### Municipios con más oferta (conteo total = 1.454)

Playa 309 · Centro Habana 301 · Plaza de la Revolución 166 · Diez de Octubre 159 · Arroyo Naranjo 111 · Cerro 79 · Boyeros 76.

### Comparación 3000W vs 125cc

| | Eléctrica 3000W (moto C6) | Gasolina 125cc (combustión C2) |
|---|---|---|
| Precio promedio | 2.725 USD | 2.038 USD |
| Diferencia | +687 USD (+33.7%) | — |

Comparación funcional, no técnica (potencia nominal muy distinta). [revisar: la equivalencia W↔cc no está cuantificada en un archivo fuente]

### Proveedores internacionales [revisar: solo documentado en journal, sin dataset primario]

- **China:** principal origen del mercado de motos eléctricas.
- **Vietnam:** proveedor relevante.
- **España:** proveedor marginal dentro de la UE.
- **Panamá:** hub logístico (Zona Libre de Colón), no origen.

---

## 5. Preguntas del proyecto (estado) [revisar]

El repo **no contiene un archivo con el listado de las 30 preguntas**; el desglose siguiente proviene del seguimiento del proyecto, no de un artefacto verificable.

| Estado | Cantidad | Números |
|---|---|---|
| Listas para redactar | 11 | 1, 2, 4, 5, 7, 8, 9, 15, 20, 22, 23 |
| Parciales (falta 1 dato) | 8 | 3, 10, 11, 13, 14, 17, 21, 26 |
| Investigación externa | 1 | 30 (accidentes: ONEI/MITRANS) |
| Difícil sin series temporales | 1 | 12 (gasolina vs precios) |
| Requieren entrevista | 9 | 16, 19, 24, 25, 27, 28, 29 + 2 agregadas |

**Total respondibles sin entrevista:** 21 de 30 (70%).

---

## 6. Decisiones metodológicas

- **Emparejamiento formal vs informal:** solo por `motor_w` (±300 W) y `autonomia_km` (±15 km). No se usa marca ni vertical. `TOL_MOTOR_W=300`, `TOL_AUTONOMIA_KM=15` en `scripts/comparar_mercados.py`.
- **Filtro de outliers:** precios a más del 30% de la mediana del grupo (`OUTLIER_PCT=0.30`); en clustering, cortes por vertical (moto 500/4000; scooter 200/2000; bicimoto 500/4000; combustión 500/5000).
- **Ventana de descartes:** se incluyen los descartes por campos faltantes; se excluyen combustión, duplicados, fuera de La Habana y tipo de vehículo incorrecto.
- **Normalización de marcas:** Yoazaky/Yoazaki→Yoazaky; Vedca/Vedga→Vedca; Mishozuki/Mishosuki→Mishozuki; Halcon/Halcón→Halcon (ver `scripts/analisis_marcas.py`).
- **Deduplicación:** por URL de anuncio; en combustión además por título normalizado, modelo y lista negra manual.
- **Municipios:** solo los 15 oficiales de La Habana. "Vedado" se descartó por ser barrio, no municipio.
- **Equivalencia W↔cc:** funcional (3000 W ≈ 125-150 cc), no nominal.
- **`motor_w` opcional en bicimoto/scooter:** si falta, el anuncio es válido pero queda fuera del clustering por feature nula.

---

## 7. Cómo continuar

```bash
# Regenerar comparación formal vs informal
venv/bin/python3 scripts/comparar_mercados.py

# Regenerar clustering de un vertical
cd verticals/moto/analisis/clustering_120
../../../venv/bin/python3 analisis_clustering.py

# Mantener el journal (no hay script; se edita a mano)
nano docs/journal.md

# Subir cambios
git add . && git commit -m "descripcion del cambio" && git push
```

---

## 8. Gotchas / cosas a tener en cuenta

1. **OpenCode falla con SSL** al escribir archivos ("unknown certificate verification error"); plan B manual. [revisar: documentado en journal, no reproducible en el repo]
2. **Playwright bloqueado en Cuba:** `cdn.playwright.dev` da 403; se usó el espejo `npmmirror.com`.
3. **`venv` es un symlink** a `/home/leandro/scraping/venv`; git lo trataría como archivo, por eso `.gitignore` usa `/venv` y `**/venv`.
4. **Los JSON originales no se tocan:** el análisis lee de `data/` o `verticals/*/analisis/clustering_*/`.
5. **El vertical de combustión es solo para comparación**, no es parte del núcleo eléctrico.
6. **Los descartados no son basura:** son fuente para el análisis de marcas; no borrarlos.
7. **`archivo/` es histórico:** no usar para el informe.
8. **Revolico y Telegram no son la misma población**; mezclarlos puede separar por fuente y no por producto.
9. **`autonomia_km` puede venir como rango** ("50-60 km") sin normalizar: introduce ruido en las features.

---

## 9. Próximos pasos concretos

| Prioridad | Tarea | Tiempo estimado |
|---|---|---|
| Alta | Redactar el informe final con los datos existentes | 4-6 h |
| Alta | Responder preguntas 3 y 17 (procesar JSON) | 1 h |
| Media | Investigar 10, 11, 13, 14 (comparten fuentes externas) | 4 h |
| Media | Cuantificar equivalencia W↔cc y proveedores internacionales | 2 h |
| Baja | Estadísticas de accidentes (30) | 3 h |
| Baja | Monto actualizado del trámite de chapa (21) | 1 h |
| Entrevistas | Preparar cuestionario y ejecutar | — |

---

## 10. Contacto y fuentes

- **Repo:** https://github.com/lg56663/ev-market-study
- **Journal:** `docs/journal.md` (registro cronológico completo, 25-sep → 9-oct)
- **Issue de seguimiento:** `matcom/ICD-26-27` [revisar: enlace exacto no disponible en el repo]
- **Fuentes externas:**
  - GeoJSON La Habana: https://github.com/yudivian/cuba-geojsons
  - OEC: https://oec.world [revisar: URL exacta no disponible]
  - ONEI: http://www.onei.gob.cu/ [revisar: URL exacta no disponible]

Fin del handoff. Al retomar, leer `docs/journal.md` y este documento: dan el contexto completo.

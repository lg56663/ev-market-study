# Handoff — El Verdadero Costo de Moverse

> Documento de traspaso del proyecto. Actualizado al 9 de octubre de 2026.
> Repo: https://github.com/lg56663/ev-market-study

---

## 1. Qué es el proyecto

**Pregunta general:** ¿Cuál es el verdadero costo operativo, económico y regulatorio de adquirir y usar un ciclomotor eléctrico en La Habana en 2026?

Análisis del costo real de la movilidad eléctrica de dos ruedas en Cuba (2026). Cubre 3 verticales eléctricos (moto, bicimoto, scooter) y 1 de combustión (moto gasolina). Incluye scraping de Revolico y Telegram, clustering, comparación formal vs informal, análisis de marcas, mapa de municipios y comparación con el exterior.

**Entrega:** Informe final + repo con todo el código y datos.

---

## 2. Estado actual

| Componente | Estado |
|---|---|
| Scraping de los 4 verticales | Completado |
| Limpieza de datasets (120 anuncios x 10 campos) | Completado |
| Clustering de los 4 verticales | Completado |
| Análisis de descartes | Completado |
| Comparación formal vs informal | Completado |
| Top 10 marcas (global + por vertical) | Completado |
| Mapa de municipios en Datawrapper | Completado |
| Journal del proyecto | Completado (199 líneas) |
| Issue en matcom/ICD-26-27 | Creado |
| Informe final | Pendiente |
| Respuestas a las 30 preguntas | Parcial (ver sección 5) |

---

## 3. Estructura del repo

    ev-market-study/
    ├── data/
    │   ├── bicis_120_con_clusters.json
    │   ├── motos_120_con_clusters.json
    │   ├── mercado_formal_bicimotos.csv
    │   └── mercado_formal_motos.csv
    ├── output/
    │   ├── comparacion_formal_vs_informal.csv
    │   ├── conteo_municipios.csv
    │   ├── anuncios_por_municipio.csv
    │   ├── top10_marcas_desglose.csv
    │   └── top10_marcas_por_vertical.csv
    ├── graficos/
    │   ├── mapa_oferta_informal_habana.png
    │   ├── grafico_top10_marcas.png
    │   └── geojson la habana.json
    ├── scripts/
    │   ├── comparar_mercados.py
    │   └── actualizar_journal.sh
    ├── verticals/
    │   ├── bicimoto/
    │   ├── moto/
    │   ├── scooter/
    │   └── combustion/
    ├── archivo/
    ├── docs/
    │   └── journal.md
    └── README.md

---

## 4. Datos clave del análisis

### Clustering

| Vertical | k | Silhouette | Clusters |
|---|---|---|---|
| Bicimoto | 5 | 0.587 | Economica basica, Economica urbana, Estandar largo alcance, Estandar urbana, Premium |
| Moto | 8 | 0.506 | Economica urbana, Economica trabajo, Economica largo alcance, Media-baja urbana, Media trabajo, Deportiva urbana, Estandar mercado, Premium |
| Scooter | 4 | 0.60 | Ver resumen_clustering_121.txt |
| Combustion | 8 | 0.600 | Economica 138cc, Economica 150cc, Media 125cc, Media 200cc, Media 150cc, Alta 200cc, Alta 250cc, Alta 162cc |

### Comparación formal vs informal

- Global: informal ~5.52% más barato
- Bicimotos: informal ~20% más barato
- Motos: formal ~4% más barato
- 4 modelos formales no encontraron homólogos (segmento premium)

### Top 10 marcas global (1.648 anuncios con marca)

| Rank | Marca | Total |
|---|---|---|
| 1 | Topmaq | 195 |
| 2 | Vedca | 125 |
| 3 | Yoazaky | 100 |
| 4 | Bucatti | 99 |
| 5 | Izuki | 74 |
| 6 | Jmd | 60 |
| 7 | Halcon | 52 |
| 8 | Fly | 49 |
| 9 | Challenger | 35 |
| 10 | Maf | 35 |

### Municipios (1.534 anuncios)

- Playa: 309
- Centro Habana: 301
- Plaza de la Revolución: 166
- Diez de Octubre: 159
- Arroyo Naranjo: 111

### Comparación 3000W vs 125cc

| | Eléctrica 3000W | Gasolina 125cc |
|---|---|---|
| Precio promedio | 2.724 USD | 2.038 USD |
| Diferencia | +687 USD (+33.7%) | — |

Nota: La comparación es funcional, no técnica. Potencia nominal: 4.08 CV vs 10-12 CV. La eléctrica entrega torque instantáneo.

### Proveedores internacionales

- China: más del 90% del mercado de motos eléctricas. VEDCA es la empresa mixta (Tianjin Dongxing + Minerva).
- Vietnam: PEGA, contratos estatales por ~3 millones USD.
- España: proveedor marginal dentro de la UE (menos del 10% del total).
- Panamá: hub logístico (Zona Libre de Colón), no origen.

---

## 5. Preguntas del proyecto (estado)

### Listas para redactar (11)

1, 2, 4, 5, 7, 8, 9, 15, 20, 22, 23

### Parciales (8) — falta 1 dato

- 3: Definir viabilidad operativa con criterios medibles
- 10: Buscar precio FOB China + flete
- 11: Buscar tarifa eléctrica + precio gasolina
- 13: Tabla de equivalencia W a cc
- 14: Cálculo de punto de equilibrio
- 17: Porcentaje litio vs gel en datasets
- 21: Monto actualizado del trámite de chapa
- 26: Frecuencia de carga (requiere encuesta o foros)

### Investigación externa (1)

- 30: Estadísticas de accidentes (ONEI, MITRANS)

### Difícil sin series temporales (1)

- 12: Correlación gasolina vs precios motos eléctricas

### Requieren entrevista (9)

16, 19, 24, 25, 27, 28, 29 + las 2 agregadas (por qué tanta gente sin chapa/licencia, y experiencia con paradas policiales)

Total respondibles sin entrevista: 21 de 30 (70%).

---

## 6. Decisiones metodológicas tomadas

1. Emparejamiento formal vs informal: solo por motor_w (+-300W) y autonomia_km (+-15km). NO se usa la marca ni el vertical como filtro. Se combinan motos y bicimotos en la misma bolsa de homólogos.
2. Filtro de outliers: descartar precios que se alejen más del 30% de la mediana del grupo.
3. Ventana de descartes: solo se incluyen descartes por falta de campos (sin marca, sin motor, sin autonomía, sin precio). Se excluyen los descartes por combustión, duplicados, fuera de La Habana, tipo de vehículo incorrecto.
4. Normalización de marcas: Yoazaky/Yoazaki a Yoazaky; Vedca/Vedga a Vedca; Mishozuki/Mishosuki a Mishozuki; Halcon/Halcon a Halcon.
5. Deduplicación: por URL de anuncio.
6. Municipios: solo los 15 oficiales de La Habana. Vedado se descartó (es barrio, no municipio).
7. Equivalencia W a cc: funcional (3000W aproximadamente 125-150cc), no nominal. Basada en homologación europea L3e-A1.

---

## 7. Cómo continuar

### Para mantener el journal al día

    cd /home/leandro/ev-market-study
    ./scripts/actualizar_journal.sh
    nano docs/journal.md

### Para regenerar el análisis de mercados

    cd /home/leandro/ev-market-study
    venv/bin/python3 scripts/comparar_mercados.py

### Para regenerar el clustering de un vertical

    cd verticals/moto/analisis/clustering_120
    ../../../venv/bin/python3 analisis_clustering.py

### Para subir cambios a GitHub

    git add .
    git commit -m "descripcion del cambio"
    git push

---

## 8. Gotchas / cosas a tener en cuenta

1. OpenCode falla con SSL: el error "unknown certificate verification error" bloquea la generación de archivos. Plan B: escribir manualmente. Solución: exportar SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt.
2. Playwright bloqueado en Cuba: cdn.playwright.dev da 403. Solución: espejo npmmirror.com.
3. El venv es un symlink a /home/leandro/scraping/venv. No se versiona (.gitignore con /venv).
4. Los JSON originales no se tocan: todo análisis lee de data/ o verticals/*/analisis/clustering_*/, nunca modifica los datos crudos.
5. El vertical de combustión sirve solo para comparación. No es parte del core del proyecto.
6. Los descartados no son basura: son fuente de análisis de marcas. No borrarlos.
7. La carpeta archivo/ contiene versiones históricas. No usar para el informe.

---

## 9. Próximos pasos concretos

| Prioridad | Tarea | Tiempo estimado |
|---|---|---|
| Alta | Redactar informe final con datos que ya existen | 4-6 h |
| Alta | Responder preguntas 3, 17 (solo procesar JSON) | 1 h |
| Media | Investigar 10, 11, 13, 14 (comparten fuentes) | 4 h |
| Baja | Buscar estadísticas de accidentes (30) | 3 h |
| Baja | Monto de trámite de chapa (21) | 1 h |
| Entrevistas | Preparar cuestionario y grabar | — |

---

## 10. Contacto y fuentes

- Repo: https://github.com/lg56663/ev-market-study
- Issue de seguimiento: https://github.com/matcom/ICD-26-27/issues
- Journal completo: docs/journal.md
- Fuentes de datos externas:
  - OEC: https://oec.world/en/profile/bilateral-product/motorcycles-and-cycles/reporter/cub
  - ONEI: http://www.onei.gob.cu/sector-externo-1
  - GeoJSON La Habana: https://github.com/yudivian/cuba-geojsons

---

Fin del handoff. Si retomas el proyecto en el futuro, empieza por leer el journal (docs/journal.md) y este documento. Ambos te dan el contexto completo en menos de 15 minutos.

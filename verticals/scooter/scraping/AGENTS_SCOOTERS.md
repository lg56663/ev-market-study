# Proyecto: Scraping de Revolico - Scooters y patinetes eléctricos

## Descripción
Scraping de anuncios de scooters y patinetes eléctricos desde Revolico.com 
para análisis de mercado en Cuba. Se extraen 30 anuncios VÁLIDOS. 
Se excluyen motos, bicimotos, triciclos y vehículos de combustión.

Para lograr los 30 válidos, el scraper ENTRA a las páginas de detalle de 
los anuncios que pasan los filtros iniciales, para extraer la descripción 
completa. El listado solo trae título, precio, provincia y municipio.

## Entorno Python
- Usar el intérprete del venv: /home/leandro/ev-market-study/venv/bin/python3
- Librerías disponibles: httpx, beautifulsoup4, lxml, pandas
- NO usar Firecrawl ni MCP. Usar httpx directamente.

## Convenciones
- Lenguaje: Python 3.14 (dentro del venv)
- Librería de scraping: httpx + BeautifulSoup
- Salida válidos: scooters_electricos.json
- Salida descartados: descartados_scooters.json
- Reporte: reporte_scraping_scooters.txt
- Cache de listado: cache_html/
- Cache de detalle: cache_detalle/
- IMPORTANTE: al escribir el JSON usar json.dump() y verificar que el archivo 
  empiece con { y termine con }. Verificar con json.tool antes de terminar.

## OBJETIVO
Obtener exactamente 30 anuncios VÁLIDOS.

## URLs DE BÚSQUEDA (usar SOLO estas 11)
1. https://www.revolico.com/search?q=scooter+electrico
2. https://www.revolico.com/search?q=patinete+electrico
3. https://www.revolico.com/search?q=patineta+electrica
4. https://www.revolico.com/search?q=scooter+electrico+cuba
5. https://www.revolico.com/search?q=patinete+electrico+cuba
6. https://www.revolico.com/search?q=patineta+electrica+cuba
7. https://www.revolico.com/search?q=patinete+electrico+plegable
8. https://www.revolico.com/search?q=scooter+electrico+plegable
9. https://www.revolico.com/search?q=patinete+electrico+350w
10. https://www.revolico.com/search?q=patinete+electrico+500w
11. https://www.revolico.com/search?q=scooter+electrico+500w

Para cada URL, recorre máximo 10 páginas (parámetro &page=N).
Combina los resultados y elimina duplicados por título.

## Restricciones ÉTICAS (OBLIGATORIO)
1. User-Agent: MiProyectoInvestigacion/1.0 (estudiante; contacto@ejemplo.com)
2. Delay entre peticiones: MÍNIMO 3 segundos (time.sleep(3))
3. Si el sitio devuelve error 429, esperar 60 segundos
4. Máximo 150 peticiones en total (listado + detalle)
5. Cachear el HTML de listado en cache_html/ y el de detalle en cache_detalle/
6. Si un HTML ya está en cache, usarlo sin volver a pedirlo
7. Registrar errores sin detener el scraping

## PÁGINAS DE DETALLE (permitido)
- El scraper SÍ puede entrar a páginas de detalle de anuncios.
- Entrar SOLO a las páginas de detalle de anuncios que hayan pasado los 
  filtros de inclusión y exclusión.
- De la página de detalle, extraer la descripción completa del anuncio.
- De la descripción, extraer motor_w, tipo_bateria, autonomia_km, peso_kg 
  y plegable si no se pudieron extraer del título.
- Si un campo ya se extrajo del título, mantener ese valor (más fiable).
- Cachear cada HTML de detalle en cache_detalle/{id}.html

## Criterios de EXCLUSIÓN (descartar si aparece cualquiera)

### Exclusión por tipo de vehículo
- "moto" (sin "scooter" o "patinete" o "patineta")
- "motico" (sin "scooter" o "patinete" o "patineta")
- "bicimoto" en título o descripción
- "bici electrica" o "bicicleta electrica" en título o descripción
- "bicicleta" sola en título o descripción
- "triciclo" en título o descripción
- "trimoto" en título o descripción
- "3 ruedas" o "tres ruedas"
- "cuatriciclo", "4 ruedas" o "cuatro ruedas"
- "infantil", "niño", "nina", "juguete" en título
- "ciclomotor"

### Exclusión por MOTOR DE COMBUSTIÓN (crítico)
Descartar si el título o descripción contiene cualquiera de estos:
- "gasolina" o "gasolinera"
- "combustion" o "combustión"
- "combustible"
- "nafta" o "bencina"
- "diesel" o "diésel"
- "2T" o "4T"
- "cilindrada" o "cilindro"
- "carburador" o "carburada"
- "inyección" (si no dice "eléctrica")
- "escape" (si no dice "eléctrica" y no es accesorio)
- "tanque de gasolina" o "tanque de combustible"
- Regex de cilindrada: \b(50|70|90|100|110|125|150|200|250|300|400|500|600|750|1000)\s*cc\b

## Criterios de INCLUSIÓN (debe tener al menos uno)
- "scooter electrico" o "scooter eléctrico"
- "patinete electrico" o "patinete eléctrico"
- "patineta electrica" o "patineta eléctrica"
- "e-scooter"

## Criterios de VALIDEZ
Un anuncio es VÁLIDO si tiene TODOS estos 6 campos obligatorios:
- marca (no vacío)
- motor_w (número)
- plegable (booleano: true o false, NUNCA null)
- autonomia_km (número)
- precio_usd (número)
- ubicacion (provincia, no vacío)

El campo tipo_bateria es OPCIONAL. Si no se puede extraer ni inferir, se marca 
como null pero el anuncio SIGUE siendo válido.

Si falta cualquiera de los 6 obligatorios, descartar.

## INFERENCIA DE tipo_bateria (opcional, no descarta)
Si el anuncio NO dice la química de la batería, pero SÍ da voltaje (V) y 
amperaje (Ah), se infiere "litio" con la siguiente lógica:
- Si dice "litio", "lithium", "li-ion", "litihum", "lifepo4" → ese valor, 
  tipo_bateria_inferido = false.
- Si dice "gel" → "gel", tipo_bateria_inferido = false.
- Si dice "plomo" → "plomo-acido", tipo_bateria_inferido = false.
- Si NO dice química pero SÍ tiene voltaje Y amperaje → "litio", 
  tipo_bateria_inferido = true.
- Si NO dice química y NO tiene voltaje ni amperaje → tipo_bateria = null, 
  tipo_bateria_inferido = false. El anuncio NO se descarta por esto.

IMPORTANTE: siempre incluir el campo "tipo_bateria_inferido" (true/false).

## FILTRO DE POTENCIA
- Si motor_w < 200 W, descartar con motivo:
  "exclusión: motor menor a 200W (probable juguete infantil)"

## UBICACIÓN
- ubicacion = provincia cubana (extraer del __APOLLO_STATE__ con provinceId)
- municipio = municipio si aparece (municipalityId), si no null
- Si no se puede determinar la provincia, descartar el anuncio
- Si no aparece municipio, municipio = null (el anuncio sigue siendo válido)

## Extracción con regex
Del título (siempre) y de la descripción de detalle (si está disponible):
- motor_w: buscar (\d{3,5})\s*[Ww]  (OBLIGATORIO)
- voltaje_v: buscar (\d{2,3})\s*[Vv]  (OPCIONAL)
- amperaje_ah: buscar (\d{1,3})\s*[Aa][Hh]  (OPCIONAL)
- autonomia_km: buscar (\d{1,4})\s*km (si hay rango, tomar el máximo)
- tipo_bateria: buscar "litio", "lithium", "li-ion", "litihum", "gel", 
  "plomo", "lifepo4" (corregir typos como "LITIHUM" → litio)
- precio_usd: buscar \$?\s*(\d{3,5})
- peso_kg: buscar (\d{1,3})\s*kg (típico en patinetes)
- velocidad_max_kmh: buscar (\d{2,3})\s*km/?h (NO confundir con autonomia_km)
- plegable: buscar "plegable", "foldable", "fold", "se dobla", "dobla", 
  "plegado", "retractable". Si aparece → true. Si NO aparece → false.
- ubicacion y municipio: del __APOLLO_STATE__ (provinceId/municipalityId)

## ORDEN DE APLICACIÓN DE FILTROS
Para cada anuncio del listado:
1. Verificar inclusión (debe contener "scooter", "patinete" o "patineta")
2. Verificar exclusión por tipo de vehículo (moto, bicimoto, etc.)
3. Verificar exclusión por combustión
4. Si pasa los 3 filtros, ENTRAR a la página de detalle (con cache)
5. Extraer campos del título Y de la descripción de detalle
6. Inferir tipo_bateria si aplica (opcional, no descarta)
7. Verificar filtro de potencia (motor_w >= 200)
8. Verificar los 6 campos obligatorios
Si falla cualquier paso, va a descartados_scooters.json con el motivo.

## Estructura de descartados_scooters.json
Cada anuncio descartado debe tener TODOS los campos del esquema de válidos,
con null en los que no se pudieron extraer, MÁS los campos adicionales 
de motivo y trazabilidad.

{
  "total_revisados": N,
  "total_descartados": N,
  "total_validos": N,
  "descartados": [
    {
      "id_anuncio": "descartado_001",
      "titulo": "...",
      "precio_usd": null,
      "precio_cup": null,
      "moneda_original": "USD",
      "marca": null,
      "modelo": null,
      "tipo_vehiculo": null,
      "motor_w": null,
      "voltaje_v": null,
      "amperaje_ah": null,
      "tipo_bateria": null,
      "tipo_bateria_inferido": false,
      "autonomia_km": null,
      "velocidad_max_kmh": null,
      "tiempo_carga_horas": null,
      "peso_kg": null,
      "plegable": null,
      "numero_ruedas": null,
      "tipo_frenos": null,
      "suspension": null,
      "accesorios": [],
      "estado": null,
      "kilometraje": null,
      "papeles": null,
      "color": null,
      "ubicacion": null,
      "municipio": null,
      "vendedor": null,
      "fecha_publicacion": null,
      "fecha_recoleccion": "...",
      "descripcion": "...",
      "url_anuncio": "...",
      "motivo": "...",
      "campos_faltantes": [],
      "url_busqueda": "...",
      "visito_detalle": false
    }
  ]
}

Reglas para descartados:
- TODOS los campos del esquema deben estar presentes, con null si no se 
  pudieron extraer.
- Si un campo SÍ se pudo extraer antes de descartar, se guarda con su valor real.
- "accesorios" siempre es lista vacía [] si no hay datos.
- "motivo" describe brevemente por qué se descartó.
- "campos_faltantes" es la lista de los 6 obligatorios que faltaron.
- "url_busqueda" indica de qué URL y página salió el anuncio.
- "visito_detalle" indica si se entró a la página de detalle o no.

## Manejo de errores
- Si no hay precio: descartar
- Si no hay ubicación (provincia): descartar
- Si falta marca, motor_w, plegable o autonomia_km: descartar
- Si motor_w < 200: descartar como probable juguete
- Si es de combustión: descartar
- Al final, reportar: páginas recorridas (listado y detalle), válidos, 
  descartados por cada motivo, y cuántos tipo_bateria fueron inferidos

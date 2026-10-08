# Proyecto: Scraping de Revolico - Motos eléctricas  
   
## Descripción  
Scraping de anuncios de motos eléctricas desde Revolico.com para análisis   
de mercado en Cuba. Se extraen 30 anuncios VÁLIDOS. Se excluyen scooters,   
bicimotos, triciclos, patinetes y motos de combustión.  
   
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
- Salida válidos: motos_electricas.json  
- Salida descartados: descartados_motos.json  
- Reporte: reporte_scraping_motos.txt  
- Cache de listado: cache_html/  
- Cache de detalle: cache_detalle/  
- IMPORTANTE: al escribir el JSON usar json.dump() y verificar que el archivo   
  empiece con { y termine con }. Verificar con json.tool antes de terminar.  
   
## OBJETIVO  
Obtener exactamente 30 anuncios VÁLIDOS.  
   
## URLs DE BÚSQUEDA (usar SOLO estas 11)  
1. https://www.revolico.com/search?q=moto+electrica  
2. https://www.revolico.com/search?q=moto+electrica+cuba  
3. https://www.revolico.com/search?q=motico+electrica  
4. https://www.revolico.com/search?q=moto+electrica+3000w  
5. https://www.revolico.com/search?q=moto+electrica+2000w  
6. https://www.revolico.com/search?q=moto+electrica+1000w  
7. https://www.revolico.com/search?q=moto+electrica+72v  
8. https://www.revolico.com/search?q=moto+electrica+60v  
9. https://www.revolico.com/search?q=moto+electrica+5000w  
10. https://www.revolico.com/search?q=moto+electrica+4000w  
11. https://www.revolico.com/search?q=motico+3000w  
   
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
- De la descripción, extraer tipo_bateria, autonomia_km y motor_w   
  si no se pudieron extraer del título.  
- Si un campo ya se extrajo del título, mantener ese valor (más fiable).  
- Cachear cada HTML de detalle en cache_detalle/{id}.html  
   
## Criterios de EXCLUSIÓN (descartar si aparece cualquiera)  
   
### Exclusión por tipo de vehículo  
- "scooter" en título o descripción  
- "bicimoto" en título o descripción  
- "bici electrica" o "bicicleta electrica" en título o descripción  
- "patinete" o "patineta" en título o descripción  
- "triciclo" en título o descripción  
- "trimoto" en título o descripción  
- "3 ruedas" o "tres ruedas"  
- "cuatriciclo", "4 ruedas" o "cuatro ruedas"  
- "infantil", "niño" o "nina" en título  
- "ciclomotor" (si no dice "moto")  
   
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
- "moto electrica" o "moto eléctrica"  
- "motico" o "motico electrica"  
- "moto" + "electrica" en el título o descripción  
   
## Criterios de VALIDEZ  
Un anuncio es VÁLIDO si tiene TODOS estos 6 campos obligatorios:  
- marca (no vacío)  
- motor_w (número)  
- tipo_bateria (litio, gel, plomo-acido, lifepo4)  
- autonomia_km (número)  
- precio_usd (número)  
- ubicacion (provincia, no vacío)  
   
Si falta cualquiera de los 6 obligatorios, descartar.  
   
## FILTRO ADICIONAL DE POTENCIA  
- Si motor_w < 1000 W, descartar con motivo:  
  "exclusión: motor menor a 1000W (probable bicimoto)"  
   
## UBICACIÓN  
- ubicacion = provincia cubana (extraer del __APOLLO_STATE__ con provinceId)  
- municipio = municipio si aparece (municipalityId), si no null  
- Si no se puede determinar la provincia, descartar el anuncio  
- Si no aparece municipio, municipio = null (el anuncio sigue siendo válido)  
   
## Extracción con regex  
Del título (siempre) y de la descripción de detalle (si está disponible):  
- motor_w: buscar (\d{3,5})\s*[Ww]  (OBLIGATORIO)  
- voltaje_v: buscar (\d{2,3})\s*[Vv]  (OPCIONAL)  
- amperaje_ah: buscar (\d{1,3})\s*[Aa][Hh]  (OPCIONAL)  
- autonomia_km: buscar (\d{1,4})\s*km (si hay rango, tomar el máximo)  
- tipo_bateria: buscar "litio", "lithium", "li-ion", "litihum", "gel",   
  "plomo", "lifepo4" (corregir typos como "LITIHUM" → litio)  
- precio_usd: buscar \$?\s*(\d{3,5})  
- velocidad_max_kmh: buscar (\d{2,3})\s*km/?h (NO confundir con autonomia_km)  
- ubicacion y municipio: del __APOLLO_STATE__ (provinceId/municipalityId)  
   
## ORDEN DE APLICACIÓN DE FILTROS  
Para cada anuncio del listado:  
1. Verificar inclusión (debe contener "moto electrica", "motico", etc.)  
2. Verificar exclusión por tipo de vehículo  
3. Verificar exclusión por combustión  
4. Si pasa los 3 filtros, ENTRAR a la página de detalle (con cache)  
5. Extraer campos del título Y de la descripción de detalle  
6. Verificar filtro de potencia (motor_w >= 1000)  
7. Verificar los 6 campos obligatorios  
Si falla cualquier paso, va a descartados_motos.json con el motivo correspondiente.  
   
## Estructura de descartados_motos.json  
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
      "tipo_vehiculo": "moto_electrica",  
      "motor_w": null,  
      "voltaje_v": null,  
      "amperaje_ah": null,  
      "tipo_bateria": null,  
      "autonomia_km": null,  
      "velocidad_max_kmh": null,  
      "tiempo_carga_horas": null,  
      "peso_kg": null,  
      "tipo_moto": null,  
      "licencia_requerida": null,  
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
- Si falta marca, motor_w, tipo_bateria o autonomia_km: descartar  
- Si motor_w < 1000: descartar como probable bicimoto  
- Si es de combustión: descartar  
- Al final, reportar: páginas recorridas (listado y detalle), válidos,   
  descartados por cada motivo  

## Archivos archivados (2026-10-08)

<!-- Archivado por archivar_verticales.sh -->
Las referencias de este documento a los archivos siguientes ahora apuntan a
`archivo/moto/` (misma subruta que tenían):

- `analisis/clustering_30`
- `scraping/descartados/descartados_motos_ampliacion.json`
- `scraping/descartados/descartados_motos.json`
- `scraping/fuentes/motos_electricas_90.json`
- `scraping/fuentes/motos_electricas_ampliacion.json`
- `scraping/reporte_correcciones_motos.json`

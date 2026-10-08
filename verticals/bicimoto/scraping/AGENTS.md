# Proyecto: Scraping de Revolico - Bicicletas eléctricas / Bicimotos  
   
## Descripción  
Scraping de anuncios de bicicletas eléctricas y bicimotos desde Revolico.com   
para análisis de mercado en Cuba. Se extraen 30 anuncios VÁLIDOS.  
   
## Entorno Python  
- Usar el intérprete del venv: /home/leandro/ev-market-study/venv/bin/python3  
- Librerías disponibles: httpx, beautifulsoup4, lxml, pandas  
- NO usar Firecrawl ni MCP. Usar httpx directamente.  
- NO entrar a páginas de detalle de anuncios. Extraer todo del listado.  
   
## Convenciones  
- Lenguaje: Python 3.14 (dentro del venv)  
- Librería de scraping: httpx + BeautifulSoup  
- Salida: JSON, archivo bicis_electricas.json  
- Reporte: reporte_scraping.txt  
   
## OBJETIVO  
Obtener exactamente 30 anuncios VÁLIDOS.  
   
## URLs DE BÚSQUEDA (usar SOLO estas 6)  
1. https://www.revolico.com/search?q=bicimoto  
2. https://www.revolico.com/search?q=bici+electrica  
3. https://www.revolico.com/search?q=bicicleta+electrica  
4. https://www.revolico.com/search?q=e-bike  
5. https://www.revolico.com/search?q=bicimoto+electrica  
6. https://www.revolico.com/search?q=bici+motor  
   
Para cada URL, recorre máximo 10 páginas (parámetro &page=N).  
Combina los resultados y elimina duplicados por título.  
   
## Restricciones ÉTICAS (OBLIGATORIO)  
1. User-Agent: MiProyectoInvestigacion/1.0 (estudiante; contacto@ejemplo.com)  
2. Delay entre peticiones: MÍNIMO 3 segundos (time.sleep(3))  
3. Si el sitio devuelve error 429, esperar 60 segundos  
4. NO sobrecargar: máximo 80 peticiones por sesión  
5. Registrar errores sin detener el scraping  
   
## Criterios de EXCLUSIÓN (descartar si aparece cualquiera)  
- "triciclo" en título o descripción  
- "trimoto" en título o descripción  
- "3 ruedas" o "tres ruedas"  
- "cuatriciclo", "4 ruedas" o "cuatro ruedas"  
- "moto" sola (sin "bici" o "bicimoto") en el título  
- "scooter" solo (sin "bici" o "bicimoto") en el título  
   
## Criterios de VALIDEZ  
Un anuncio es VÁLIDO si tiene TODOS estos 5 campos obligatorios:  
- marca (no vacío)  
- tipo_bateria (litio, gel, o plomo-acido)  
- autonomia_km (número)  
- precio_usd (número)  
- ubicacion (provincia, no vacío)  
   
El campo motor_w es OPCIONAL. Si no aparece, se marca como null   
pero el anuncio SIGUE siendo válido.  
   
Si falta cualquiera de los 5 obligatorios, descartar.  
   
## UBICACIÓN  
- ubicacion = provincia cubana (ej: "La Habana", "Matanzas", "Villa Clara")  
- municipio = municipio si aparece en el anuncio (ej: "Playa", "Vedado", "Centro Habana")  
- Si no se puede determinar la provincia, descartar el anuncio  
- Si aparece municipio pero no provincia, intentar inferir la provincia;  
  si no es posible, ubicacion = null y descartar  
- Si no aparece municipio, municipio = null (el anuncio sigue siendo válido)  
   
## Extracción con regex (del título + descripción corta)  
- motor_w: buscar `(\d{2,5})\s*[Ww]`  (OPCIONAL)  
- autonomia_km: buscar `(\d{1,4})\s*km`  
- tipo_bateria: buscar "litio", "gel", "plomo"  
- precio_usd: buscar `\$?\s*(\d{3,5})`  
- ubicacion: buscar provincias cubanas conocidas:  
  Pinar del Río, Artemisa, La Habana, Mayabeque, Matanzas, Cienfuegos,  
  Villa Clara, Sancti Spíritus, Ciego de Ávila, Camagüey, Las Tunas,  
  Granma, Holguín, Santiago de Cuba, Guantánamo, Isla de la Juventud  
- municipio: buscar municipios conocidos de La Habana:  
  Playa, Plaza, Centro Habana, Habana Vieja, Vedado, Marianao,   
  Cerro, Diez de Octubre, Boyeros, Arroyo Naranjo, San Miguel del Padrón,  
  Guanabacoa, Regla, Habana del Este, Cotorro, Lisa, Playa  
   
## Manejo de errores  
- Si no hay precio: descartar  
- Si no hay ubicación (provincia): descartar  
- Si falta tipo_bateria o autonomia_km o marca: descartar  
- Al final, reportar: páginas recorridas, válidos, descartados por cada motivo  

## Archivos archivados (2026-10-08)

<!-- Archivado por archivar_verticales.sh -->
Las referencias de este documento a los archivos siguientes ahora apuntan a
`archivo/bicimoto/` (misma subruta que tenían):

- `scraping/descartados/descartados_bicis_inconsistentes.json`
- `scraping/descartados/descartados_bicis_reemplazo.json`
- `scraping/descartados/descartados_bicis_sin_motor.json`
- `scraping/fuentes/bicis_electricas_reemplazo.json`
- `scraping/fuentes/bicis_telegram_30.json`
- `scraping/fuentes/telegram_bicis_30.txt`
- `scraping/reporte_correcciones.json`

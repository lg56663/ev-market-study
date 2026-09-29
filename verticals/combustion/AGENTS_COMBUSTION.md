# AGENTS — Vertical Motos de Combustión

## Contexto

Vertical de **control** del proyecto "El Verdadero Costo de Moverse".
Recopila anuncios de motos de combustión (gasolina) en Revolico.com para
comparar con las motos eléctricas.

Sirve para:
- Comparar costo de adquisición: eléctrica vs combustión.
- Calcular costo por km de autonomía en ambos casos.
- Evaluar viabilidad económica del eléctrico frente al de gasolina.

## Fuente

- Sitio: https://www.revolico.com
- Tecnología: Next.js + Apollo (SSR).
- Datos en `__NEXT_DATA__` → `__APOLLO_STATE__`.
- NO se entra a páginas de detalle. Todo del listado.

## Configuración de red

- User-Agent: `MiProyectoInvestigacion/1.0`
- Delay entre peticiones: 3 segundos.
- Timeout: 30 segundos.
- Reintentos: 2 por URL con 5 s de espera.
- Cacheo: `cache_html/`.
- Límite: máximo 30 válidos.
- Dedup: por título normalizado.

## Criterios de inclusión

- Moto de combustión (gasolina, 2T, 4T, carburador, cc).
- Precio en USD.
- Marca identificable.
- Cilindrada identificable.
- Ubicación.
- Fecha de publicación en 2026.
- Título no vacío.

## Criterios de exclusión

- Eléctrica: electrica, eléctrico, 1000w, 2000w, 3000w, bateria litio.
- Scooter, patinete, patineta, bicimoto, bici electrica, bicicleta.
- Triciclo, trimoto, cuatriciclo, 3 ruedas, infantil, ciclomotor.
- Sin precio, marca, cilindrada, ubicación o fecha.
- Fecha anterior a 2026.

## Campos

**Obligatorios:**
`titulo`, `precio_usd`, `marca`, `cilindrada_cc`, `ubicacion`,
`fecha_publicacion`, `url`.

**Opcionales:**
`tipo_motor`, `autonomia_km`, `rendimiento_km_l`, `capacidad_tanque_l`,
`fuente_capacidad_tanque`, `descripcion`.

## Inferencia de capacidad de tanque

1. Buscar en el anuncio con regex (`tanque de X litros`).
2. Si no, tabla por cilindrada:
   - 50 cc → 3.5 L
   - 70 cc → 4.0 L
   - 90 cc → 4.2 L
   - 100 cc → 4.5 L
   - 110 cc → 5.0 L
   - 125 cc → 5.5 L
   - 150 cc → 7.0 L
   - 200 cc → 9.0 L
   - 250 cc → 11.0 L
   - 300 cc → 12.0 L
   - 400 cc → 14.0 L
   - 500 cc → 16.0 L
3. Interpolar si cae entre dos valores.

`fuente_capacidad_tanque` indica: `"anuncio"` o `"tabla_cc"`.

## Rendimiento

**NO se infiere por cilindrada.** Solo se usa si el anuncio lo reporta.
Si no se reporta: `rendimiento_km_l = null`.

## Autonomía

- Si el anuncio la reporta: usar ese valor.
- Si no, y hay `rendimiento_km_l` + `capacidad_tanque_l`:
  `autonomia_km = round(capacidad_tanque_l * rendimiento_km_l)`.
- Si no hay rendimiento: `autonomia_km = null`.

No se inventa autonomía.

## Archivos generados

- `motos_combustion.json` → 30 anuncios válidos.
- `descartados_combustion.json` → descartes con motivo. Cada descarte
  conserva TODOS los campos extraídos (aunque sean null), más el motivo.
- `reporte_scraping_combustion.txt` → resumen.
- `cache_html/` → caché.

## Reglas críticas

- NO inventar datos.
- NO usar Firecrawl ni Playwright.
- NO entrar a páginas de detalle.
- NO mezclar con motos eléctricas.
- NO tocar otras carpetas.

## Ejecución

```bash
/home/leandro/ev-market-study/venv/bin/python3 scraper_combustion.py

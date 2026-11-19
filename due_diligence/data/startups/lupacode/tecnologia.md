---
empresa: Lupa Code
titulo: Tecnología y uso de modelos de lenguaje
fecha: 2026-10
nota: documento ficticio creado para la demo
---
# Lupa Code — Tecnología y uso de modelos de lenguaje

## Arquitectura
Cada pull request se divide en fragmentos de código; cada fragmento pasa por un LLM con el contexto del
repositorio y las convenciones del equipo. Un pull request promedio genera 20 llamadas.

## Consumo del LLM
- Llamadas al LLM por mes: 3.000.000.
- Tokens de entrada promedio por llamada: 3.000.
- Tokens de salida promedio por llamada: 400.

## Precios de lista del proveedor
- Precio por millón de tokens de entrada: USD 1,25.
- Precio por millón de tokens de salida: USD 5.

## Créditos promocionales
- El proveedor otorgó créditos promocionales por USD 15.000 por mes que cubren la mayor parte del
  consumo del LLM.
- Los créditos vencen el 28/02/2027 y no hay un acuerdo firmado para renovarlos.

## Dependencia de proveedores
- Proveedor único de LLM; el equipo probó un modelo alternativo en un entorno de prueba, no en producción.

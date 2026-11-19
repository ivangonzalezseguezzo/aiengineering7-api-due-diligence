---
empresa: Anamnesis
titulo: Tecnología y uso de modelos de lenguaje
fecha: 2026-10
nota: documento ficticio creado para la demo
---
# Anamnesis — Tecnología y uso de modelos de lenguaje

## Arquitectura
El audio de la consulta se transcribe con un servicio externo y la transcripción completa pasa por un
LLM que redacta la historia clínica con la plantilla de cada especialidad.

## Consumo del LLM
- Llamadas al LLM por mes: 400.000.
- Tokens de entrada promedio por llamada: 6.000.
- Tokens de salida promedio por llamada: 900.

## Precios del contrato con el proveedor
- Precio por millón de tokens de entrada: USD 1,25.
- Precio por millón de tokens de salida: USD 10.

## Dependencia de proveedores
- Dos proveedores de LLM probados en producción; el segundo atiende el 20 % del tráfico.

## Regulación y datos
- Los datos de salud se alojan en la región y se anonimizan antes de llegar al LLM.
- La certificación como software de uso clínico está en trámite y no tiene fecha de aprobación.

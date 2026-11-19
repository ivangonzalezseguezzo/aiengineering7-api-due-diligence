---
empresa: VozActiva
titulo: Tecnología y uso de modelos de lenguaje
fecha: 2026-10
---
# VozActiva — Tecnología y uso de modelos de lenguaje

## Arquitectura
Cada turno de la conversación pasa por reconocimiento de voz, un LLM que decide la respuesta y consulta
los sistemas del cliente, y síntesis de voz. Una llamada promedio tiene 9 turnos.

## Consumo del LLM
- Llamadas al LLM por mes: 2.400.000.
- Tokens de entrada promedio por llamada: 1.800.
- Tokens de salida promedio por llamada: 250.

## Precios del contrato con el proveedor
- Precio por millón de tokens de entrada: USD 2,50.
- Precio por millón de tokens de salida: USD 10.
- Cláusula de ajuste: desde el 01/01/2027 el precio por millón de tokens de entrada pasa a USD 3,50 (+40 %).

## Dependencia de proveedores
- Proveedor único de LLM, sin un modelo alternativo probado en producción.
- El prompt del agente de voz está optimizado para ese proveedor: migrar requiere reescribirlo y volver a validarlo.

## Equipo técnico
- La CTO, Lucía Benítez, trabaja con dedicación parcial (50 %) y asesora a otra empresa.
- El equipo de ingeniería tiene 5 personas.

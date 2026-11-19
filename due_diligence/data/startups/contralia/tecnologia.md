---
empresa: Contralia
titulo: Tecnología y uso de modelos de lenguaje
fecha: 2026-10
---
# Contralia — Tecnología y uso de modelos de lenguaje

## Arquitectura
Cada contrato se divide en cláusulas. Un modelo abierto autoalojado clasifica cada cláusula (su costo
está dentro de la infraestructura cloud) y un LLM comercial analiza solo las cláusulas marcadas como
riesgosas y redacta la alternativa.

## Consumo del LLM comercial
- Llamadas al LLM por mes: 520.000.
- Tokens de entrada promedio por llamada: 4.000.
- Tokens de salida promedio por llamada: 700.

## Precios del contrato con el proveedor
- Precio por millón de tokens de entrada: USD 1,25.
- Precio por millón de tokens de salida: USD 10.
- Contrato anual con precio fijo hasta diciembre de 2027.

## Dependencia de proveedores
- Hoy el 100 % del análisis de cláusulas usa un único proveedor comercial.
- Hay un segundo proveedor evaluado en pruebas internas, con calidad similar en el 90 % de los casos,
  pero todavía no está en producción.

## Seguridad y datos
Los contratos de los clientes no se usan para entrenar modelos. Los datos se cifran en reposo y se
borran a los 90 días si el cliente no los marca para conservar.

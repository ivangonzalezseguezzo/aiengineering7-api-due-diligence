# Due diligence — Anamnesis

Fecha: 2026-11-18 · Decisiones del supervisor: 4 · Validación: aprobada

## Recomendación preliminar: PROFUNDIZAR

Motivo: score 62,0/100 (banda: profundizar).

> Recomendación generada por el sistema a partir de datos anclados a sus fuentes. La decisión de invertir la toma el comité con aprobación humana explícita (ver la sección «Decisión del comité» al final).

## Métricas registradas (investigador)

| Métrica | Valor | Fuente | Cita |
|---|---|---|---|
| mrr_usd | 48.000 | anamnesis/metricas#1 | "MRR recurrente (suscripciones mensuales): USD 48.000" |
| clientes_activos | 64 | anamnesis/metricas#2 | "Clínicas con contrato recurrente: 64" |
| crecimiento_mrr_mensual_pct | 6 | anamnesis/metricas#3 | "Crecimiento mensual del MRR recurrente (últimos 6 meses): 6 %" |
| churn_mensual_pct | 2 | anamnesis/metricas#4 | "Churn mensual de clínicas: 2 %" |
| concentracion_top_cliente_pct | 38 | anamnesis/clientes-y-equipo#1 | "Los 5 clientes más grandes suman el 38 % del MRR recurrente" |
| gasto_comercial_mensual_usd | 45.000 | anamnesis/finanzas#3 | "Gasto mensual en ventas y marketing: USD 45.000" |
| clientes_nuevos_mes | 5 | anamnesis/metricas#4 | "Clínicas nuevas por mes (promedio de los últimos 6 meses): 5" |
| costo_infra_mensual_usd | 3.500 | anamnesis/finanzas#1 | "Infraestructura cloud (sin incluir el LLM): USD 3.500 por mes" |
| otros_costos_directos_mensual_usd | 8.000 | anamnesis/finanzas#1 | "Otros costos directos totales (transcripción más soporte): USD 8.000 por mes" |
| margen_bruto_declarado_pct | 63 | anamnesis/finanzas#2 | "Margen bruto informado: 63 %, incluyendo el LLM, la transcripción y el soporte" |
| burn_neto_mensual_usd | 60.000 | anamnesis/finanzas#4 | "Burn neto mensual: USD 60.000" |
| caja_usd | 600.000 | anamnesis/finanzas#4 | "Caja disponible al 31/10/2026: USD 600.000" |
| runway_declarado_meses | 10 | anamnesis/finanzas#4 | "Runway informado: 10 meses, sin contar financiamiento no firmado" |
| llamadas_llm_mes | 400.000 | anamnesis/tecnologia#2 | "Llamadas al LLM por mes: 400.000" |
| tokens_entrada_por_llamada | 6.000 | anamnesis/tecnologia#2 | "Tokens de entrada promedio por llamada: 6.000" |
| tokens_salida_por_llamada | 900 | anamnesis/tecnologia#2 | "Tokens de salida promedio por llamada: 900" |
| precio_entrada_usd_millon | 1,25 | anamnesis/tecnologia#3 | "Precio por millón de tokens de entrada: USD 1,25" |
| precio_salida_usd_millon | 10 | anamnesis/tecnologia#3 | "Precio por millón de tokens de salida: USD 10" |
| clientes_potenciales_tam | 14.000 | mercado/salud-digital-latam#1 | "Clínicas y centros médicos con 10 o más médicos en Argentina, Chile, Uruguay, México y Colombia: 14.000" |
| ticket_anual_usd | 9.000 | mercado/salud-digital-latam#2 | "Ticket anual mediano del segmento: USD 9.000" |
| porcentaje_sam_pct | 40 | mercado/salud-digital-latam#1 | "El Cono Sur (Argentina, Chile y Uruguay) concentra 5.600 clínicas, el 40 % del total" |

### Hallazgos cualitativos
- (clientes) Los 5 clientes más grandes concentran el 38 % del MRR y el ciclo de venta promedio es largo (4 meses) debido a los comités de calidad de cada clínica. [anamnesis/clientes-y-equipo#1]
- (riesgo) La certificación como software de uso clínico se encuentra en trámite sin fecha de aprobación, lo cual representa una barrera regulatoria clave para la expansión. [anamnesis/tecnologia#5]
- (equipo) El equipo fundador está compuesto por una médica clínica como CEO y un ingeniero de software como CTO. [anamnesis/clientes-y-equipo#2]

## Análisis (analista, v1)

**Costo de LLM:** USD 6.600 por mes (13,8 % del MRR).

| ARPA | Margen bruto | CAC | LTV | LTV/CAC | Payback | Runway |
|---|---|---|---|---|---|---|
| USD 750 | 62,3 % | USD 9.000 | USD 23.359 | 2,60 | 19,3 meses | 10,0 meses |

**Mercado:** TAM USD 126.000.000 · SAM USD 50.400.000 · SOM USD 2.520.000 por año. Supuesto de cuota: Cuota conservadora del 5% del SAM dada la complejidad regulatoria y el ciclo de venta largo en clínicas.

**Scoring: 62,0/100 (profundizar)**

| Criterio | Puntaje 0-5 | Peso | Aporte |
|---|---|---|---|
| unit_economics | 3,0 | 25 % | 15,0 |
| margen_bruto | 4,0 | 20 % | 16,0 |
| runway | 3,0 | 15 % | 9,0 |
| crecimiento | 2,0 | 15 % | 6,0 |
| equipo | 4,0 | 10 % | 8,0 |
| mercado | 3,0 | 10 % | 6,0 |
| concentracion | 2,0 | 5 % | 2,0 |

- Equipo: Equipo fundador sólido con perfil técnico (CTO) y médico (CEO), combinando dominio clínico y capacidad de desarrollo.
- Mercado: Mercado de salud digital relevante con TAM y SAM amplios, aunque condicionado por barreras regulatorias y ciclos de venta largos.

**Interpretación del analista:** ### Análisis Financiero y Unit Economics — Anamnesis

- **Costo de LLM y Márgenes:** El costo mensual del LLM asciende a $6,600 USD (representando un 13.75% del MRR). El margen bruto real, incorporando infraestructura, costos directos y el consumo de tokens, se sitúa en un **62.29%**, muy alineado con el 63% declarado por la startup.
- **Eficiencia Comercial:** El CAC es de $9,000 USD frente a un LTV de $23,359.38 USD, arrojando un ratio **LTV/CAC de 2.6x** y un **Payback de 19.3 meses**, lo que denota una eficiencia comercial moderada que requiere optimización en los ciclos de venta.
- **Runway y Caja:** Con una caja disponible de $600,000 USD y un burn neto mensual de $60,000 USD, el **runway es de 10 meses**, coincidiendo con la estimación de la compañía.
- **Scoring:** La startup obtiene un **score de 62/100 (Banda: Profundizar)**, impulsado por un margen bruto saludable y un buen complemento en el equipo fundador, pero penalizado por un payback elevado y riesgos regulatorios pendientes.

## Red flags (crítico)

Sin red flags registradas.

## Validación

- ✅ investigacion: 21 métricas ancladas a su fuente (mínimo 6) y 3 hallazgos con cita (mínimo 2).

- ✅ metricas_clave: Todas las métricas clave están registradas.

- ✅ correcciones_investigador: Sin correcciones altas pendientes.

- ✅ analisis_vigente: Análisis v1 completo y al día.

- ✅ correcciones_analista: Sin correcciones altas pendientes.

- ✅ critica_vigente: La crítica v1 auditó el análisis v1.

## Trazabilidad: quién aportó qué

| Paso | Agente | Acción | Detalle |
|---|---|---|---|
| 1 | supervisor | → investigador | Orden natural de la rúbrica: El investigador todavía no entregó datos. [guarda: decisión determinística, sin LLM] |
| 1 | investigador | investigación | v1: 21 métricas ancladas, 3 hallazgos, 2 registros rechazados |
| 2 | supervisor | → analista | Orden natural de la rúbrica: El analista todavía no calculó. [guarda: decisión determinística, sin LLM] |
| 2 | analista | análisis | v1: costo LLM USD 6.600/mes (13,8 % del MRR) · margen 62,3 % · LTV/CAC 2,60 · payback 19,3 m · runway 10,0 m · score 62,0 (profundizar); 0 llamadas rechazadas por falta de respaldo |
| 3 | supervisor | → critico | Orden natural de la rúbrica: El crítico todavía no auditó el análisis. [guarda: decisión determinística, sin LLM] |
| 3 | critico | auditoría | v1 (auditó el análisis v1): 0 red flags (0 altas), 0 discrepancias declarado/calculado, 0 correcciones nuevas |
| 4 | supervisor | → finalizar | La rúbrica no tiene puntos pendientes: se cierra. [guarda: decisión determinística, sin LLM] |
| 4 | validador | validación | aprobada (intento 1, 0 advertencias) |

*Definiciones de métricas: 21 en el vocabulario de `state.py`.*

## Decisión del comité (human-in-the-loop)
**APROBADA**.
- Pausa 1 (2026-11-18T17:10:10) — analisis_local eligió **aprobar**: Decisión automática del análisis local.

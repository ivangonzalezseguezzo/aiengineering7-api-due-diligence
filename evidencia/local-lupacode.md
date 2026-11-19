# Due diligence — Lupa Code

Fecha: 2026-11-18 · Decisiones del supervisor: 4 · Validación: aprobada

## Recomendación preliminar: PROFUNDIZAR

Motivo: score 89,0/100 (banda: avanzar a comité); 2 red flags de severidad alta impiden avanzar directo a comité.

> Recomendación generada por el sistema a partir de datos anclados a sus fuentes. La decisión de invertir la toma el comité con aprobación humana explícita (ver la sección «Decisión del comité» al final).

## Métricas registradas (investigador)

| Métrica | Valor | Fuente | Cita |
|---|---|---|---|
| mrr_usd | 60.000 | lupacode/metricas#1 | "MRR recurrente (suscripciones mensuales): USD 60.000" |
| clientes_activos | 150 | lupacode/metricas#2 | "Equipos con suscripción paga: 150" |
| crecimiento_mrr_mensual_pct | 12 | lupacode/metricas#3 | "Crecimiento mensual del MRR recurrente (últimos 6 meses): 12 %" |
| churn_mensual_pct | 2,5 | lupacode/metricas#4 | "Churn mensual de equipos: 2,5 %" |
| concentracion_top_cliente_pct | 6 | lupacode/metricas#2 | "El cliente más grande representa el 6 % del MRR recurrente" |
| gasto_comercial_mensual_usd | 45.000 | lupacode/finanzas#3 | "Gasto mensual en ventas y marketing: USD 45.000" |
| clientes_nuevos_mes | 18 | lupacode/metricas#4 | "Equipos nuevos por mes (promedio de los últimos 6 meses): 18" |
| costo_infra_mensual_usd | 4.000 | lupacode/finanzas#1 | "Infraestructura cloud (sin incluir el LLM): USD 4.000 por mes" |
| margen_bruto_declarado_pct | 82 | lupacode/finanzas#2 | "Margen bruto informado: 82 %" |
| burn_neto_mensual_usd | 70.000 | lupacode/finanzas#4 | "Burn neto mensual: USD 70.000" |
| caja_usd | 1.400.000 | lupacode/finanzas#4 | "Caja disponible al 31/10/2026: USD 1.400.000" |
| runway_declarado_meses | 20 | lupacode/finanzas#4 | "Runway informado: 20 meses" |
| llamadas_llm_mes | 3.000.000 | lupacode/tecnologia#2 | "Llamadas al LLM por mes: 3.000.000" |
| tokens_entrada_por_llamada | 3.000 | lupacode/tecnologia#2 | "Tokens de entrada promedio por llamada: 3.000" |
| tokens_salida_por_llamada | 400 | lupacode/tecnologia#2 | "Tokens de salida promedio por llamada: 400" |
| precio_entrada_usd_millon | 1,25 | lupacode/tecnologia#3 | "Precio por millón de tokens de entrada: USD 1,25" |
| precio_salida_usd_millon | 5 | lupacode/tecnologia#3 | "Precio por millón de tokens de salida: USD 5" |
| clientes_potenciales_tam | 20.800 | mercado/herramientas-desarrollo-latam#1 | "Los equipos que trabajan con GitHub o GitLab en la nube suman 20.800, el 65 % del total" |
| ticket_anual_usd | 4.800 | mercado/herramientas-desarrollo-latam#2 | "Ticket anual mediano del segmento: USD 4.800" |
| porcentaje_sam_pct | 65 | mercado/herramientas-desarrollo-latam#1 | "Los equipos que trabajan con GitHub o GitLab en la nube suman 20.800, el 65 % del total" |
| otros_costos_directos_mensual_usd | 2.500 | lupacode/finanzas#1 | "Otros costos directos totales: USD 2.500 por mes" |

### Hallazgos cualitativos
- (riesgo) El margen bruto declarado es optimista dado que depende de créditos promocionales temporales del proveedor de LLM. [lupacode/finanzas#2]
- (competencia) El panorama competitivo incluye actores globales establecidos y startups locales. [mercado/herramientas-desarrollo-latam#4]
- (riesgo) Existe una dependencia de un proveedor único de LLM sin acuerdo de renovación para los créditos. [lupacode/tecnologia#4]
- (equipo) El equipo fundador posee amplia experiencia técnica y liderazgo. [lupacode/clientes-y-equipo#2]

## Análisis (analista, v1)

**Costo de LLM:** USD 17.250 por mes (28,8 % del MRR).

| ARPA | Margen bruto | CAC | LTV | LTV/CAC | Payback | Runway |
|---|---|---|---|---|---|---|
| USD 400 | 60,4 % | USD 2.500 | USD 9.667 | 3,87 | 10,3 meses | 20,0 meses |

**Mercado:** TAM USD 99.840.000 · SAM USD 64.896.000 · SOM USD 3.244.800 por año. Supuesto de cuota: Cuota conservadora del 5% del SAM en 5 años dado el crecimiento sostenido y competencia moderada.

**Scoring: 89,0/100 (avanzar a comité)**

| Criterio | Puntaje 0-5 | Peso | Aporte |
|---|---|---|---|
| unit_economics | 5,0 | 25 % | 25,0 |
| margen_bruto | 4,0 | 20 % | 16,0 |
| runway | 5,0 | 15 % | 15,0 |
| crecimiento | 4,0 | 15 % | 12,0 |
| equipo | 4,0 | 10 % | 8,0 |
| mercado | 4,0 | 10 % | 8,0 |
| concentracion | 5,0 | 5 % | 5,0 |

- Equipo: El equipo fundador posee amplia experiencia técnica y liderazgo (puntaje 4/5).
- Mercado: Mercado amplio con competencia global y local, y un TAM/SAM significativo (puntaje 4/5).

**Interpretación del analista:** ### Resumen del Análisis Financiero y de Unit Economics

- **Costo de LLM y Margen Bruto Real:** El costo mensual del LLM asciende a **$17,250 USD** (28.75% del MRR). Esto reduce el margen bruto real de la startup al **60.42%**, impactando considerablemente el 82% declarado debido al peso de los tokens y la infraestructura directa.
- **Eficiencia Comercial:** Presenta un ARPA de **$400 USD**, un CAC de **$2,500 USD** y un LTV de **$9,666.67 USD**, lo que resulta en un sólido ratio **LTV/CAC de 3.87x** y un **Payback de 10.3 meses**.
- **Runway y Caja:** Con una caja disponible de **$1.4M USD** y un burn neto mensual de **$70,000 USD**, el runway se mantiene en **20 meses**.
- **Scoring de Inversión:** La startup obtuvo un puntaje total de **89/100**, clasificando en la banda de **"avanzar a comité"**, respaldado por buenos unit economics, un crecimiento mensual del MRR del 12% y un sólido runway, a pesar de los riesgos en el margen bruto por la dependencia del proveedor de LLM.

## Red flags (crítico)

- **[alta] Vencimiento de créditos promocionales de LLM sin renovación firmada**: La dependencia de créditos promocionales temporales pone en riesgo el margen bruto una vez que expiren, ya que la startup deberá absorber el costo total del LLM. Evidencia: "Los créditos vencen el 28/02/2027 y no hay un acuerdo firmado para renovarlos" [lupacode/tecnologia#4]

- **[alta] Margen bruto declarado inflado** (regla de contraste): Margen bruto declarado 82,0 % vs. calculado con todos los costos directos 60,4 % (21,6 puntos de diferencia). Evidencia: "Margen bruto informado: 82 %" [lupacode/finanzas#2]

## Validación

- ✅ investigacion: 21 métricas ancladas a su fuente (mínimo 6) y 4 hallazgos con cita (mínimo 2).

- ✅ metricas_clave: Todas las métricas clave están registradas.

- ✅ correcciones_investigador: Sin correcciones altas pendientes.

- ✅ analisis_vigente: Análisis v1 completo y al día.

- ✅ correcciones_analista: Sin correcciones altas pendientes.

- ✅ critica_vigente: La crítica v1 auditó el análisis v1.

## Trazabilidad: quién aportó qué

| Paso | Agente | Acción | Detalle |
|---|---|---|---|
| 1 | supervisor | → investigador | Orden natural de la rúbrica: El investigador todavía no entregó datos. [guarda: decisión determinística, sin LLM] |
| 1 | investigador | investigación | v1: 21 métricas ancladas, 4 hallazgos, 3 registros rechazados |
| 2 | supervisor | → analista | Orden natural de la rúbrica: El analista todavía no calculó. [guarda: decisión determinística, sin LLM] |
| 2 | analista | análisis | v1: costo LLM USD 17.250/mes (28,8 % del MRR) · margen 60,4 % · LTV/CAC 3,87 · payback 10,3 m · runway 20,0 m · score 89,0 (avanzar a comité); 0 llamadas rechazadas por falta de respaldo |
| 3 | supervisor | → critico | Orden natural de la rúbrica: El crítico todavía no auditó el análisis. [guarda: decisión determinística, sin LLM] |
| 3 | critico | auditoría | v1 (auditó el análisis v1): 2 red flags (2 altas), 1 discrepancias declarado/calculado, 0 correcciones nuevas |
| 4 | supervisor | → finalizar | La rúbrica no tiene puntos pendientes: se cierra. [guarda: decisión determinística, sin LLM] |
| 4 | validador | validación | aprobada (intento 1, 0 advertencias) |

*Definiciones de métricas: 21 en el vocabulario de `state.py`.*

## Decisión del comité (human-in-the-loop)
**APROBADA**.
- Pausa 1 (2026-11-18T17:08:39) — analisis_local eligió **aprobar**: Decisión automática del análisis local.

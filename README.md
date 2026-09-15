## Pagos Chappsa

App de captura y aplicación de pagos de clientes para `opticas.chappsa.com` (Frappe/ERPNext v15).

**No toca contabilidad.** Los saldos se derivan exclusivamente de sus propios DocTypes; ERPNext
(Payment Entry, GL Entry) queda intacto. El diseño está preparado para que en el futuro estos
pagos se conviertan en Payment Entries reales.

### DocTypes

| DocType | Rol |
|---|---|
| **Pago Cliente** (submittable) | Encabezado: serie, empresa, cliente, fecha, moneda, monto recibido, totales, excedente/anticipo, comentarios |
| **Detalle Documento Pago** (hijo) | Una línea por documento abonado: tipo, documento, fecha, valor original, abonado previo, saldo anterior, abono, saldo |
| **Detalle Forma Pago** (hijo) | Una línea por forma de pago: id_linea, tipo, forma de pago, fecha, monto, no. documento, banco, cuenta banco empresa, cuenta contable, comentario |

### Convención de signos

Todo documento se maneja con la misma fórmula, sin importar su naturaleza:

```
saldo = valor_original - abonado

valor_original > 0  ->  CARGO   (Nota de Entrega: el cliente debe)
valor_original < 0  ->  CRÉDITO (devolución o anticipo: a favor del cliente)
```

Gracias a eso, "matar" un anticipo contra una factura es simplemente una línea con abono
negativo dentro del mismo pago, y el cuadre siempre se cumple:

```
excedente = monto_recibido - suma(abonos)
```

### Cambiar de Nota de Entrega a Factura

Todo el conocimiento de qué documento se cobra vive en `pagos_chappsa/saldos.py`:
`CONFIG_DOCUMENTOS` (ya trae la entrada `Sales Invoice` lista) y `TIPOS_ACTIVOS`. Cambiar
`TIPOS_ACTIVOS = ["Sales Invoice"]` migra la app entera sin tocar nada más; los pagos históricos
siguen apuntando a sus Notas de Entrega porque cada línea guarda su propio `tipo_documento`
(Dynamic Link).

### Página

`/app/pagos` — panel de captura en 3 pestañas: distribución, formas de pago y pagos recientes.

### Endpoints

`pagos_chappsa.api.pagos.{get_contexto, get_monedas_con_documentos, get_cuentas_banco, get_documentos, distribuir, guardar_pago, get_pago, registrar_reimpresion, actualizar_no_recibo, get_pagos_recientes}`

### Perfil de Cobranza

DocType opcional (uno por usuario cobrador, `autoname: field:usuario`) que acota lo que ese
usuario puede hacer: guardar borradores, validar, cancelar (borradores/validados), reimprimir
recibo, permitir pagos parciales/anticipos, y alcance por empresas y bodegas. **Sin perfil, sin
restricción** — un usuario sin Perfil trabaja igual que si el módulo no existiera; el perfil solo
sirve para acotar. Lógica en `pagos_chappsa/permisos.py`.

### No. de recibo manual

`Pago Cliente.no_recibo_manual`: número del recibo físico (talonario) que se le entrega al
cliente. Obligatorio, no se puede repetir entre pagos no cancelados, y es corregible aun con el
pago ya validado (endpoint `actualizar_no_recibo`) porque es un dato transcrito a mano.

### Reportes y estado de cuenta

- **Cobros por Empleado y Día**, **Recibos Emitidos por Estado**, **Estado de Cuenta Resumen**,
  **Estado de Cuenta Detallado** (Script Reports, módulo Pagos Chappsa).
- `/estado_cuenta?tipo=resumen|detallado&cliente=&empresa=&moneda=&hasta=` — versión imprimible
  en `pagos_chappsa/www/estado_cuenta.py`.

### Dependencia de datos a revisar antes de instalar en otro sitio

`saldos.py::CONFIG_DOCUMENTOS` referencia el Custom Field `custom_numero_nota` en Delivery Note
(número interno de la nota, usado en el recibo y en reportes). Si el sitio destino no tiene ese
campo, agregarlo o quitar la referencia antes de usar la app ahí.

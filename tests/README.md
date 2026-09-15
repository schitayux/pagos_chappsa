# Verificación cruzada de la distribución

La lógica de reparto existe dos veces: `pagos_chappsa/saldos.py::distribuir_monto`
(servidor, la que valida al guardar) y `distribuir()` dentro de
`pagos_chappsa/page/pagos/pagos.js` (navegador, la que ve el usuario mientras captura).
Tienen que dar exactamente el mismo resultado.

Cada vez que se toque una de las dos, correr:

```bash
python3 tests/gen_escenarios.py
node tests/harness_dist.js \
    pagos_chappsa/pagos_chappsa/page/pagos/pagos.js /tmp/escenarios.json /tmp/salida_js.json
# y dentro de: bench --site <sitio> console
exec(open('tests/comparar_dist.py').read(), globals())
```

`harness_dist.js` extrae el método `distribuir()` tal cual del archivo desplegado
(no una copia), así que prueba el código real. Debe reportar 0 discrepancias y
0 violaciones del invariante `excedente = monto - suma(abonos)`.

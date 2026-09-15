import json
from frappe.utils import flt
from pagos_chappsa.saldos import distribuir_monto

escenarios = json.load(open("/tmp/escenarios.json"))
js = json.load(open("/tmp/salida_js.json"))

difs = []
for i, (esc, res_js) in enumerate(zip(escenarios, js)):
    filas, exc_py = distribuir_monto(esc["documentos"], esc["monto"])
    abonos_py = [flt(f["abono"], 2) for f in filas]
    if abonos_py != [flt(a, 2) for a in res_js["abonos"]] or abs(exc_py - res_js["excedente"]) > 0.005:
        difs.append((i, esc["monto"], abonos_py, res_js["abonos"], exc_py, res_js["excedente"]))

print(f"Escenarios comparados: {len(escenarios)}")
print(f"Discrepancias Python vs JS: {len(difs)}")
for d in difs[:5]:
    print("  escenario", d[0], "monto", d[1])
    print("     python:", d[2], "exc", d[4])
    print("     js    :", d[3], "exc", d[5])

# invariante contable en ambas implementaciones
malos = 0
for esc, res_js in zip(escenarios, js):
    filas, exc_py = distribuir_monto(esc["documentos"], esc["monto"])
    if abs(exc_py - (esc["monto"] - sum(flt(f["abono"]) for f in filas))) > 0.005:
        malos += 1
print(f"Violaciones del invariante excedente = monto - suma(abonos): {malos}")

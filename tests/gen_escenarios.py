import json, random
random.seed(20260828)
escenarios = []
for _ in range(400):
    docs, n_cargos = [], random.randint(0, 6)
    for i in range(n_cargos):
        docs.append({"documento": f"NE{i}", "clase": "Cargo",
                     "saldo_anterior": round(random.uniform(10, 3000), 2),
                     "abono": 0, "manual": False, "aplicar": False})
    for i in range(random.randint(0, 2)):
        docs.append({"documento": f"CR{i}", "clase": "Crédito",
                     "saldo_anterior": -round(random.uniform(10, 2000), 2),
                     "abono": 0, "manual": False,
                     "aplicar": random.choice([True, False])})
    random.shuffle(docs)
    docs.sort(key=lambda d: 0 if d["clase"] == "Crédito" else 1)
    escenarios.append({"monto": round(random.uniform(0, 8000), 2), "documentos": docs})
json.dump(escenarios, open("/tmp/escenarios.json", "w"))
print(f"{len(escenarios)} escenarios generados")

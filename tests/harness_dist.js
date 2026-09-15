// Extrae el método distribuir() TAL CUAL del pagos.js desplegado y lo ejecuta
// fuera del navegador, para compararlo contra la implementación de Python.
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");

const ini = src.indexOf("\tdistribuir() {");
const fin = src.indexOf("\ttotales() {");
if (ini < 0 || fin < 0) throw new Error("no se encontró distribuir()");
let cuerpo = src.slice(ini, fin).trimEnd();          // termina en el "}" del método

const TOL = 0.005;
function flt(v, p) {
	let n = parseFloat(v);
	if (isNaN(n)) n = 0;
	if (p === undefined || p === null) return n;
	return Math.round((n + Number.EPSILON) * Math.pow(10, p)) / Math.pow(10, p);
}
function precision_moneda() { return 2; }

const Clase = eval(`(class { ${cuerpo}\n render_todo(){} })`);

const escenarios = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const salida = escenarios.map((esc) => {
	const inst = new Clase();
	inst.monto = esc.monto;
	inst.documentos = esc.documentos.map((d) => ({ ...d }));
	inst.distribuir();
	return {
		abonos: inst.documentos.map((d) => flt(d.abono, 2)),
		excedente: flt(inst.monto - inst.documentos.reduce((s, d) => s + flt(d.abono), 0), 2),
	};
});
fs.writeFileSync(process.argv[4], JSON.stringify(salida));
console.log(`JS: ${salida.length} escenarios ejecutados`);

#!/usr/bin/env node
/**
 * Calibra o limiar PROSA_SIMILARITY_MIN com pares de frases anotados (por exemplo, o ASSIN,
 * que tem 5000 pares pt-PT e 5000 pt-BR com semelhança de 1 a 5).
 *
 * Entrada: ficheiro TSV ou JSONL com as colunas/campos `a`, `b` e `score` (numérico, maior é mais semelhante).
 * Uso:
 *   node scripts/calibrate-similarity.mjs pares.tsv [--model bge-m3] [--base http://localhost:11434] [--positive 4]
 *
 * Calcula o cosseno de cada par com o modelo de embeddings do Ollama e mostra, para vários limiares,
 * quantos pares «semelhantes» (score >= --positive) ficariam abaixo (falsos avisos) e quantos pares
 * «diferentes» ficariam acima (avisos perdidos). Escolhe o limiar que equilibra os dois.
 */
import fs from "node:fs";

const args = process.argv.slice(2);
const file = args.find((a) => !a.startsWith("--"));
const opt = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i !== -1 && args[i + 1] ? args[i + 1] : fallback;
};
if (!file) {
  console.error("Indica o ficheiro de pares (TSV com cabeçalho a\tb\tscore, ou JSONL).");
  process.exit(1);
}
const model = opt("model", "bge-m3");
const base = opt("base", "http://localhost:11434").replace(/\/v1\/?$/, "").replace(/\/$/, "");
const positive = Number(opt("positive", "4"));
const limit = Number(opt("limit", "500"));

function readPairs(path) {
  const raw = fs.readFileSync(path, "utf8").trim().split("\n");
  if (path.endsWith(".jsonl")) return raw.map((l) => JSON.parse(l));
  const header = raw[0].split("\t");
  const ia = header.indexOf("a");
  const ib = header.indexOf("b");
  const is = header.indexOf("score");
  return raw.slice(1).map((l) => {
    const cols = l.split("\t");
    return { a: cols[ia], b: cols[ib], score: Number(cols[is]) };
  });
}

async function embed(texts) {
  const response = await fetch(`${base}/api/embed`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ model, input: texts }) });
  if (!response.ok) throw new Error(`Ollama respondeu ${response.status}: ${await response.text()}`);
  return (await response.json()).embeddings;
}

function cosine(a, b) {
  let dot = 0, na = 0, nb = 0;
  for (let i = 0; i < a.length; i += 1) { dot += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

const pairs = readPairs(file).filter((p) => p.a && p.b && Number.isFinite(p.score)).slice(0, limit);
console.log(`${pairs.length} pares · modelo ${model} · positivo se score >= ${positive}`);
const results = [];
for (let i = 0; i < pairs.length; i += 16) {
  const batch = pairs.slice(i, i + 16);
  const vectors = await embed(batch.flatMap((p) => [p.a, p.b]));
  batch.forEach((p, j) => results.push({ score: p.score, sim: cosine(vectors[j * 2], vectors[j * 2 + 1]) }));
  process.stdout.write(`\r${Math.min(i + 16, pairs.length)}/${pairs.length}`);
}
console.log("");
const positives = results.filter((r) => r.score >= positive);
const negatives = results.filter((r) => r.score < positive);
console.log("limiar\tfalsos avisos (pares semelhantes abaixo)\tavisos perdidos (pares diferentes acima)");
for (let t = 0.5; t <= 0.95; t += 0.05) {
  const fa = positives.filter((r) => r.sim < t).length;
  const ap = negatives.filter((r) => r.sim >= t).length;
  console.log(`${t.toFixed(2)}\t${fa}/${positives.length}\t${ap}/${negatives.length}`);
}

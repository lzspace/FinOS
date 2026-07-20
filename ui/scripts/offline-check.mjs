import { readFile, readdir } from "node:fs/promises";
import { extname, resolve } from "node:path";

const root = resolve("dist");
const files = [];
async function walk(directory) {
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) await walk(path);
    else files.push(path);
  }
}
await walk(root);
for (const file of files) {
  if (![".html", ".css", ".js", ".json"].includes(extname(file))) continue;
  const content = await readFile(file, "utf8");
  const runtimeInternalsRemoved = content
    .replaceAll(/https:\/\/react\.dev\/errors\//g, "")
    .replaceAll(/http:\/\/www\.w3\.org\/(?:2000\/svg|1998\/Math\/MathML|1999\/xlink|XML\/1998\/namespace)/g, "");
  if (/https?:\/\//i.test(runtimeInternalsRemoved)) {
    throw new Error(`External resource candidate found in ${file}`);
  }
}
console.log(`Offline check passed for ${files.length} build files`);

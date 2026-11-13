import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { Window } from "happy-dom";

const source = (await readFile(new URL("../web/dictation.js", import.meta.url), "utf8")).replace(/^import .*$/gm, "");
const { insertDictation } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

test("dictated text goes in at the caret with a separating space", () => {
  const { document } = new Window();
  const field = document.createElement("textarea");
  document.body.append(field);
  let inputs = 0;
  field.addEventListener("input", () => inputs++);
  field.value = "A girl walks. Ends here.";
  field.setSelectionRange(13, 13);
  insertDictation(field, "It rains.");
  assert.equal(field.value, "A girl walks. It rains. Ends here.");
  assert.equal(inputs, 1);
  field.value = "";
  field.setSelectionRange(0, 0);
  insertDictation(field, "First words.");
  assert.equal(field.value, "First words.");
});

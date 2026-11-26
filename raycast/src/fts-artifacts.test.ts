import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { STOPWORDS, TYPES, ftsQuery, searchSQL } from "./fts-artifacts.ts";

const ARTLIB = readFileSync(join(import.meta.dirname, "..", "..", "scripts", "artlib.py"), "utf8");
const CATS = ["work", "side", "family"];

test("same outputs as artlib.fts_query", () => {
  assert.equal(ftsQuery("what's the boat price"), '"boat"* AND "price"*');
  assert.equal(ftsQuery("what's the boat price", " OR "), '"boat"* OR "price"*');
  assert.equal(ftsQuery("the a"), "");
  assert.equal(ftsQuery('"); drop'), '"drop"*');
  assert.equal(ftsQuery("Übersicht"), '"Übersicht"*');
  assert.equal(ftsQuery("plan 2"), '"plan"* AND "2"*');
});

test("stop-words match the Python list", () => {
  const py = /STOPWORDS = frozenset\("""([\s\S]*?)"""/.exec(ARTLIB)![1].split(/\s+/).filter(Boolean);
  assert.deepEqual([...STOPWORDS].sort(), [...new Set(py)].sort());
});

test("types match artlib.TYPES", () => {
  const py = /TYPES = \(([^)]*)\)/.exec(ARTLIB)![1].match(/"(\w+)"/g)!.map((s) => s.slice(1, -1));
  assert.deepEqual([...TYPES], py);
});

test("category filter only for configured names", () => {
  assert.match(searchSQL("boat", { area: "work", categories: CATS }), /where a\.area = 'work'/);
  assert.match(searchSQL("", { area: "family", categories: CATS }), /where area = 'family' order by updated_at desc/);
  assert.doesNotMatch(searchSQL("boat", { area: "personal", categories: CATS }), /area =/);
  assert.doesNotMatch(searchSQL("boat", { area: "work" }), /area =/);
  assert.doesNotMatch(searchSQL("boat", { area: "all", categories: CATS }), /area =/);
});

test("recording mode hides private categories", () => {
  assert.match(searchSQL("", { categories: CATS, hide: ["family"] }), /where coalesce\(area, ''\) not in \('family'\) order by/);
  assert.match(
    searchSQL("boat", { categories: CATS, hide: ["family", "side"] }),
    /coalesce\(a\.area, ''\) not in \('family', 'side'\)/,
  );
  assert.doesNotMatch(searchSQL("", { categories: CATS, hide: ["x'; drop"] }), /not in/);
});

test("type filter", () => {
  assert.match(searchSQL("swipe", { type: "slides" }), /where a\.type = 'slides'/);
  assert.match(searchSQL("", { area: "work", type: "doc", categories: CATS }), /where area = 'work' and type = 'doc' order by/);
  assert.doesNotMatch(searchSQL("swipe", { type: "evil'" as never }), /type =/);
});

test("no stray single quotes in SQL", () => {
  for (const q of ["it's x", "'; drop table artifacts; --", "a'b'c"]) {
    assert.doesNotMatch(searchSQL(q).replace(/match '(?:[^']|'')*'/g, ""), /'/);
  }
});

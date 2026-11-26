// Same rule as artlib.fts_query (Python, ../../scripts/artlib.py): word tokens, drop stop-words and
// single letters, quote each with a prefix star; artifacts matching all words first, then any.
// A test reads artlib.py and fails if STOPWORDS or TYPES drift apart.

export const STOPWORDS = new Set(
  `a an the and or of to in on for with at by from is are was were be been it its this that
these those what which who whom whose how why when where do does did i me my we our you your he she they them
their there here as about into than then so not no yes can could should would will just also any some all
der die das den dem des ein eine einer eines einem und oder zu im in am an auf für mit von ist sind war wie was
wer wo wann warum ich wir du sie es nicht kein keine auch nur noch`.split(/\s+/),
);

export const TYPES = ["page", "slides", "design", "doc", "motion", "data", "sheet", "whiteboard", "other"] as const;
export type ArtifactType = "all" | (typeof TYPES)[number];

// Category names come from config.json; artlib only accepts these characters, so they are safe in SQL.
export const NAME_RE = /^[a-z0-9][a-z0-9-]{0,30}$/;

// Python's \w (artlib) has no combining marks, so neither does this.
function tokens(q: string): string[] {
  return (q.match(/[\p{L}\p{N}_]+/gu) ?? []).filter(
    (t) => !STOPWORDS.has(t.toLowerCase()) && (t.length > 1 || /^\d$/.test(t)),
  );
}

export function ftsQuery(q: string, op = " AND "): string {
  return tokens(q)
    .map((t) => `"${t}"*`)
    .join(op);
}

const RANK = "bm25(artifacts_fts, 10, 6, 5, 4, 1, 2, 2)";
const lit = (s: string) => `'${s.replace(/'/g, "''")}'`;

export type Filter = {
  area?: string; // "all" or a configured category
  type?: ArtifactType;
  categories?: readonly string[]; // configured category names
  hide?: readonly string[]; // categories hidden in recording mode
};

// All words first, then artifacts matching only some, each group by relevance (same as artlib.SEARCH_SQL).
// Empty query: newest update first. Filters apply only to known values (never raw input).
export function searchSQL(q: string, f: Filter = {}, limit = 60): string {
  const cats = (f.categories ?? []).filter((c) => NAME_RE.test(c));
  const conds: string[] = [];
  if (f.area && cats.includes(f.area)) conds.push(`{p}area = '${f.area}'`);
  if (f.type && (TYPES as readonly string[]).includes(f.type)) conds.push(`{p}type = '${f.type}'`);
  const hide = (f.hide ?? []).filter((c) => cats.includes(c));
  if (hide.length) conds.push(`coalesce({p}area, '') not in (${hide.map((c) => `'${c}'`).join(", ")})`);
  const where = (p: string) => (conds.length ? `where ${conds.join(" and ").replaceAll("{p}", p)} ` : "");
  const all = ftsQuery(q);
  if (!all) return `select * from artifacts ${where("")}order by updated_at desc limit ${limit}`;
  const any = ftsQuery(q, " OR ");
  return `with m as (select f.rowid rid, 0 grp, ${RANK} r from artifacts_fts f where artifacts_fts match ${lit(all)}),
o as (select f.rowid rid, 1 grp, ${RANK} r from artifacts_fts f where artifacts_fts match ${lit(any)} and f.rowid not in (select rid from m))
select a.* from (select * from m union all select * from o) x join artifacts a on a.rowid = x.rid ${where("a.")}order by x.grp, x.r limit ${limit}`;
}

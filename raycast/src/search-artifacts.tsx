import {
  Action,
  ActionPanel,
  Alert,
  Color,
  confirmAlert,
  getPreferenceValues,
  Icon,
  List,
  open,
  showHUD,
  showToast,
  Toast,
} from "@raycast/api";
import { runAppleScript, useCachedState, useSQL } from "@raycast/utils";
import { execFile } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import { basename, join } from "node:path";
import { useMemo, useState } from "react";
import { ArtifactType, NAME_RE, TYPES, searchSQL } from "./fts-artifacts";

const ROOT = join(homedir(), "Documents", "Artifacts");
const DB = join(ROOT, "library.db");
// The library runs on macOS's own Python (Pillow installed there by setup); Homebrew's python3 may come
// first on Raycast's PATH and lack it.
const PYTHON = "/usr/bin/python3";
const ARTLIB = join(homedir(), ".claude", "artifact-library", "scripts", "artlib.py");

type Artifact = {
  id: string;
  url: string;
  title: string;
  about: string | null;
  tags: string | null;
  area: string | null;
  kind: string | null;
  source_file: string | null;
  project_dir: string | null;
  session_id: string | null;
  host_session_id: string | null;
  session_title: string | null;
  publish_count: number;
  type: string | null;
  created_at: string;
  updated_at: string;
};

type Category = { name: string; label: string; private: boolean };

// Same order as artlib.PALETTE, so a category has the same colour in Raycast and on its placeholder card.
const COLORS = [Color.Purple, Color.Green, Color.Orange, Color.Magenta, Color.Blue, Color.Yellow];

const TYPE_LABEL: Record<string, string> = {
  page: "Page", slides: "Slides", design: "Design", doc: "Doc", motion: "Motion", data: "Data",
  sheet: "Sheet", whiteboard: "Whiteboard", other: "Other",
};

// Optional categories from ~/Documents/Artifacts/config.json (written by setup). No file: no categories.
function loadCategories(): Category[] {
  try {
    const d = JSON.parse(readFileSync(join(ROOT, "config.json"), "utf8"));
    return (Array.isArray(d.categories) ? d.categories : [])
      .filter((c: { name?: unknown }) => typeof c?.name === "string" && NAME_RE.test(c.name))
      .map((c: { name: string; label?: string; private?: boolean }) => ({
        name: c.name,
        label: c.label || c.name[0].toUpperCase() + c.name.slice(1),
        private: Boolean(c.private),
      }));
  } catch {
    return [];
  }
}

type Plan = { keywords: string; category: string; type: string; error?: string };

// Turns a sentence into keywords + category/type with Claude Haiku (artlib.py plan); token from the preference.
function askHaiku(request: string): Promise<Plan> {
  const { claudeToken } = getPreferenceValues<{ claudeToken?: string }>();
  if (!claudeToken?.trim()) return Promise.reject(new Error("setup-token missing"));
  const env = {
    ...process.env,
    PATH: `${join(homedir(), ".local", "bin")}:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin`,
    CLAUDE_CODE_OAUTH_TOKEN: claudeToken,
  };
  return new Promise((resolve, reject) =>
    execFile(PYTHON, [ARTLIB, "plan", request], { env, timeout: 60000 }, (err, stdout) => {
      try {
        const plan = JSON.parse(stdout || "{}") as Plan;
        if (plan.error || err) reject(new Error(plan.error || String(err)));
        else resolve(plan);
      } catch {
        reject(err ?? new Error("No answer from Haiku"));
      }
    }),
  );
}

const img = (a: Artifact) => join(ROOT, "img", `${a.id}.png`);
const thumb = (a: Artifact) => join(ROOT, "thumb", `${a.id}.png`);
const page = (a: Artifact) => join(ROOT, "page", `${a.id}.html`);
const date = (iso: string) =>
  new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

async function openConversation(a: Artifact) {
  if (a.host_session_id) {
    await open(`claude://code/continue?session=${encodeURIComponent(a.host_session_id)}`);
    return;
  }
  if (!a.session_id) return;
  const dir = (a.project_dir ?? homedir()).replace(/'/g, "'\\''");
  const cmd = `cd '${dir}' && claude --resume ${a.session_id}`.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
  await runAppleScript(`tell application "Terminal"
  activate
  do script "${cmd}"
end tell`);
}

function localFile(a: Artifact): string | null {
  if (a.source_file && existsSync(a.source_file)) return a.source_file;
  return existsSync(page(a)) ? page(a) : null;
}

export default function Command() {
  const [query, setQuery] = useState("");
  const [asking, setAsking] = useState(false);
  const categories = useMemo(loadCategories, []);
  const names = categories.map((c) => c.name);
  const label = (n: string | null) => categories.find((c) => c.name === n)?.label ?? n ?? "–";
  const color = (n: string) => COLORS[Math.max(0, names.indexOf(n)) % COLORS.length];
  const privateNames = categories.filter((c) => c.private).map((c) => c.name);
  // Recording mode hides private categories, e.g. for screen recordings. Only offered when one is private.
  const [recording, setRecording] = useCachedState<boolean>("recording-mode", false);
  const hide = recording ? privateNames : [];
  // One dropdown (Raycast allows one): "all", "area:<x>" or "type:<x>".
  const [filter, setFilter] = useCachedState<string>("artifact-filter", "all");
  const [kind, value] = filter.includes(":") ? filter.split(":") : ["", ""];
  const area = kind === "area" && !hide.includes(value) ? value : "all";
  const type = (kind === "type" ? value : "all") as ArtifactType;
  const dbExists = existsSync(DB);
  const { data, isLoading, permissionView, revalidate } = useSQL<Artifact>(
    DB,
    searchSQL(query, { area, type, categories: names, hide }),
    { execute: dbExists },
  );

  if (permissionView) return permissionView;
  if (!dbExists) {
    return (
      <List>
        <List.EmptyView
          title="No artifact library yet"
          description="Publish an artifact from Claude Code and it will show up here."
        />
      </List>
    );
  }

  async function ask() {
    if (!query.trim()) return;
    setAsking(true);
    const toast = await showToast({ style: Toast.Style.Animated, title: "Asking Haiku…" });
    try {
      const p = await askHaiku(query);
      const category = hide.includes(p.category) ? "all" : p.category;
      setFilter(p.type !== "all" ? `type:${p.type}` : category !== "all" ? `area:${category}` : "all");
      setQuery(p.keywords);
      toast.style = Toast.Style.Success;
      toast.title = `Haiku: ${p.keywords}`;
      toast.message = [category !== "all" ? label(category) : "", p.type !== "all" ? TYPE_LABEL[p.type] : ""]
        .filter(Boolean)
        .join(" · ");
    } catch (e) {
      toast.style = Toast.Style.Failure;
      toast.title = "Haiku could not answer";
      toast.message = /setup-token|time limit|authenticate|login|401/i.test(String(e))
        ? "Run `claude setup-token` in Terminal and paste the token into this command's settings (⌘K → Configure Command)"
        : String(e).slice(0, 200);
    } finally {
      setAsking(false);
    }
  }

  const askAction = (
    <Action title="Ask Haiku" icon={Icon.Stars} shortcut={{ modifiers: ["cmd"], key: "return" }} onAction={ask} />
  );

  const recordingAction = privateNames.length ? (
    <Action
      title={recording ? "Recording Mode: Turn Off" : "Recording Mode: Hide Private Categories"}
      icon={recording ? Icon.EyeDisabled : Icon.Eye}
      shortcut={{ modifiers: ["cmd", "shift"], key: "r" }}
      onAction={() => {
        setRecording(!recording);
        showHUD(recording ? "Showing all artifacts" : "Recording mode: private categories hidden");
      }}
    />
  ) : null;

  async function remove(a: Artifact) {
    const ok = await confirmAlert({
      title: `Remove "${a.title}" from the library?`,
      message: "The artifact on claude.ai stays. Its thumbnail and page copy go to the Trash.",
      primaryAction: { title: "Remove", style: Alert.ActionStyle.Destructive },
    });
    if (!ok) return;
    execFile(PYTHON, [ARTLIB, "remove", a.id], (err) => {
      if (err) showToast({ style: Toast.Style.Failure, title: "Could not remove", message: String(err) });
      else {
        showHUD("Removed from library");
        revalidate();
      }
    });
  }

  const visible = categories.filter((c) => !hide.includes(c.name));

  return (
    <List
      isShowingDetail
      isLoading={isLoading || asking}
      filtering={false}
      searchText={query}
      throttle
      onSearchTextChange={setQuery}
      searchBarPlaceholder={
        recording
          ? "Recording mode: private categories hidden (⌘⇧R shows all)"
          : "Search artifacts, or type a sentence and press ⌘↵ to ask Haiku"
      }
      searchBarAccessory={
        <List.Dropdown tooltip="Filter by category or type" value={filter} onChange={setFilter}>
          <List.Dropdown.Item title="All" value="all" />
          {visible.length ? (
            <List.Dropdown.Section title="Category">
              {visible.map((c) => (
                <List.Dropdown.Item key={c.name} title={c.label} value={`area:${c.name}`} />
              ))}
            </List.Dropdown.Section>
          ) : null}
          <List.Dropdown.Section title="Type">
            {TYPES.map((x) => (
              <List.Dropdown.Item key={x} title={TYPE_LABEL[x]} value={`type:${x}`} />
            ))}
          </List.Dropdown.Section>
        </List.Dropdown>
      }
    >
      <List.EmptyView
        title="Nothing matches"
        description="Press ⌘↵ to let Haiku turn your sentence into a search"
        actions={
          <ActionPanel>
            {askAction}
            {recordingAction}
          </ActionPanel>
        }
      />
      {(data ?? []).map((a) => {
        const file = localFile(a);
        const hasThumb = existsSync(thumb(a));
        const tags = [...new Set((a.tags ?? "").split(/\s+/).filter(Boolean))];
        const preview = hasThumb ? `![${a.title.replace(/[[\]]/g, "")}](${encodeURI(`file://${img(a)}`)})\n\n` : "";
        return (
          <List.Item
            key={a.id}
            id={a.id}
            icon={hasThumb ? { source: thumb(a) } : Icon.Globe}
            title={a.title}
            accessories={[
              ...(a.type && a.type !== "page" ? [{ text: TYPE_LABEL[a.type] ?? a.type }] : []),
              ...(a.area && names.includes(a.area) ? [{ tag: { value: label(a.area), color: color(a.area) } }] : []),
            ]}
            quickLook={hasThumb ? { path: img(a), name: a.title } : undefined}
            detail={
              <List.Item.Detail
                markdown={`${preview}${a.about ?? ""}`}
                metadata={
                  <List.Item.Detail.Metadata>
                    <List.Item.Detail.Metadata.Label title="Updated" text={date(a.updated_at)} />
                    <List.Item.Detail.Metadata.Label title="Created" text={date(a.created_at)} />
                    {names.length ? (
                      <List.Item.Detail.Metadata.Label title="Category" text={a.area ? label(a.area) : "–"} />
                    ) : null}
                    <List.Item.Detail.Metadata.Label title="Type" text={TYPE_LABEL[a.type ?? ""] ?? "–"} />
                    {a.publish_count > 1 ? (
                      <List.Item.Detail.Metadata.Label title="Versions" text={String(a.publish_count)} />
                    ) : null}
                    {a.project_dir ? (
                      <List.Item.Detail.Metadata.Label title="Folder" text={basename(a.project_dir)} />
                    ) : null}
                    {a.session_title ? (
                      <List.Item.Detail.Metadata.Label title="Conversation" text={a.session_title} />
                    ) : null}
                    <List.Item.Detail.Metadata.Link title="Link" target={a.url} text={a.url.replace("https://", "")} />
                    {tags.length ? (
                      <List.Item.Detail.Metadata.TagList title="Keywords">
                        {tags.map((t) => (
                          <List.Item.Detail.Metadata.TagList.Item key={t} text={t} />
                        ))}
                      </List.Item.Detail.Metadata.TagList>
                    ) : null}
                  </List.Item.Detail.Metadata>
                }
              />
            }
            actions={
              <ActionPanel>
                <Action.OpenInBrowser url={a.url} />
                {askAction}
                {recordingAction}
                <Action.CopyToClipboard
                  title="Copy Link"
                  content={a.url}
                  shortcut={{ modifiers: ["cmd", "shift"], key: "c" }}
                />
                {hasThumb ? <Action.ToggleQuickLook shortcut={{ modifiers: ["cmd"], key: "y" }} /> : null}
                {a.session_id || a.host_session_id ? (
                  <Action
                    title="Open Conversation"
                    icon={Icon.Message}
                    shortcut={{ modifiers: ["cmd"], key: "o" }}
                    onAction={() =>
                      openConversation(a).catch((e) =>
                        showToast({ style: Toast.Style.Failure, title: "Could not open conversation", message: String(e) }),
                      )
                    }
                  />
                ) : null}
                {file ? <Action.ShowInFinder path={file} shortcut={{ modifiers: ["cmd", "shift"], key: "f" }} /> : null}
                <Action
                  title="Remove from Library"
                  icon={Icon.Trash}
                  style={Action.Style.Destructive}
                  shortcut={{ modifiers: ["ctrl"], key: "x" }}
                  onAction={() => remove(a)}
                />
              </ActionPanel>
            }
          />
        );
      })}
    </List>
  );
}

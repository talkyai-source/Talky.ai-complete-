"use client";

/**
 * Campaign knowledge panel — the vectorless-RAG knowledge tree.
 *
 * Upload a .md/.txt doc; the backend parses it into a heading tree, LLM-enriches
 * each node, and retrieves it into the agent's prompt on calls. This panel lets
 * the owner:
 *   - "Test a question" → see exactly which node(s) the retriever returns,
 *   - search/filter the tree, expand/collapse all,
 *   - tune nodes: enable/disable, pin (priority), and edit heading / spoken
 *     answer / content inline,
 *   - watch which nodes get used (hit_count) + the active retrieval mode.
 *
 * Behind CAMPAIGN_KNOWLEDGE_ENABLED on the backend; when off the GET 404s and
 * this renders nothing.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    AlertCircle, BookOpen, Check, ChevronDown, ChevronRight, ChevronsDownUp,
    ChevronsUpDown, Eye, EyeOff, FileText, Loader2, Pencil, Pin, Search,
    Sparkles, Trash2, TrendingUp, Upload, X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
    api, CampaignKnowledge, KnowledgeHit, KnowledgeNode, KnowledgeSource,
} from "@/lib/api";
import { ApiClientError } from "@/lib/http-client";

const PINNED_PRIORITY = 10;

type NodeEdit = { heading: string; voice_answer: string; content: string };

function modeLook(mode: string | null | undefined): { label: string; desc: string; cls: string } {
    switch (mode) {
        case "inline":
            return { label: "Inline", desc: "Small enough that the whole tree rides in every call's prompt.", cls: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300" };
        case "map_retrieve":
            return { label: "Map + retrieve", desc: "The agent always sees the outline; details are fetched per question.", cls: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300" };
        case "retrieve":
            return { label: "Retrieve", desc: "Large KB — the best-matching sections are searched in per question.", cls: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300" };
        default:
            return { label: "No knowledge", desc: "Upload a document to give this campaign a knowledge base.", cls: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300" };
    }
}

function sourceStatusLook(status: string): { cls: string; spin?: boolean } {
    switch (status) {
        case "ready": return { cls: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300" };
        case "processing": return { cls: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300", spin: true };
        default: return { cls: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300" };
    }
}

function countNodes(nodes: KnowledgeNode[]): number {
    return nodes.reduce((n, node) => n + 1 + countNodes(node.children), 0);
}
function allIds(nodes: KnowledgeNode[]): string[] {
    return nodes.flatMap((n) => [n.id, ...allIds(n.children)]);
}
function patchNode(nodes: KnowledgeNode[], id: string, patch: Partial<KnowledgeNode>): KnowledgeNode[] {
    return nodes.map((n) => (n.id === id ? { ...n, ...patch } : { ...n, children: patchNode(n.children, id, patch) }));
}
function matchNode(n: KnowledgeNode, term: string): boolean {
    const t = term.toLowerCase();
    return [n.heading, n.summary, n.voice_answer, n.content, ...(n.keywords || [])]
        .some((s) => (s || "").toLowerCase().includes(t));
}
function filterTree(nodes: KnowledgeNode[], term: string): KnowledgeNode[] {
    if (!term.trim()) return nodes;
    const out: KnowledgeNode[] = [];
    for (const n of nodes) {
        const kids = filterTree(n.children, term);
        if (matchNode(n, term) || kids.length) out.push({ ...n, children: kids });
    }
    return out;
}

type TreeNodeProps = {
    node: KnowledgeNode;
    collapsed: Set<string>;
    busy: Set<string>;
    editingId: string | null;
    forceExpand: boolean;
    onToggleCollapse: (id: string) => void;
    onToggleEnabled: (node: KnowledgeNode) => void;
    onTogglePin: (node: KnowledgeNode) => void;
    onStartEdit: (node: KnowledgeNode) => void;
    onCancelEdit: () => void;
    onSaveEdit: (node: KnowledgeNode, patch: NodeEdit) => void;
};

function KnowledgeTreeNode(props: TreeNodeProps) {
    const { node, collapsed, busy, editingId, forceExpand } = props;
    const hasChildren = node.children.length > 0;
    const isCollapsed = !forceExpand && collapsed.has(node.id);
    const isBusy = busy.has(node.id);
    const isEditing = editingId === node.id;
    const isPinned = node.priority >= PINNED_PRIORITY;

    const [draft, setDraft] = useState<NodeEdit>({ heading: "", voice_answer: "", content: "" });
    useEffect(() => {
        if (isEditing) setDraft({
            heading: node.heading ?? "",
            voice_answer: node.voice_answer ?? "",
            content: node.content ?? "",
        });
    }, [isEditing, node.heading, node.voice_answer, node.content]);

    const answer = node.voice_answer || node.summary || "";

    return (
        <div className="border-l border-gray-200 dark:border-white/10" style={{ marginLeft: node.depth > 0 ? 12 : 0 }}>
            <div className={`group rounded-lg px-2 py-1.5 hover:bg-gray-50 dark:hover:bg-white/[0.04] ${node.enabled ? "" : "opacity-50"}`}>
                <div className="flex items-start gap-1.5">
                    <button
                        type="button"
                        onClick={() => hasChildren && props.onToggleCollapse(node.id)}
                        className={`mt-0.5 shrink-0 text-muted-foreground ${hasChildren ? "" : "invisible"}`}
                        aria-label={isCollapsed ? "Expand" : "Collapse"}
                    >
                        {isCollapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                    </button>

                    <div className="min-w-0 flex-1">
                        {!isEditing && (
                            <div className="flex items-center gap-2 flex-wrap">
                                <span className="text-sm font-medium text-gray-900 dark:text-zinc-100">{node.heading}</span>
                                {isPinned && <span title="Pinned (prioritised in retrieval)"><Pin className="h-3 w-3 text-amber-500 fill-amber-500" /></span>}
                                {node.hit_count > 0 && (
                                    <span className="inline-flex items-center gap-1 rounded-full bg-orange-100 dark:bg-orange-950/60 px-1.5 py-0.5 text-[10px] font-medium text-orange-700 dark:text-orange-300" title={`Used in ${node.hit_count} call turn(s)`}>
                                        <TrendingUp className="h-2.5 w-2.5" />{node.hit_count}
                                    </span>
                                )}
                            </div>
                        )}

                        {!isEditing && answer && <p className="mt-0.5 text-xs text-muted-foreground line-clamp-3">{answer}</p>}
                        {!isEditing && node.keywords && node.keywords.length > 0 && (
                            <div className="mt-1 flex flex-wrap gap-1">
                                {node.keywords.slice(0, 8).map((k) => (
                                    <span key={k} className="rounded bg-gray-100 dark:bg-white/10 px-1.5 py-0.5 text-[10px] text-gray-600 dark:text-zinc-400">{k}</span>
                                ))}
                            </div>
                        )}

                        {isEditing && (
                            <div className="mt-1.5 space-y-1.5">
                                <input
                                    value={draft.heading}
                                    onChange={(e) => setDraft((d) => ({ ...d, heading: e.target.value }))}
                                    placeholder="Section title"
                                    className="w-full rounded-md border border-gray-300 dark:border-white/15 bg-white dark:bg-zinc-900 px-2 py-1 text-sm font-medium text-gray-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                                />
                                <textarea
                                    value={draft.voice_answer}
                                    onChange={(e) => setDraft((d) => ({ ...d, voice_answer: e.target.value }))}
                                    rows={2}
                                    placeholder="Spoken answer — what the agent says for this topic"
                                    className="w-full rounded-md border border-gray-300 dark:border-white/15 bg-white dark:bg-zinc-900 px-2 py-1.5 text-xs text-gray-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-emerald-500"
                                />
                                <textarea
                                    value={draft.content}
                                    onChange={(e) => setDraft((d) => ({ ...d, content: e.target.value }))}
                                    rows={3}
                                    placeholder="Full content (used for retrieval matching)"
                                    className="w-full rounded-md border border-gray-300 dark:border-white/15 bg-white dark:bg-zinc-900 px-2 py-1.5 text-xs text-muted-foreground focus:outline-none focus:ring-2 focus:ring-emerald-500"
                                />
                                <div className="flex gap-1.5">
                                    <Button size="sm" onClick={() => props.onSaveEdit(node, draft)} disabled={isBusy} className="h-7 px-2 text-xs">
                                        {isBusy ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />} Save
                                    </Button>
                                    <Button size="sm" variant="ghost" onClick={props.onCancelEdit} disabled={isBusy} className="h-7 px-2 text-xs">
                                        <X className="h-3 w-3" /> Cancel
                                    </Button>
                                </div>
                            </div>
                        )}
                    </div>

                    {!isEditing && (
                        <div className="flex shrink-0 items-center gap-0.5 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
                            <button type="button" onClick={() => props.onStartEdit(node)} className="rounded p-1 text-muted-foreground hover:text-gray-900 dark:hover:text-zinc-100 hover:bg-gray-100 dark:hover:bg-white/10" title="Edit"><Pencil className="h-3.5 w-3.5" /></button>
                            <button type="button" onClick={() => props.onTogglePin(node)} disabled={isBusy} className={`rounded p-1 hover:bg-gray-100 dark:hover:bg-white/10 ${isPinned ? "text-amber-500" : "text-muted-foreground hover:text-gray-900 dark:hover:text-zinc-100"}`} title={isPinned ? "Unpin" : "Pin (prioritise)"}><Pin className={`h-3.5 w-3.5 ${isPinned ? "fill-amber-500" : ""}`} /></button>
                            <button type="button" onClick={() => props.onToggleEnabled(node)} disabled={isBusy} className="rounded p-1 text-muted-foreground hover:text-gray-900 dark:hover:text-zinc-100 hover:bg-gray-100 dark:hover:bg-white/10" title={node.enabled ? "Disable" : "Enable"}>
                                {isBusy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : node.enabled ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
                            </button>
                        </div>
                    )}
                </div>
            </div>

            {hasChildren && !isCollapsed && (
                <div className="ml-2">
                    {node.children.map((child) => (
                        <KnowledgeTreeNode key={child.id} {...props} node={child} />
                    ))}
                </div>
            )}
        </div>
    );
}

export type KnowledgePanelProps = { campaignId: string };

export function KnowledgePanel({ campaignId }: KnowledgePanelProps) {
    const [data, setData] = useState<CampaignKnowledge | null>(null);
    const [loading, setLoading] = useState(true);
    const [disabled, setDisabled] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [uploading, setUploading] = useState(false);
    const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
    const [busy, setBusy] = useState<Set<string>>(new Set());
    const [editingId, setEditingId] = useState<string | null>(null);
    const [deletingSourceId, setDeletingSourceId] = useState<string | null>(null);
    const [search, setSearch] = useState("");
    const fileRef = useRef<HTMLInputElement>(null);

    // Test-a-question
    const [testQuery, setTestQuery] = useState("");
    const [testing, setTesting] = useState(false);
    const [testHits, setTestHits] = useState<KnowledgeHit[] | null>(null);

    const refresh = useCallback(async () => {
        try {
            const res = await api.getCampaignKnowledge(campaignId);
            setData(res);
            setError(null);
        } catch (err) {
            if (err instanceof ApiClientError && err.status === 404) { setDisabled(true); return; }
            setError(err instanceof Error ? err.message : "Failed to load knowledge");
        } finally {
            setLoading(false);
        }
    }, [campaignId]);

    useEffect(() => { void refresh(); }, [refresh]);

    const markBusy = (id: string, on: boolean) =>
        setBusy((prev) => { const n = new Set(prev); if (on) n.add(id); else n.delete(id); return n; });

    const onUpload = async (file: File) => {
        setUploading(true); setError(null);
        try { await api.uploadCampaignKnowledge(campaignId, file); await refresh(); }
        catch (err) { setError(err instanceof Error ? err.message : "Upload failed"); }
        finally { setUploading(false); if (fileRef.current) fileRef.current.value = ""; }
    };

    const mutateNode = async (node: KnowledgeNode, patch: Partial<KnowledgeNode>) => {
        if (!data) return;
        const prevTree = data.tree;
        setData({ ...data, tree: patchNode(data.tree, node.id, patch) });
        markBusy(node.id, true);
        try {
            await api.updateKnowledgeNode(campaignId, node.id, patch);
        } catch (err) {
            setData({ ...data, tree: prevTree });
            setError(err instanceof Error ? err.message : "Update failed");
        } finally {
            markBusy(node.id, false);
        }
    };

    const onToggleEnabled = (node: KnowledgeNode) => mutateNode(node, { enabled: !node.enabled });
    const onTogglePin = (node: KnowledgeNode) => mutateNode(node, { priority: node.priority >= PINNED_PRIORITY ? 0 : PINNED_PRIORITY });
    const onSaveEdit = async (node: KnowledgeNode, patch: NodeEdit) => {
        await mutateNode(node, { heading: patch.heading, voice_answer: patch.voice_answer, content: patch.content });
        setEditingId(null);
    };

    const onDeleteSource = async (source: KnowledgeSource) => {
        setDeletingSourceId(source.id); setError(null);
        try { await api.deleteKnowledgeSource(campaignId, source.id); await refresh(); }
        catch (err) { setError(err instanceof Error ? err.message : "Delete failed"); }
        finally { setDeletingSourceId(null); }
    };

    const runTest = async () => {
        const q = testQuery.trim();
        if (!q) return;
        setTesting(true); setTestHits(null); setError(null);
        try { const res = await api.testCampaignKnowledge(campaignId, q, 3); setTestHits(res.hits); }
        catch (err) { setError(err instanceof Error ? err.message : "Test failed"); }
        finally { setTesting(false); }
    };

    const toggleCollapse = (id: string) =>
        setCollapsed((prev) => { const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n; });

    const nodeCount = useMemo(() => (data ? countNodes(data.tree) : 0), [data]);
    const visibleTree = useMemo(() => (data ? filterTree(data.tree, search) : []), [data, search]);

    if (disabled) return null;
    const mode = modeLook(data?.knowledge_mode);

    return (
        <div className="rounded-2xl border border-gray-200 dark:border-white/10 bg-white dark:bg-white/5 shadow-sm overflow-hidden">
            <div className="flex items-center justify-between gap-2 px-4 py-3 border-b border-gray-200 dark:border-white/10">
                <div className="flex items-center gap-2 min-w-0">
                    <BookOpen className="h-4 w-4 text-emerald-500 shrink-0" />
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-zinc-100">Knowledge base</h3>
                    {data && <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ${mode.cls}`} title={mode.desc}>{mode.label}</span>}
                    {nodeCount > 0 && <span className="text-xs text-muted-foreground">{nodeCount} sections</span>}
                </div>
                <div className="flex items-center gap-2">
                    {error && <span className="text-xs text-red-600 dark:text-red-400 truncate max-w-[30%]" title={error}>{error}</span>}
                    <input ref={fileRef} type="file" accept=".md,.txt,text/markdown,text/plain" className="hidden" onChange={(e) => { const f = e.target.files?.[0]; if (f) void onUpload(f); }} />
                    <Button size="sm" variant="outline" onClick={() => fileRef.current?.click()} disabled={uploading} className="h-8 px-2.5 text-xs">
                        {uploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Upload className="h-3.5 w-3.5" />}
                        {uploading ? "Processing…" : "Upload .md / .txt"}
                    </Button>
                </div>
            </div>

            {loading ? (
                <div className="flex items-center gap-2 px-4 py-6 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" /> Loading knowledge…</div>
            ) : !data || (data.sources.length === 0 && nodeCount === 0) ? (
                <div className="px-4 py-8 text-center">
                    <BookOpen className="mx-auto h-8 w-8 text-muted-foreground/40" />
                    <p className="mt-2 text-sm font-medium text-gray-900 dark:text-zinc-100">No knowledge yet</p>
                    <p className="mt-1 text-xs text-muted-foreground max-w-md mx-auto">Upload a Markdown or text doc — pricing, FAQs, services. We&apos;ll parse it into sections, write a spoken answer for each, and the agent will use it on calls.</p>
                </div>
            ) : (
                <div className="divide-y divide-gray-200 dark:divide-white/10">
                    {/* Test a question */}
                    <div className="px-4 py-3 bg-gray-50/60 dark:bg-white/[0.02]">
                        <div className="flex items-center gap-2">
                            <input
                                value={testQuery}
                                onChange={(e) => setTestQuery(e.target.value)}
                                onKeyDown={(e) => { if (e.key === "Enter") void runTest(); }}
                                placeholder="Test a question — e.g. “how much does it cost?”"
                                className="flex-1 rounded-md border border-gray-300 dark:border-white/15 bg-white dark:bg-zinc-900 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-emerald-500"
                            />
                            <Button size="sm" onClick={runTest} disabled={testing || !testQuery.trim()} className="h-8 px-3 text-xs">
                                {testing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Sparkles className="h-3.5 w-3.5" />} Test
                            </Button>
                        </div>
                        {testHits !== null && (
                            <div className="mt-2 space-y-1.5">
                                {testHits.length === 0 ? (
                                    <p className="text-xs text-muted-foreground">No match — the agent would say it&apos;ll follow up. Try rephrasing, or add/enable a section for this.</p>
                                ) : testHits.map((h, i) => (
                                    <div key={h.id} className="rounded-md border border-gray-200 dark:border-white/10 bg-white dark:bg-zinc-900 px-2.5 py-1.5">
                                        <div className="flex items-center gap-2">
                                            <span className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-emerald-100 text-[10px] font-semibold text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">{i + 1}</span>
                                            <span className="text-xs font-medium text-gray-900 dark:text-zinc-100">{h.heading}</span>
                                        </div>
                                        {(h.voice_answer || h.summary) && <p className="mt-0.5 pl-6 text-xs text-muted-foreground">{h.voice_answer || h.summary}</p>}
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>

                    {/* sources */}
                    {data.sources.length > 0 && (
                        <div className="px-4 py-2.5 space-y-1.5">
                            {data.sources.map((s) => {
                                const look = sourceStatusLook(s.status);
                                return (
                                    <div key={s.id} className="flex items-center gap-2 text-xs">
                                        <FileText className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                                        <span className="font-medium text-gray-900 dark:text-zinc-100 truncate">{s.filename || "document"}</span>
                                        <span className={`inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium ${look.cls}`}>{look.spin && <Loader2 className="h-2.5 w-2.5 animate-spin" />}{s.status}</span>
                                        <span className="text-muted-foreground">~{s.token_count} tokens</span>
                                        {s.status === "failed" && s.error && <span className="inline-flex items-center gap-1 text-red-600 dark:text-red-400 truncate" title={s.error}><AlertCircle className="h-3 w-3" /> {s.error}</span>}
                                        <button type="button" onClick={() => void onDeleteSource(s)} disabled={deletingSourceId === s.id} className="ml-auto rounded p-1 text-muted-foreground hover:text-red-600 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40" title="Delete this source and its sections">
                                            {deletingSourceId === s.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}
                                        </button>
                                    </div>
                                );
                            })}
                        </div>
                    )}

                    {/* tree toolbar + tree */}
                    {nodeCount > 0 && (
                        <div className="px-3 py-2">
                            <div className="mb-2 flex items-center gap-2">
                                <div className="relative flex-1">
                                    <Search className="absolute left-2 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                                    <input
                                        value={search}
                                        onChange={(e) => setSearch(e.target.value)}
                                        placeholder="Search sections…"
                                        className="w-full rounded-md border border-gray-300 dark:border-white/15 bg-white dark:bg-zinc-900 pl-7 pr-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-emerald-500"
                                    />
                                </div>
                                <button type="button" onClick={() => setCollapsed(new Set(allIds(data.tree)))} className="inline-flex items-center gap-1 rounded p-1 text-xs text-muted-foreground hover:text-gray-900 dark:hover:text-zinc-100" title="Collapse all"><ChevronsDownUp className="h-3.5 w-3.5" /></button>
                                <button type="button" onClick={() => setCollapsed(new Set())} className="inline-flex items-center gap-1 rounded p-1 text-xs text-muted-foreground hover:text-gray-900 dark:hover:text-zinc-100" title="Expand all"><ChevronsUpDown className="h-3.5 w-3.5" /></button>
                            </div>
                            {visibleTree.length === 0 ? (
                                <p className="px-2 py-4 text-xs text-muted-foreground">No sections match “{search}”.</p>
                            ) : visibleTree.map((node) => (
                                <KnowledgeTreeNode
                                    key={node.id}
                                    node={node}
                                    collapsed={collapsed}
                                    busy={busy}
                                    editingId={editingId}
                                    forceExpand={!!search.trim()}
                                    onToggleCollapse={toggleCollapse}
                                    onToggleEnabled={onToggleEnabled}
                                    onTogglePin={onTogglePin}
                                    onStartEdit={(n) => setEditingId(n.id)}
                                    onCancelEdit={() => setEditingId(null)}
                                    onSaveEdit={onSaveEdit}
                                />
                            ))}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

export default KnowledgePanel;

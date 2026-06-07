import { useState, useEffect, useCallback, useRef } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useWorkshopHost } from "@/hooks/useWorkshopHost";
import { useWebSocket } from "@/hooks/useWebSocket";
import { groupApi, knowledgeApi } from "@/services/api";
import { clearLastHostWorkshop, saveLastHostWorkshop } from "@/lib/hostSession";
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  Clock,
  Copy,
  Download,
  Edit3,
  FileText,
  Play,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
  Unlock,
  Users,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { LoadingSpinner } from "@/components/Shared/LoadingSpinner";
import { MarkdownContent } from "@/components/Shared/MarkdownContent";
import { KnowledgeUploadPanel } from "@/components/Knowledge/KnowledgeUploadPanel";
import { copyText } from "@/lib/clipboard";
import { cn } from "@/lib/utils";
import type { Answer, GroupRoundResult, RoundInfo, WSMessage } from "@/types";

const ROUND_STATUS_LABEL: Record<string, string> = {
  locked: "已锁定",
  active: "讨论中",
  input: "填写中",
  closing: "收尾中",
  completed: "已完成",
};

const ROUND_STATUS_VARIANT: Record<string, "default" | "secondary" | "outline" | "destructive"> = {
  locked: "outline",
  active: "default",
  input: "secondary",
  closing: "secondary",
  completed: "outline",
};

const AI_RESULT_BOX_CLASS = "min-h-[18rem] max-h-[32rem] overflow-y-auto";
const AI_RESULT_TEXTAREA_CLASS = "min-h-[18rem] max-h-[32rem] overflow-y-auto resize-y font-mono text-sm";

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function finalResultContent(result: GroupRoundResult) {
  return result.edited_content ?? result.original_content ?? "";
}

function resultStatusLabel(status: string) {
  if (status === "edited") return "已编辑";
  if (status === "ready") return "已完成";
  if (status === "processing") return "提炼中";
  if (status === "validation_failed") return "提炼失败";
  return "未提炼";
}

function resultStatusVariant(status: string): "default" | "secondary" | "outline" | "destructive" {
  if (status === "validation_failed") return "destructive";
  if (status === "ready" || status === "edited") return "default";
  if (status === "processing") return "secondary";
  return "outline";
}

export function HostDashboard() {
  const { id } = useParams<{ id: string }>();
  const [searchParams] = useSearchParams();
  const workshopId = id ? parseInt(id, 10) : null;
  const hostCode = searchParams.get("code");

  const {
    workshop,
    loading,
    error,
    fetchHost,
    unlockRound,
    previousRound,
    updateRoundSettings,
    startTimer,
    submitHostInput,
    editGroupResult,
    editSynthesis,
    setGroupLeader,
    exportMarkdown,
  } = useWorkshopHost(workshopId, hostCode);

  const [activeTab, setActiveTab] = useState("overview");
  const [selectedGroup, setSelectedGroup] = useState("1");
  const [selectedResultRound, setSelectedResultRound] = useState("1");
  const [editingResultId, setEditingResultId] = useState<number | null>(null);
  const [editingResultContent, setEditingResultContent] = useState("");
  const [savingResultEditId, setSavingResultEditId] = useState<number | null>(null);

  const [selectedSynthesisRound, setSelectedSynthesisRound] = useState("1");
  const [editingSynthesis, setEditingSynthesis] = useState(false);
  const [synthesisEditContent, setSynthesisEditContent] = useState("");
  const [synthesizing, setSynthesizing] = useState(false);
  const [editingSynthesisGroupId, setEditingSynthesisGroupId] = useState<number | null>(null);
  const [synthesisGroupDrafts, setSynthesisGroupDrafts] = useState<Record<number, string>>({});
  const [savingSynthesisGroupId, setSavingSynthesisGroupId] = useState<number | null>(null);

  const [roundTime, setRoundTime] = useState("");
  const [unlocking, setUnlocking] = useState(false);
  const [revertingRound, setRevertingRound] = useState(false);
  const [startingTimer, setStartingTimer] = useState(false);

  const [hostInputContent, setHostInputContent] = useState("");
  const [savingHostInput, setSavingHostInput] = useState(false);

  const [deletingDocId, setDeletingDocId] = useState<number | null>(null);

  const [exporting, setExporting] = useState(false);
  const [exportData, setExportData] = useState<{ markdown: string; filename: string } | null>(null);
  const [showExportDialog, setShowExportDialog] = useState(false);

  const [localError, setLocalError] = useState<string | null>(null);
  const [localNotice, setLocalNotice] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);
  const [aiResultUnread, setAiResultUnread] = useState(false);
  const [highlightedResultKey, setHighlightedResultKey] = useState<string | null>(null);
  const [leaderTarget, setLeaderTarget] = useState<{ groupId: number; participantId: number; name: string } | null>(null);
  const [settingLeader, setSettingLeader] = useState(false);
  const aiStatusEventRef = useRef<Record<string, { time: number; signature: string }>>({});
  const actionLocksRef = useRef<Set<string>>(new Set());

  const currentRound = workshop?.rounds[Math.max(0, (workshop?.current_round ?? 1) - 1)] ?? null;
  const selectedResultRoundInfo =
    workshop?.rounds.find((round) => round.round_number === parseInt(selectedResultRound, 10)) ?? currentRound;
  const selectedSynthesisRoundInfo =
    workshop?.rounds.find((round) => round.round_number === parseInt(selectedSynthesisRound, 10)) ?? currentRound;
  const groupNumbers = Array.from({ length: workshop?.group_count ?? 0 }, (_, index) => index + 1);
  const isCompletedView = workshop?.status === "completed";

  useEffect(() => {
    if (!workshop || !hostCode) return;
    saveLastHostWorkshop({
      workshop_id: workshop.id,
      host_code: hostCode,
      title: workshop.title,
    });
  }, [workshop?.id, workshop?.title, hostCode]);

  useEffect(() => {
    if (!error || !workshopId || !hostCode) return;
    if (/invalid host code|403/i.test(error)) {
      clearLastHostWorkshop({ workshop_id: workshopId, host_code: hostCode });
      setLocalError("主持人码无效，请重新输入");
    }
  }, [error, workshopId, hostCode]);

  useEffect(() => {
    if (!workshop || workshop.rounds.length === 0) return;
    const next = String(workshop.current_round);
    setSelectedResultRound((value) => value || next);
    setSelectedSynthesisRound((value) => value || next);
  }, [workshop?.current_round, workshop?.rounds.length]);

  useEffect(() => {
    if (!workshop || workshop.rounds.length === 0) return;
    const round = workshop.rounds[workshop.current_round - 1];
    if (round) setRoundTime(String(round.discussion_time ?? round.input_time ?? ""));
  }, [workshop?.current_round, workshop?.rounds]);

  useEffect(() => {
    if (!currentRound) return;
    setHostInputContent(currentRound.host_input?.content ?? "");
  }, [currentRound?.id, currentRound?.host_input?.content]);

  useEffect(() => {
    if (!localError) return;
    const timer = window.setTimeout(() => setLocalError(null), 5000);
    return () => window.clearTimeout(timer);
  }, [localError]);

  useEffect(() => {
    if (!localNotice) return;
    const timer = window.setTimeout(() => setLocalNotice(null), 5000);
    return () => window.clearTimeout(timer);
  }, [localNotice]);

  useEffect(() => {
    if (!highlightedResultKey) return;
    const timer = window.setTimeout(() => setHighlightedResultKey(null), 5000);
    return () => window.clearTimeout(timer);
  }, [highlightedResultKey]);

  useEffect(() => {
    if (activeTab === "group-results") setAiResultUnread(false);
  }, [activeTab]);

  const handleWSMessage = useCallback((msg: WSMessage) => {
    if (msg.type === "ai_result_status") {
      const groupId = Number(msg.data.group_id);
      const roundNumber = Number(msg.data.round_number);
      const status = String(msg.data.status ?? "");
      const reason = typeof msg.data.validation_error === "string" ? msg.data.validation_error : "";
      const eventKey = `${roundNumber}:${groupId}`;
      const signature = `${status}:${reason}`;
      const now = Date.now();
      const last = aiStatusEventRef.current[eventKey];
      const isDuplicate = last && last.signature === signature && now - last.time < 12_000;
      aiStatusEventRef.current[eventKey] = { time: now, signature };

      fetchHost();
      setAiResultUnread(activeTab !== "group-results");
      setHighlightedResultKey(eventKey);

      if (!isDuplicate && Number.isFinite(groupId) && Number.isFinite(roundNumber)) {
        if (status === "processing") {
          setLocalNotice(`第 ${groupId} 组 AI 提炼已开始`);
        } else if (status === "validation_failed") {
          setLocalError(`第 ${groupId} 组 AI 提炼失败：${reason || "未知原因"}`);
        } else if (status === "ready" || status === "edited") {
          setLocalNotice(`第 ${groupId} 组 AI 提炼已完成`);
        }
      }
      return;
    }

    if (["result_ready", "synthesis_ready", "round_changed", "new_answer", "timer", "workshop_completed"].includes(msg.type)) {
      fetchHost();
    }
  }, [activeTab, fetchHost]);

  useWebSocket({
    workshopId,
    channel: "host",
    onMessage: handleWSMessage,
    enabled: !!workshopId && !!hostCode,
  });

  const clearLocalError = () => setLocalError(null);

  const getErrorMessage = (err: unknown, fallback: string) => {
    if (!(err instanceof Error)) return fallback;
    return err.message.replace(/^API error \d+:\s*/, "") || fallback;
  };

  const runLocked = useCallback(async <T,>(key: string, task: () => Promise<T>): Promise<T | null> => {
    if (actionLocksRef.current.has(key)) return null;
    actionLocksRef.current.add(key);
    try {
      return await task();
    } finally {
      actionLocksRef.current.delete(key);
    }
  }, []);

  const handleCopyText = async (text: string | null | undefined, key: string) => {
    const content = text?.trim();
    if (!content) {
      setLocalError("没有可复制的内容");
      return;
    }
    try {
      const copied = await copyText(content);
      if (!copied) {
        setLocalError("复制失败，请手动复制");
        return;
      }
      setCopiedKey(key);
      window.setTimeout(() => setCopiedKey(null), 1500);
    } catch {
      setLocalError("复制失败，请重试");
    }
  };

  const handleConfirmSetLeader = async () => {
    if (!leaderTarget) return;
    setSettingLeader(true);
    try {
      const result = await runLocked("set-group-leader", () => setGroupLeader(leaderTarget.groupId, leaderTarget.participantId));
      if (result) {
        setLocalNotice(`已将 ${leaderTarget.name} 设置为第 ${leaderTarget.groupId} 组组长`);
        setLeaderTarget(null);
      }
    } finally {
      setSettingLeader(false);
    }
  };

  const copyButton = (text: string | null | undefined, key: string) => (
    <Button
      type="button"
      size="sm"
      variant="outline"
      className="gap-1"
      onClick={() => handleCopyText(text, key)}
    >
      {copiedKey === key ? <CheckCircle2 className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
      {copiedKey === key ? "已复制" : "复制"}
    </Button>
  );

  const handleUnlockRound = async () => {
    setUnlocking(true);
    clearLocalError();
    try {
      const result = await runLocked("unlock-round", unlockRound);
      if (!result) setLocalError("进入下一轮失败，请重试");
    } finally {
      setUnlocking(false);
    }
  };

  const handlePreviousRound = async () => {
    setRevertingRound(true);
    clearLocalError();
    try {
      const result = await runLocked("previous-round", previousRound);
      if (!result) setLocalError("回到上一轮失败，请重试");
    } finally {
      setRevertingRound(false);
    }
  };

  const handleUpdateSettings = async () => {
    clearLocalError();
    const minutes = roundTime ? parseInt(roundTime, 10) : undefined;
    const result = await runLocked("round-settings", () => updateRoundSettings(minutes, minutes));
    if (!result) setLocalError("更新设置失败，请重试");
  };

  const handleStartTimer = async () => {
    setStartingTimer(true);
    clearLocalError();
    try {
      const result = await runLocked("start-timer", startTimer);
      if (!result) setLocalError("开始计时失败，请重试");
    } finally {
      setStartingTimer(false);
    }
  };

  const handleStartEditResult = (result: GroupRoundResult) => {
    if (isCompletedView) return;
    setEditingResultId(result.id);
    setEditingResultContent(result.edited_content ?? result.original_content ?? "");
  };

  const handleSaveEditResult = async () => {
    if (isCompletedView) return;
    if (editingResultId === null) return;
    clearLocalError();
    setSavingResultEditId(editingResultId);
    try {
      const result = await runLocked(`edit-group-result:${editingResultId}`, () => editGroupResult(editingResultId, editingResultContent));
      if (result) {
        setEditingResultId(null);
        setEditingResultContent("");
        fetchHost();
      } else {
        setLocalError("保存编辑结果失败");
      }
    } finally {
      setSavingResultEditId(null);
    }
  };

  const handleTriggerSynthesis = async () => {
    if (isCompletedView) return;
    if (!selectedSynthesisRoundInfo) return;
    setSynthesizing(true);
    clearLocalError();
    try {
      const result = await runLocked(`synthesize:${selectedSynthesisRoundInfo.id}`, async () => {
        await groupApi.synthesize(selectedSynthesisRoundInfo.id);
        await fetchHost();
        return true;
      });
      if (!result) return;
    } catch (err) {
      setLocalError(getErrorMessage(err, "综合提炼失败，请重试"));
    } finally {
      setSynthesizing(false);
    }
  };

  const handleStartEditSynthesis = () => {
    if (isCompletedView) return;
    if (!selectedSynthesisRoundInfo?.synthesis) return;
    setSynthesisEditContent(
      selectedSynthesisRoundInfo.synthesis.edited_content ??
      selectedSynthesisRoundInfo.synthesis.original_content ??
      "",
    );
    setEditingSynthesis(true);
  };

  const handleSaveEditSynthesis = async () => {
    if (isCompletedView) return;
    if (!selectedSynthesisRoundInfo) return;
    clearLocalError();
    const result = await runLocked(`edit-synthesis:${selectedSynthesisRoundInfo.id}`, () => editSynthesis(selectedSynthesisRoundInfo.id, synthesisEditContent));
    if (result) {
      setEditingSynthesis(false);
      fetchHost();
    } else {
      setLocalError("保存综合编辑失败");
    }
  };

  const handleSaveHostInput = async () => {
    if (isCompletedView) return;
    if (!currentRound) return;
    setSavingHostInput(true);
    clearLocalError();
    try {
      const result = await runLocked(`host-input:${currentRound.id}`, () => submitHostInput(currentRound.id, hostInputContent));
      if (!result) setLocalError("保存主持人输入失败");
    } finally {
      setSavingHostInput(false);
    }
  };

  const uploadKnowledgeFile = async (file: File, contentBase64: string) => {
    if (!workshop) throw new Error("缺少会议信息，请刷新后重试");
    clearLocalError();
    return knowledgeApi.upload(
      file.name,
      contentBase64,
      file.type || "application/octet-stream",
      workshop.id,
      workshop.kb_admin_code,
    );
  };

  const handleDeleteDoc = async (docId: number) => {
    if (!workshop) return;
    setDeletingDocId(docId);
    clearLocalError();
    try {
      await knowledgeApi.delete(docId, workshop.id, workshop.kb_admin_code);
      await fetchHost();
    } catch (err) {
      setLocalError(getErrorMessage(err, "删除失败"));
    }
    setDeletingDocId(null);
  };

  const handleExport = async () => {
    setExporting(true);
    clearLocalError();
    try {
      const data = await runLocked("export", exportMarkdown);
      if (data) {
        setExportData(data);
        setShowExportDialog(true);
      } else {
        setLocalError("导出失败");
      }
    } finally {
      setExporting(false);
    }
  };

  const handleStartEditSynthesisGroup = (result: GroupRoundResult) => {
    if (isCompletedView) return;
    setEditingSynthesisGroupId(result.id);
    setSynthesisGroupDrafts((prev) => ({
      ...prev,
      [result.id]: result.edited_content ?? result.original_content ?? "",
    }));
  };

  const handleCancelEditSynthesisGroup = () => {
    setEditingSynthesisGroupId(null);
  };

  const handleSaveSynthesisGroupResult = async (result: GroupRoundResult) => {
    if (isCompletedView) return;
    const draft = (synthesisGroupDrafts[result.id] ?? "").trim();
    if (!draft) {
      setLocalError("编辑内容不能为空");
      return;
    }
    clearLocalError();
    setSavingSynthesisGroupId(result.id);
    try {
      const updated = await runLocked(`edit-synthesis-group:${result.id}`, () => editGroupResult(result.id, draft));
      if (updated) {
        setEditingSynthesisGroupId(null);
        await fetchHost();
      } else {
        setLocalError("保存各组 AI 提炼结果编辑失败");
      }
    } finally {
      setSavingSynthesisGroupId(null);
    }
  };

  const handleDownloadExport = () => {
    if (!exportData) return;
    const blob = new Blob([exportData.markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = exportData.filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  };

  const answersForQuestion = (round: RoundInfo, questionId: number, groupId: number): Answer[] =>
    round.answers.filter((answer) => answer.question_id === questionId && answer.group_id === groupId);

  if (loading && !workshop) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-3.5rem)]">
        <LoadingSpinner size="lg" text="加载主持人面板..." />
      </div>
    );
  }

  if (error && !workshop) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-3.5rem)]">
        <div className="flex flex-col items-center gap-4 text-center max-w-sm">
          <AlertTriangle className="h-12 w-12 text-destructive" />
          <h2 className="text-xl font-semibold">加载失败</h2>
          <p className="text-muted-foreground">{localError ?? error}</p>
          <Button variant="outline" onClick={fetchHost}>
            <RefreshCw className="h-4 w-4 mr-2" />
            重试
          </Button>
        </div>
      </div>
    );
  }

  if (!workshop) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-3.5rem)]">
        <div className="flex flex-col items-center gap-4 text-center max-w-sm">
          <AlertTriangle className="h-12 w-12 text-muted-foreground" />
          <h2 className="text-xl font-semibold">研讨会未找到</h2>
          <p className="text-muted-foreground">请检查链接或主持人码是否正确</p>
          <Button variant="outline" onClick={fetchHost}>
            <RefreshCw className="h-4 w-4 mr-2" />
            重试
          </Button>
        </div>
      </div>
    );
  }

  const selectedGroupId = parseInt(selectedGroup, 10);

  return (
    <div className="min-h-[calc(100vh-3.5rem)] flex flex-col">
      <header className="border-b bg-card px-6 py-4 shrink-0">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div>
            <Link to="/" className="text-xl font-bold hover:text-primary transition-colors">
              {workshop.title}
            </Link>
            <p className="text-sm text-muted-foreground">
              主持人: {workshop.host_name} &middot;
              邀请码: <code className="font-mono bg-muted px-1 rounded">{workshop.invite_code}</code> &middot;
              第 {workshop.current_round} / 4 轮
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant={workshop.status === "active" ? "default" : "secondary"}>
              {workshop.status === "active" ? "进行中" : "已结束"}
            </Badge>
            <Button variant="outline" size="sm" onClick={fetchHost}>
              <RefreshCw className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </header>

      {localError && (
        <div className="bg-destructive/10 text-destructive text-sm px-6 py-2">
          <div className="max-w-7xl mx-auto">{localError}</div>
        </div>
      )}
      {localNotice && (
        <div className="bg-primary/10 text-primary text-sm px-6 py-2">
          <div className="max-w-7xl mx-auto">{localNotice}</div>
        </div>
      )}
      {isCompletedView && (
        <div className="bg-muted px-6 py-2 text-sm text-muted-foreground">
          <div className="max-w-7xl mx-auto">本次研讨已结束，当前仅支持查看会议资料。</div>
        </div>
      )}

      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col">
        <div className="border-b px-6 flex justify-center">
          <TabsList className="h-12 flex-wrap justify-center">
            <TabsTrigger value="overview">总览</TabsTrigger>
            <TabsTrigger value="round-control">轮次管理</TabsTrigger>
            <TabsTrigger value="group-results" className="relative">
              各组成果
              {aiResultUnread && (
                <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-destructive" />
              )}
            </TabsTrigger>
            <TabsTrigger value="synthesis">综合汇总</TabsTrigger>
            <TabsTrigger value="host-input">主持人输入</TabsTrigger>
            <TabsTrigger value="knowledge">知识库</TabsTrigger>
            <TabsTrigger value="export">导出</TabsTrigger>
          </TabsList>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-6">
          <div className="max-w-7xl mx-auto">
            <TabsContent value="overview" className="mt-0 space-y-6">
              <Card>
                <CardHeader>
                  <CardTitle className="text-lg">研讨会信息</CardTitle>
                </CardHeader>
                <CardContent className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
                  <div>
                    <p className="text-muted-foreground">标题</p>
                    <p className="font-medium">{workshop.title}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">主持人</p>
                    <p className="font-medium">{workshop.host_name}</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">当前轮次</p>
                    <p className="font-medium">{workshop.current_round} / 4</p>
                  </div>
                  <div>
                    <p className="text-muted-foreground">状态</p>
                    <Badge variant={workshop.status === "active" ? "default" : "secondary"}>
                      {workshop.status === "active" ? "进行中" : "已结束"}
                    </Badge>
                  </div>
                  <div>
                    <p className="text-muted-foreground">邀请码</p>
                    <code className="font-mono bg-muted px-1.5 py-0.5 rounded text-sm">{workshop.invite_code}</code>
                  </div>
                  <div>
                    <p className="text-muted-foreground">主持人码</p>
                    <code className="font-mono bg-muted px-1.5 py-0.5 rounded text-sm">{workshop.host_code}</code>
                  </div>
                  <div>
                    <p className="text-muted-foreground">创建时间</p>
                    <p className="font-medium">{new Date(workshop.created_at).toLocaleString("zh-CN")}</p>
                  </div>
                </CardContent>
              </Card>

              <div>
                <h3 className="text-base font-semibold mb-3">分组概览</h3>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  {workshop.groups.map((group) => (
                    <Card key={group.group_id}>
                      <CardContent className="p-4 flex items-center gap-4">
                        <div className="h-12 w-12 rounded-full bg-primary/10 flex items-center justify-center text-primary font-bold text-lg shrink-0">
                          {group.group_id}
                        </div>
                        <div className="min-w-0">
                          <p className="font-semibold">第 {group.group_id} 组</p>
                          <p className="text-sm text-muted-foreground">
                            <Users className="h-3.5 w-3.5 inline mr-1" />
                            {group.participant_count} 人
                          </p>
                          <p className="text-sm text-muted-foreground">
                            组长: {group.leader_name ?? "暂无组长"}
                          </p>
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {group.members.length > 0 ? (
                              group.members.map((member) => (
                                <button
                                  key={member.id}
                                  type="button"
                                  disabled={isCompletedView || member.is_group_leader}
                                  onClick={() => setLeaderTarget({ groupId: group.group_id, participantId: member.id, name: member.name })}
                                  className={cn(
                                    "inline-flex max-w-full items-center gap-1 rounded-full border px-2 py-0.5 text-xs transition-colors",
                                    member.is_group_leader
                                      ? "border-primary bg-primary text-primary-foreground"
                                      : "border-border bg-background text-foreground",
                                    !isCompletedView && !member.is_group_leader && "cursor-pointer hover:border-primary hover:bg-primary/5",
                                    (isCompletedView || member.is_group_leader) && "cursor-default",
                                  )}
                                  title={
                                    isCompletedView
                                      ? "已结束会议仅支持查看"
                                      : member.is_group_leader
                                        ? "当前组长"
                                        : `设置 ${member.name} 为第 ${group.group_id} 组组长`
                                  }
                                >
                                  <span className="truncate">{member.name}</span>
                                  {member.is_group_leader && <span className="text-[10px]">队长</span>}
                                </button>
                              ))
                            ) : (
                              <span className="text-sm text-muted-foreground">暂无成员</span>
                            )}
                          </div>
                        </div>
                      </CardContent>
                    </Card>
                  ))}
                </div>
              </div>

              {currentRound && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-lg">当前轮次</CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    <div className="flex items-center gap-2">
                      <span className="text-muted-foreground">第 {currentRound.round_number} 轮:</span>
                      <span className="font-medium">{currentRound.title}</span>
                      <Badge variant={ROUND_STATUS_VARIANT[currentRound.status] ?? "outline"}>
                        {ROUND_STATUS_LABEL[currentRound.status] ?? currentRound.status}
                      </Badge>
                    </div>
                    {currentRound.objective && <p className="text-muted-foreground">{currentRound.objective}</p>}
                    <span className="text-muted-foreground">
                      <Clock className="h-3.5 w-3.5 inline mr-1" />
                      本轮时长: {currentRound.discussion_time} 分钟
                    </span>
                  </CardContent>
                </Card>
              )}
            </TabsContent>

            <TabsContent value="round-control" className="mt-0 space-y-6">
              {!currentRound ? (
                <Card>
                  <CardContent className="p-8 text-center text-muted-foreground">暂无轮次数据</CardContent>
                </Card>
              ) : (
                <>
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-lg">当前轮次信息</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium">第 {currentRound.round_number} 轮: {currentRound.title}</span>
                        <Badge variant={ROUND_STATUS_VARIANT[currentRound.status] ?? "outline"}>
                          {ROUND_STATUS_LABEL[currentRound.status] ?? currentRound.status}
                        </Badge>
                      </div>
                      {currentRound.objective && <p className="text-sm text-muted-foreground">{currentRound.objective}</p>}
                      {workshop.status === "active" && !isCompletedView && (
                        <div className="flex flex-wrap gap-2">
                          <Button
                            variant="outline"
                            onClick={handlePreviousRound}
                            disabled={isCompletedView || revertingRound || workshop.is_review_mode || (workshop.flow_round_number ?? workshop.current_round) <= 1}
                            className="gap-2"
                          >
                            {revertingRound ? <LoadingSpinner size="sm" /> : <RefreshCw className="h-4 w-4" />}
                            回到上一轮
                          </Button>
                          <Button onClick={handleUnlockRound} disabled={isCompletedView || unlocking} className="gap-2">
                            {unlocking ? <LoadingSpinner size="sm" /> : <Unlock className="h-4 w-4" />}
                            {(workshop.flow_round_number ?? currentRound.round_number) < 4 ? "进入下一轮" : "结束研讨"}
                          </Button>
                        </div>
                      )}
                      {workshop.is_review_mode && (
                        <p className="text-sm text-primary">
                          当前为历史轮次查看模式。点击主流程按钮将回到原主流程并继续推进。
                        </p>
                      )}
                      {(currentRound.status === "active" || currentRound.status === "input") && (
                        <div className="flex flex-wrap items-center gap-3">
                          <Button onClick={handleStartTimer} disabled={isCompletedView || startingTimer} className="gap-2">
                            {startingTimer ? <LoadingSpinner size="sm" /> : <Play className="h-4 w-4" />}
                            {currentRound.timer_started_at ? "重新开始计时" : "开始计时"}
                          </Button>
                          {currentRound.timer_remaining_seconds !== null && (
                            <span className="text-sm text-muted-foreground">
                              剩余 {Math.floor(currentRound.timer_remaining_seconds / 60)}:
                              {String(currentRound.timer_remaining_seconds % 60).padStart(2, "0")}
                            </span>
                          )}
                        </div>
                      )}
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader>
                      <CardTitle className="text-lg">计时设置</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      <div className="max-w-xs space-y-2">
                        <Label htmlFor="round-time">本轮时长 (分钟)</Label>
                        <Input
                          id="round-time"
                          type="number"
                          min={1}
                          value={roundTime}
                          onChange={(event) => setRoundTime(event.target.value)}
                          placeholder="本轮时长"
                          disabled={isCompletedView || workshop.is_review_mode}
                        />
                      </div>
                      <Button onClick={handleUpdateSettings} variant="outline" className="gap-2" disabled={isCompletedView || workshop.is_review_mode || actionLocksRef.current.has("round-settings")}>
                        <Save className="h-4 w-4" />
                        保存设置
                      </Button>
                    </CardContent>
                  </Card>
                </>
              )}
            </TabsContent>

            <TabsContent value="group-results" className="mt-0 space-y-6">
              <div className="flex items-center gap-4 flex-wrap">
                <div className="flex items-center gap-2">
                  <Label className="shrink-0">选择轮次:</Label>
                  <Select value={selectedResultRound} onValueChange={setSelectedResultRound}>
                    <SelectTrigger className="w-48"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {workshop.rounds.map((round) => (
                        <SelectItem key={round.id} value={String(round.round_number)}>
                          第 {round.round_number} 轮: {round.title}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="flex items-center gap-2">
                  <Label className="shrink-0">选择小组:</Label>
                  <Select value={selectedGroup} onValueChange={setSelectedGroup}>
                    <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {workshop.groups.map((group) => (
                        <SelectItem key={group.group_id} value={String(group.group_id)}>
                          第 {group.group_id} 组
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              {selectedResultRoundInfo && (
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
                  {groupNumbers.map((groupId) => {
                    const result = selectedResultRoundInfo.group_results.find((item) => item.group_id === groupId);
                    const status = result?.status ?? "pending";
                    const highlightKey = `${selectedResultRoundInfo.round_number}:${groupId}`;
                    return (
                      <button
                        key={groupId}
                        type="button"
                        onClick={() => setSelectedGroup(String(groupId))}
                        className={cn(
                          "rounded-md border bg-card p-3 text-left transition-colors",
                          selectedGroupId === groupId && "border-primary bg-primary/5",
                          highlightedResultKey === highlightKey && "border-primary bg-primary/10 shadow-sm",
                        )}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-sm font-medium">第 {groupId} 组</span>
                          <Badge variant={resultStatusVariant(status)}>{resultStatusLabel(status)}</Badge>
                        </div>
                        <p className="mt-2 text-xs text-muted-foreground">
                          最近更新：
                          {result?.updated_at ? new Date(result.updated_at).toLocaleString("zh-CN") : "暂无"}
                        </p>
                        {result?.validation_error && (
                          <p className="mt-1 line-clamp-2 text-xs text-destructive">{result.validation_error}</p>
                        )}
                      </button>
                    );
                  })}
                </div>
              )}

              {!selectedResultRoundInfo ? (
                <Card>
                  <CardContent className="p-8 text-center text-muted-foreground">暂无轮次数据</CardContent>
                </Card>
              ) : (
                <div className="space-y-4">
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base">问题与成员回答</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      {selectedResultRoundInfo.questions.length === 0 ? (
                        <p className="text-sm text-muted-foreground">本轮暂无问题</p>
                      ) : (
                        selectedResultRoundInfo.questions.map((question, index) => {
                          const questionAnswers = answersForQuestion(selectedResultRoundInfo, question.id, selectedGroupId);
                          return (
                            <div key={question.id} className="rounded-md border p-3 space-y-2">
                              <p className="text-sm font-medium">Q{index + 1}: {question.content}</p>
                              {questionAnswers.length === 0 ? (
                                <p className="text-sm text-muted-foreground">该组暂无回答</p>
                              ) : (
                                <div className="space-y-2">
                                  {questionAnswers.map((answer) => (
                                    <div key={answer.id} className="rounded-md bg-muted/30 p-2 text-sm">
                                      <div className="text-xs text-muted-foreground mb-1">
                                        {answer.participant_name ?? `成员 ${answer.participant_id}`} · {new Date(answer.created_at).toLocaleString("zh-CN")}
                                      </div>
                                      <div className="whitespace-pre-wrap">{answer.content}</div>
                                    </div>
                                  ))}
                                </div>
                              )}
                            </div>
                          );
                        })
                      )}
                    </CardContent>
                  </Card>

                  {selectedResultRoundInfo.group_results
                    .filter((result) => result.group_id === selectedGroupId)
                    .map((result) => (
                      <Card key={result.id}>
                        <CardHeader className="pb-3">
                          <div className="flex items-center justify-between gap-3">
                            <CardTitle className="text-base">
                              第 {selectedGroupId} 组 AI 提炼结果
                              <Badge className="ml-2" variant={resultStatusVariant(result.status)}>
                                {resultStatusLabel(result.status)}
                              </Badge>
                              <span className="ml-2 text-xs font-normal text-muted-foreground">
                                {new Date(result.updated_at).toLocaleString("zh-CN")}
                              </span>
                            </CardTitle>
                            <Button size="sm" variant="outline" onClick={() => handleStartEditResult(result)} disabled={isCompletedView} className="gap-1">
                              <Edit3 className="h-4 w-4" />
                              编辑最终结果
                            </Button>
                          </div>
                        </CardHeader>
                        <CardContent className="space-y-4">
                          {editingResultId === result.id ? (
                            <div className="space-y-3">
                              <Textarea
                                value={editingResultContent}
                                onChange={(event) => setEditingResultContent(event.target.value)}
                                className={AI_RESULT_TEXTAREA_CLASS}
                                disabled={isCompletedView}
                              />
                              <div className="flex gap-2">
                                <Button size="sm" onClick={handleSaveEditResult} disabled={isCompletedView || savingResultEditId === result.id} className="gap-1">
                                  {savingResultEditId === result.id ? <LoadingSpinner size="sm" /> : <Save className="h-4 w-4" />}
                                  {savingResultEditId === result.id ? "保存中..." : "保存"}
                                </Button>
                                <Button size="sm" variant="outline" onClick={() => setEditingResultId(null)} disabled={savingResultEditId === result.id} className="gap-1">
                                  <X className="h-4 w-4" />
                                  取消
                                </Button>
                              </div>
                            </div>
                          ) : (
                            <>
                              <div className="space-y-2">
                                <div className="flex items-center justify-between">
                                  <h4 className="text-sm font-medium">原始 AI 提炼结果</h4>
                                  {copyButton(result.original_content, `group-${result.id}-original`)}
                                </div>
                                <MarkdownContent content={result.original_content} className={AI_RESULT_BOX_CLASS} />
                              </div>
                              <div className="space-y-2">
                                <div className="flex items-center justify-between">
                                  <h4 className="text-sm font-medium">编辑后的 AI 提炼结果</h4>
                                  {copyButton(result.edited_content ?? result.original_content, `group-${result.id}-edited`)}
                                </div>
                                <MarkdownContent
                                  content={result.edited_content}
                                  emptyText="(未编辑)"
                                  className={AI_RESULT_BOX_CLASS}
                                />
                              </div>
                              {result.validation_error && (
                                <p className="text-sm text-destructive">失败原因: {result.validation_error}</p>
                              )}
                            </>
                          )}
                        </CardContent>
                      </Card>
                    ))}

                  {selectedResultRoundInfo.group_results.filter((result) => result.group_id === selectedGroupId).length === 0 && (
                    <Card>
                      <CardContent className="p-8 text-center text-muted-foreground">该组暂无 AI 结果</CardContent>
                    </Card>
                  )}
                </div>
              )}
            </TabsContent>

            <TabsContent value="synthesis" className="mt-0 space-y-6">
              <div className="flex items-center gap-4 flex-wrap">
                <div className="flex items-center gap-2">
                  <Label className="shrink-0">选择轮次:</Label>
                  <Select value={selectedSynthesisRound} onValueChange={setSelectedSynthesisRound}>
                    <SelectTrigger className="w-56"><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {workshop.rounds.map((round) => (
                        <SelectItem key={round.id} value={String(round.round_number)}>
                          第 {round.round_number} 轮: {round.title}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <Button onClick={handleTriggerSynthesis} disabled={isCompletedView || synthesizing || !selectedSynthesisRoundInfo} className="gap-2">
                  {synthesizing ? <LoadingSpinner size="sm" /> : <Sparkles className="h-4 w-4" />}
                  综合提炼
                </Button>
              </div>

              {!selectedSynthesisRoundInfo ? (
                <Card>
                  <CardContent className="p-8 text-center text-muted-foreground">暂无轮次数据</CardContent>
                </Card>
              ) : (
                <div className="space-y-4">
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base">各组最终提交的 AI 提炼结果</CardTitle>
                    </CardHeader>
                    <CardContent className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                      {groupNumbers.map((groupId) => {
                        const result = selectedSynthesisRoundInfo.group_results.find((item) => item.group_id === groupId);
                        const content = result ? finalResultContent(result) : "";
                        const isEditingGroupResult = Boolean(result && editingSynthesisGroupId === result.id);
                        const isSavingGroupResult = Boolean(result && savingSynthesisGroupId === result.id);
                        return (
                          <div key={groupId} className="rounded-md border p-3 space-y-2">
                            <div className="flex items-center justify-between gap-2">
                              <div className="flex items-center gap-2">
                                <h4 className="text-sm font-medium">第 {groupId} 组</h4>
                                {result && (
                                  <Badge variant={result.status === "edited" ? "default" : "outline"}>
                                    {result.status === "edited" ? "已编辑" : "原始结果"}
                                  </Badge>
                                )}
                              </div>
                              {result && !isEditingGroupResult && (
                                <div className="flex gap-2">
                                  {copyButton(content, `synthesis-round-${selectedSynthesisRoundInfo.id}-group-${groupId}`)}
                                  <Button size="sm" variant="outline" onClick={() => handleStartEditSynthesisGroup(result)} disabled={isCompletedView} className="gap-1">
                                    <Edit3 className="h-4 w-4" />
                                    编辑
                                  </Button>
                                </div>
                              )}
                            </div>
                            {result && isEditingGroupResult ? (
                              <div className="space-y-2">
                                <Textarea
                                  value={synthesisGroupDrafts[result.id] ?? content}
                                  onChange={(event) => setSynthesisGroupDrafts((prev) => ({ ...prev, [result.id]: event.target.value }))}
                                  className={AI_RESULT_TEXTAREA_CLASS}
                                  disabled={isSavingGroupResult}
                                />
                                <div className="flex gap-2">
                                  <Button size="sm" onClick={() => handleSaveSynthesisGroupResult(result)} disabled={isSavingGroupResult} className="gap-1">
                                    {isSavingGroupResult ? <LoadingSpinner size="sm" /> : <Save className="h-4 w-4" />}
                                    {isSavingGroupResult ? "保存中..." : "保存"}
                                  </Button>
                                  <Button size="sm" variant="outline" onClick={handleCancelEditSynthesisGroup} disabled={isSavingGroupResult} className="gap-1">
                                    <X className="h-4 w-4" />
                                    取消
                                  </Button>
                                </div>
                              </div>
                            ) : (
                              <MarkdownContent
                                content={content}
                                emptyText="暂无提交结果"
                                className={cn(AI_RESULT_BOX_CLASS, "p-3")}
                              />
                            )}
                          </div>
                        );
                      })}
                    </CardContent>
                  </Card>

                  {selectedSynthesisRoundInfo.synthesis ? (
                    <Card>
                      <CardHeader>
                        <div className="flex items-center justify-between gap-3">
                          <CardTitle className="text-base">
                            综合提炼结果
                            <Badge className="ml-2" variant={selectedSynthesisRoundInfo.synthesis.status === "edited" ? "default" : selectedSynthesisRoundInfo.synthesis.status === "ready" ? "secondary" : "outline"}>
                              {resultStatusLabel(selectedSynthesisRoundInfo.synthesis.status)}
                            </Badge>
                          </CardTitle>
                          <div className="flex gap-2">
                            {copyButton(
                              selectedSynthesisRoundInfo.synthesis.edited_content ?? selectedSynthesisRoundInfo.synthesis.original_content,
                              `synthesis-${selectedSynthesisRoundInfo.synthesis.id}`,
                            )}
                            <Button size="sm" variant="outline" onClick={handleStartEditSynthesis} disabled={isCompletedView} className="gap-1">
                              <Edit3 className="h-4 w-4" />
                              编辑
                            </Button>
                          </div>
                        </div>
                      </CardHeader>
                      <CardContent>
                        {editingSynthesis ? (
                          <div className="space-y-3">
                            <Textarea
                              value={synthesisEditContent}
                              onChange={(event) => setSynthesisEditContent(event.target.value)}
                              className={AI_RESULT_TEXTAREA_CLASS}
                              disabled={isCompletedView}
                            />
                            <div className="flex gap-2">
                              <Button size="sm" onClick={handleSaveEditSynthesis} disabled={isCompletedView} className="gap-1">
                                <Save className="h-4 w-4" />
                                保存
                              </Button>
                              <Button size="sm" variant="outline" onClick={() => setEditingSynthesis(false)} className="gap-1">
                                <X className="h-4 w-4" />
                                取消
                              </Button>
                            </div>
                          </div>
                        ) : (
                          <div className="space-y-3">
                            <MarkdownContent
                              content={selectedSynthesisRoundInfo.synthesis.edited_content ?? selectedSynthesisRoundInfo.synthesis.original_content}
                              className={AI_RESULT_BOX_CLASS}
                            />
                            {selectedSynthesisRoundInfo.synthesis.validation_error && (
                              <p className="text-sm text-destructive">
                                失败原因: {selectedSynthesisRoundInfo.synthesis.validation_error}
                              </p>
                            )}
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  ) : (
                    <Card>
                      <CardContent className="p-8 text-center text-muted-foreground">
                        <Sparkles className="h-8 w-8 mx-auto mb-2 opacity-50" />
                        <p>尚未生成综合提炼</p>
                      </CardContent>
                    </Card>
                  )}
                </div>
              )}
            </TabsContent>

            <TabsContent value="host-input" className="mt-0 space-y-6">
              {!currentRound ? (
                <Card>
                  <CardContent className="p-8 text-center text-muted-foreground">暂无轮次数据</CardContent>
                </Card>
              ) : (
                <>
                  <Card>
                    <CardHeader>
                      <CardTitle className="text-lg">
                        主持人输入 - 第 {currentRound.round_number} 轮: {currentRound.title}
                      </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-4">
                      <p className="text-sm text-muted-foreground">
                        在此输入线下共识的结果或补充信息，将合并到最终导出中。
                      </p>
                      <Textarea
                        value={hostInputContent}
                        onChange={(event) => setHostInputContent(event.target.value)}
                        rows={10}
                        placeholder="请输入内容..."
                        className="font-mono text-sm"
                        disabled={isCompletedView}
                      />
                      <Button onClick={handleSaveHostInput} disabled={isCompletedView || savingHostInput} className="gap-2">
                        {savingHostInput ? <LoadingSpinner size="sm" /> : <Save className="h-4 w-4" />}
                        保存
                      </Button>
                      {currentRound.host_input && (
                        <p className="text-xs text-muted-foreground">
                          上次保存: {new Date(currentRound.host_input.updated_at).toLocaleString("zh-CN")}
                        </p>
                      )}
                    </CardContent>
                  </Card>

                  <Card>
                    <CardHeader>
                      <CardTitle className="text-base">各轮主持人输入内容</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                      {workshop.rounds.map((round) => (
                        <div key={round.id} className="rounded-md border p-3 space-y-2">
                          <div className="flex items-center justify-between">
                            <h4 className="text-sm font-medium">第 {round.round_number} 轮: {round.title}</h4>
                            {round.host_input && copyButton(round.host_input.content, `host-input-${round.id}`)}
                          </div>
                          <div className="bg-muted/30 rounded-md p-3 text-sm whitespace-pre-wrap font-mono">
                            {round.host_input?.content || "暂无输入"}
                          </div>
                        </div>
                      ))}
                    </CardContent>
                  </Card>
                </>
              )}
            </TabsContent>

            <TabsContent value="knowledge" className="mt-0 space-y-6">
              <KnowledgeUploadPanel
                onUpload={uploadKnowledgeFile}
                onUploaded={fetchHost}
                disabled={!workshop}
              />

              <Card>
                <CardHeader>
                  <CardTitle className="text-lg">文档列表</CardTitle>
                </CardHeader>
                <CardContent>
                  {workshop.knowledge_docs.length === 0 ? (
                    <div className="py-8 text-center text-muted-foreground">
                      <BookOpen className="h-8 w-8 mx-auto mb-2 opacity-50" />
                      <p>暂无文档</p>
                      <p className="text-xs mt-1">上传文档以供 AI 参考</p>
                    </div>
                  ) : (
                    <ScrollArea className="max-h-96">
                      <div className="space-y-2">
                        {workshop.knowledge_docs.map((doc) => (
                          <div key={doc.id} className="flex items-center justify-between bg-muted/30 rounded-lg p-3 text-sm">
                            <div className="min-w-0 flex-1">
                              <p className="font-medium truncate">{doc.original_filename}</p>
                              <p className="text-xs text-muted-foreground">
                                {formatFileSize(doc.file_size)} &middot; {doc.chunk_count} 分块 &middot; {new Date(doc.uploaded_at).toLocaleString("zh-CN")}
                              </p>
                            </div>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handleDeleteDoc(doc.id)}
                              disabled={deletingDocId === doc.id}
                              className="text-destructive hover:text-destructive shrink-0 ml-2"
                            >
                              {deletingDocId === doc.id ? <LoadingSpinner size="sm" /> : <Trash2 className="h-4 w-4" />}
                            </Button>
                          </div>
                        ))}
                      </div>
                    </ScrollArea>
                  )}
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="export" className="mt-0 space-y-6">
              <Card>
                <CardHeader>
                  <CardTitle className="text-lg">导出 Markdown</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <p className="text-sm text-muted-foreground">
                    将所有轮次的内容、AI 分析结果、综合汇总和主持人输入导出为 Markdown 格式。
                  </p>
                  <div className="flex items-center gap-3">
                    <Button onClick={handleExport} disabled={exporting} className="gap-2">
                      {exporting ? <LoadingSpinner size="sm" /> : <Download className="h-4 w-4" />}
                      {exporting ? "生成中..." : "生成并预览"}
                    </Button>
                    {exportData && (
                      <Button variant="outline" onClick={handleDownloadExport} className="gap-2">
                        <Download className="h-4 w-4" />
                        下载
                      </Button>
                    )}
                  </div>
                </CardContent>
              </Card>

              <Dialog open={showExportDialog} onOpenChange={setShowExportDialog}>
                <DialogContent className="max-w-4xl max-h-[80vh]">
                  <DialogHeader>
                    <DialogTitle className="flex items-center gap-2">
                      <FileText className="h-5 w-5" />
                      导出预览
                    </DialogTitle>
                  </DialogHeader>
                  {exportData && (
                    <div className="space-y-4">
                      <div className="flex items-center justify-between gap-2">
                        <p className="text-sm text-muted-foreground">文件名: {exportData.filename}</p>
                        {copyButton(exportData.markdown, "export-markdown")}
                        <Button size="sm" onClick={handleDownloadExport} className="gap-1">
                          <Download className="h-4 w-4" />
                          下载
                        </Button>
                      </div>
                      <ScrollArea className="h-[50vh] rounded-md border">
                        <div className="p-4">
                          <MarkdownContent content={exportData.markdown} className="bg-transparent p-0" />
                        </div>
                      </ScrollArea>
                    </div>
                  )}
                </DialogContent>
              </Dialog>
            </TabsContent>
          </div>
        </div>
      </Tabs>
      <Dialog open={Boolean(leaderTarget)} onOpenChange={(open) => !open && setLeaderTarget(null)}>
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>设置组长</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <p className="text-sm text-muted-foreground">
              是否将 {leaderTarget?.name} 设置为第 {leaderTarget?.groupId} 组组长？
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setLeaderTarget(null)} disabled={settingLeader}>
                取消
              </Button>
              <Button onClick={handleConfirmSetLeader} disabled={settingLeader || isCompletedView}>
                {settingLeader ? <LoadingSpinner size="sm" /> : "确认设置"}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

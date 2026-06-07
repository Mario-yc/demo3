import { useState, useEffect, useCallback } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import { knowledgeApi } from "@/services/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { LoadingSpinner } from "@/components/Shared/LoadingSpinner";
import { Separator } from "@/components/ui/separator";
import { KnowledgeUploadPanel } from "@/components/Knowledge/KnowledgeUploadPanel";
import {
  FileText,
  Trash2,
  AlertCircle,
  ArrowLeft,
  Database,
  Loader2,
  CheckCircle2,
} from "lucide-react";
import type { KnowledgeDocument } from "@/types";

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr);
  return d.toLocaleDateString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function KnowledgeBasePage() {
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const adminFromUrl = searchParams.get("admin") || "";
  const workshopIdFromUrl = searchParams.get("workshop");
  const workshopId = workshopIdFromUrl ? parseInt(workshopIdFromUrl, 10) : null;

  // ── Auth state ──────────────────────────────────────────────────────
  const [adminCode, setAdminCode] = useState(adminFromUrl);
  const [validated, setValidated] = useState(false);
  const [validating, setValidating] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [workshopTitle, setWorkshopTitle] = useState<string | null>(null);

  // ── Documents state ─────────────────────────────────────────────────
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [loading, setLoading] = useState(false);
  const [docError, setDocError] = useState<string | null>(null);

  // ── Delete state ────────────────────────────────────────────────────
  const [deletingId, setDeletingId] = useState<number | null>(null);

  // ── Validate on mount ───────────────────────────────────────────────
  useEffect(() => {
    if (adminFromUrl) {
      setValidating(true);
      setAuthError(null);
      knowledgeApi
        .validateAdmin(adminFromUrl)
        .then((res) => {
          if (res.valid) {
            setValidated(true);
            setWorkshopTitle(res.workshop_title ?? null);
            if (res.workshop_id && !workshopId) {
              const params = new URLSearchParams(searchParams);
              params.set("admin", adminFromUrl);
              params.set("workshop", String(res.workshop_id));
              navigate(`/knowledge?${params.toString()}`, { replace: true });
            }
          } else {
            setAuthError("管理码无效");
          }
        })
        .catch((err) => {
          setAuthError(err instanceof Error ? err.message : "验证失败");
        })
        .finally(() => setValidating(false));
    }
  }, [adminFromUrl, navigate, searchParams, workshopId]);

  // ── Fetch documents ─────────────────────────────────────────────────
  const fetchDocuments = useCallback(async () => {
    if (!workshopId) return;
    setLoading(true);
    setDocError(null);
    try {
      const docs = await knowledgeApi.list(workshopId, adminCode);
      setDocuments(docs);
    } catch (err) {
      setDocError(err instanceof Error ? err.message : "获取文档列表失败");
    } finally {
      setLoading(false);
    }
  }, [workshopId, adminCode]);

  useEffect(() => {
    if (validated && workshopId) {
      fetchDocuments();
    }
  }, [validated, workshopId, fetchDocuments]);

  // ── Validate admin code ─────────────────────────────────────────────
  const handleValidate = async () => {
    const code = adminCode.trim();
    if (!code) return;
    setValidating(true);
    setAuthError(null);
    try {
      const res = await knowledgeApi.validateAdmin(code);
      if (res.valid) {
        setValidated(true);
        setWorkshopTitle(res.workshop_title ?? null);
        // Update URL
        const params = new URLSearchParams(searchParams);
        params.set("admin", code);
        if (res.workshop_id) {
          params.set("workshop", String(res.workshop_id));
        } else if (workshopId) {
          params.set("workshop", String(workshopId));
        }
        navigate(`/knowledge?${params.toString()}`, { replace: true });
      } else {
        setAuthError("管理码无效，请重试");
      }
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : "验证失败");
    } finally {
      setValidating(false);
    }
  };

  // ── Upload ──────────────────────────────────────────────────────────
  const createKnowledgeUploadTask = useCallback(
    async (file: File, contentBase64: string) => {
      if (!workshopId) throw new Error("缺少会议信息，请重新进入知识库管理页面");
      return knowledgeApi.createUploadTask(
        file.name,
        contentBase64,
        file.type || "application/octet-stream",
        workshopId,
        adminCode,
      );
    },
    [workshopId, adminCode],
  );

  // ── Delete ──────────────────────────────────────────────────────────
  const handleDelete = async (docId: number) => {
    if (!workshopId) return;
    if (!window.confirm("确认删除此文档？删除后不可恢复。")) return;
    setDeletingId(docId);
    try {
      await knowledgeApi.delete(docId, workshopId, adminCode);
      setDocuments((prev) => prev.filter((d) => d.id !== docId));
    } catch (err) {
      alert(err instanceof Error ? err.message : "删除失败");
    } finally {
      setDeletingId(null);
    }
  };

  // ── Render: Validating URL code ─────────────────────────────────────
  if (adminFromUrl && validating && !validated) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-3.5rem)]">
        <LoadingSpinner size="lg" text="验证管理码中..." />
      </div>
    );
  }

  // ── Render: Auth form ───────────────────────────────────────────────
  if (!validated) {
    return (
      <div className="flex items-center justify-center min-h-[calc(100vh-3.5rem)]">
        <Card className="w-full max-w-md">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="h-5 w-5" />
              知识库管理
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="adminCode">管理码</Label>
              <Input
                id="adminCode"
                value={adminCode}
                onChange={(e) => setAdminCode(e.target.value)}
                placeholder="请输入知识库管理码"
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleValidate();
                }}
                autoFocus
              />
            </div>
            {authError && (
              <div className="flex items-center gap-2 text-sm text-destructive">
                <AlertCircle className="h-4 w-4" />
                {authError}
              </div>
            )}
            <Button
              className="w-full"
              onClick={handleValidate}
              disabled={validating || !adminCode.trim()}
            >
              {validating ? (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              ) : null}
              验证
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="w-full"
              onClick={() => navigate("/")}
            >
              <ArrowLeft className="h-4 w-4 mr-2" />
              返回首页
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  // ── Render: Main panel ──────────────────────────────────────────────
  return (
    <div className="max-w-5xl mx-auto px-6 py-8 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate("/")}>
            <ArrowLeft className="h-4 w-4 mr-1" />
            返回
          </Button>
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <Database className="h-6 w-6" />
              知识库管理
            </h1>
            {workshopTitle && (
              <p className="text-sm text-muted-foreground">
                工作坊：{workshopTitle}
              </p>
            )}
          </div>
        </div>
        <Badge variant="outline" className="gap-1">
          <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
          已认证
        </Badge>
      </div>

      <Separator />

      {/* Upload area */}
      <div>
        <h2 className="text-lg font-semibold mb-3">上传文档</h2>
        <KnowledgeUploadPanel
          variant="dropzone"
          onCreateUploadTask={createKnowledgeUploadTask}
          onPollUploadTask={knowledgeApi.getUploadTask}
          onUploaded={fetchDocuments}
        />
      </div>

      <Separator />

      {/* Document list */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">文档列表</h2>
          {documents.length > 0 && (
            <span className="text-sm text-muted-foreground">
              共 {documents.length} 个文档
            </span>
          )}
        </div>

        {loading ? (
          <div className="py-12">
            <LoadingSpinner size="md" text="加载文档列表中..." />
          </div>
        ) : docError ? (
          <div className="flex flex-col items-center gap-3 py-12 text-center">
            <AlertCircle className="h-8 w-8 text-destructive" />
            <p className="text-sm text-destructive">{docError}</p>
            <Button variant="outline" size="sm" onClick={fetchDocuments}>
              重试
            </Button>
          </div>
        ) : documents.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <FileText className="h-10 w-10 mb-3" />
            <p className="text-sm">暂无文档</p>
            <p className="text-xs mt-1">上传知识库文档以增强 AI 效果</p>
          </div>
        ) : (
          <div className="rounded-lg border overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="bg-muted/50 border-b">
                    <th className="text-left font-medium px-4 py-3 text-muted-foreground">文件名</th>
                    <th className="text-left font-medium px-4 py-3 text-muted-foreground">类型</th>
                    <th className="text-right font-medium px-4 py-3 text-muted-foreground">大小</th>
                    <th className="text-right font-medium px-4 py-3 text-muted-foreground">分块数</th>
                    <th className="text-left font-medium px-4 py-3 text-muted-foreground">嵌入模型</th>
                    <th className="text-left font-medium px-4 py-3 text-muted-foreground">上传时间</th>
                    <th className="text-center font-medium px-4 py-3 text-muted-foreground">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {documents.map((doc) => (
                    <tr key={doc.id} className="border-b last:border-b-0 hover:bg-muted/30 transition-colors">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <FileText className="h-4 w-4 text-muted-foreground shrink-0" />
                          <span className="truncate max-w-[200px]" title={doc.original_filename}>
                            {doc.original_filename}
                          </span>
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <Badge variant="outline" className="text-[10px]">
                          {doc.content_type === "text/plain" ? "TXT" :
                           doc.content_type === "text/markdown" || doc.content_type === "text/x-markdown" ? "MD" :
                           doc.content_type.split("/").pop()?.toUpperCase() ?? doc.content_type}
                        </Badge>
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                        {formatBytes(doc.file_size)}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums">
                        {doc.chunk_count}
                      </td>
                      <td className="px-4 py-3">
                        <code className="text-[11px] bg-muted px-1.5 py-0.5 rounded">
                          {doc.embedding_model}
                        </code>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground whitespace-nowrap">
                        {formatDate(doc.uploaded_at)}
                      </td>
                      <td className="px-4 py-3 text-center">
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-8 w-8 text-muted-foreground hover:text-destructive"
                          onClick={() => handleDelete(doc.id)}
                          disabled={deletingId === doc.id}
                        >
                          {deletingId === doc.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Trash2 className="h-4 w-4" />
                          )}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

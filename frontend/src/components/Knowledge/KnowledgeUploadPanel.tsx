import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import { AlertCircle, CheckCircle2, FileUp, Loader2, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import type { KnowledgeUploadTask } from "@/types";

export const KNOWLEDGE_ACCEPT = ".ppt,.pptx,.xls,.xlsx,.doc,.docx,.md,.txt";
export const KNOWLEDGE_FORMAT_LABEL = "支持 PPT, PPTX, XLS, XLSX, DOC, DOCX, MD, TXT";

const ALLOWED_EXTENSIONS = new Set(["ppt", "pptx", "xls", "xlsx", "doc", "docx", "md", "txt"]);

type UploadStage =
  | "idle"
  | "reading"
  | "pending"
  | "uploading"
  | "uploaded"
  | "model_loading"
  | "index_initializing"
  | "parsing"
  | "chunking"
  | "extracting"
  | "merging"
  | "embedding"
  | "writing"
  | "completed"
  | "done"
  | "failed";

const STAGE_META: Record<UploadStage, { label: string; percent: number }> = {
  idle: { label: "等待上传", percent: 0 },
  reading: { label: "正在读取文件", percent: 8 },
  pending: { label: "等待处理", percent: 10 },
  uploading: { label: "正在上传文件", percent: 10 },
  uploaded: { label: "文件保存完成", percent: 10 },
  model_loading: { label: "正在加载 embedding 模型", percent: 20 },
  index_initializing: { label: "正在初始化知识库索引", percent: 30 },
  parsing: { label: "正在解析文件内容", percent: 40 },
  chunking: { label: "正在切分文档内容", percent: 50 },
  extracting: { label: "正在抽取实体与关系", percent: 65 },
  merging: { label: "正在合并知识图谱", percent: 78 },
  embedding: { label: "正在生成向量", percent: 90 },
  writing: { label: "正在写入知识库", percent: 96 },
  completed: { label: "持久化完成", percent: 100 },
  done: { label: "上传完成", percent: 100 },
  failed: { label: "上传失败", percent: 100 },
};

function normalizeStage(stage: string): UploadStage {
  return stage in STAGE_META ? (stage as UploadStage) : "uploading";
}

function cleanErrorMessage(err: unknown, fallback = "上传失败，请重试") {
  if (!(err instanceof Error)) return fallback;
  const message = err.message.replace(/^API error \d+:\s*/, "").trim();
  return message || fallback;
}

function extensionOf(file: File) {
  return file.name.split(".").pop()?.toLowerCase() ?? "";
}

async function readFileBase64(file: File, onProgress: (percent: number) => void) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onprogress = (event) => {
      if (!event.lengthComputable) return;
      onProgress(Math.min(25, Math.round((event.loaded / event.total) * 25)));
    };
    reader.onload = () => {
      const result = String(reader.result ?? "");
      const base64 = result.split(",")[1] ?? result;
      if (!base64) {
        reject(new Error("文件读取失败，未获取到有效内容"));
        return;
      }
      resolve(base64);
    };
    reader.onerror = () => reject(new Error("文件读取失败，请重新选择文件"));
    reader.readAsDataURL(file);
  });
}

interface KnowledgeUploadPanelProps {
  onCreateUploadTask: (file: File, contentBase64: string) => Promise<KnowledgeUploadTask>;
  onPollUploadTask: (taskId: string) => Promise<KnowledgeUploadTask>;
  onUploaded: () => Promise<unknown> | unknown;
  variant?: "card" | "dropzone";
  disabled?: boolean;
}

export function KnowledgeUploadPanel({
  onCreateUploadTask,
  onPollUploadTask,
  onUploaded,
  variant = "card",
  disabled = false,
}: KnowledgeUploadPanelProps) {
  const [stage, setStage] = useState<UploadStage>("idle");
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [currentFilename, setCurrentFilename] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const pollTimerRef = useRef<number | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploading = stage !== "idle" && stage !== "done" && stage !== "failed";

  const clearTimers = useCallback(() => {
    if (pollTimerRef.current !== null) {
      window.clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  useEffect(() => clearTimers, [clearTimers]);

  const setStageProgress = useCallback((next: UploadStage, overridePercent?: number) => {
    setStage(next);
    setProgress(overridePercent ?? STAGE_META[next].percent);
  }, []);

  const applyTask = useCallback((task: KnowledgeUploadTask) => {
    const nextStage = task.status === "success" ? "done" : task.status === "failed" ? "failed" : normalizeStage(task.stage);
    const fallback = STAGE_META[nextStage];
    setStage(nextStage);
    setProgress(Math.max(0, Math.min(task.progress ?? fallback.percent, 100)));
    setCurrentFilename(task.filename);
    if (task.status === "failed") {
      setError(task.error || "上传失败，请重试");
    }
  }, []);

  const pollTaskUntilDone = useCallback(async (taskId: string) => {
    const task = await onPollUploadTask(taskId);
    applyTask(task);
    if (task.status === "success") {
      await onUploaded();
      window.setTimeout(() => {
        setStageProgress("idle");
        setCurrentFilename(null);
      }, 1500);
      return;
    }
    if (task.status === "failed") return;
    pollTimerRef.current = window.setTimeout(() => {
      void pollTaskUntilDone(taskId);
    }, 1000);
  }, [applyTask, onPollUploadTask, onUploaded, setStageProgress]);

  const handleFile = useCallback(async (file: File) => {
    if (uploading || disabled) return;
    const ext = extensionOf(file);
    if (!ALLOWED_EXTENSIONS.has(ext)) {
      setStageProgress("failed");
      setError(`文件格式不支持：.${ext || "未知"}，${KNOWLEDGE_FORMAT_LABEL}`);
      return;
    }

    setError(null);
    setCurrentFilename(file.name);
    setStageProgress("reading");
    try {
      const contentBase64 = await readFileBase64(file, (percent) => {
        setProgress(Math.max(STAGE_META.reading.percent, percent));
      });
      setStageProgress("uploading", 10);
      const task = await onCreateUploadTask(file, contentBase64);
      applyTask(task);
      await pollTaskUntilDone(task.task_id);
    } catch (err) {
      clearTimers();
      setStageProgress("failed");
      setError(cleanErrorMessage(err));
    }
  }, [applyTask, clearTimers, disabled, onCreateUploadTask, pollTaskUntilDone, setStageProgress, uploading]);

  const handleInputChange = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) void handleFile(file);
    event.target.value = "";
  };

  const input = (
    <input
      ref={fileInputRef}
      type="file"
      className={variant === "card" ? "absolute inset-0 opacity-0 cursor-pointer" : "hidden"}
      onChange={handleInputChange}
      disabled={uploading || disabled}
      accept={KNOWLEDGE_ACCEPT}
    />
  );

  const status = stage === "idle" ? null : (
    <div className="mt-3 space-y-2">
      <div className="flex items-center justify-between text-xs">
        <span className={stage === "failed" ? "text-destructive" : "text-muted-foreground"}>
          {STAGE_META[stage].label}
        </span>
        <span className="tabular-nums text-muted-foreground">{Math.round(progress)}%</span>
      </div>
      {currentFilename && (
        <p className="truncate text-xs text-muted-foreground">当前文件：{currentFilename}</p>
      )}
      <Progress value={progress} className={stage === "failed" ? "bg-destructive/20" : undefined} />
    </div>
  );

  const errorNode = error ? (
    <div className="flex items-center gap-2 mt-2 text-sm text-destructive">
      <AlertCircle className="h-4 w-4 shrink-0" />
      <span>{error}</span>
    </div>
  ) : null;

  if (variant === "dropzone") {
    return (
      <div>
        <div
          className={`
            relative flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-8 transition-colors
            ${dragOver ? "border-primary bg-primary/5" : "border-muted-foreground/25 hover:border-muted-foreground/50"}
            ${uploading || disabled ? "pointer-events-none opacity-60" : "cursor-pointer"}
          `}
          onDrop={(event) => {
            event.preventDefault();
            setDragOver(false);
            const file = event.dataTransfer.files?.[0];
            if (file) void handleFile(file);
          }}
          onDragOver={(event) => {
            event.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onClick={() => fileInputRef.current?.click()}
        >
          {input}
          {uploading ? (
            <Loader2 className="h-10 w-10 text-primary animate-spin mb-3" />
          ) : stage === "done" ? (
            <CheckCircle2 className="h-10 w-10 text-green-500 mb-3" />
          ) : (
            <Upload className="h-10 w-10 text-muted-foreground mb-3" />
          )}
          <p className="text-sm font-medium">
            {uploading ? STAGE_META[stage].label : "拖拽文件到此处，或点击选择文件"}
          </p>
          <p className="text-xs text-muted-foreground mt-1">{KNOWLEDGE_FORMAT_LABEL}</p>
        </div>
        {status}
        {errorNode}
      </div>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-lg">上传文档</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <Button variant="outline" disabled={uploading || disabled} className="relative gap-2 w-fit">
            {uploading ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileUp className="h-4 w-4" />}
            {uploading ? STAGE_META[stage].label : "选择文件"}
            {input}
          </Button>
          <span className="text-xs text-muted-foreground">{KNOWLEDGE_FORMAT_LABEL}</span>
        </div>
        {status}
        {errorNode}
      </CardContent>
    </Card>
  );
}

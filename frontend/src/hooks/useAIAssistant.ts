import { useState, useCallback, useEffect, useRef } from "react";
import { aiApi } from "@/services/api";
import type { AIQuestion } from "@/types";

export function useAIAssistant(workshopId: number | null, participantId: number | null, roundId?: number | null) {
  const [history, setHistory] = useState<AIQuestion[]>([]);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const askingRef = useRef(false);

  const sortHistory = useCallback((items: AIQuestion[]) => {
    return [...items].sort((a, b) => {
      const timeDiff = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
      return timeDiff || a.id - b.id;
    });
  }, []);

  useEffect(() => {
    if (!error) return;
    const timer = window.setTimeout(() => setError(null), 5000);
    return () => window.clearTimeout(timer);
  }, [error]);

  const ask = useCallback(async (question: string) => {
    if (!workshopId || !participantId) return null;
    if (askingRef.current) return null;
    askingRef.current = true;
    setAsking(true);
    setError(null);
    try {
      const a = await aiApi.ask(workshopId, participantId, question);
      setHistory((prev) => sortHistory([...prev, a]));
      return a;
    } catch (err) {
      setError(err instanceof Error ? err.message : "AI 问答失败，请稍后重试");
      return null;
    } finally {
      askingRef.current = false;
      setAsking(false);
    }
  }, [workshopId, participantId, sortHistory]);

  const fetchHistory = useCallback(async () => {
    if (!workshopId || !participantId) return;
    try {
      setHistory(sortHistory(await aiApi.getHistory(workshopId, participantId, roundId)));
    } catch { /* ignore */ }
  }, [workshopId, participantId, roundId, sortHistory]);

  const clearHistory = useCallback(() => {
    setHistory([]);
    setError(null);
  }, []);

  const clearError = useCallback(() => setError(null), []);

  return { history, asking, error, ask, fetchHistory, clearHistory, clearError };
}

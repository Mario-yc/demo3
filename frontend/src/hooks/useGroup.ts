import { useState, useEffect, useCallback, useRef } from "react";
import { groupApi } from "@/services/api";
import type { Question, Answer, GroupRoundResult } from "@/types";

export function useGroup(workshopId: number | null, groupId: number | null, roundId?: number | null) {
  const [questions, setQuestions] = useState<Question[]>([]);
  const [answers, setAnswers] = useState<Answer[]>([]);
  const [aiResult, setAiResult] = useState<GroupRoundResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [aiLoading, setAiLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const requestLocksRef = useRef<Set<string>>(new Set());

  const fetchQuestions = useCallback(async () => {
    if (!workshopId || !groupId) return;
    setLoading(true);
    try {
      setQuestions(await groupApi.getQuestions(groupId, workshopId, roundId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "获取问题失败");
    } finally {
      setLoading(false);
    }
  }, [workshopId, groupId, roundId]);

  const fetchAnswers = useCallback(async () => {
    if (!workshopId || !groupId) return;
    try {
      setAnswers(await groupApi.getAnswers(groupId, workshopId, roundId));
    } catch { /* ignore */ }
  }, [workshopId, groupId, roundId]);

  const fetchAIResult = useCallback(async () => {
    if (!workshopId || !groupId) return;
    try {
      const r = await groupApi.getAIResult(groupId, workshopId, roundId);
      if (!roundId || r.round_id === roundId) setAiResult(r);
    } catch {
      setAiResult(null);
    }
  }, [workshopId, groupId, roundId]);

  const clearRoundState = useCallback(() => {
    setQuestions([]);
    setAnswers([]);
    setAiResult(null);
    setError(null);
  }, []);

  const submitAnswer = useCallback(async (participant_id: number, session_token: string, question_id: number, content: string) => {
    if (!groupId) return null;
    const key = `answer:${question_id}`;
    if (requestLocksRef.current.has(key)) return null;
    requestLocksRef.current.add(key);
    try {
      const a = await groupApi.submitAnswer(groupId, { participant_id, session_token, question_id, content });
      setAnswers((prev) => (prev.some((item) => item.id === a.id) ? prev : [...prev, a]));
      return a;
    } catch (err) {
      setError(err instanceof Error ? err.message : "提交失败");
      return null;
    } finally {
      requestLocksRef.current.delete(key);
    }
  }, [groupId]);

  const transferLeader = useCallback(async (
    participant_id: number,
    session_token: string,
    new_leader_participant_id: number,
  ) => {
    if (!workshopId || !groupId) return null;
    if (requestLocksRef.current.has("transfer-leader")) return null;
    requestLocksRef.current.add("transfer-leader");
    try {
      return await groupApi.transferLeader(
        groupId,
        workshopId,
        participant_id,
        session_token,
        new_leader_participant_id,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "队长转移失败");
      return null;
    } finally {
      requestLocksRef.current.delete("transfer-leader");
    }
  }, [workshopId, groupId]);

  const triggerAI = useCallback(async (participant_id: number, session_token: string) => {
    if (!workshopId || !groupId) return null;
    if (requestLocksRef.current.has("trigger-ai")) return null;
    requestLocksRef.current.add("trigger-ai");
    setAiLoading(true);
    try {
      const r = await groupApi.triggerAI(groupId, workshopId, participant_id, session_token, roundId);
      if (!roundId || r.round_id === roundId) setAiResult(r);
      return r;
    } catch (err) {
      setError(err instanceof Error ? err.message : "AI生成失败");
      return null;
    } finally {
      setAiLoading(false);
      requestLocksRef.current.delete("trigger-ai");
    }
  }, [workshopId, groupId, roundId]);

  const editAIResult = useCallback(async (
    participant_id: number,
    session_token: string,
    edited_content: string,
  ) => {
    if (!workshopId || !groupId) return null;
    if (requestLocksRef.current.has("edit-ai-result")) return null;
    requestLocksRef.current.add("edit-ai-result");
    try {
      const r = await groupApi.editAIResult(
        groupId,
        workshopId,
        participant_id,
        session_token,
        edited_content,
        roundId,
      );
      if (!roundId || r.round_id === roundId) setAiResult(r);
      return r;
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存 AI 结果失败");
      return null;
    } finally {
      requestLocksRef.current.delete("edit-ai-result");
    }
  }, [workshopId, groupId, roundId]);

  const addAnswer = useCallback((answer: Answer) => {
    setAnswers((prev) => {
      if (prev.some((a) => a.id === answer.id)) return prev;
      return [...prev, answer];
    });
  }, []);

  useEffect(() => {
    if (workshopId && groupId) {
      clearRoundState();
      fetchQuestions();
      fetchAnswers();
      fetchAIResult();
    }
  }, [workshopId, groupId, roundId, clearRoundState, fetchQuestions, fetchAnswers, fetchAIResult]);

  return {
    questions, answers, aiResult,
    loading, aiLoading, error,
    fetchQuestions, fetchAnswers, fetchAIResult, clearRoundState,
    submitAnswer, triggerAI, editAIResult, transferLeader, addAnswer,
  };
}

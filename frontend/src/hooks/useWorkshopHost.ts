import { useState, useEffect, useCallback } from "react";
import { workshopApi, groupApi } from "@/services/api";
import type { WorkshopHostView } from "@/types";

export function useWorkshopHost(workshopId: number | null, hostCode: string | null) {
  const [workshop, setWorkshop] = useState<WorkshopHostView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchHost = useCallback(async () => {
    if (!workshopId || !hostCode) return;
    setLoading(true);
    setError(null);
    try {
      const data = await workshopApi.getHost(workshopId, hostCode);
      setWorkshop(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "获取主持人视图失败");
    } finally {
      setLoading(false);
    }
  }, [workshopId, hostCode]);

  const unlockRound = useCallback(async () => {
    if (!workshopId || !hostCode) return null;
    try {
      const data = await workshopApi.unlockRound(workshopId, hostCode);
      setWorkshop(data);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "解锁轮次失败");
      return null;
    }
  }, [workshopId, hostCode]);

  const updateRoundSettings = useCallback(async (discussion_time?: number, input_time?: number) => {
    if (!workshopId || !hostCode) return null;
    try {
      const data = await workshopApi.updateRoundSettings(workshopId, hostCode, discussion_time, input_time);
      setWorkshop(data);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "更新设置失败");
      return null;
    }
  }, [workshopId, hostCode]);

  const previousRound = useCallback(async () => {
    if (!workshopId || !hostCode) return null;
    try {
      const data = await workshopApi.previousRound(workshopId, hostCode);
      setWorkshop(data);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "回到上一轮失败");
      return null;
    }
  }, [workshopId, hostCode]);

  const returnCurrentRound = useCallback(async () => {
    if (!workshopId || !hostCode) return null;
    try {
      const data = await workshopApi.returnCurrentRound(workshopId, hostCode);
      setWorkshop(data);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "返回当前轮次失败");
      return null;
    }
  }, [workshopId, hostCode]);

  const startTimer = useCallback(async () => {
    if (!workshopId || !hostCode) return null;
    try {
      const data = await workshopApi.startTimer(workshopId, hostCode);
      setWorkshop(data);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "开启计时失败");
      return null;
    }
  }, [workshopId, hostCode]);

  const submitHostInput = useCallback(async (round_id: number, content: string) => {
    if (!workshopId || !hostCode) return null;
    try {
      const data = await workshopApi.submitHostInput(workshopId, hostCode, round_id, content);
      setWorkshop((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          rounds: prev.rounds.map((r) =>
            r.id === round_id ? { ...r, host_input: data } : r
          ),
        };
      });
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
      return null;
    }
  }, [workshopId, hostCode]);

  const editGroupResult = useCallback(async (resultId: number, edited_content: string) => {
    if (!workshopId || !hostCode) return null;
    try {
      const data = await workshopApi.editGroupResult(workshopId, resultId, hostCode, edited_content);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "编辑失败");
      return null;
    }
  }, [workshopId, hostCode]);

  const editSynthesis = useCallback(async (roundId: number, edited_content: string) => {
    if (!workshopId || !hostCode) return null;
    try {
      const data = await workshopApi.editSynthesis(workshopId, roundId, hostCode, edited_content);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "编辑失败");
      return null;
    }
  }, [workshopId, hostCode]);

  const setGroupLeader = useCallback(async (groupId: number, new_leader_participant_id: number) => {
    if (!workshopId || !hostCode) return null;
    try {
      const data = await workshopApi.setGroupLeader(workshopId, groupId, hostCode, new_leader_participant_id);
      setWorkshop(data);
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "设置组长失败");
      return null;
    }
  }, [workshopId, hostCode]);

  const triggerSynthesis = useCallback(async (roundId: number) => {
    try {
      const data = await groupApi.synthesize(roundId);
      setWorkshop((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          rounds: prev.rounds.map((r) =>
            r.id === roundId ? { ...r, synthesis: data } : r
          ),
        };
      });
      return data;
    } catch (err) {
      setError(err instanceof Error ? err.message : "综合失败");
      return null;
    }
  }, []);

  const exportMarkdown = useCallback(async () => {
    if (!workshopId || !hostCode) return null;
    try {
      return await workshopApi.export(workshopId, hostCode);
    } catch (err) {
      setError(err instanceof Error ? err.message : "导出失败");
      return null;
    }
  }, [workshopId, hostCode]);

  useEffect(() => {
    if (workshopId && hostCode) fetchHost();
  }, [workshopId, hostCode, fetchHost]);

  return {
    workshop, loading, error,
    fetchHost, unlockRound, previousRound, returnCurrentRound, updateRoundSettings,
    startTimer,
    submitHostInput, editGroupResult, editSynthesis, setGroupLeader,
    triggerSynthesis, exportMarkdown,
  };
}

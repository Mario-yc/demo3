import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Check, Copy, RefreshCw, Sparkles, UserCog, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { LoadingSpinner } from "@/components/Shared/LoadingSpinner";
import { copyText } from "@/lib/clipboard";
import { workshopApi } from "@/services/api";
import {
  clearLastHostWorkshop,
  loadLastHostWorkshop,
  saveLastHostWorkshop,
  type LastHostWorkshop,
} from "@/lib/hostSession";
import {
  clearLastMemberWorkshop,
  loadLastMemberWorkshop,
  saveLastMemberWorkshop,
  type LastMemberWorkshop,
} from "@/lib/memberSession";
import type { WorkshopCreateResponse } from "@/types";

const DEFAULT_TITLE = "领导力共创研讨会";

export function HomePage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const loadingRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(() => {
    const saved = sessionStorage.getItem("workshop_notice");
    if (saved) sessionStorage.removeItem("workshop_notice");
    return saved;
  });

  const [hostName, setHostName] = useState("");
  const [workshopTitle, setWorkshopTitle] = useState(DEFAULT_TITLE);
  const [groupCount, setGroupCount] = useState(4);
  const [created, setCreated] = useState<WorkshopCreateResponse | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const [memberName, setMemberName] = useState("");
  const [inviteCode, setInviteCode] = useState("");

  const [hostCodeInput, setHostCodeInput] = useState("");
  const [lastHost, setLastHost] = useState<LastHostWorkshop | null>(() => loadLastHostWorkshop());
  const [lastMember, setLastMember] = useState<LastMemberWorkshop | null>(() => loadLastMemberWorkshop());

  useEffect(() => {
    if (!error) return;
    const timer = window.setTimeout(() => setError(null), 5000);
    return () => window.clearTimeout(timer);
  }, [error]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  useEffect(() => {
    const record = loadLastMemberWorkshop();
    if (!record) return;
    let cancelled = false;
    workshopApi.get(record.workshop_id, record.participant_id, record.session_token)
      .then((data) => {
        if (cancelled) return;
        if (data.status === "completed" || !data.participant) {
          clearLastMemberWorkshop(record);
          sessionStorage.removeItem("participant");
          setLastMember(null);
        }
      })
      .catch(() => {
        if (cancelled) return;
        clearLastMemberWorkshop(record);
        sessionStorage.removeItem("participant");
        setLastMember(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const persistHost = (w: WorkshopCreateResponse) => {
    saveLastHostWorkshop({
      workshop_id: w.id,
      host_code: w.host_code,
      title: w.title,
    });
    setLastHost(loadLastHostWorkshop());
  };

  const handleCreate = async () => {
    if (!hostName.trim()) return;
    if (loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    setError(null);
    try {
      const boundedGroupCount = Math.min(Math.max(groupCount, 1), 10);
      const w = await workshopApi.create(workshopTitle || DEFAULT_TITLE, hostName.trim(), boundedGroupCount);
      setCreated(w);
      persistHost(w);
    } catch (err) {
      setError(err instanceof Error ? err.message : "创建失败，请重试");
    } finally {
      setLoading(false);
    }
  };

  const handleJoin = async () => {
    if (!memberName.trim() || !inviteCode.trim()) return;
    if (loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    setError(null);
    try {
      const valid = await workshopApi.validateInvite(inviteCode.trim());
      if (!valid.valid || !valid.workshop_id) {
        setError("邀请码无效或研讨会已结束");
        return;
      }
      const p = await workshopApi.join(valid.workshop_id, memberName.trim(), inviteCode.trim());
      sessionStorage.setItem("participant", JSON.stringify(p));
      saveLastMemberWorkshop(inviteCode.trim().toUpperCase(), p);
      setLastMember(loadLastMemberWorkshop());
      navigate(`/workshop/${valid.workshop_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加入失败，请重试");
    } finally {
      loadingRef.current = false;
      setLoading(false);
    }
  };

  const handleHostRecover = async () => {
    const code = hostCodeInput.trim().toUpperCase();
    if (!code) return;
    if (loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    setError(null);
    try {
      const valid = await workshopApi.validateHost(code);
      if (!valid.valid || !valid.workshop_id) {
        setError("主持人码无效，请检查后重试");
        return;
      }
      saveLastHostWorkshop({
        workshop_id: valid.workshop_id,
        host_code: code,
        title: valid.workshop_title ?? DEFAULT_TITLE,
      });
      navigate(`/workshop/${valid.workshop_id}/host?code=${encodeURIComponent(code)}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "校验主持人码失败，请重试");
    } finally {
      loadingRef.current = false;
      setLoading(false);
    }
  };

  const handleContinueHost = () => {
    const record = loadLastHostWorkshop();
    if (!record) {
      clearLastHostWorkshop();
      setLastHost(null);
      setError("本机没有可继续的主持记录，请输入主持人码");
      return;
    }
    navigate(`/workshop/${record.workshop_id}/host?code=${encodeURIComponent(record.host_code)}`);
  };

  const handleContinueMember = async () => {
    const record = loadLastMemberWorkshop();
    if (!record) {
      clearLastMemberWorkshop();
      setLastMember(null);
      setError("本机没有可继续的成员记录，请重新输入邀请码加入研讨会");
      return;
    }
    if (loadingRef.current) return;
    loadingRef.current = true;
    setLoading(true);
    setError(null);
    try {
      const data = await workshopApi.get(record.workshop_id, record.participant_id, record.session_token);
      if (data.status === "completed") {
        clearLastMemberWorkshop(record);
        sessionStorage.removeItem("participant");
        setLastMember(null);
        setError("该研讨会已结束，请创建或加入新的研讨会。");
        return;
      }
      if (!data.participant) {
        clearLastMemberWorkshop(record);
        sessionStorage.removeItem("participant");
        setLastMember(null);
        setError("成员身份已失效，请重新输入邀请码加入研讨会");
        return;
      }
      sessionStorage.setItem("participant", JSON.stringify(data.participant));
      saveLastMemberWorkshop(record.invite_code, data.participant);
      setLastMember(loadLastMemberWorkshop());
      navigate(`/workshop/${record.workshop_id}`);
    } catch (err) {
      clearLastMemberWorkshop(record);
      sessionStorage.removeItem("participant");
      setLastMember(null);
      setError(err instanceof Error ? err.message : "恢复成员身份失败，请重新加入研讨会");
    } finally {
      loadingRef.current = false;
      setLoading(false);
    }
  };
  
  const copyCode = async (code: string, label: string) => {
    const copiedCode = await copyText(code);
    if (!copiedCode) {
      setError("复制失败，请手动复制");
      return;
    }
    setCopied(label);
    setTimeout(() => setCopied(null), 2000);
  };

  const enterHost = () => {
    if (!created) return;
    persistHost(created);
    navigate(`/workshop/${created.id}/host?code=${encodeURIComponent(created.host_code)}`);
  };

  const enterKB = () => {
    if (created) {
      navigate(`/knowledge?admin=${created.kb_admin_code}&workshop=${created.id}`);
    }
  };

  return (
    <div className="min-h-[calc(100vh-3.5rem)] flex flex-col">
      {notice && (
        <div className="bg-primary/10 px-6 py-2 text-sm text-primary">
          <div className="mx-auto max-w-2xl">{notice}</div>
        </div>
      )}
      <div className="flex-1 flex flex-col items-center justify-center px-4 py-12">
        <div className="max-w-2xl w-full text-center mb-10">
          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-primary/10 text-primary text-sm font-medium mb-6">
            <Sparkles className="h-4 w-4" />
            AI 驱动的领导力共创
          </div>
          <h1 className="text-4xl sm:text-5xl font-bold mb-4 tracking-tight">领导力共创研讨会</h1>
          <p className="text-lg text-muted-foreground max-w-lg mx-auto">
            主持人控场 · 多组共创 · AI 提炼 · 知识库增强 · 完整导出
          </p>
        </div>

        {created ? (
          <Card className="w-full max-w-lg">
            <CardContent className="p-6 space-y-4">
              <h3 className="font-semibold text-lg text-center">研讨会已创建</h3>
              <div className="space-y-3">
                {[
                  { label: "成员邀请码", value: created.invite_code },
                  { label: "主持人码", value: created.host_code },
                  { label: "知识库管理码", value: created.kb_admin_code },
                ].map(({ label, value }) => (
                  <div key={label} className="flex items-center justify-between bg-muted/50 rounded-lg p-3">
                    <span className="text-sm font-medium">{label}</span>
                    <div className="flex items-center gap-2">
                      <code className="text-lg font-bold tracking-wider">{value}</code>
                      <Button variant="ghost" size="icon" onClick={() => copyCode(value, label)}>
                        {copied === label ? <Check className="h-4 w-4 text-green-500" /> : <Copy className="h-4 w-4" />}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
              <div className="flex gap-3 pt-2">
                <Button className="flex-1" onClick={enterHost}>
                  <UserCog className="h-4 w-4 mr-2" />
                  进入主持人后台
                </Button>
                <Button variant="outline" onClick={enterKB}>
                  知识库管理
                </Button>
              </div>
            </CardContent>
          </Card>
        ) : (
          <Tabs defaultValue="member" className="w-full max-w-lg">
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="member">
                <Users className="h-4 w-4 mr-2" />
                成员入口
              </TabsTrigger>
              <TabsTrigger value="host">
                <UserCog className="h-4 w-4 mr-2" />
                主持人入口
              </TabsTrigger>
            </TabsList>

            <TabsContent value="member">
              <Card>
                <CardContent className="p-6 space-y-4">
                  {lastMember && (
                    <Button variant="secondary" className="w-full gap-2" onClick={handleContinueMember} disabled={loading}>
                      <RefreshCw className="h-4 w-4" />
                      继续上次研讨：{lastMember.name}（第 {lastMember.group_id} 组）
                    </Button>
                  )}
                  <div className="space-y-2">
                    <Label htmlFor="memberName">您的姓名</Label>
                    <Input
                      id="memberName"
                      value={memberName}
                      onChange={(event) => setMemberName(event.target.value)}
                      placeholder="请输入您的姓名"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="inviteCode">邀请码</Label>
                    <Input
                      id="inviteCode"
                      value={inviteCode}
                      onChange={(event) => setInviteCode(event.target.value.toUpperCase())}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") handleJoin();
                      }}
                      placeholder="请输入6位邀请码"
                      maxLength={6}
                      className="font-mono text-lg tracking-widest"
                    />
                  </div>
                  {error && <p className="text-sm text-destructive">{error}</p>}
                  <Button
                    className="w-full"
                    size="lg"
                    onClick={handleJoin}
                    disabled={loading || !memberName.trim() || inviteCode.length < 6}
                  >
                    {loading ? <LoadingSpinner size="sm" /> : <>加入研讨会<ArrowRight className="h-4 w-4 ml-2" /></>}
                  </Button>
                </CardContent>
              </Card>
            </TabsContent>

            <TabsContent value="host">
              <Card>
                <CardContent className="p-6 space-y-5">
                  {lastHost && (
                    <Button variant="secondary" className="w-full gap-2" onClick={handleContinueHost}>
                      <RefreshCw className="h-4 w-4" />
                      继续主持上次研讨会：{lastHost.title}
                    </Button>
                  )}

                  <div className="space-y-3 rounded-lg border p-4">
                    <div className="space-y-1">
                      <Label htmlFor="hostCodeRecover">返回主持人界面 / 我是主持人</Label>
                      <p className="text-xs text-muted-foreground">输入主持人码后进入对应主持人后台。</p>
                    </div>
                    <div className="flex gap-2">
                      <Input
                        id="hostCodeRecover"
                        value={hostCodeInput}
                        onChange={(event) => setHostCodeInput(event.target.value.toUpperCase())}
                        onKeyDown={(event) => {
                          if (event.key === "Enter") handleHostRecover();
                        }}
                        placeholder="请输入主持人码"
                        className="font-mono tracking-widest"
                        maxLength={8}
                      />
                      <Button onClick={handleHostRecover} disabled={loading || !hostCodeInput.trim()}>
                        进入
                      </Button>
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="hostName">主持人姓名</Label>
                    <Input
                      id="hostName"
                      value={hostName}
                      onChange={(event) => setHostName(event.target.value)}
                      placeholder="请输入您的姓名"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="title">研讨会标题</Label>
                    <Input
                      id="title"
                      value={workshopTitle}
                      onChange={(event) => setWorkshopTitle(event.target.value)}
                      placeholder={DEFAULT_TITLE}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="groupCount">小组数量</Label>
                    <Input
                      id="groupCount"
                      type="number"
                      min={1}
                      max={10}
                      value={groupCount}
                      onChange={(event) => {
                        const next = parseInt(event.target.value, 10);
                        setGroupCount(Number.isFinite(next) ? Math.min(Math.max(next, 1), 10) : 1);
                      }}
                    />
                  </div>
                  {error && <p className="text-sm text-destructive">{error}</p>}
                  <Button className="w-full" size="lg" onClick={handleCreate} disabled={loading || !hostName.trim()}>
                    {loading ? <LoadingSpinner size="sm" /> : <>创建研讨会<ArrowRight className="h-4 w-4 ml-2" /></>}
                  </Button>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        )}
      </div>

      <div className="border-t bg-muted/30 py-10">
        <div className="max-w-4xl mx-auto px-4">
          <h2 className="text-xl font-semibold text-center mb-8">四轮共创流程</h2>
          <div className="grid sm:grid-cols-4 gap-4">
            {[
              { step: "1", title: "关键领导力维度", desc: "各组提炼关键领导力维度" },
              { step: "2", title: "领导力维度分层", desc: "定义不同管理层级的差异" },
              { step: "3", title: "领导力行为描述", desc: "形成可观察的具体行为" },
              { step: "4", title: "领导力应用场景", desc: "收集落地应用建议" },
            ].map((item) => (
              <Card key={item.step} className="text-center">
                <CardContent className="p-4">
                  <div className="h-8 w-8 rounded-full bg-primary/10 text-primary text-sm font-bold flex items-center justify-center mx-auto mb-2">
                    {item.step}
                  </div>
                  <h4 className="font-medium text-sm mb-1">{item.title}</h4>
                  <p className="text-xs text-muted-foreground">{item.desc}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
